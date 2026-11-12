"""The services refuse an update this Home Assistant is too old for (#111).

Hiding the row in the card is not the protection — it is the *presentation*.
An automation, a script or a Developer-Tools call reaches the same two
services, so the refusal lives there as well:

* ``custom_component_monitor.update_and_action`` raises rather than installing;
* ``custom_component_monitor.update_all`` drops the blocked entity and installs
  the rest, because a bulk action that threw halfway would leave the house in
  an unknown state.

Deliberately asymmetric: *blocked* (a declared minimum positively not met) is
refused here, *unknown* is not — the service cannot see the card's per-row
acknowledgement, and refusing every undeclared component would refuse almost
all of them.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.custom_component_monitor.const import (
    ATTR_HA_REQUIREMENT,
    DOMAIN,
    SERVICE_UPDATE_ALL,
    SERVICE_UPDATE_AND_ACTION,
)

INSTALLED_HA = "2026.10.4"


def row(entity_id: str, name: str, requirement: dict) -> dict:
    return {
        "name": name,
        "type": "Integration",
        "repository": f"https://github.com/someone/{name.lower()}",
        "current_version": "1.0.0",
        "available_version": "1.1.0",
        "entity_id": entity_id,
        ATTR_HA_REQUIREMENT: requirement,
    }


def met(required: str = "2025.6.0") -> dict:
    return {
        "state": "met",
        "required": required,
        "installed": INSTALLED_HA,
        "reason": None,
    }


def blocked(required: str = "2026.11.0") -> dict:
    return {
        "state": "blocked",
        "required": required,
        "installed": INSTALLED_HA,
        "reason": None,
    }


def unknown() -> dict:
    return {
        "state": "unknown",
        "required": None,
        "installed": INSTALLED_HA,
        "reason": "not_declared",
    }


def scan_result(rows: list[dict]) -> dict:
    states = [r[ATTR_HA_REQUIREMENT]["state"] for r in rows]
    return {
        "count": len(rows),
        "updates": rows,
        "blocked": states.count("blocked"),
        "unknown_requirement": states.count("unknown"),
    }


@pytest.fixture
def hacs_updates(hass: HomeAssistant):
    """Register ``update.*`` entities that really are on the ``hacs`` platform.

    The real entity registry, not a patched one: ``update_all`` reads it from
    ``__init__`` while the scanner reads it from ``sensor``, and only an actual
    registry entry satisfies both.
    """

    def _add(object_id: str, **attributes) -> str:
        entry = er.async_get(hass).async_get_or_create(
            "update",
            "hacs",
            f"unique-{object_id}",
            suggested_object_id=object_id,
        )
        hass.states.async_set(
            entry.entity_id,
            "on",
            {
                "friendly_name": attributes.pop("friendly_name", object_id),
                "installed_version": "1.0.0",
                "latest_version": "1.1.0",
                **attributes,
            },
        )
        return entry.entity_id

    return _add


@pytest.fixture
async def integration(hass: HomeAssistant):
    """Set the integration up with the scan rows a test asks for.

    Returns ``(setup, installs, scan)``: a callable taking the update rows,
    the list every captured ``update.install`` call lands in, and the patched
    scanner mock, so a test can count re-scans.
    """
    installs: list[dict] = []

    async def capture_install(call: ServiceCall) -> None:
        installs.append(dict(call.data))

    hass.services.async_register("update", "install", capture_install)

    scan = AsyncMock(return_value=scan_result([]))

    async def _setup(rows: list[dict]):
        scan.return_value = scan_result(rows)
        entry = MockConfigEntry(domain=DOMAIN, title="CCM", data={}, options={})
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        return entry

    with (
        patch(
            "custom_components.custom_component_monitor.sensor"
            ".async_repository_snapshot",
            return_value=("hacs", {}),
        ),
        patch(
            "custom_components.custom_component_monitor.sensor.ComponentScanner"
            ".scan_updates",
            scan,
        ),
        patch(
            "custom_components.custom_component_monitor.sensor.ComponentScanner"
            ".scan_all_hacs_components",
            return_value=[],
        ),
        patch(
            "custom_components.custom_component_monitor.sensor.ComponentScanner"
            ".scan_themes",
            return_value={"total": 0, "used": [], "unused": []},
        ),
        patch(
            "custom_components.custom_component_monitor.sensor.ComponentScanner"
            ".scan_frontend",
            return_value={"total": 0, "used": [], "unused": []},
        ),
        patch(
            "custom_components.custom_component_monitor.sensor.ComponentScanner"
            ".scan_integrations",
            return_value={"total": 0, "used": [], "unused": []},
        ),
    ):
        yield _setup, installs, scan


async def test_update_and_action_refuses_a_blocked_update(
    hass: HomeAssistant, hacs_updates, integration
):
    """The Microsoft 365 Calendar case, reached by service call."""
    setup, installs, _scan = integration
    entity_id = hacs_updates(
        "calendar_thing_update", friendly_name="Microsoft 365 — Calendar update"
    )
    await setup([row(entity_id, "Calendar", blocked())])

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_UPDATE_AND_ACTION,
            {"entity_id": entity_id},
            blocking=True,
        )

    message = str(err.value)
    # Names the component and both versions: enough to act on without
    # going and reading the manifest.
    assert "Microsoft 365 — Calendar" in message
    assert "2026.11.0" in message
    assert INSTALLED_HA in message
    assert installs == [], "nothing may be installed once the check refuses"


async def test_update_and_action_installs_a_met_requirement(
    hass: HomeAssistant, hacs_updates, integration
):
    """The guard must not become a wall in front of ordinary updates."""
    setup, installs, _scan = integration
    entity_id = hacs_updates("fine_thing_update", friendly_name="Fine Thing update")
    await setup([row(entity_id, "Fine", met())])

    await hass.services.async_call(
        DOMAIN, SERVICE_UPDATE_AND_ACTION, {"entity_id": entity_id}, blocking=True
    )

    assert installs == [{"entity_id": entity_id}]


async def test_update_and_action_allows_an_unknown_requirement(
    hass: HomeAssistant, hacs_updates, integration
):
    """Unknown is not a refusal here — only the card can take that risk.

    Most components declare no minimum at all, so refusing them in the
    service would refuse nearly everything; the card asks per row instead.
    """
    setup, installs, _scan = integration
    entity_id = hacs_updates("mystery_update", friendly_name="Dad Jokes update")
    await setup([row(entity_id, "Mystery", unknown())])

    await hass.services.async_call(
        DOMAIN, SERVICE_UPDATE_AND_ACTION, {"entity_id": entity_id}, blocking=True
    )

    assert installs == [{"entity_id": entity_id}]


async def test_update_all_skips_the_blocked_one_and_installs_the_rest(
    hass: HomeAssistant, hacs_updates, integration
):
    """A bulk run must not stop halfway, and must not install the blocked row."""
    setup, installs, _scan = integration
    good = hacs_updates("good_update", friendly_name="Good Thing update")
    bad = hacs_updates("bad_update", friendly_name="Bad Thing update")
    other = hacs_updates("mystery_update", friendly_name="Dad Jokes update")
    await setup(
        [
            row(good, "Good", met()),
            row(bad, "Bad", blocked()),
            row(other, "Mystery", unknown()),
        ]
    )

    await hass.services.async_call(DOMAIN, SERVICE_UPDATE_ALL, {}, blocking=True)

    installed = {call["entity_id"] for call in installs}
    assert installed == {good, other}
    assert bad not in installed


async def test_update_all_honours_the_cards_selection_and_still_filters(
    hass: HomeAssistant, hacs_updates, integration
):
    """Even if the blocked entity is passed in explicitly, it is dropped."""
    setup, installs, _scan = integration
    good = hacs_updates("good_update", friendly_name="Good Thing update")
    bad = hacs_updates("bad_update", friendly_name="Bad Thing update")
    await setup([row(good, "Good", met()), row(bad, "Bad", blocked())])

    await hass.services.async_call(
        DOMAIN, SERVICE_UPDATE_ALL, {"entity_ids": [good, bad]}, blocking=True
    )

    assert [call["entity_id"] for call in installs] == [good]


async def test_an_unscanned_entity_is_rescanned_before_the_decision(
    hass: HomeAssistant, hacs_updates, integration
):
    """An update HACS surfaced since the last scan is not a way past the check.

    Without the re-scan, ``known_update_entity_ids()`` simply wouldn't contain
    it, ``update_requirement()`` would return ``None``, and the install would
    go ahead — for exactly the newly-appeared updates this feature is for.
    """
    setup, installs, scan = integration
    latecomer = hacs_updates("latecomer_update", friendly_name="Latecomer update")
    await setup([])  # the first scan knows nothing about it
    scans_after_setup = scan.await_count

    # The next scan does know it, and knows it is blocked.
    scan.return_value = scan_result([row(latecomer, "Latecomer", blocked())])

    with pytest.raises(ServiceValidationError, match="2026.11.0"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_UPDATE_AND_ACTION,
            {"entity_id": latecomer},
            blocking=True,
        )

    assert scan.await_count == scans_after_setup + 1, "exactly one re-scan"
    assert installs == []


async def test_two_rows_claiming_one_entity_take_the_strict_answer(
    hass: HomeAssistant, hacs_updates, integration
):
    """A fork beside its original can land both rows on one update entity.

    The scanner matches a snapshot row to a live entity by repository URL,
    then by name, then by slug, and the name/slug fallbacks are first-match —
    so two components whose names normalise the same can both claim one
    entity. Believing the permissive row would install the very update this
    feature exists to hold back, so the blocked claim wins.
    """
    setup, installs, _scan = integration
    entity_id = hacs_updates("dad_jokes_update", friendly_name="Dad Jokes update")
    await setup(
        [
            row(entity_id, "Jokes-Fork", met()),
            row(entity_id, "Jokes", blocked()),
        ]
    )

    with pytest.raises(ServiceValidationError, match="2026.11.0"):
        await hass.services.async_call(
            DOMAIN, SERVICE_UPDATE_AND_ACTION, {"entity_id": entity_id}, blocking=True
        )

    await hass.services.async_call(DOMAIN, SERVICE_UPDATE_ALL, {}, blocking=True)
    assert installs == [], "the bulk path takes the strict answer too"


async def test_a_known_entity_does_not_trigger_a_rescan(
    hass: HomeAssistant, hacs_updates, integration
):
    """The guard costs nothing on the normal path."""
    setup, installs, scan = integration
    entity_id = hacs_updates("fine_thing_update", friendly_name="Fine Thing update")
    await setup([row(entity_id, "Fine", met())])
    scans_after_setup = scan.await_count

    await hass.services.async_call(
        DOMAIN, SERVICE_UPDATE_AND_ACTION, {"entity_id": entity_id}, blocking=True
    )

    assert scan.await_count == scans_after_setup
    assert installs == [{"entity_id": entity_id}]
