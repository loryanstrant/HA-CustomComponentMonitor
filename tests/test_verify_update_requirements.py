"""The requirement comes from the release on OFFER, not the one installed (#116).

Every test in ``test_scan_updates_requirement.py`` hands the scanner a
``repository_manifest`` and an ``available_version`` that describe the same
thing, so all of them pass against a scanner that reads the installed
version's requirement and labels the offered one with it. That is precisely
what v1.19.0 shipped, and what the house saw: Microsoft 365 - Calendar
v1.14.0 needs Home Assistant 2026.11.0 and the card said "needs 2025.6.0 —
you have it", which is the installed v1.13.0's figure.

So every test here makes the two disagree. HACS stores one ``hacs.json`` per
repository with no record of which release it describes, so the only source
that can answer for a specific release is that release's own tag.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant

from custom_components.custom_component_monitor.const import ATTR_HA_REQUIREMENT
from custom_components.custom_component_monitor.sensor import ComponentScanner

# Older and newer than whatever Home Assistant the suite runs on, so these
# tests keep meaning the same thing in 2027.
OLDER = "2000.1.0"
NEWER = "2999.12.0"

RAW = "https://raw.githubusercontent.com"


@pytest.fixture
def scanner(hass: HomeAssistant) -> ComponentScanner:
    return ComponentScanner(hass)


def repo(full_name: str, *, stored=None, offered: str = "1.1.0"):
    """A pending-update row whose STORED manifest is the installed version's."""
    entry = {
        "installed": True,
        "full_name": full_name,
        "category": "integration",
        "pending_update": True,
        "current_version": "1.0.0",
        "available_version": offered,
        "name": full_name.split("/")[-1],
    }
    if stored is not None:
        entry["repository_manifest"] = stored
    return entry


async def scan_and_verify(scanner: ComponentScanner, repos: dict) -> dict:
    """Run the offline scan, then the pass that corrects it."""
    with patch.object(
        ComponentScanner, "_load_hacs_repositories", AsyncMock(return_value=repos)
    ):
        result = await scanner.scan_updates()
    await scanner.async_verify_update_requirements(result)
    return result


def by_name(result: dict) -> dict:
    return {item["name"]: item[ATTR_HA_REQUIREMENT] for item in result["updates"]}


async def test_the_offered_release_overrides_the_stored_manifest(
    scanner, aioclient_mock
):
    """#116 itself: stored says fine, the offered release says no.

    The Microsoft 365 Calendar shape, with the versions abstracted so the
    test doesn't expire.
    """
    aioclient_mock.get(
        f"{RAW}/someone/calendar/v1.14.0/hacs.json", json={"homeassistant": NEWER}
    )

    result = await scan_and_verify(
        scanner,
        {"1": repo("someone/calendar", stored={"homeassistant": OLDER}, offered="v1.14.0")},
    )

    requirement = by_name(result)["calendar"]
    assert requirement["state"] == "blocked"
    assert requirement["required"] == NEWER
    assert requirement["installed"] == HA_VERSION
    assert result["blocked"] == 1
    assert result["unknown_requirement"] == 0


async def test_the_offered_release_can_also_clear_a_stored_block(
    scanner, aioclient_mock
):
    """The correction runs both ways — it is not a one-way ratchet.

    A component that raised its minimum, then lowered it again, must not stay
    blocked on the strength of a snapshot describing the older release.
    """
    aioclient_mock.get(
        f"{RAW}/someone/relaxed/1.1.0/hacs.json", json={"homeassistant": OLDER}
    )

    result = await scan_and_verify(
        scanner, {"1": repo("someone/relaxed", stored={"homeassistant": NEWER})}
    )

    assert by_name(result)["relaxed"]["state"] == "met"
    assert result["blocked"] == 0


async def test_the_tag_is_requested_verbatim_including_its_v_prefix(
    scanner, aioclient_mock
):
    """HACS reports the tag, and a tag keeps whatever prefix it shipped with."""
    aioclient_mock.get(
        f"{RAW}/someone/prefixed/v2.3.4/hacs.json", json={"homeassistant": OLDER}
    )

    result = await scan_and_verify(
        scanner, {"1": repo("someone/prefixed", offered="v2.3.4")}
    )

    assert by_name(result)["prefixed"]["state"] == "met"
    assert aioclient_mock.mock_calls[0][1].path.endswith("/v2.3.4/hacs.json")


