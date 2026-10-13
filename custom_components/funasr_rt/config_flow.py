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
    CONF_SERVER_URL,
    CONF_VOCAB_PREFIX,
    CONF_WORKSPACE,
    DEFAULT_FINAL_TIMEOUT,
    DEFAULT_MODEL,
    DEFAULT_VOCAB_PREFIX,
    DOMAIN,
    LOCAL_CONNECT_TIMEOUT,
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

    async def _async_probe_local_url(self, url: str) -> str | None:
        """Open a probe WebSocket to the runtime; None when OK, else error key."""
        try:
            async with aiohttp.ClientSession() as session:
                ws = await session.ws_connect(url, timeout=LOCAL_CONNECT_TIMEOUT)
                await ws.close()
        except Exception as err:  # noqa: BLE001 (any failure -> form error)
            _LOGGER.info("FunASR runtime probe failed for %s: %s", url, err)
            return "cannot_connect_server"
        return None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ):
        """Step 1: choose the recognition backend."""
        if user_input is not None:
            if user_input.get(CONF_BACKEND, BACKEND_CLOUD) == BACKEND_LOCAL:
                return await self.async_step_local()
            return await self.async_step_cloud()

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
                                label="本地 Local — FunASR runtime (WebSocket)",
                            ),
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    async def async_step_cloud(
        self, user_input: dict[str, Any] | None = None
    ):
        """Cloud backend: DashScope API key (+ optional workspace)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            api_key = user_input[CONF_API_KEY].strip()
            error = await self._async_validate_cloud_key(api_key)
            if error:
                errors["base"] = error
            else:
                return self.async_create_entry(
                    title="FunASR Realtime",
                    data={
                        CONF_BACKEND: BACKEND_CLOUD,
                        CONF_API_KEY: api_key,
                        CONF_WORKSPACE: (
                            user_input.get(CONF_WORKSPACE) or ""
                        ).strip(),
                    },
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
                vol.Required(CONF_API_KEY): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD, multiline=False)
                ),
                vol.Optional(CONF_WORKSPACE, default=""): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.TEXT)
                ),
            }
        )
        return self.async_show_form(step_id="cloud", data_schema=schema, errors=errors)

    async def async_step_local(
        self, user_input: dict[str, Any] | None = None
    ):
        """Local backend: FunASR runtime WebSocket URL."""
        errors: dict[str, str] = {}
        if user_input is not None:
            url = user_input[CONF_SERVER_URL].strip()
            error = await self._async_probe_local_url(url)
            if error:
                errors["base"] = error
            else:
                return self.async_create_entry(
                    title="FunASR Local",
                    data={
                        CONF_BACKEND: BACKEND_LOCAL,
                        CONF_SERVER_URL: url,
                    },
                    options={
                        CONF_HOTWORDS: "",
                        CONF_AUTO_HOTWORDS: True,
                        CONF_FINAL_TIMEOUT: DEFAULT_FINAL_TIMEOUT,
                    },
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_SERVER_URL): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.URL)
                ),
            }
        )
        return self.async_show_form(step_id="local", data_schema=schema, errors=errors)


class FunAsrRtOptionsFlow(OptionsFlow):
    """Handle options for FunASR Realtime STT."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ):
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        opts = self.config_entry.options
        is_local = self.config_entry.data.get(CONF_BACKEND) == BACKEND_LOCAL
        fields: dict = {}
        if not is_local:
            fields[
                vol.Optional(CONF_MODEL, default=opts.get(CONF_MODEL, DEFAULT_MODEL))
            ] = TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT))
        fields[
            vol.Optional(CONF_HOTWORDS, default=opts.get(CONF_HOTWORDS, ""))
        ] = TextSelector(
            TextSelectorConfig(type=TextSelectorType.TEXT, multiline=True)
        )
        fields[
            vol.Optional(
                CONF_AUTO_HOTWORDS, default=opts.get(CONF_AUTO_HOTWORDS, True)
            )
        ] = BooleanSelector()
        if not is_local:
            fields[
                vol.Optional(
                    CONF_VOCAB_PREFIX,
                    default=opts.get(CONF_VOCAB_PREFIX, DEFAULT_VOCAB_PREFIX),
                )
            ] = TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT))
        fields[
            vol.Optional(
                CONF_FINAL_TIMEOUT,
                default=opts.get(CONF_FINAL_TIMEOUT, DEFAULT_FINAL_TIMEOUT),
            )
        ] = NumberSelector(
            NumberSelectorConfig(min=5, max=60, step=1, mode=NumberSelectorMode.BOX)
        )
        return self.async_show_form(step_id="init", data_schema=vol.Schema(fields))
