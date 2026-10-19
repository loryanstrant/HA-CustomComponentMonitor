"""Seen/dismissed/auto-hidden state for newly indexed HACS repositories (#95).

HACS flags a freshly indexed repository with ``new: true`` in its own
snapshot, but it also clears that flag itself — the HACS panel has a "clear
all new" action, and some HACS upgrades reset it wholesale. Treating
``new: true`` as the sole definition of "new" would empty the New-in-HACS
card the moment someone next opens the HACS panel, so this module keeps its
own record of when each repository was first seen and never moves that date
forward again, the same "first seen never moves forward" rule
``install_dates.py`` uses for installed components.

On top of first-seen it also remembers what the user has already dealt with:
items they dismissed by hand, items the integration auto-hid because they
looked foreign to the configured home country (and why, and whether that was
decided by the rules engine or an AI call), a "never hide again" override for
anything a restore has ever pulled back out of auto-hide, and the cached AI
category verdict for each repository so a restart does not re-spend AI calls
on items already categorised.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    NEW_ITEM_AI_PROMPT_VERSION,
    NEW_ITEMS_GRACE_DAYS,
    NEW_ITEMS_MAX_ENTRIES,
    NEW_ITEMS_STORAGE_KEY,
    NEW_ITEMS_STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)


def item_key(full_name: str) -> str:
    """Normalise ``Owner/Repo`` to a stable storage key.

    Lower-cased and stripped, so a value that round-tripped through a
    select-selector or a stray bit of whitespace from a form field still
    matches the key this module recorded it under.
    """
    return str(full_name or "").strip().lower()


class NewItemStore(Store):
    """The new-items store.

    v1 is the first version, so the migration hook cannot fire yet; it
    exists so a future shape change has a home, and returns an empty
    registry rather than guessing at an older shape. Anything else unreadable
    is handled by :meth:`NewItemRegistry.async_load`.
    """

    async def _async_migrate_func(
        self, old_major_version: int, old_minor_version: int, old_data: dict[str, Any]
    ) -> dict[str, Any]:
        return {
            "seen": {},
            "dismissed": {},
            "auto_hidden": {},
            "never_hide": [],
            "ai": {},
        }


class NewItemRegistry:
    """Remembers what the user has already seen and dealt with in HACS's "new" feed.

    One registry per coordinator, and the integration is effectively
    single-instance (``hass.data[DOMAIN]["coordinator"]`` is a single slot),
    so there is only ever one writer for the store.

    Usage::

        await registry.async_load()           # idempotent
        registry.note_seen(full_name)          # once per scan, per new repo
        ...
        await registry.async_save()            # only writes if something changed
    """

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialise an empty, unloaded registry."""
        self.hass = hass
        self._store: Store | None = NewItemStore(
            hass, NEW_ITEMS_STORAGE_VERSION, NEW_ITEMS_STORAGE_KEY
        )
        self._loaded = False
        self._dirty = False
        self._seen: dict[str, dict[str, Any]] = {}
        self._dismissed: dict[str, str] = {}
        self._auto_hidden: dict[str, dict[str, Any]] = {}
        self._never_hide: set[str] = set()
        self._ai: dict[str, dict[str, Any]] = {}

    # -- loading / saving -----------------------------------------------------

    async def async_load(self) -> None:
        """Load the registry once. Safe to call on every refresh.

        A corrupt or unreadable store must never block setup, so it degrades
        to an empty registry — the next scan simply re-discovers everything
        currently flagged "new" in HACS's own snapshot as if it were new here
        too, which is no worse than this module not existing.
        """
        if self._loaded:
            return
        raw: dict[str, Any] = {}
        if self._store is not None:
            try:
                raw = await self._store.async_load() or {}
            except Exception:  # pragma: no cover - a bad store must not break setup
                _LOGGER.warning(
                    "Could not read the New-in-HACS registry; dismissals, "
                    "auto-hides and first-seen dates are starting over",
                    exc_info=True,
                )
                raw = {}
        self._seen = _clean_seen(raw.get("seen"))
        self._dismissed = _clean_dismissed(raw.get("dismissed"))
        self._auto_hidden = _clean_auto_hidden(raw.get("auto_hidden"))
        self._never_hide = _clean_never_hide(raw.get("never_hide"))
        self._ai = _clean_ai(raw.get("ai"))
        self._loaded = True
        self._dirty = False

    async def async_save(self) -> None:
        """Persist the registry, but only if something has actually changed."""
        if not self._dirty:
            return
        if self._store is not None:
            await self._store.async_save(self._as_raw())
        self._dirty = False

    def _as_raw(self) -> dict[str, Any]:
        return {
            "seen": self._seen,
            "dismissed": self._dismissed,
            "auto_hidden": self._auto_hidden,
            "never_hide": sorted(self._never_hide),
            "ai": self._ai,
        }

    # -- first-seen ------------------------------------------------------------

    def note_seen(self, full_name: str, now: datetime | None = None) -> str:
        """Record and return the first-seen ISO timestamp. Never moves forward.

        Called once per scan for every repository HACS currently reports as
        new. ``last_present`` is refreshed every time, even when
        ``first_seen`` does not move — it is what :meth:`prune` measures the
        grace period against, so a long-standing item is never pruned while
        it is still present in the HACS snapshot.
        """
        now_utc = _now_utc(now)
        key = item_key(full_name)
        record = self._seen.get(key)
        if record is None:
            record = {
                "first_seen": now_utc.isoformat(),
                "last_present": now_utc.isoformat(),
            }
            self._seen[key] = record
        else:
            record["last_present"] = now_utc.isoformat()
        self._dirty = True
        return record["first_seen"]

    def first_seen(self, full_name: str) -> str | None:
        """ISO timestamp of when *full_name* was first seen, or ``None``."""
        record = self._seen.get(item_key(full_name))
        return record["first_seen"] if record else None

    def age_days(self, full_name: str, now: datetime | None = None) -> int:
        """Whole days since *full_name* was first seen, never negative."""
        first_seen = _parse(self.first_seen(full_name))
        if first_seen is None:
            return 0
        now_utc = _now_utc(now)
        return max(0, (now_utc - first_seen).days)

    # -- dismissal ---------------------------------------------------------------

    def is_dismissed(self, full_name: str) -> bool:
        return item_key(full_name) in self._dismissed

    def dismiss(self, full_name: str, now: datetime | None = None) -> bool:
        """Dismiss *full_name*. Returns ``False`` if it was already dismissed."""
        key = item_key(full_name)
        if key in self._dismissed:
            return False
        self._dismissed[key] = _now_utc(now).isoformat()
        self._dirty = True
        return True

    def restore(self, full_name: str) -> bool:
        """Undo a dismissal AND an auto-hide.

        If the item was auto-hidden, also add it to ``never_hide`` so the
        country rule (or the AI call behind it) cannot hide it again — a
        restore is the user saying "no, I do want to see this one", and
        without the override the very next scan would just auto-hide it a
        second time. Returns ``True`` if anything changed.
        """
        key = item_key(full_name)
        changed = False
        if key in self._dismissed:
            del self._dismissed[key]
            changed = True
        if key in self._auto_hidden:
            del self._auto_hidden[key]
            self._never_hide.add(key)
            changed = True
        if changed:
            self._dirty = True
        return changed

    @property
    def dismissed_count(self) -> int:
        return len(self._dismissed)

    @property
    def dismissed_keys(self) -> frozenset[str]:
        """Every dismissed repository, as storage keys.

        The ``restore_new_items`` service's ``category`` / ``all`` selectors
        have to reach dismissed items, which by definition are absent from
        the sensor's visible list — so they need the registry's own view of
        them. Returned frozen so a caller cannot mutate the registry's state
        behind its back.
        """
        return frozenset(self._dismissed)

    # -- auto-hide audit ----------------------------------------------------------

    def is_never_hide(self, full_name: str) -> bool:
        return item_key(full_name) in self._never_hide

    def record_hidden(
        self,
        full_name: str,
        *,
        country: str,
        term: str,
        field: str,
        source: str,
        now: datetime | None = None,
    ) -> None:
        """Record that *full_name* was auto-hidden, and why.

        ``source`` is ``"rules"`` for the plain country/term match and
        ``"ai"`` for a model-made call, so the card can explain its decision
        either way.
        """
        key = item_key(full_name)
        self._auto_hidden[key] = {
            "country": country,
            "term": term,
            "field": field,
            "source": source,
            "at": _now_utc(now).isoformat(),
        }
        self._dirty = True

    def clear_hidden(self, full_name: str) -> None:
        """Drop the auto-hide record for *full_name*, if any."""
        key = item_key(full_name)
        if self._auto_hidden.pop(key, None) is not None:
            self._dirty = True

    def hidden_record(self, full_name: str) -> dict[str, Any] | None:
        record = self._auto_hidden.get(item_key(full_name))
        return dict(record) if record is not None else None

    # -- AI verdict cache ----------------------------------------------------------

    def get_ai(self, full_name: str) -> dict[str, Any] | None:
        """The cached AI verdict for *full_name*.

        Returns ``None`` when there is no cached verdict, or when there is
        one but it was produced under a prompt that has since changed —
        ``NEW_ITEM_AI_PROMPT_VERSION`` is bumped deliberately to invalidate
        every cached verdict at once, so a stale one must never be handed
        back as current.
        """
        record = self._ai.get(item_key(full_name))
        if record is None:
            return None
        if record.get("prompt_version") != NEW_ITEM_AI_PROMPT_VERSION:
            return None
        return dict(record)

    def clear_ai(self, full_name: str) -> bool:
        """Forget the cached AI verdict for *full_name*. Returns whether one went.

        This is what ``categorise_new_items`` with ``force: true`` needs.
        Marking the coordinator's in-memory item ``ai_pending`` is not enough
        on its own: the batch runner asks :meth:`get_ai` first, so without
        dropping the cached record a forced re-run would hand back the very
        verdict the user asked to replace — and a call that then failed would
        leave the stale category in place looking like a fresh one.
        """
        if self._ai.pop(item_key(full_name), None) is not None:
            self._dirty = True
            return True
        return False

    def set_ai(
        self,
        full_name: str,
        *,
        category: str,
        country_scope: str | None,
        model: str | None,
        now: datetime | None = None,
    ) -> None:
        key = item_key(full_name)
        self._ai[key] = {
            "category": category,
            "country_scope": country_scope,
            "model": model,
            "prompt_version": NEW_ITEM_AI_PROMPT_VERSION,
            "at": _now_utc(now).isoformat(),
        }
        self._dirty = True

    # -- housekeeping ----------------------------------------------------------

    def prune(
        self, known_full_names: Iterable[str], now: datetime | None = None
    ) -> int:
        """Age- and size-bound the registry. Returns how many records were dropped.

        Drops ``seen``/``auto_hidden``/``ai`` records for repositories absent
        from the current HACS snapshot for longer than
        ``NEW_ITEMS_GRACE_DAYS`` — the snapshot lags and flaps, the same
        reason ``install_dates.py`` waits out a grace period before
        evicting, so a short absence must not reset anything. ``dismissed``
        and ``never_hide`` are never pruned: those are the user's own
        decisions and must survive a repository vanishing (an uninstall, a
        HACS re-index, an upstream rename) just as much as one that stays.

        Also enforces ``NEW_ITEMS_MAX_ENTRIES`` on ``seen``, oldest
        ``first_seen`` dropped first, skipping anything ``dismissed`` or
        ``never_hide`` so the cap never erases a user decision either.
        """
        now_utc = _now_utc(now)
        known_keys = {item_key(name) for name in known_full_names if name}
        cutoff = now_utc - timedelta(days=NEW_ITEMS_GRACE_DAYS)
        dropped = 0

        candidates = set(self._seen) | set(self._auto_hidden) | set(self._ai)
        for key in candidates:
            if key in known_keys or key in self._dismissed or key in self._never_hide:
                continue
            last_present = (
                _parse((self._seen.get(key) or {}).get("last_present"))
                or _parse((self._auto_hidden.get(key) or {}).get("at"))
                or _parse((self._ai.get(key) or {}).get("at"))
            )
            if last_present is not None and last_present >= cutoff:
                continue
            removed = False
            if self._seen.pop(key, None) is not None:
                removed = True
            if self._auto_hidden.pop(key, None) is not None:
                removed = True
            if self._ai.pop(key, None) is not None:
                removed = True
            if removed:
                dropped += 1
                self._dirty = True

        if len(self._seen) > NEW_ITEMS_MAX_ENTRIES:
            prunable = [
                (key, record)
                for key, record in self._seen.items()
                if key not in self._dismissed and key not in self._never_hide
            ]
            prunable.sort(key=lambda kv: str(kv[1].get("first_seen") or ""))
            excess = len(self._seen) - NEW_ITEMS_MAX_ENTRIES
            for key, _record in prunable:
                if excess <= 0:
                    break
                del self._seen[key]
                self._auto_hidden.pop(key, None)
                self._ai.pop(key, None)
                dropped += 1
                excess -= 1
                self._dirty = True

        return dropped


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_utc(now: datetime | None) -> datetime:
    """The given moment as aware UTC, or the current one."""
    return dt_util.as_utc(now) if now is not None else dt_util.utcnow()


