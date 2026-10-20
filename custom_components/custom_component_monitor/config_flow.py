"""Config flow for Custom Component Monitor integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .const import (
    CONF_AI_CATEGORIZATION_ENABLED,
    CONF_AI_TASK_ENTITY,
    CONF_AUTO_HIDE_FOREIGN,
    CONF_EXCLUDE,
    CONF_EXTRA_HOME_TERMS,
    CONF_HOME_COUNTRY,
    CONF_NEW_ITEMS_AI_BUDGET_SECONDS,
    CONF_NEW_ITEMS_ENABLED,
    DEFAULT_HOME_COUNTRY,
    DOMAIN,
    NEW_ITEMS_AI_DEFAULT_BUDGET_SECONDS,
    NEW_ITEMS_AI_MAX_BUDGET_SECONDS,
    NEW_ITEMS_AI_MIN_BUDGET_SECONDS,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema({})


class PlaceholderHub:
    """Placeholder class to make tests pass."""

    def __init__(self, host: str) -> None:
        """Initialize."""
        self.host = host

    async def authenticate(self, username: str, password: str) -> bool:
        """Test if we can authenticate with the host."""
        return True


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect."""
    # For now, no validation is needed as this integration doesn't require configuration
    return {"title": "Custom Component Monitor"}


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Custom Component Monitor."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> "OptionsFlowHandler":
        """Get the options flow (exclusion list, #70)."""
        return OptionsFlowHandler()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(title=info["title"], data=user_input)

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Manage the exclusion list of components wrongly flagged as unused (#70)."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            exclude = user_input.get(CONF_EXCLUDE, [])
            # Normalise to a clean list of strings.
            if isinstance(exclude, str):
                exclude = [exclude]
            exclude = [str(x).strip() for x in exclude if str(x).strip()]

            # Multi-line text box -> a clean list of stripped, non-empty
            # terms (#95). sensor.py reads this option as a list, so it must
            # be stored as one, not as the raw newline-separated string.
            extra_terms_raw = user_input.get(CONF_EXTRA_HOME_TERMS, "")
            extra_home_terms = [
                line.strip()
                for line in str(extra_terms_raw or "").splitlines()
                if line.strip()
            ]

            home_country = (
                str(user_input.get(CONF_HOME_COUNTRY) or "").strip()
                or getattr(self.hass.config, "country", None)
                or DEFAULT_HOME_COUNTRY
            )

            return self.async_create_entry(
                title="",
                data={
                    CONF_EXCLUDE: exclude,
                    CONF_AI_CATEGORIZATION_ENABLED: user_input.get(
                        CONF_AI_CATEGORIZATION_ENABLED, False
                    ),
                    CONF_AI_TASK_ENTITY: user_input.get(CONF_AI_TASK_ENTITY, ""),
                    CONF_NEW_ITEMS_ENABLED: user_input.get(
                        CONF_NEW_ITEMS_ENABLED, True
                    ),
                    CONF_AUTO_HIDE_FOREIGN: user_input.get(
                        CONF_AUTO_HIDE_FOREIGN, True
                    ),
                    CONF_HOME_COUNTRY: home_country,
                    CONF_EXTRA_HOME_TERMS: extra_home_terms,
                    CONF_NEW_ITEMS_AI_BUDGET_SECONDS: int(
                        user_input.get(
                            CONF_NEW_ITEMS_AI_BUDGET_SECONDS,
                            NEW_ITEMS_AI_DEFAULT_BUDGET_SECONDS,
                        )
                    ),
                },
            )

        current = self.config_entry.options.get(CONF_EXCLUDE, []) or []
        current_ai_enabled = self.config_entry.options.get(
            CONF_AI_CATEGORIZATION_ENABLED, False
        )
        current_ai_entity = self.config_entry.options.get(CONF_AI_TASK_ENTITY, "")

        # New in HACS (#95) — current values to pre-fill the form.
        current_new_items_enabled = self.config_entry.options.get(
            CONF_NEW_ITEMS_ENABLED, True
        )
        current_auto_hide_foreign = self.config_entry.options.get(
            CONF_AUTO_HIDE_FOREIGN, True
        )
        current_home_country = self.config_entry.options.get(
            CONF_HOME_COUNTRY
        ) or (getattr(self.hass.config, "country", None) or DEFAULT_HOME_COUNTRY)
        current_extra_home_terms = (
            self.config_entry.options.get(CONF_EXTRA_HOME_TERMS) or []
        )
        if isinstance(current_extra_home_terms, str):
            current_extra_home_terms = [current_extra_home_terms]
        current_extra_home_terms_text = "\n".join(current_extra_home_terms)
        # v1.15.1 replaced "how many items per cycle" with "how long per
        # pass". The old key may still be in this entry's options; it is
        # ignored, and saving the form drops it.
        current_ai_budget_seconds = self.config_entry.options.get(
            CONF_NEW_ITEMS_AI_BUDGET_SECONDS, NEW_ITEMS_AI_DEFAULT_BUDGET_SECONDS
        )

        # Offer the currently-unused component names as quick-pick options, while
        # still allowing free-text entries (custom_value) for anything else.
        unused_names: set[str] = set(current)
        coordinator = self.hass.data.get(DOMAIN, {}).get("coordinator")
        if coordinator is not None and coordinator.data:
            for bucket in ("integrations", "themes", "frontend"):
                for item in coordinator.data.get(bucket, {}).get("unused", []):
                    name = item.get("name") or item.get("domain") or item.get("card_type")
                    if name:
                        unused_names.add(str(name))

        schema = vol.Schema(
            {
                vol.Optional(CONF_EXCLUDE, default=current): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=sorted(unused_names),
                        multiple=True,
                        custom_value=True,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                # AI summarise & categorise updates (#67) — optional.
                vol.Optional(
                    CONF_AI_CATEGORIZATION_ENABLED, default=current_ai_enabled
                ): selector.BooleanSelector(),
                vol.Optional(
                    CONF_AI_TASK_ENTITY,
                    description={"suggested_value": current_ai_entity or None},
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=["ai_task", "conversation"])
                ),
                # New in HACS (#95) — review card for newly-indexed repos.
                vol.Optional(
                    CONF_NEW_ITEMS_ENABLED, default=current_new_items_enabled
                ): selector.BooleanSelector(),
                vol.Optional(
                    CONF_AUTO_HIDE_FOREIGN, default=current_auto_hide_foreign
                ): selector.BooleanSelector(),
                vol.Optional(
                    CONF_HOME_COUNTRY, default=current_home_country
                ): selector.TextSelector(),
                vol.Optional(
                    CONF_EXTRA_HOME_TERMS,
                    description={
                        "suggested_value": current_extra_home_terms_text or None
                    },
                ): selector.TextSelector(
                    selector.TextSelectorConfig(multiline=True)
                ),
                vol.Optional(
                    CONF_NEW_ITEMS_AI_BUDGET_SECONDS,
                    default=current_ai_budget_seconds,
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=NEW_ITEMS_AI_MIN_BUDGET_SECONDS,
                        max=NEW_ITEMS_AI_MAX_BUDGET_SECONDS,
                        unit_of_measurement="s",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)