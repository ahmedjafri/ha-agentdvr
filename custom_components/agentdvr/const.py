"""Constants for the AgentDVR integration."""

from __future__ import annotations

DOMAIN = "agentdvr"
VERSION = "0.1.0"  # keep in sync with manifest.json

# Frontend card served over HTTP by this integration.
CARD_FILENAME = "agentdvr-recordings-card.js"
CARD_URL = f"/{DOMAIN}/{CARD_FILENAME}"

# Config keys not provided by homeassistant.const.
CONF_USE_SSL = "use_ssl"

DEFAULT_PORT = 8090

# AgentDVR object typeIDs.
OT_AUDIO = 1
OT_CAMERA = 2

# .NET ticks -> unix seconds:  unix = ticks / 1e7 - 62135596800
TICKS_PER_SECOND = 10_000_000
DOTNET_EPOCH_OFFSET = 62_135_596_800  # seconds between 0001-01-01 and 1970-01-01

# getEvents returns at most this many events per call (paginate with enddate).
EVENTS_PAGE_SIZE = 400
# Keep browsing responsive: one page of recordings per camera by default.
MAX_EVENTS_PER_CAMERA = 400

# Container extensions playable directly in a browser <video> element.
# AgentDVR also records .mkv, which browsers cannot play natively.
PLAYABLE_EXTENSIONS = (".mp4",)

# How long a signed thumbnail URL stays valid (seconds). Long enough to browse
# comfortably; thumbnails are re-signed on every browse.
THUMB_SIGN_EXPIRY = 24 * 60 * 60
