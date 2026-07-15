"""HTTP views that proxy AgentDVR media through Home Assistant.

Thumbnails and recording streams are proxied so the browser only ever talks to
Home Assistant (same origin). This means dashboards work even when the browser
can't resolve/reach the AgentDVR host directly, and any AgentDVR credentials
stay server-side. Access is gated by Home Assistant's signed-URL auth.

Recordings are remuxed to "faststart" on the way through: AgentDVR writes the
mp4 ``moov`` atom at the end of the file, which desktop browsers tolerate but
iOS/macOS AVPlayer (the Home Assistant mobile app's player) will not play over
HTTP. We move ``moov`` to the front and fix the chunk-offset tables, then serve
byte ranges from the rewritten buffer so seeking works.
"""

from __future__ import annotations

import asyncio
import logging
import struct
from collections import OrderedDict

from aiohttp import ClientError, web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .api import AgentDVRClient
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Cap on the total size of remuxed recordings held in memory. Recordings are
# short event clips (~1 MB), so this holds many at once; the oldest are evicted.
CACHE_MAX_BYTES = 128 * 1024 * 1024

# mp4 atoms that contain child atoms (where stco/co64 offset tables live).
_CONTAINERS = frozenset(
    (b"moov", b"trak", b"mdia", b"minf", b"stbl", b"edts", b"udta")
)


def _client(hass: HomeAssistant) -> AgentDVRClient | None:
    """Return the client from the (single) config entry, if set up."""
    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries or getattr(entries[0], "runtime_data", None) is None:
        return None
    return entries[0].runtime_data


def _parse_range(header: str | None, total: int) -> tuple[int, int] | None:
    """Resolve a single ``bytes=`` range to inclusive (start, end) offsets.

    Returns ``None`` when there is no range, the syntax is unsupported
    (multi-range/invalid), or the range is unsatisfiable — callers then send
    the whole body.
    """
    if not header or "," in header:
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
            return max(0, total - length), total - 1
        start = int(start_s)
        end = int(end_s) if end_s else total - 1
    except ValueError:
        return None
    end = min(end, total - 1)
    if start > end or start < 0:
        return None
    return start, end


# ---------------------------------------------------------------------------- #
# mp4 faststart remux
# ---------------------------------------------------------------------------- #
def _iter_atoms(buf: bytes, start: int, end: int):
    """Yield (type, offset, size, header_len) for atoms in ``buf[start:end]``."""
    off = start
    while off + 8 <= end:
        size = struct.unpack(">I", buf[off:off + 4])[0]
        typ = bytes(buf[off + 4:off + 8])
        hdr = 8
        if size == 1:
            size = struct.unpack(">Q", buf[off + 8:off + 16])[0]
            hdr = 16
        elif size == 0:
            size = end - off
        if size < hdr or off + size > end:
            return
        yield typ, off, size, hdr
        off += size


def _shift_chunk_offsets(buf: bytearray, start: int, end: int, shift: int) -> None:
    """Add ``shift`` to every stco/co64 entry within ``buf[start:end]``."""
    for typ, off, size, hdr in _iter_atoms(buf, start, end):
        if typ in _CONTAINERS:
            _shift_chunk_offsets(buf, off + hdr, off + size, shift)
        elif typ in (b"stco", b"co64"):
            wide = typ == b"co64"
            base = off + hdr + 4  # skip version/flags
            count = struct.unpack(">I", buf[base:base + 4])[0]
            pos = base + 4
            for _ in range(count):
                if wide:
                    val = struct.unpack(">Q", buf[pos:pos + 8])[0]
                    struct.pack_into(">Q", buf, pos, val + shift)
                    pos += 8
                else:
                    val = struct.unpack(">I", buf[pos:pos + 4])[0]
                    struct.pack_into(">I", buf, pos, (val + shift) & 0xFFFFFFFF)
                    pos += 4


