"""The AgentDVR integration.

Exposes AgentDVR recordings through Home Assistant's Media Source and serves a
bundled Lovelace card for browsing them on a dashboard.
"""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components import frontend
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import AgentDVRClient
from .const import CARD_FILENAME, CARD_URL, CONF_USE_SSL, DEFAULT_PORT, VERSION
from .views import AgentDVRMediaView

_LOGGER = logging.getLogger(__name__)

type AgentDVRConfigEntry = ConfigEntry[AgentDVRClient]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the bundled Lovelace card and media proxy once, at load."""
    card_path = Path(__file__).parent / "www" / CARD_FILENAME
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL, str(card_path), True)]
    )
    # Inject the card module on every dashboard; cache-bust on version bump.
    frontend.add_extra_js_url(hass, f"{CARD_URL}?v={VERSION}")
    # Proxy AgentDVR thumbnails/streams through HA (same-origin, signed auth).
    hass.http.register_view(AgentDVRMediaView(hass))
    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: AgentDVRConfigEntry
) -> bool:
    """Set up AgentDVR from a config entry."""
    client = AgentDVRClient(
        async_get_clientsession(hass),
        host=entry.data[CONF_HOST],
        port=entry.data.get(CONF_PORT, DEFAULT_PORT),
        username=entry.data.get(CONF_USERNAME),
        password=entry.data.get(CONF_PASSWORD),
        use_ssl=entry.data.get(CONF_USE_SSL, False),
    )
    entry.runtime_data = client
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: AgentDVRConfigEntry
) -> bool:
    """Unload a config entry.

    The aiohttp session is HA-shared (async_get_clientsession), so there is
    nothing to tear down here.
    """
    return True