def _parse(value: Any) -> datetime | None:
    """Parse a stored ISO timestamp into an aware UTC datetime."""
    parsed = dt_util.parse_datetime(str(value)) if value else None
    return dt_util.as_utc(parsed) if parsed else None


def _clean_seen(raw: Any) -> dict[str, dict[str, Any]]:
    """Coerce the stored ``seen`` map, dropping anything unusable.

    A value that is a bare string rather than a dict is tolerated and
    treated as ``first_seen`` on its own — an older or hand-edited store
    shape, not a reason to lose the record.
    """
    if not isinstance(raw, dict):
        return {}
    cleaned: dict[str, dict[str, Any]] = {}
    for key, value in raw.items():
        if isinstance(value, str):
            first_seen = _parse(value)
            last_present = first_seen
        elif isinstance(value, dict):
            first_seen = _parse(value.get("first_seen"))
            last_present = _parse(value.get("last_present")) or first_seen
        else:
            continue
        if first_seen is None:
            continue
        cleaned[str(key)] = {
            "first_seen": first_seen.isoformat(),
            "last_present": (last_present or first_seen).isoformat(),
        }
    return cleaned


def _clean_dismissed(raw: Any) -> dict[str, str]:
    """Coerce the stored ``dismissed`` map, dropping unparseable timestamps."""
    if not isinstance(raw, dict):
        return {}
    cleaned: dict[str, str] = {}
    for key, value in raw.items():
        parsed = _parse(value)
        if parsed is None:
            continue
        cleaned[str(key)] = parsed.isoformat()
    return cleaned