def _faststart(data: bytes) -> bytes:
    """Return ``data`` with the mp4 ``moov`` atom relocated to the front.

    Returns the input unchanged if it isn't a moov+mdat mp4 or is already
    faststart. On any parse error the original bytes are returned so playback
    degrades to the (desktop-only) status quo rather than failing.
    """
    try:
        tops = list(_iter_atoms(data, 0, len(data)))
        moov = next((a for a in tops if a[0] == b"moov"), None)
        mdat = next((a for a in tops if a[0] == b"mdat"), None)
        if moov is None or mdat is None or moov[1] < mdat[1]:
            return data

        _, m_off, m_size, _ = moov
        moov_bytes = bytearray(data[m_off:m_off + m_size])
        # Media data shifts back by the size of the relocated moov atom, so
        # every absolute chunk offset must grow by the same amount.
        _shift_chunk_offsets(moov_bytes, 8, len(moov_bytes), m_size)

        ftyp = tops[0] if tops and tops[0][0] == b"ftyp" else None
        out = bytearray()
        if ftyp:
            out += data[ftyp[1]:ftyp[1] + ftyp[2]]
        out += moov_bytes
        for typ, off, size, _hdr in tops:
            if typ == b"moov" or (ftyp is not None and off == ftyp[1]):
                continue
            out += data[off:off + size]
        return bytes(out)
    except (struct.error, ValueError, IndexError):
        _LOGGER.debug("faststart remux failed; serving original bytes")
        return data


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
        # Recordings are immutable, so remuxed bytes are cached indefinitely
        # (bounded by CACHE_MAX_BYTES). Per-key locks collapse the burst of
        # range requests AVPlayer fires for a cold file into one fetch.
        self._cache: OrderedDict[tuple[int, int, str], bytes] = OrderedDict()
        self._cache_bytes = 0
        self._locks: dict[tuple[int, int, str], asyncio.Lock] = {}

    async def get(
        self, request: web.Request, kind: str, tail: str
    ) -> web.StreamResponse:
        """Proxy a thumbnail or stream from AgentDVR."""
        client = _client(self.hass)
        if client is None:
            raise web.HTTPNotFound

        if kind == "thumb":
            try:
                oid, filename = tail.split("/", 1)
                oid_i = int(oid)
            except (ValueError, IndexError) as err:
                raise web.HTTPBadRequest from err
            return await self._thumb(client, oid_i, filename)

        if kind == "stream":
            try:
                oid, ot, filename = tail.split("/", 2)
                oid_i, ot_i = int(oid), int(ot)
            except (ValueError, IndexError) as err:
                raise web.HTTPBadRequest from err
            return await self._stream(request, client, oid_i, ot_i, filename)

        raise web.HTTPNotFound

    async def _thumb(
        self, client: AgentDVRClient, oid: int, filename: str
    ) -> web.StreamResponse:
        """Fetch and return a recording thumbnail (small; buffered)."""
        try:
            upstream = await client.session.get(
                client.thumb_url(oid, filename), auth=client.auth
            )
        except ClientError as err:
            _LOGGER.error("AgentDVR thumbnail request failed: %s", err)
            raise web.HTTPBadGateway from err
        try:
            if upstream.status != 200:
                raise web.HTTPBadGateway
            body = await upstream.read()
        finally:
            upstream.release()
        return web.Response(body=body, content_type="image/jpeg")

    async def _stream(
        self,
        request: web.Request,
        client: AgentDVRClient,
        oid: int,
        ot: int,
        filename: str,
    ) -> web.StreamResponse:
        """Serve a faststart-remuxed recording with byte-range support."""
        data = await self._recording_bytes(client, oid, ot, filename)
        if data is None:
            raise web.HTTPBadGateway

        total = len(data)
        rng = _parse_range(request.headers.get("Range"), total)
        if rng is None:
            return web.Response(
                body=data,
                content_type="video/mp4",
                headers={"Accept-Ranges": "bytes"},
            )

        start, end = rng
        return web.Response(
            status=206,
            body=data[start:end + 1],
            content_type="video/mp4",
            headers={
                "Accept-Ranges": "bytes",
                "Content-Range": f"bytes {start}-{end}/{total}",
            },
        )

    async def _recording_bytes(
        self, client: AgentDVRClient, oid: int, ot: int, filename: str
    ) -> bytes | None:
        """Return the faststart-remuxed recording, fetching/caching as needed."""
        key = (oid, ot, filename)
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            return cached

        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                return cached
            try:
                data = await self._fetch_and_remux(client, oid, ot, filename)
            finally:
                self._locks.pop(key, None)
            if data is not None:
                self._store(key, data)
            return data

    async def _fetch_and_remux(
        self, client: AgentDVRClient, oid: int, ot: int, filename: str
    ) -> bytes | None:
        """Download a recording from AgentDVR and remux it to faststart."""
        try:
            upstream = await client.session.get(
                client.stream_url(oid, ot, filename), auth=client.auth
            )
        except ClientError as err:
            _LOGGER.error("AgentDVR stream request failed: %s", err)
            return None
        try:
            if upstream.status != 200:
                return None
            raw = await upstream.read()
        except ClientError as err:
            _LOGGER.error("AgentDVR stream read failed: %s", err)
            return None
        finally:
            upstream.release()
        return _faststart(raw)

    def _store(self, key: tuple[int, int, str], data: bytes) -> None:
        """Cache remuxed bytes, evicting oldest entries past the size cap."""
        if len(data) > CACHE_MAX_BYTES:
            return
        self._cache[key] = data
        self._cache.move_to_end(key)
        self._cache_bytes += len(data)
        while self._cache_bytes > CACHE_MAX_BYTES and len(self._cache) > 1:
            _, evicted = self._cache.popitem(last=False)
            self._cache_bytes -= len(evicted)