async def test_a_release_that_declares_nothing_is_not_declared(
    scanner, aioclient_mock
):
    """A manifest we DID read, which simply has no minimum in it."""
    aioclient_mock.get(f"{RAW}/someone/silent/1.1.0/hacs.json", json={"name": "Silent"})

    result = await scan_and_verify(
        scanner, {"1": repo("someone/silent", stored={"homeassistant": OLDER})}
    )

    requirement = by_name(result)["silent"]
    assert requirement["state"] == "unknown"
    assert requirement["reason"] == "not_declared"
    assert result["unknown_requirement"] == 1


async def test_a_404_never_reads_as_compatible(scanner, aioclient_mock):
    """The most dangerous failure: a tag that isn't there.

    A missing file is not a declaration of nothing — the tag may not exist
    under the name HACS reported. Treating it as an empty manifest would turn
    a blocked update green, and keeping the stored answer would leave the #116
    bug exactly as it was.
    """
    aioclient_mock.get(f"{RAW}/someone/gone/1.1.0/hacs.json", status=404)

    result = await scan_and_verify(
        scanner, {"1": repo("someone/gone", stored={"homeassistant": OLDER})}
    )

    requirement = by_name(result)["gone"]
    assert requirement["state"] == "unknown"
    assert requirement["reason"] == "target_unreadable"
    assert requirement["required"] is None
    assert result["blocked"] == 0
    assert result["unknown_requirement"] == 1


async def test_a_failed_read_keeps_a_stored_block(scanner, aioclient_mock):
    """Unreadable must not relax a requirement already known to be unmet.

    ``blocked`` is the more restrictive claim, so it survives a failed read:
    over-restricting shows a warning the user can override, under-restricting
    breaks their Home Assistant.
    """
    aioclient_mock.get(f"{RAW}/someone/strict/1.1.0/hacs.json", status=500)

    result = await scan_and_verify(
        scanner, {"1": repo("someone/strict", stored={"homeassistant": NEWER})}
    )

    requirement = by_name(result)["strict"]
    assert requirement["state"] == "blocked"
    assert requirement["required"] == NEWER
    assert result["blocked"] == 1


async def test_a_non_json_body_is_a_failed_read(scanner, aioclient_mock):
    """A repository serving HTML (or a rate-limit page) tells us nothing."""
    aioclient_mock.get(f"{RAW}/someone/html/1.1.0/hacs.json", text="<html>nope</html>")

    result = await scan_and_verify(
        scanner, {"1": repo("someone/html", stored={"homeassistant": OLDER})}
    )

    assert by_name(result)["html"]["reason"] == "target_unreadable"


async def test_a_body_too_big_to_be_a_manifest_is_a_failed_read(
    scanner, aioclient_mock
):
    """A hacs.json is a few hundred bytes, so the read is bounded.

    Valid JSON on the way in, so what this pins is the cap itself and not the
    parser: a body that size is something other than a manifest, and it is
    refused rather than pulled into memory whole.
    """
    oversized = b'{"homeassistant": "' + OLDER.encode() + b'", "pad": "' + b"x" * 300_000 + b'"}'
    aioclient_mock.get(f"{RAW}/someone/huge/1.1.0/hacs.json", content=oversized)

    result = await scan_and_verify(
        scanner, {"1": repo("someone/huge", stored={"homeassistant": OLDER})}
    )

    assert by_name(result)["huge"]["reason"] == "target_unreadable"


async def test_bytes_that_are_not_text_are_a_failed_read(scanner, aioclient_mock):
    """Undecodable bytes are an unreadable manifest, not a crashed scan."""
    aioclient_mock.get(
        f"{RAW}/someone/binary/1.1.0/hacs.json", content=b"\xff\xfe\x00\x01"
    )

    result = await scan_and_verify(
        scanner, {"1": repo("someone/binary", stored={"homeassistant": OLDER})}
    )

    assert by_name(result)["binary"]["reason"] == "target_unreadable"


async def test_a_row_with_no_repository_is_reported_unchecked(
    scanner, aioclient_mock
):
    """Nothing to fetch means nothing is known — not that the snapshot is right."""
    result = await scan_and_verify(
        scanner, {"1": repo("", stored={"homeassistant": OLDER})}
    )

    assert by_name(result)[""]["reason"] == "target_unreadable"
    assert aioclient_mock.call_count == 0


