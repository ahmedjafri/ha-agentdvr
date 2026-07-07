"""Config flow for the AgentDVR integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import AgentDVRAuthError, AgentDVRClient, AgentDVRConnError
from .const import CONF_USE_SSL, DEFAULT_PORT, DOMAIN

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
        vol.Optional(CONF_USERNAME): str,
        vol.Optional(CONF_PASSWORD): str,
        vol.Optional(CONF_USE_SSL, default=False): bool,
    }
)


class AgentDVRConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for AgentDVR."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST]
            port = user_input.get(CONF_PORT, DEFAULT_PORT)

            await self.async_set_unique_id(f"{host}:{port}")
            self._abort_if_unique_id_configured()

            client = AgentDVRClient(
                async_get_clientsession(self.hass),
                host=host,
                port=port,
                username=user_input.get(CONF_USERNAME),
                password=user_input.get(CONF_PASSWORD),
                use_ssl=user_input.get(CONF_USE_SSL, False),
            )
            try:
                await client.async_validate()
            except AgentDVRAuthError:
                errors["base"] = "invalid_auth"
            except AgentDVRConnError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(
                    title=f"AgentDVR ({host})", data=user_input
                )

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )
