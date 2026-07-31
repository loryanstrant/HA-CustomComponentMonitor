"""The install-date registry (#93).

Install ages used to come straight from the component directory's mtime/ctime.
A HACS *update* rewrites that directory, so an old component looked freshly
installed and the Recently-installed-but-unused card kept surfacing things the
user had had for years. HACS itself records no install date, so the integration
now records the first time it sees a component and never moves that date
forward.
"""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest

from homeassistant.util import dt as dt_util

from custom_components.custom_component_monitor.const import (
    INSTALL_DATES_GRACE_DAYS,
    INSTALL_DATES_STORAGE_KEY,
    INSTALL_DATES_STORAGE_VERSION,
)
from custom_components.custom_component_monitor.install_dates import (
    InstallDateRegistry,
    InstallDateStore,
    install_key,
)


def make_repo(full_name: str = "bramstroker/homeassistant-powercalc", **extra):
    """A minimal HACS repository dict."""
    return {
        "full_name": full_name,
        "category": "integration",
        "installed": True,
        "manifest_name": "Powercalc",
        **extra,
    }


def days_ago(days: float) -> float:
    """An epoch timestamp *days* in the past."""
    return (dt_util.utcnow() - timedelta(days=days)).timestamp()


def now():
    """The `now` the scanner passes in — local, as `dt_util.now()` returns."""
    return dt_util.now()


# -- seeding ----------------------------------------------------------------


def test_first_run_seeds_from_the_filesystem_and_marks_it_estimated(registry):
    registry.begin_scan()

    days, estimated = registry.observe(make_repo(), days_ago(100), now())

    assert days == 100
    assert estimated is True


def test_seeded_at_is_stamped_after_a_cycle_that_saw_components(registry):
    assert registry.seeded_at is None

    registry.begin_scan()
    registry.observe(make_repo(), days_ago(3), now())
    registry.end_scan()

    assert registry.seeded_at is not None


def test_an_empty_snapshot_does_not_stamp_seeded_at(registry):
    """HACS not loaded yet must not count as "you own nothing".

    `_load_storage_file` swallows a missing or half-written
    hacs.repositories and returns {}. Seeding on that would stamp the clock
    against an empty view, and every component would look installed *today* on
    the next cycle — worse than the bug being fixed.
    """
    registry.begin_scan()
    registry.end_scan()

    assert registry.seeded_at is None


def test_eviction_is_skipped_when_the_scan_saw_nothing(registry):
    registry.begin_scan()
    registry.observe(make_repo(), days_ago(5), now())
    registry.end_scan()
    # Backdate it well past the grace period, then run a blind cycle.
    key = install_key(make_repo())
    registry._components[key]["last_seen"] = (
        dt_util.utcnow() - timedelta(days=INSTALL_DATES_GRACE_DAYS + 10)
    ).isoformat()

    registry.begin_scan()
    registry.end_scan()

    assert key in registry._components


# -- the bug ----------------------------------------------------------------


def test_a_hacs_update_does_not_reset_the_install_date(registry):
    """The regression this whole module exists to prevent."""
    repo = make_repo()
    registry.begin_scan()
    registry.observe(repo, days_ago(100), now())
    registry.end_scan()

    # HACS updates the component: the directory is rewritten, so its
    # filesystem timestamp is now.
    registry.begin_scan()
    days, estimated = registry.observe(repo, days_ago(0), now())
    registry.end_scan()

    assert days == 100
    assert estimated is True


def test_an_exact_record_ignores_the_filesystem(registry):
    repo = make_repo()
    registry.begin_scan()
    registry.observe(make_repo("other/thing"), days_ago(50), now())
    registry.end_scan()

    registry.begin_scan()
    registry.observe(repo, days_ago(0), now())  # a genuine new install
    registry.end_scan()

    registry.begin_scan()
    days, estimated = registry.observe(repo, days_ago(200), now())

    assert days == 0
    assert estimated is False


def test_estimated_first_seen_only_moves_backwards(registry):
    """Older files are evidence; newer files are just an update."""
    repo = make_repo()
    registry.begin_scan()
    registry.observe(repo, days_ago(10), now())
    registry.end_scan()

    registry.begin_scan()
    days, _ = registry.observe(repo, days_ago(50), now())

    assert days == 50


# -- post-seed installs -----------------------------------------------------


@pytest.fixture
def seeded(registry):
    """A registry that has already been seeded from one other component."""
    registry.begin_scan()
    registry.observe(make_repo("someone/already-installed"), days_ago(365), now())
    registry.end_scan()
    return registry


def test_a_key_first_seen_after_seeding_is_an_exact_install(seeded):
    seeded.begin_scan()
    days, estimated = seeded.observe(make_repo(), days_ago(0), now())

    assert days == 0
    assert estimated is False


def test_a_component_older_than_seeded_at_is_estimated_not_new(seeded):
    """A partial HACS snapshot at seeding, or a repo renamed upstream.

    Its key is new to us, but the files prove it predates tracking, so it must
    not be announced as "installed today".
    """
    seeded.begin_scan()
    days, estimated = seeded.observe(make_repo(), days_ago(80), now())

    assert days == 80
    assert estimated is True


def test_a_new_install_with_no_resolvable_path_is_still_exact(seeded):
    """Appearing in the snapshot for the first time is evidence by itself."""
    seeded.begin_scan()
    days, estimated = seeded.observe(make_repo(), None, now())

    assert days == 0
    assert estimated is False


def test_a_renamed_repository_is_not_announced_as_installed_today(seeded):
    """A rename gives a component a brand-new key with old files.

    Its last HACS update reset the files, so they're newer than `seeded_at` —
    but they aren't *recent*, and only recent files mean a new install.
    """
    seeded.begin_scan()
    days, estimated = seeded.observe(make_repo("renamed/powercalc"), days_ago(30), now())

    assert days == 30
    assert estimated is True


def test_a_component_installed_while_home_assistant_was_down_is_dated_by_its_files(
    seeded,
):
    seeded.begin_scan()
    days, estimated = seeded.observe(make_repo(), days_ago(6), now())

    assert days == 6
    assert estimated is True


# -- flapping snapshots and eviction ----------------------------------------


def test_a_component_missing_from_one_scan_keeps_its_record(registry):
    """The HACS snapshot lags and flaps; that must not reset install dates."""
    repo = make_repo()
    registry.begin_scan()
    registry.observe(repo, days_ago(100), now())
    registry.observe(make_repo("other/thing"), days_ago(2), now())
    registry.end_scan()

    # A cycle where HACS has dropped it from the snapshot.
    registry.begin_scan()
    registry.observe(make_repo("other/thing"), days_ago(2), now())
    registry.end_scan()

    registry.begin_scan()
    days, _ = registry.observe(repo, days_ago(0), now())

    assert days == 100


def test_a_component_absent_beyond_the_grace_period_is_evicted(registry):
    repo = make_repo()
    registry.begin_scan()
    registry.observe(repo, days_ago(100), now())
    registry.observe(make_repo("other/thing"), days_ago(2), now())
    registry.end_scan()
    key = install_key(repo)
    registry._components[key]["last_seen"] = (
        dt_util.utcnow() - timedelta(days=INSTALL_DATES_GRACE_DAYS + 1)
    ).isoformat()

    registry.begin_scan()
    registry.observe(make_repo("other/thing"), days_ago(2), now())
    registry.end_scan()

    assert key not in registry._components


def test_a_reinstall_after_eviction_counts_as_new(registry):
    repo = make_repo()
    registry.begin_scan()
    registry.observe(repo, days_ago(100), now())
    registry.observe(make_repo("other/thing"), days_ago(2), now())
    registry.end_scan()
    registry._components.pop(install_key(repo))

    registry.begin_scan()
    days, estimated = registry.observe(repo, days_ago(0), now())

    assert days == 0
    assert estimated is False


# -- defensive --------------------------------------------------------------


def test_days_are_never_negative(registry):
    """A file dated in the future must not read as a negative age.

    The card treats a negative `days_installed` as unknown and hides the row.
    """
    registry.begin_scan()

    days, _ = registry.observe(make_repo(), days_ago(-30), now())

    assert days == 0


def test_a_repo_without_a_full_name_falls_back_to_the_filesystem(registry):
    registry.begin_scan()

    days, estimated = registry.observe({"category": "theme"}, days_ago(7), now())

    assert days == 7
    assert estimated is True
    # Nothing keyable, so nothing is recorded — a synthetic key could hand this
    # component another one's install date.
    assert registry._components == {}


def test_a_repo_without_a_full_name_or_files_reports_an_unknown_age(registry):
    registry.begin_scan()

    days, estimated = registry.observe({"category": "theme"}, None, now())

    assert days is None
    assert estimated is True


def test_seeding_a_component_it_cannot_find_on_disk_reports_an_unknown_age(registry):
    """Not "installed today" — that would pin an old component to the upgrade.

    A theme whose directory name defeats the path heuristics has no date we can
    honestly give it, and the card already knows how to hide those.
    """
    registry.begin_scan()

    days, estimated = registry.observe(make_repo(), None, now())

    assert days is None
    assert estimated is True
    # Nothing recorded, so a later scan that *can* date it still gets it right.
    assert registry._components == {}


async def test_a_corrupt_store_yields_an_empty_registry(hass):
    reg = InstallDateRegistry(hass)
    with patch.object(
        InstallDateStore, "async_load", side_effect=ValueError("boom")
    ):
        await reg.async_load()

    assert reg.seeded_at is None
    assert reg._components == {}


async def test_junk_records_are_dropped_on_load(hass, hass_storage):
    hass_storage[INSTALL_DATES_STORAGE_KEY] = {
        "version": INSTALL_DATES_STORAGE_VERSION,
        "data": {
            "seeded_at": dt_util.utcnow().isoformat(),
            "components": {
                "good/one": {
                    "first_seen": dt_util.utcnow().isoformat(),
                    "last_seen": dt_util.utcnow().isoformat(),
                    "estimated": False,
                },
                "bad/nodate": {"estimated": True},
                "bad/notadict": "nonsense",
            },
        },
    }
    reg = InstallDateRegistry(hass)

    await reg.async_load()

    assert set(reg._components) == {"good/one"}


async def test_the_store_round_trips(hass, hass_storage):
    reg = InstallDateRegistry(hass)
    await reg.async_load()
    reg.begin_scan()
    reg.observe(make_repo(), days_ago(30), now())
    reg.end_scan()
    await reg._store.async_save(
        {"seeded_at": reg.seeded_at, "components": reg._components}
    )

    reloaded = InstallDateRegistry(hass)
    await reloaded.async_load()

    assert reloaded.seeded_at == reg.seeded_at
    reloaded.begin_scan()
    days, estimated = reloaded.observe(make_repo(), days_ago(0), now())
    assert days == 30
    assert estimated is True


# -- identity ---------------------------------------------------------------


def test_install_key_matches_the_update_repo_key():
    """Both sides of the integration must key a repository the same way."""
    from custom_components.custom_component_monitor.sensor import repo_key

    repo = make_repo("BramStroker/HomeAssistant-Powercalc")

    assert install_key(repo) == repo_key(
        f"https://github.com/{repo['full_name']}"
    )


def test_install_key_is_empty_without_an_owner():
    assert install_key({"full_name": "justaname"}) == ""
    assert install_key({}) == ""