async def test_a_success_is_cached_and_never_re_fetched(scanner, aioclient_mock):
    """A tag's contents are immutable, so one read per newly-offered update."""
    aioclient_mock.get(
        f"{RAW}/someone/cached/1.1.0/hacs.json", json={"homeassistant": OLDER}
    )
    repos = {"1": repo("someone/cached")}

    first = await scan_and_verify(scanner, repos)
    second = await scan_and_verify(scanner, repos)

    assert by_name(first)["cached"]["state"] == "met"
    assert by_name(second)["cached"]["state"] == "met"
    assert aioclient_mock.call_count == 1


async def test_a_failure_is_not_cached_so_the_next_scan_retries(
    scanner, aioclient_mock
):
    """The opposite of HACS's mistake: a bad answer must never calcify."""
    aioclient_mock.get(f"{RAW}/someone/flaky/1.1.0/hacs.json", status=503)
    repos = {"1": repo("someone/flaky", stored={"homeassistant": OLDER})}

    first = await scan_and_verify(scanner, repos)
    assert by_name(first)["flaky"]["reason"] == "target_unreadable"

    aioclient_mock.clear_requests()
    aioclient_mock.get(
        f"{RAW}/someone/flaky/1.1.0/hacs.json", json={"homeassistant": NEWER}
    )
    second = await scan_and_verify(scanner, repos)

    assert by_name(second)["flaky"]["state"] == "blocked"
    assert aioclient_mock.call_count == 1


async def test_a_tag_no_longer_on_offer_is_dropped_from_the_cache(
    scanner, aioclient_mock
):
    """The cache is pruned to what is actually pending, so it can't grow."""
    aioclient_mock.get(
        f"{RAW}/someone/moving/1.1.0/hacs.json", json={"homeassistant": OLDER}
    )
    await scan_and_verify(scanner, {"1": repo("someone/moving")})
    assert ("someone/moving", "1.1.0") in scanner._target_manifest_cache

    aioclient_mock.get(
        f"{RAW}/someone/moving/1.2.0/hacs.json", json={"homeassistant": NEWER}
    )
    result = await scan_and_verify(
        scanner, {"1": repo("someone/moving", offered="1.2.0")}
    )

    assert by_name(result)["moving"]["state"] == "blocked"
    assert ("someone/moving", "1.1.0") not in scanner._target_manifest_cache
    assert ("someone/moving", "1.2.0") in scanner._target_manifest_cache


async def test_the_counts_are_recomputed_from_the_corrected_rows(
    scanner, aioclient_mock
):
    """The scan's own counts describe the pre-correction rows, so they must not
    be the ones published. #116 showed up as "7 updates / 2 incompatible" when
    three were."""
    aioclient_mock.get(
        f"{RAW}/someone/one/1.1.0/hacs.json", json={"homeassistant": NEWER}
    )
    aioclient_mock.get(
        f"{RAW}/someone/two/1.1.0/hacs.json", json={"homeassistant": NEWER}
    )
    aioclient_mock.get(
        f"{RAW}/someone/three/1.1.0/hacs.json", json={"homeassistant": OLDER}
    )
    aioclient_mock.get(f"{RAW}/someone/four/1.1.0/hacs.json", status=404)

    result = await scan_and_verify(
        scanner,
        {
            # Every stored manifest says "fine", as a stale snapshot does.
            "1": repo("someone/one", stored={"homeassistant": OLDER}),
            "2": repo("someone/two", stored={"homeassistant": OLDER}),
            "3": repo("someone/three", stored={"homeassistant": OLDER}),
            "4": repo("someone/four", stored={"homeassistant": OLDER}),
        },
    )

    assert result["count"] == 4
    assert result["blocked"] == 2
    assert result["unknown_requirement"] == 1


async def test_an_empty_update_list_fetches_nothing(scanner, aioclient_mock):
    result = {"count": 0, "updates": [], "blocked": 0, "unknown_requirement": 0}
    await scanner.async_verify_update_requirements(result)

    assert aioclient_mock.call_count == 0
    assert result == {
        "count": 0,
        "updates": [],
        "blocked": 0,
        "unknown_requirement": 0,
    }


async def test_a_fetch_that_explodes_cannot_fail_the_scan(scanner):
    """The pass is a correction, not a gate. A bug in it degrades the answer
    to "unchecked"; it never takes the dashboard's counts down with it."""
    with patch.object(
        ComponentScanner,
        "_fetch_target_manifests",
        AsyncMock(side_effect=RuntimeError("boom")),
    ):
        result = await scan_and_verify(
            scanner, {"1": repo("someone/boom", stored={"homeassistant": OLDER})}
        )

    assert by_name(result)["boom"]["reason"] == "target_unreadable"
    assert result["unknown_requirement"] == 1