def _clean_auto_hidden(raw: Any) -> dict[str, dict[str, Any]]:
    """Coerce the stored ``auto_hidden`` map, dropping anything unusable."""
    if not isinstance(raw, dict):
        return {}
    cleaned: dict[str, dict[str, Any]] = {}
    for key, record in raw.items():
        if not isinstance(record, dict):
            continue
        at = _parse(record.get("at"))
        if at is None:
            continue
        cleaned[str(key)] = {
            "country": str(record.get("country") or ""),
            "term": str(record.get("term") or ""),
            "field": str(record.get("field") or ""),
            "source": str(record.get("source") or ""),
            "at": at.isoformat(),
        }
    return cleaned


def _clean_never_hide(raw: Any) -> set[str]:
    """Coerce the stored ``never_hide`` list into a set of keys."""
    if not isinstance(raw, list):
        return set()
    return {str(key) for key in raw if key}


def _clean_ai(raw: Any) -> dict[str, dict[str, Any]]:
    """Coerce the stored ``ai`` map, dropping anything unusable."""
    if not isinstance(raw, dict):
        return {}
    cleaned: dict[str, dict[str, Any]] = {}
    for key, record in raw.items():
        if not isinstance(record, dict):
            continue
        at = _parse(record.get("at"))
        cleaned[str(key)] = {
            **record,
            "at": at.isoformat() if at else None,
        }
    return cleaned
