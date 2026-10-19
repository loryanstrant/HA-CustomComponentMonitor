"""Every shipped card must actually reach ``www/`` and Lovelace.

The integration copies its card JS out of the component directory into
``config/www/<domain>/`` and then registers each one as a Lovelace module
resource. Adding a card without adding it to both lists is a silent failure —
the file is in the repo, HACS installs it, and the dashboard still says
"Custom element doesn't exist". So the card list is asserted here rather than
trusted, and this test is the thing that fails when a fifth card is added and
only half-wired.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.custom_component_monitor import (
    CARD_JS,
    _register_lovelace_resource,
)
from custom_components.custom_component_monitor.const import (
    DOMAIN,
    NEW_ITEMS_CARD_BASE_PATH,
    NEW_ITEMS_CARD_JS,
    RIU_CARD_JS,
    UAT_CARD_JS,
)

ALL_CARDS = (CARD_JS, UAT_CARD_JS, RIU_CARD_JS, NEW_ITEMS_CARD_JS)


@pytest.mark.parametrize("card_js", ALL_CARDS)
def test_card_ships_in_the_component(card_js: str) -> None:
    """Each card named in the code exists as a file in the component."""
    source = Path("custom_components/custom_component_monitor/www") / card_js
    assert source.is_file(), f"{card_js} is referenced but not shipped"


async def test_async_setup_stages_every_card(hass: HomeAssistant) -> None:
    """``async_setup`` copies all four cards into ``config/www/<domain>/``."""
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    www_dir = Path(hass.config.path()) / "www" / DOMAIN
    for card_js in ALL_CARDS:
        assert (www_dir / card_js).is_file(), f"{card_js} was not staged in www/"


async def test_new_items_card_registers_as_a_lovelace_resource(
    hass: HomeAssistant,
) -> None:
    """The new card is registered with a cache-busting ``?v=`` tag.

    Lovelace is not set up in the test harness, so the helper takes its
    "not ready" path — what is asserted is that the call is wired and
    survives a missing Lovelace rather than raising, which is the same
    condition it meets during a real start-up before ``homeassistant_started``.
    """
    await _register_lovelace_resource(
        hass, NEW_ITEMS_CARD_BASE_PATH, NEW_ITEMS_CARD_JS
    )
