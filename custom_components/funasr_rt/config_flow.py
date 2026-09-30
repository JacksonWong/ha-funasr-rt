"""Config flow for FunASR Realtime STT."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    BACKEND_CLOUD,
    BACKEND_LOCAL,
    CLOUD_VALIDATE_URL,
    CONF_API_KEY,
    CONF_AUTO_HOTWORDS,
    CONF_BACKEND,
    CONF_FINAL_TIMEOUT,
    CONF_HOTWORDS,
    CONF_MODEL,
    CONF_VOCAB_PREFIX,
    CONF_WORKSPACE,
    DEFAULT_FINAL_TIMEOUT,
    DEFAULT_MODEL,
    DEFAULT_VOCAB_PREFIX,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class FunAsrRtConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for FunASR Realtime STT."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> OptionsFlow:
        """Get the options flow for this handler."""
        return FunAsrRtOptionsFlow()

    async def _async_validate_cloud_key(self, api_key: str) -> str | None:
        """Validate the key against DashScope; return None when OK, else error key."""
        session = async_get_clientsession(self.hass)
        auth_scheme = "Bearer"
        headers = {"Authorization": f"{auth_scheme} {api_key}"}
        try:
            resp = await session.get(
                CLOUD_VALIDATE_URL,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            )
        except (aiohttp.ClientError, TimeoutError):
            return "cannot_connect"
        if resp.status == 200:
            return None
        if resp.status in (401, 403):
            return "invalid_api_key"
        _LOGGER.warning("DashScope key validation returned HTTP %s", resp.status)
        return "cannot_connect"

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ):
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            backend = user_input.get(CONF_BACKEND, BACKEND_CLOUD)
            if backend == BACKEND_LOCAL:
                errors["base"] = "local_not_supported"
            else:
                api_key = user_input[CONF_API_KEY].strip()
                error = await self._async_validate_cloud_key(api_key)
                if error:
                    errors["base"] = error
                else:
                    data = dict(user_input)
                    data[CONF_API_KEY] = api_key
                    data[CONF_WORKSPACE] = (
                        user_input.get(CONF_WORKSPACE) or ""
                    ).strip()
                    return self.async_create_entry(
                        title="FunASR Realtime",
                        data=data,
                        options={
                            CONF_MODEL: DEFAULT_MODEL,
                            CONF_HOTWORDS: "",
                            CONF_AUTO_HOTWORDS: True,
                            CONF_VOCAB_PREFIX: DEFAULT_VOCAB_PREFIX,
                            CONF_FINAL_TIMEOUT: DEFAULT_FINAL_TIMEOUT,
                        },
                    )

        schema = vol.Schema(
            {
                vol.Required(CONF_BACKEND, default=BACKEND_CLOUD): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(
                                value=BACKEND_CLOUD,
                                label="云端 Cloud — 阿里云百炼 DashScope",
                            ),
                            SelectOptionDict(
                                value=BACKEND_LOCAL,
                                label="本地 Local — FunASR runtime（规划中 planned）",
                            ),
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(CONF_API_KEY): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD, multiline=False)
                ),
                vol.Optional(CONF_WORKSPACE, default=""): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.TEXT)
                ),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)


class FunAsrRtOptionsFlow(OptionsFlow):
    """Handle options for FunASR Realtime STT."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ):
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        opts = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_MODEL, default=opts.get(CONF_MODEL, DEFAULT_MODEL)
                ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
                vol.Optional(
                    CONF_HOTWORDS, default=opts.get(CONF_HOTWORDS, "")
                ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT, multiline=True)),
                vol.Optional(
                    CONF_AUTO_HOTWORDS, default=opts.get(CONF_AUTO_HOTWORDS, True)
                ): BooleanSelector(),
                vol.Optional(
                    CONF_VOCAB_PREFIX,
                    default=opts.get(CONF_VOCAB_PREFIX, DEFAULT_VOCAB_PREFIX),
                ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
                vol.Optional(
                    CONF_FINAL_TIMEOUT,
                    default=opts.get(CONF_FINAL_TIMEOUT, DEFAULT_FINAL_TIMEOUT),
                ): NumberSelector(
                    NumberSelectorConfig(min=5, max=60, step=1, mode=NumberSelectorMode.BOX)
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
