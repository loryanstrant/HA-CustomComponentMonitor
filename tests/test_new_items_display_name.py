"""Display names for new HACS repositories (#95).

HACS only stores a real name for repositories it has fetched the contents of
— in practice, the ones you have installed. For the thousands merely *listed*
in the store, ``repository_manifest`` is an empty dict and ``name`` is absent,
so HACS's own panel falls back to ``slug.replace("-", " ").title()``.

These pin both halves of that: the stored name is preferred and the preference
order matches HACS's own, and the fallback is a tidier version of HACS's that
does not destroy capitalisation the author chose.
"""
from __future__ import annotations

import pytest

from custom_components.custom_component_monitor.new_items import (
    display_name_for,
    prettify_repo_name,
)

# -- the stored name wins, in HACS's own order ---------------------------------


def test_the_hacs_json_name_is_preferred_over_everything():
    repo = {
        "repository_manifest": {"name": "Mushroom"},
        "manifest_name": "Lovelace Mushroom",
        "name": "lovelace-mushroom",
    }

    assert display_name_for("piitaya/lovelace-mushroom", repo) == "Mushroom"


def test_the_integration_manifest_name_is_used_when_hacs_json_has_none():
    repo = {"repository_manifest": {}, "manifest_name": "Frigate"}

    assert display_name_for("blakeblackshear/frigate-hass-integration", repo) == "Frigate"


def test_a_name_that_is_merely_the_slug_is_not_a_display_name():
    """``name`` is often just the repo slug — prettify instead of showing it."""
    repo = {"repository_manifest": {}, "name": "ha-checklist-card"}

    assert display_name_for("yosef-chai/ha-checklist-card", repo) == "Checklist Card"


@pytest.mark.parametrize("repo", [None, {}, {"repository_manifest": None}])
def test_a_record_with_no_name_at_all_falls_back_to_the_slug(repo):
    """The real shape of all 2,244 not-installed new repos in the prod store."""
    assert display_name_for("gurroz/ha-media-gallery", repo) == "Media Gallery"


def test_a_name_that_is_the_slug_in_different_punctuation_is_still_the_slug():
    """Case and separators must not let the slug through as a display name."""
    assert display_name_for("Owner/My-Repo", {"name": "my_repo"}) == "My Repo"
    assert display_name_for("owner/ha_washdata", {"name": "ha-washdata"}) == "Washdata"


def test_a_blank_stored_name_is_ignored():
    repo = {"repository_manifest": {"name": "   "}, "manifest_name": ""}

    assert display_name_for("gurroz/ha-media-gallery", repo) == "Media Gallery"


# -- prettifying the slug -------------------------------------------------------


@pytest.mark.parametrize(
    ("full_name", "expected"),
    [
        # The Home-Assistant-flavoured decoration goes, front and back.
        ("yosef-chai/ha-checklist-card", "Checklist Card"),
        ("3dg1luk43/ha_washdata", "Washdata"),
        ("akustikrausch/gonzales-integration", "Gonzales"),
        ("JimCarnicelli/aevocam-homeassistant", "Aevocam"),
        ("MauroDruwel/Weathercloud-HA", "Weathercloud"),
        ("kasztp/hass_databricks", "Databricks"),
        ("piitaya/lovelace-mushroom", "Mushroom"),
        # camelCase comes apart, and an acronym butted against a word with it.
        ("simplytoast1/ScentAirHABridge", "Scent Air HA Bridge"),
        ("treee111/hagerFlow", "Hager Flow"),
        # ...and an oddly-capitalised run is left whole rather than cut after
        # its first letter ("H Asmartirrigation" is worse than doing nothing).
        ("altmenorg/HAsmartirrigation", "HAsmartirrigation"),
        # ...but a single-letter brand prefix does not. "iOS", not "i OS".
        ("1bobby-git/HA-iOS-ANCS", "iOS ANCS"),
        ("skircr115/ha-verizonFiOS", "Verizon FiOS"),
        # Initialisms are upper-cased rather than title-cased into "Mqtt".
        ("hoizi89/pv_management", "PV Management"),
        ("JTertin/shelly-ble-rpc", "Shelly BLE RPC"),
        ("someone/esp32-sensor", "ESP32 Sensor"),
        # Capitalisation the author chose is left alone.
        ("someone/SmartThings-bridge", "Smart Things Bridge"),
        ("someone/AEMO-prices", "AEMO Prices"),
        # A preposition left dangling by the removed suffix goes with it...
        ("javaDevJT/DTE-Rates-for-Home-Assistant", "DTE Rates"),
        # ...but a last word that was never decoration stays put. Nothing was
        # stripped from either of these, so there is nothing dangling.
        ("someone/awesome-plug-in", "Awesome Plug In"),
        ("someone/listen-in-on", "Listen In On"),
        ("someone/switch-it-on", "Switch It On"),
        # Dots and mixed separators are separators too.
        ("someone/my.thing_here-now", "My Thing Here Now"),
    ],
)
def test_prettify_repo_name(full_name, expected):
    assert prettify_repo_name(full_name) == expected


def test_a_name_made_entirely_of_decoration_keeps_its_slug():
    """Stripping must never leave nothing — a bare slug beats a blank row."""
    assert prettify_repo_name("someone/ha") == "HA"
    assert prettify_repo_name("someone/hass-") == "Hass"
    assert prettify_repo_name("someone/ha-") == "HA"


@pytest.mark.parametrize("full_name", ["", None, "   ", "/"])
def test_prettify_tolerates_junk_without_raising(full_name):
    assert prettify_repo_name(full_name) == ""  # type: ignore[arg-type]


def test_a_repo_with_no_owner_segment_still_works():
    assert prettify_repo_name("ha-media-gallery") == "Media Gallery"


def test_the_result_is_never_empty_for_a_real_repository():
    """The property that matters: every row gets something to show."""
    for slug in ("a", "x/y", "OWNER/UPPER", "o/123", "o/ha-ha", "o/___"):
        assert display_name_for(slug, {}).strip()
