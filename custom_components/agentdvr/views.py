"""HTTP views that proxy AgentDVR media through Home Assistant.

Thumbnails and recording streams are proxied so the browser only ever talks to
Home Assistant (same origin). This means dashboards work even when the browser
can't resolve/reach the AgentDVR host directly, and any AgentDVR credentials
stay server-side. Access is gated by Home Assistant's signed-URL auth.
"""

from __future__ import annotations

import logging

from aiohttp import ClientError, ClientResponse, web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .api import AgentDVRClient
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STREAM_CHUNK = 64 * 1024


def _client(hass: HomeAssistant) -> AgentDVRClient | None:
    """Return the client from the (single) config entry, if set up."""
    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries or getattr(entries[0], "runtime_data", None) is None:
        return None
    return entries[0].runtime_data


def _content_length(upstream: ClientResponse) -> int | None:
    """Total body length from the upstream response, if it advertised one."""
    raw = upstream.headers.get("Content-Length")
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _parse_range(header: str | None, total: int | None) -> tuple[int, int] | None:
    """Resolve a single ``bytes=`` range to inclusive (start, end) offsets.

    Returns ``None`` when there is no range, the total size is unknown, the
    syntax is unsupported (multi-range/invalid), or the range is unsatisfiable —
    callers then fall back to sending the whole body.
    """
    if not header or total is None or "," in header:
        return None
    units, _, spec = header.partition("=")
    if units.strip().lower() != "bytes":
        return None
    start_s, sep, end_s = spec.strip().partition("-")
    if not sep:
        return None
    try:
        if not start_s:
            # Suffix range: bytes=-N -> last N bytes.
            length = int(end_s)
            if length <= 0:
                return None
            start = max(0, total - length)
            return start, total - 1
        start = int(start_s)
        end = int(end_s) if end_s else total - 1
    except ValueError:
        return None
    end = min(end, total - 1)
    if start > end or start < 0:
        return None
    return start, end


class AgentDVRMediaView(HomeAssistantView):
    """Proxy AgentDVR thumbnails and recording streams.

    URLs:
        /api/agentdvr/thumb/<oid>/<filename>
        /api/agentdvr/stream/<oid>/<ot>/<filename>

    Requests are authenticated via Home Assistant signed URLs (``authSig``),
    which is why ``requires_auth`` is left at its default of ``True``.
    """

    url = "/api/agentdvr/{kind}/{tail:.*}"
    name = "api:agentdvr:media"

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialise the view."""
        self.hass = hass

    async def get(
        self, request: web.Request, kind: str, tail: str
    ) -> web.StreamResponse:
        """Proxy a thumbnail or stream from AgentDVR."""
        client = _client(self.hass)
        if client is None:
            raise web.HTTPNotFound

        try:
            if kind == "thumb":
                oid, filename = tail.split("/", 1)
                upstream_url = client.thumb_url(int(oid), filename)
            elif kind == "stream":
                oid, ot, filename = tail.split("/", 2)
                upstream_url = client.stream_url(int(oid), int(ot), filename)
            else:
                raise web.HTTPNotFound
        except (ValueError, IndexError) as err:
            raise web.HTTPBadRequest from err

        # Forward the client's Range header so byte-serving works end to end.
        # iOS/macOS AVPlayer (the Home Assistant mobile app's video player)
        # probes with a Range request and refuses to play unless the server
        # answers 206 Partial Content with Accept-Ranges, hence the crossed-out
        # play button. Passing the header through lets AgentDVR do the slicing.
        headers = {}
        if kind == "stream" and (rng := request.headers.get("Range")):
            headers["Range"] = rng

        try:
            upstream = await client.session.get(
                upstream_url, auth=client.auth, headers=headers or None
            )
        except ClientError as err:
            _LOGGER.error("AgentDVR media request failed: %s", err)
            raise web.HTTPBadGateway from err

        if upstream.status not in (200, 206):
            upstream.release()
            raise web.HTTPBadGateway

        # Thumbnails are small: buffer and return.
        if kind == "thumb":
            try:
                body = await upstream.read()
            finally:
                upstream.release()
            return web.Response(body=body, content_type="image/jpeg")

        # Streams: always advertise range support and relay chunks so large
        # files aren't buffered in memory.
        resp_headers = {
            "Content-Type": "video/mp4",
            "Accept-Ranges": "bytes",
        }

        if upstream.status == 206:
            # AgentDVR honoured the range: mirror its 206 verbatim.
            for header in ("Content-Length", "Content-Range"):
                if (value := upstream.headers.get(header)) is not None:
                    resp_headers[header] = value
            return await self._relay(request, upstream, 206, resp_headers)

        # Upstream returned the whole file (200) — streamFile.cgi is chunked and
        # ignores Range. If the client asked for a range, slice the response
        # ourselves; without this, AVPlayer gets a 200 to its range probe and
        # refuses to play. The total size can't come from the chunked upstream,
        # so it rides in on ``len`` (set when the media source resolves the URL).
        total = _content_length(upstream)
        if total is None and (len_q := request.query.get("len")):
            try:
                total = int(len_q)
            except ValueError:
                total = None
        rng = _parse_range(request.headers.get("Range"), total)
        if rng is None:
            if total is not None:
                resp_headers["Content-Length"] = str(total)
            return await self._relay(request, upstream, 200, resp_headers)

        start, end = rng  # inclusive, satisfiable against a known total
        resp_headers["Content-Length"] = str(end - start + 1)
        resp_headers["Content-Range"] = f"bytes {start}-{end}/{total}"
        return await self._relay(
            request, upstream, 206, resp_headers, skip=start, length=end - start + 1
        )

    @staticmethod
    async def _relay(
        request: web.Request,
        upstream: ClientResponse,
        status: int,
        headers: dict[str, str],
        *,
        skip: int = 0,
        length: int | None = None,
    ) -> web.StreamResponse:
        """Stream upstream bytes to the client, optionally slicing a range.

        ``skip`` bytes are discarded from the front and at most ``length`` bytes
        are forwarded (used when we honour a range the upstream ignored).
        """
        downstream = web.StreamResponse(status=status, headers=headers)
        await downstream.prepare(request)
        remaining = length
        try:
            async for chunk in upstream.content.iter_chunked(STREAM_CHUNK):
                if skip:
                    if len(chunk) <= skip:
                        skip -= len(chunk)
                        continue
                    chunk = chunk[skip:]
                    skip = 0
                if remaining is not None:
                    if len(chunk) >= remaining:
                        await downstream.write(chunk[:remaining])
                        break
                    remaining -= len(chunk)
                await downstream.write(chunk)
        except (ClientError, ConnectionResetError) as err:
            _LOGGER.debug("AgentDVR stream ended: %s", err)
        finally:
            upstream.release()
        await downstream.write_eof()
        return downstream
