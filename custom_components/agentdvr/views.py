"""HTTP views that proxy AgentDVR media through Home Assistant.

Thumbnails and recording streams are proxied so the browser only ever talks to
Home Assistant (same origin). This means dashboards work even when the browser
can't resolve/reach the AgentDVR host directly, and any AgentDVR credentials
stay server-side. Access is gated by Home Assistant's signed-URL auth.
"""

from __future__ import annotations

import logging

from aiohttp import ClientError, web

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

        try:
            upstream = await client.session.get(upstream_url, auth=client.auth)
        except ClientError as err:
            _LOGGER.error("AgentDVR media request failed: %s", err)
            raise web.HTTPBadGateway from err

        if upstream.status != 200:
            upstream.release()
            raise web.HTTPBadGateway

        # Thumbnails are small: buffer and return.
        if kind == "thumb":
            try:
                body = await upstream.read()
            finally:
                upstream.release()
            return web.Response(body=body, content_type="image/jpeg")

        # Streams: relay chunks so large files aren't buffered in memory.
        downstream = web.StreamResponse(
            status=200, headers={"Content-Type": "video/mp4"}
        )
        await downstream.prepare(request)
        try:
            async for chunk in upstream.content.iter_chunked(STREAM_CHUNK):
                await downstream.write(chunk)
        except (ClientError, ConnectionResetError) as err:
            _LOGGER.debug("AgentDVR stream ended: %s", err)
        finally:
            upstream.release()
        await downstream.write_eof()
        return downstream
