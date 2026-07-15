"""Async client for the AgentDVR local HTTP API."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from urllib.parse import urlencode

import aiohttp
import async_timeout

from .const import (
    DOTNET_EPOCH_OFFSET,
    OT_CAMERA,
    TICKS_PER_SECOND,
)

REQUEST_TIMEOUT = 15


class AgentDVRError(Exception):
    """Base error for the AgentDVR client."""


class AgentDVRAuthError(AgentDVRError):
    """Authentication with AgentDVR failed."""


class AgentDVRConnError(AgentDVRError):
    """Could not connect to AgentDVR."""


@dataclass(slots=True)
class AgentCamera:
    """A camera (object with typeID 2) exposed by AgentDVR."""

    oid: int
    ot: int
    name: str


@dataclass(slots=True)
class AgentRecording:
    """A single recorded clip for an object."""

    oid: int
    ot: int
    filename: str  # "fn", e.g. "5_2026-07-07_15-48-33_329.mp4"
    ticks: str  # "c", raw .NET ticks string
    unix_ts: float  # derived from ticks
    duration: int  # "d", seconds
    tags: list[str]  # "tg" split on ","
    size: int | None  # "sb" bytes, if present

    @property
    def extension(self) -> str:
        """Lower-cased file extension including the leading dot."""
        return "." + self.filename.rsplit(".", 1)[-1].lower()


class AgentDVRClient:
    """Minimal async wrapper around the AgentDVR local API.

    The client only needs an aiohttp session (injected by Home Assistant) and
    connection details. It exposes JSON queries plus absolute URL builders for
    browser-facing media (streams and thumbnails).
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        port: int,
        username: str | None = None,
        password: str | None = None,
        use_ssl: bool = False,
    ) -> None:
        """Initialise the client."""
        self._session = session
        scheme = "https" if use_ssl else "http"
        self._base = f"{scheme}://{host}:{port}"
        self._auth = (
            aiohttp.BasicAuth(username, password or "") if username else None
        )

    @property
    def base_url(self) -> str:
        """Base URL of the AgentDVR server (no trailing slash)."""
        return self._base

    @property
    def session(self) -> aiohttp.ClientSession:
        """The shared aiohttp session."""
        return self._session

    @property
    def auth(self) -> aiohttp.BasicAuth | None:
        """Optional basic auth for AgentDVR requests."""
        return self._auth

    # ------------------------------------------------------------------ #
    # Low-level
    # ------------------------------------------------------------------ #
    async def _get_json(self, path: str, **params: object) -> dict:
        url = f"{self._base}{path}"
        try:
            async with async_timeout.timeout(REQUEST_TIMEOUT):
                async with self._session.get(
                    url, params=params, auth=self._auth
                ) as resp:
                    if resp.status in (401, 403):
                        raise AgentDVRAuthError(url)
                    resp.raise_for_status()
                    # AgentDVR mislabels JSON content types, so don't validate.
                    return await resp.json(content_type=None)
        except AgentDVRAuthError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise AgentDVRConnError(str(err)) from err

    # ------------------------------------------------------------------ #
    # High-level queries
    # ------------------------------------------------------------------ #
    async def async_validate(self) -> None:
        """Verify the connection (used by the config flow). Raises on failure."""
        await self._get_json("/command/getObjects")

    async def async_get_cameras(self) -> list[AgentCamera]:
        """Return the list of camera objects (typeID 2)."""
        data = await self._get_json("/command/getObjects")
        cameras: list[AgentCamera] = []
        for obj in data.get("objectList", []):
            if obj.get("typeID") == OT_CAMERA:
                cameras.append(
                    AgentCamera(
                        oid=int(obj["id"]),
                        ot=OT_CAMERA,
                        name=obj.get("name") or f"Camera {obj['id']}",
                    )
                )
        return cameras

    async def async_get_recordings(
        self, oid: int, ot: int = OT_CAMERA, limit: int | None = None
    ) -> list[AgentRecording]:
        """Return recordings for an object, newest first."""
        data = await self._get_json(
            "/q/getEvents", oid=oid, ot=ot, compress="false"
        )
        recordings: list[AgentRecording] = []
        for ev in data.get("events", []):
            ticks = str(ev["c"])
            unix_ts = int(ticks) / TICKS_PER_SECOND - DOTNET_EPOCH_OFFSET
            recordings.append(
                AgentRecording(
                    oid=int(ev.get("oid", oid)),
                    ot=int(ev.get("ot", ot)),
                    filename=ev["fn"],
                    ticks=ticks,
                    unix_ts=unix_ts,
                    duration=int(ev.get("d", 0)),
                    tags=[t for t in ev.get("tg", "").split(",") if t],
                    size=ev.get("sb"),
                )
            )
        recordings.sort(key=lambda r: r.unix_ts, reverse=True)
        return recordings[:limit] if limit else recordings

    async def async_get_recording_size(
        self, oid: int, ot: int, filename: str
    ) -> int | None:
        """Return a recording's size in bytes from its event metadata.

        ``streamFile.cgi`` serves recordings chunked with no ``Content-Length``,
        so the size the range-proxy needs comes from the event's ``sb`` field.
        """
        data = await self._get_json(
            "/q/getEvents", oid=oid, ot=ot, compress="false"
        )
        for ev in data.get("events", []):
            if ev.get("fn") == filename and ev.get("sb") is not None:
                return int(ev["sb"])
        return None

    # ------------------------------------------------------------------ #
    # Browser-facing URL builders (absolute)
    # ------------------------------------------------------------------ #
    def stream_url(self, oid: int, ot: int, filename: str) -> str:
        """Absolute URL that streams a recording."""
        query = urlencode({"oid": oid, "ot": ot, "fn": filename})
        return f"{self._base}/streamFile.cgi?{query}"

    def thumb_url(self, oid: int, filename: str) -> str:
        """Absolute URL for a recording's thumbnail image.

        AgentDVR stores each recording's thumbnail as ``<basename>_large.jpg``.
        Passing the video filename (``.mp4``/``.mkv``) returns a placeholder
        image, so derive the thumbnail name from the recording basename.
        """
        thumb_name = filename.rsplit(".", 1)[0] + "_large.jpg"
        query = urlencode({"oid": oid, "fn": thumb_name})
        return f"{self._base}/fileThumb.jpg?{query}"
