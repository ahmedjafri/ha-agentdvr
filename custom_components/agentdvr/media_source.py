"""Media source platform exposing AgentDVR recordings."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.components.http.auth import async_sign_path
from homeassistant.components.media_player import BrowseError, MediaClass, MediaType
from homeassistant.components.media_source import (
    BrowseMediaSource,
    MediaSource,
    MediaSourceItem,
    PlayMedia,
    Unresolvable,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .api import AgentDVRClient, AgentRecording
from .const import (
    DOMAIN,
    MAX_EVENTS_PER_CAMERA,
    OT_CAMERA,
    PLAYABLE_EXTENSIONS,
    THUMB_SIGN_EXPIRY,
)


async def async_get_media_source(hass: HomeAssistant) -> "AgentDVRMediaSource":
    """Set up the AgentDVR media source."""
    return AgentDVRMediaSource(hass)


class AgentDVRMediaSource(MediaSource):
    """Browse and resolve AgentDVR recordings.

    Identifier scheme:
        ""                        -> root (list cameras)
        "c/<oid>/<ot>"            -> a camera folder (list recordings)
        "r/<oid>/<ot>/<filename>" -> a single recording
    """

    name = "AgentDVR Recordings"

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialise the media source."""
        super().__init__(DOMAIN)
        self.hass = hass

    def _client(self) -> AgentDVRClient:
        """Return the client from the single config entry."""
        entries = self.hass.config_entries.async_entries(DOMAIN)
        if not entries or getattr(entries[0], "runtime_data", None) is None:
            raise BrowseError("AgentDVR is not configured")
        return entries[0].runtime_data

    # ------------------------------------------------------------------ #
    # Browse
    # ------------------------------------------------------------------ #
    async def async_browse_media(
        self, item: MediaSourceItem
    ) -> BrowseMediaSource:
        """Browse the recordings tree."""
        identifier = item.identifier or ""
        if identifier == "":
            return await self._browse_root()

        kind, _, rest = identifier.partition("/")
        if kind == "c":
            oid_s, _, ot_s = rest.partition("/")
            return await self._browse_camera(int(oid_s), int(ot_s or OT_CAMERA))

        raise BrowseError(f"Unknown path: {identifier}")

    async def _browse_root(self) -> BrowseMediaSource:
        client = self._client()
        cameras = await client.async_get_cameras()
        children = [
            BrowseMediaSource(
                domain=DOMAIN,
                identifier=f"c/{cam.oid}/{cam.ot}",
                media_class=MediaClass.DIRECTORY,
                media_content_type=MediaType.VIDEO,
                title=cam.name,
                can_play=False,
                can_expand=True,
                children_media_class=MediaClass.VIDEO,
            )
            for cam in cameras
        ]
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier="",
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.VIDEO,
            title="AgentDVR",
            can_play=False,
            can_expand=True,
            children=children,
            children_media_class=MediaClass.DIRECTORY,
        )

    async def _browse_camera(self, oid: int, ot: int) -> BrowseMediaSource:
        client = self._client()
        cameras = {cam.oid: cam.name for cam in await client.async_get_cameras()}
        recordings = await client.async_get_recordings(
            oid, ot, limit=MAX_EVENTS_PER_CAMERA
        )
        children = []
        for rec in recordings:
            playable = rec.extension in PLAYABLE_EXTENSIONS
            children.append(
                BrowseMediaSource(
                    domain=DOMAIN,
                    identifier=f"r/{rec.oid}/{rec.ot}/{rec.filename}",
                    media_class=MediaClass.VIDEO,
                    media_content_type=MediaType.VIDEO,
                    title=self._title(rec),
                    can_play=playable,
                    can_expand=False,
                    thumbnail=self._thumb_url(rec.oid, rec.filename),
                )
            )
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=f"c/{oid}/{ot}",
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.VIDEO,
            title=cameras.get(oid, f"Camera {oid}"),
            can_play=False,
            can_expand=True,
            children=children,
            children_media_class=MediaClass.VIDEO,
        )

    def _thumb_url(self, oid: int, filename: str) -> str:
        """Signed, same-origin thumbnail URL proxied through Home Assistant."""
        return async_sign_path(
            self.hass,
            f"/api/agentdvr/thumb/{oid}/{filename}",
            timedelta(seconds=THUMB_SIGN_EXPIRY),
            use_content_user=True,
        )

    @staticmethod
    def _title(rec: AgentRecording) -> str:
        local = dt_util.as_local(dt_util.utc_from_timestamp(rec.unix_ts))
        stamp = local.strftime("%Y-%m-%d %H:%M:%S")
        mins, secs = divmod(int(rec.duration), 60)
        duration = f"{mins}:{secs:02d}"
        tags = f" · {', '.join(rec.tags)}" if rec.tags else ""
        flag = (
            "" if rec.extension in PLAYABLE_EXTENSIONS else " (mkv – not playable)"
        )
        return f"{stamp} · {duration}{tags}{flag}"

    # ------------------------------------------------------------------ #
    # Resolve
    # ------------------------------------------------------------------ #
    async def async_resolve_media(self, item: MediaSourceItem) -> PlayMedia:
        """Resolve a recording to a playable URL."""
        kind, _, rest = (item.identifier or "").partition("/")
        if kind != "r":
            raise Unresolvable(f"Not a recording: {item.identifier}")

        oid_s, ot_s, filename = rest.split("/", 2)
        oid, ot = int(oid_s), int(ot_s)
        extension = "." + filename.rsplit(".", 1)[-1].lower()
        if extension not in PLAYABLE_EXTENSIONS:
            raise Unresolvable("MKV recordings are not playable in a browser")

        # Return a same-origin path (no query string) so Home Assistant signs it
        # for playback and the browser streams via our proxy, not AgentDVR.
        url = f"/api/agentdvr/stream/{oid}/{ot}/{filename}"
        return PlayMedia(url=url, mime_type="video/mp4")
