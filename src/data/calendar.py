"""NEPSE trading calendar abstraction (Sprint 13.4 + 13.5 governance).

Sprint 13.3 flagged that historical gap detection relied on a generic
business-day heuristic (``np.busday_count``) which cannot distinguish a
legitimate NEPSE market closure from missing data.  This module
centralises the trading-calendar semantics so every component agrees on
what a trading session is.

Provenance (Phase 11 policy):

- **Weekend rule** — NEPSE trades **Sunday–Thursday**; Friday and
  Saturday are the weekly closure.  This is the long-standing NEPSE
  trading schedule (documented by NEPSE itself; not invented here).
- **Holidays** — an explicit, operator-maintained, versioned list in
  the calendar data file.  No holiday is hard-coded in Python without
  provenance.
- **Special sessions** — a date designated as a trading session even
  though it falls on a weekend/holiday (e.g. a government-directed
  Saturday session).  Explicitly governed in the data file.
- **Exceptional closures** — a scheduled trading day that is
  exceptionally closed (e.g. an emergency market holiday).  Explicitly
  governed in the data file.
- **Observed sessions** — the strongest available evidence: the union
  of validated historical sessions derived from the corpus
  (``NepseCalendar.from_corpus``).  A weekday with *no* corpus session
  is treated as a market closure, not fabricated data.

Day classification::

    TRADING_DAY | HOLIDAY | WEEKEND | UNKNOWN | SPECIAL_SESSION | EXCEPTIONAL_CLOSURE

Gap classification (Phase 12)::

    EXPECTED_NON_TRADING_DAY   — weekend / holiday / closure / corpus-verified closure
    MISSING_TRADING_SESSION    — scheduled trading day, no record for a symbol
    UNKNOWN                    — no corpus evidence either way (never guessed)

The calendar never fabricates missing records — it only classifies.

Sprint 13.5 governance: the persisted calendar (``NEPSE_CALENDAR_FILE``)
is a **versioned, provenance-aware data file** (calendar_version,
effective_from/effective_to, timezone, trading_week, holidays,
special_sessions, closures, provenance, last_validated).  Loading
validates the file (``validate_calendar_data``) and **fails safe**: a
malformed calendar is rejected with logged diagnostics and the base
weekend-rule calendar is used instead — a broken calendar file can never
silently turn an exchange closure into a normal trading day.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping

from src.config import DATA_DIRECTORY, NEPSE_CALENDAR_FILE

logger = logging.getLogger(__name__)

# ───────────────────────────────────────────────────────────────────
# Status codes
# ───────────────────────────────────────────────────────────────────

# Single-day classification
TRADING_DAY = "TRADING_DAY"
HOLIDAY = "HOLIDAY"
WEEKEND = "WEEKEND"
UNKNOWN = "UNKNOWN"
SPECIAL_SESSION = "SPECIAL_SESSION"
EXCEPTIONAL_CLOSURE = "EXCEPTIONAL_CLOSURE"

# Gap classification (what a missing record means)
EXPECTED_NON_TRADING_DAY = "EXPECTED_NON_TRADING_DAY"
MISSING_TRADING_SESSION = "MISSING_TRADING_SESSION"

# Python ``date.weekday()`` values for the NEPSE weekly closure:
# Monday=0 .. Sunday=6 → Friday=4, Saturday=5.
NEPSE_WEEKEND_DAYS: tuple[int, ...] = (4, 5)

# NEPSE trades Sunday–Thursday: weekday() 6,0,1,2,3.
NEPSE_TRADING_WEEK: tuple[int, ...] = (6, 0, 1, 2, 3)

DEFAULT_CALENDAR_VERSION = "1.0"
# Versions of the calendar data-file schema accepted on load.  The
# Sprint 13.5 governed file schema is ``2.0`` (version / effective
# window / timezone / trading_week / holidays / special_sessions /
# closures / provenance / last_validated); ``1.0`` is the v1
# backward-compatible shape.  ``from_corpus``/bare constructors keep the
# backward-compatible ``1.0`` default so existing callers behave
# byte-identically.
SUPPORTED_CALENDAR_VERSIONS: tuple[str, ...] = ("1.0", "2.0")
DEFAULT_TIMEZONE = "Asia/Kathmandu"
DEFAULT_CALENDAR_PROVENANCE = (
    "NEPSE trading schedule: Sunday–Thursday open, Friday–Saturday closed. "
    "Holiday list maintained by operators; no holiday hard-coded without provenance. "
    "Observed sessions may be derived from the validated corpus (from_corpus)."
)


@dataclass
class SessionFinding:
    """Classification of one calendar day relative to a symbol's records."""

    date: date
    classification: str  # EXPECTED_NON_TRADING_DAY | MISSING_TRADING_SESSION | TRADING_DAY | UNKNOWN
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date.isoformat(),
            "classification": self.classification,
            "reason": self.reason,
        }


def _as_date(value: Any) -> date | None:
    """Coerce a date/str/datetime to ``datetime.date`` (None on failure)."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


# ═══════════════════════════════════════════════════════════════════
# Calendar governance (Sprint 13.5)
# ═══════════════════════════════════════════════════════════════════


def _extract_dates(entries: Any) -> tuple[list[Any], list[Any]]:
    """Split a calendar-file date collection into (dates, names/notes).

    Accepts both a flat list of ISO strings (v1) and a list of dicts
    with a ``date`` key (v2, entries may carry ``name``/``reason``/
    ``source`` metadata).  Malformed entries are kept in the *names*
    list as ``None`` so validation can report them precisely.
    """
    dates: list[Any] = []
    names: list[Any] = []
    for entry in entries or ():
        if isinstance(entry, Mapping):
            dates.append(entry.get("date"))
            names.append(entry.get("name") or entry.get("reason"))
        else:
            dates.append(entry)
            names.append(None)
    return dates, names


def validate_calendar_data(data: Mapping[str, Any]) -> list[str]:
    """Validate a calendar data-file mapping; return diagnostics.

    Returns a list of human-readable problem strings.  **Empty list =
    valid.**  A calendar that fails validation must never be used —
    ``NepseCalendar.load`` rejects it and falls back to the base
    weekend-rule calendar (a broken calendar can never silently turn an
    exchange closure into a normal trading day).

    Checks:

    - supported calendar version (``calendar_version`` / ``version``)
    - effective_from <= effective_to; entries within the window where
      the window is declared
    - every holiday / special session / closure is a parseable date
    - no duplicate dates within a category
    - no date listed in both holidays and special sessions, both
      holidays and closures, or both special sessions and closures
      (contradictory definitions)
    - weekend / trading_week values in 0..6 and not contradictory
    - provenance present (a governed calendar must carry provenance)
    """
    problems: list[str] = []

    # Fail-safe governance: a non-mapping payload (``None``, a list, a
    # scalar) must be a diagnostic, never a crash (Sprint 13.5).
    if not isinstance(data, Mapping):
        return ["invalid calendar data: expected a mapping of calendar fields"]

    version = str(data.get("calendar_version") or data.get("version") or DEFAULT_CALENDAR_VERSION)
    # A version is accepted when its *major* line is supported: activation
    # bumps the patch component (e.g. "2.0" -> "2.1", Sprint 13.7 holiday
    # intake) while staying on the governed schema line.  A wholly foreign
    # major (e.g. "9.9") is rejected.
    if version.split(".")[0] not in {
        v.split(".")[0] for v in SUPPORTED_CALENDAR_VERSIONS
    }:
        problems.append(
            f"unsupported calendar version '{version}' (supported: {', '.join(SUPPORTED_CALENDAR_VERSIONS)})"
        )

    ef = _as_date(data.get("effective_from"))
    et = _as_date(data.get("effective_to"))
    if data.get("effective_from") is not None and ef is None:
        problems.append(f"invalid effective_from: {data.get('effective_from')!r}")
    if data.get("effective_to") is not None and et is None:
        problems.append(f"invalid effective_to: {data.get('effective_to')!r}")
    if ef is not None and et is not None and ef > et:
        problems.append(
            f"invalid date range: effective_from {ef} is after effective_to {et}"
        )

    # Weekday fields: every value must be an integer in 0..6 (checked on
    # the *raw* value — ``w % 7`` before the range check would silently
    # map 9 -> 2, a trading day, and mask the malformed entry).  The
    # validator must NEVER raise: a malformed value is a diagnostic, not
    # a crash (Sprint 13.5 fail-safe governance).
    def _parse_weekdays(field: str) -> set[int]:
        raw = data.get(field)
        if raw is None:
            return set()
        if not isinstance(raw, (list, tuple, set)):
            problems.append(f"{field} must be a list of weekday integers 0-6")
            return set()
        out: set[int] = set()
        for w in raw:
            try:
                val = int(w)
            except (TypeError, ValueError):
                problems.append(f"{field} value not an integer: {w!r}")
                continue
            if val not in range(7):
                problems.append(f"{field} value out of range 0-6: {w!r}")
                continue
            out.add(val)
        return out

    weekend = _parse_weekdays("weekend_days")
    trading = _parse_weekdays("trading_week")
    # Contradiction only when the file explicitly declares BOTH fields
    # (a trading_week-only file legitimately derives its weekend as the
    # complement — see the constructor).
    if data.get("weekend_days") is not None and data.get("trading_week") is not None:
        overlap = weekend & trading
        if overlap:
            problems.append(
                f"contradictory week: weekday(s) {sorted(overlap)} in both trading_week and weekend_days"
            )

    # Date categories: parse + duplicates + cross-category contradictions.
    categories = ("holidays", "special_sessions", "closures")
    parsed: dict[str, set[date]] = {}
    for cat in categories:
        dates, names = _extract_dates(data.get(cat))
        seen: set[date] = set()
        for raw, name in zip(dates, names):
            d = _as_date(raw)
            if d is None:
                problems.append(f"{cat}: invalid date entry {raw!r} (name={name!r})")
                continue
            if ef is not None and d < ef:
                problems.append(f"{cat}: date {d} before effective_from {ef}")
            if et is not None and d > et:
                problems.append(f"{cat}: date {d} after effective_to {et}")
            if d in seen:
                problems.append(f"{cat}: duplicate date {d}")
            seen.add(d)
        parsed[cat] = seen

    for cat in categories:
        for other in categories:
            if cat >= other:
                continue
            clash = sorted(parsed[cat] & parsed[other])
            if clash:
                problems.append(
                    f"contradictory definitions: {', '.join(d.isoformat() for d in clash)}"
                    f" listed in both {cat} and {other}"
                )

    # An exceptional closure on a weekend is an impossible/redundant
    # state (the day is already closed) — report it so operators cannot
    # ship a contradictory calendar (Sprint 13.5 governance).
    eff_weekend = weekend if weekend else set(NEPSE_WEEKEND_DAYS)
    for d in sorted(parsed["closures"]):
        if d.weekday() in eff_weekend:
            problems.append(f"closures: {d} is already a weekend day (redundant closure)")

    # A governed calendar must carry provenance (v2 requirement).
    provenance = data.get("provenance")
    source = data.get("source")
    if not provenance and not source:
        problems.append("missing provenance: a governed calendar must document its source")

    return problems


# ═══════════════════════════════════════════════════════════════════
# Calendar update / rollback governance (Sprint 13.6 §9)
# ═══════════════════════════════════════════════════════════════════
#
# The calendar is a governed, versioned artifact.  Updating it goes
# through a validation pipeline before activation and always preserves
# the previous valid version for rollback:
#
#     Candidate
#        ↓
#     Schema validation   (validate_calendar_data)
#        ↓
#     Semantic validation (candidate-vs-active compatibility)
#        ↓
#     Coverage validation (effective window / trading week overlap)
#        ↓
#     Provenance validation (mandatory)
#        ↓
#     Regression validation (classifies known NEPSE weekends)
#        ↓
#     Activate (atomic write)  →  backup previous version first
#        ↓
#     Record version history
#
# On failure the candidate is rejected and the previous valid calendar
# remains active.  Backups are bounded (CALENDAR_MAX_BACKUPS) and the
# version history is bounded (CALENDAR_MAX_HISTORY) — no unbounded
# growth.

CALENDAR_HISTORY_FILE = os.environ.get(
    "NEPSE_CALENDAR_HISTORY_FILE",
    "data/state/nepse_calendar_history.json",
)
CALENDAR_MAX_BACKUPS = 10
CALENDAR_MAX_HISTORY = 20


def validate_candidate_calendar(
    data: Mapping[str, Any],
    current: NepseCalendar | None = None,
) -> list[str]:
    """Full validation pipeline for a *candidate* calendar update.

    Schema validation (``validate_calendar_data``) plus candidate-vs-
    active compatibility: when the candidate's effective window overlaps
    the active calendar's window, the trading week must match — an
    incompatible calendar version must never replace the active one over
    the same period.  Returns deterministic diagnostics (empty = valid).
    Never raises; never activates anything.
    """
    problems = list(validate_calendar_data(data))
    if problems:
        return problems

    candidate = NepseCalendar.from_dict(data)

    # Regression validation runs for EVERY candidate (even without an
    # active calendar to compare against): the candidate must classify
    # known NEPSE weekend days (Fri/Sat) as non-trading within its
    # window.  Deterministic sanity check, not fabricated holiday data.
    for sample in (date(2026, 7, 24), date(2026, 7, 25)):  # Fri, Sat
        if candidate.covers(sample) and candidate.is_trading_day(sample):
            problems.append(
                f"candidate regression: {sample} (a known NEPSE weekend) would be "
                "classified as a trading day"
            )
    if problems:
        return problems
    if current is None:
        return problems

    # Coverage validation: does the candidate window overlap the active
    # calendar?  Unbounded windows are treated as overlapping everything.
    def _overlaps(a_from, a_to, b_from, b_to):
        if a_from is None and a_to is None:
            return True
        if b_from is None and b_to is None:
            return True
        a_start = a_from or date.min
        a_end = a_to or date.max
        b_start = b_from or date.min
        b_end = b_to or date.max
        return not (a_end < b_start or b_end < a_start)

    overlap = _overlaps(
        current.effective_from, current.effective_to,
        candidate.effective_from, candidate.effective_to,
    )
    if overlap and set(current.trading_week) != set(candidate.trading_week):
        problems.append(
            "candidate trading_week conflicts with the active calendar over an "
            "overlapping window — overlapping incompatible versions are rejected"
        )
    return problems


def _calendar_backup_path(target: Path) -> Path:
    """Backup file path for the calendar (bounded, timestamped)."""
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    return target.parent / f"{target.stem}.{stamp}.bak.json"


def _prune_calendar_backups(target: Path) -> None:
    """Keep at most CALENDAR_MAX_BACKUPS backup files for *target*."""
    backups = sorted(target.parent.glob(f"{target.stem}.*.bak.json"))
    for stale in backups[:-CALENDAR_MAX_BACKUPS]:
        try:
            stale.unlink(missing_ok=True)
        except OSError:
            pass


def _record_calendar_history(entry: Mapping[str, Any]) -> None:
    """Append a bounded, compact activation record."""
    from src.utils.json_store import update_json  # noqa: PLC0415 - lazy

    path = Path(CALENDAR_HISTORY_FILE)

    def _mutate(history: Any) -> list[dict[str, Any]]:
        entries = list(history) if isinstance(history, list) else []
        entries.append({
            "version": str(entry.get("version")),
            "activated_at": entry.get("activated_at")
            or datetime.now().isoformat(timespec="seconds"),
            "effective_from": entry.get("effective_from"),
            "effective_to": entry.get("effective_to"),
            "source": entry.get("source", ""),
            "via": entry.get("via", "update"),
        })
        return entries[-CALENDAR_MAX_HISTORY:]

    update_json(path, _mutate, [], log_name="CalendarHistory")


def calendar_version_history() -> list[dict[str, Any]]:
    """Bounded history of calendar activations (latest first)."""
    from src.utils.json_store import load_json  # noqa: PLC0415 - lazy

    data = load_json(Path(CALENDAR_HISTORY_FILE), [], log_name="CalendarHistory")
    if not isinstance(data, list):
        return []
    return list(reversed(data[-CALENDAR_MAX_HISTORY:]))


def update_calendar(
    data: Mapping[str, Any],
    *,
    path: str | Path | None = None,
    backup: bool = True,
    via: str = "update",
    check_compatibility: bool = True,
) -> tuple[bool, list[str]]:
    """Validate and activate a candidate calendar (atomic, versioned).

    Returns ``(ok, problems)``.  On failure the previous valid calendar
    stays active and the candidate is never written.  On success the
    previous calendar is preserved as a bounded backup, the candidate is
    written atomically (``json_store`` temp-file + rename), the module
    cache is reset, and the activation is recorded in version history.

    Args:
        via: History marker — ``"update"`` (default) or ``"rollback"``.
        check_compatibility: When ``True`` (default) the candidate is
            validated against the currently-active calendar (overlapping
            incompatible versions are rejected).  Rollback passes
            ``False`` — restoring a previously-valid backup must never
            be blocked by the (possibly incompatible) current calendar;
            the backup is validated on its own merits instead.

    Never fabricates holiday data: the candidate is taken verbatim; only
    validation gates it.
    """
    target = Path(path) if path else Path(NEPSE_CALENDAR_FILE)
    current = NepseCalendar.load(target)
    if check_compatibility:
        problems = validate_candidate_calendar(data, current=current)
    else:
        # Rollback path: validate the backup on its own merits only
        # (schema + weekend regression), never against the current
        # active calendar (Sprint 13.6 §9 rollback must work even when
        # the current calendar is incompatible).
        problems = validate_candidate_calendar(data, current=None)
    if problems:
        return False, problems

    candidate = NepseCalendar.from_dict(data)
    if backup and target.exists():
        try:
            _backup_artifacts = _calendar_backup_path(target)
            _backup_artifacts.write_bytes(target.read_bytes())
            _prune_calendar_backups(target)
        except OSError:
            pass  # backup is best-effort; a failed backup never blocks activation
    candidate.save(target)
    reset_calendar_cache()
    _record_calendar_history({
        "version": candidate.version,
        "effective_from": candidate.effective_from.isoformat() if candidate.effective_from else None,
        "effective_to": candidate.effective_to.isoformat() if candidate.effective_to else None,
        "source": candidate.source,
        "via": via,
    })
    return True, []


def rollback_calendar(*, path: str | Path | None = None) -> tuple[bool, list[str]]:
    """Restore the most recent valid backup calendar.

    Returns ``(ok, problems)``.  The rollback candidate goes through
    schema + regression validation (never the candidate-vs-active
    compatibility check — a rollback must succeed even when the current
    calendar is incompatible); a corrupt backup is rejected and the
    active calendar is left untouched.  The pre-rollback calendar is
    itself preserved as a backup (so rollback is reversible).
    """
    target = Path(path) if path else Path(NEPSE_CALENDAR_FILE)
    backups = sorted(target.parent.glob(f"{target.stem}.*.bak.json"))
    if not backups:
        return False, ["no calendar backup available to roll back to"]

    latest = backups[-1]
    from src.utils.json_store import load_json  # noqa: PLC0415 - lazy

    data = load_json(latest, None, log_name="CalendarRollback")
    if not isinstance(data, dict):
        return False, [f"backup {latest.name} is unreadable"]

    return update_calendar(
        data,
        path=target,
        backup=True,
        via="rollback",
        check_compatibility=False,
    )


# ═══════════════════════════════════════════════════════════════════
# Holiday intake workflow (Sprint 13.7 §12–14)
# ═══════════════════════════════════════════════════════════════════
#
# A safe, operator-facing workflow for adding a single holiday to the
# governed calendar.  The candidate is validated (schema, semantic,
# conflict, provenance, regression) *before* activation; on failure the
# candidate is rejected and the active calendar is untouched.  On
# success the date is merged into the current calendar and activated
# through the existing ``update_calendar`` pipeline (atomic write +
# backup + version history + cache reset).  Never fabricates holiday
# data — the operator supplies every field.

# Candidate field names (the operator-facing contract, §12).
HOLIDAY_DATE = "date"
HOLIDAY_DESCRIPTION = "description"
HOLIDAY_TYPE = "type"  # "holiday" | "special_session" | "exceptional_closure"
HOLIDAY_SOURCE = "source"
HOLIDAY_PROVENANCE = "provenance"
HOLIDAY_OPERATOR = "operator"
HOLIDAY_NOTES = "notes"

_HOLIDAY_TYPE_CATEGORY = {
    "holiday": "holidays",
    "special_session": "special_sessions",
    "exceptional_closure": "closures",
}


def validate_holiday_candidate(
    candidate: Mapping[str, Any],
    calendar: NepseCalendar | None = None,
) -> list[str]:
    """Validate a single operator-submitted holiday candidate.

    Checks (all deterministic, never raises):

    - the candidate is a mapping
    - ``date`` is a parseable date
    - ``type`` is one of holiday / special_session / exceptional_closure
    - ``provenance`` or ``source`` is present (operator attribution)
    - effective-range: the date falls inside the calendar's window when
      the window is declared
    - weekend consistency: an ``exceptional_closure`` on a weekend is
      redundant (the day is already closed); a ``holiday`` on a weekend
      is allowed only when the calendar does not already treat that
      weekend day as closed — it is flagged as redundant otherwise
    - duplicate date in the target category
    - conflicting special session / exceptional closure on the same date

    Args:
        candidate: Operator-supplied holiday definition.
        calendar: The active calendar to validate against (optional —
            duplicate/conflict/range checks need it; schema/provenance
            checks do not).

    Returns:
        Deterministic diagnostics; empty list = valid.
    """
    problems: list[str] = []
    if not isinstance(candidate, Mapping):
        return ["invalid holiday candidate: expected a mapping"]

    raw_date = candidate.get(HOLIDAY_DATE)
    day = _as_date(raw_date)
    if raw_date is None or day is None:
        problems.append(f"invalid holiday date: {raw_date!r}")

    htype = str(candidate.get(HOLIDAY_TYPE) or "").strip().lower()
    if htype not in _HOLIDAY_TYPE_CATEGORY:
        problems.append(
            f"invalid holiday type {htype!r} (expected holiday | "
            "special_session | exceptional_closure)"
        )

    provenance = candidate.get(HOLIDAY_PROVENANCE)
    source = candidate.get(HOLIDAY_SOURCE)
    operator = candidate.get(HOLIDAY_OPERATOR)
    if not provenance and not source and not operator:
        problems.append(
            "missing provenance: a holiday candidate must document its source, "
            "operator, or provenance"
        )

    if day is None or htype not in _HOLIDAY_TYPE_CATEGORY or calendar is None:
        return problems

    category = _HOLIDAY_TYPE_CATEGORY[htype]

    # Effective range.
    if calendar.effective_from is not None and day < calendar.effective_from:
        problems.append(f"holiday {day} before effective_from {calendar.effective_from}")
    if calendar.effective_to is not None and day > calendar.effective_to:
        problems.append(f"holiday {day} after effective_to {calendar.effective_to}")

    # Weekend consistency.
    if calendar.is_weekend(day):
        if htype == "exceptional_closure":
            problems.append(
                f"exceptional closure {day} is already a weekend day (redundant closure)"
            )
        elif htype == "holiday" and not calendar.is_special_session(day):
            problems.append(
                f"holiday {day} is already a weekend day (redundant; use a "
                "special_session to make it a trading day)"
            )

    # Duplicate in the target category.
    existing = getattr(calendar, category, ())
    if day in existing:
        problems.append(f"duplicate {htype}: {day} is already in the calendar")

    # Conflicting definitions across categories.
    if htype == "holiday":
        if calendar.is_special_session(day):
            problems.append(f"conflicting special_session on {day} (already a special session)")
        if calendar.is_closure(day):
            problems.append(f"conflicting exceptional_closure on {day}")
    elif htype == "special_session":
        if calendar.is_holiday(day):
            problems.append(f"conflicting holiday on {day} (already a holiday)")
        if calendar.is_closure(day):
            problems.append(f"conflicting exceptional_closure on {day}")
    else:  # exceptional_closure
        if calendar.is_holiday(day):
            problems.append(f"conflicting holiday on {day}")
        if calendar.is_special_session(day):
            problems.append(f"conflicting special_session on {day}")

    return problems


def _bump_version(version: str) -> str:
    """Increment the patch component of a calendar version string.

    ``"2.0" -> "2.1"``.  Every activation produces a new version so
    the change is auditable in version history and rollback targets
    are unambiguous.  A non-numeric tail falls back to appending ``.1``
    (never raises).
    """
    parts = str(version).split(".")
    try:
        parts[-1] = str(int(parts[-1]) + 1)
    except (TypeError, ValueError):
        parts.append("1")
    return ".".join(parts)


def submit_holiday_candidate(
    candidate: Mapping[str, Any],
    *,
    path: str | Path | None = None,
    via: str = "holiday_intake",
) -> tuple[bool, list[str]]:
    """Validate and activate a single operator-submitted holiday.

    Pipeline (§12): schema → semantic → conflict → provenance →
    regression (via ``update_calendar``) → version bump → activation.
    On any failure the candidate is rejected and the active calendar is
    left byte-identical.

    Args:
        candidate: Holiday definition (see ``validate_holiday_candidate``).
        path: Calendar file to update (defaults to the governed file).
        via: History marker for the activation record.

    Returns:
        ``(ok, problems)``.  ``ok=True`` means the calendar was updated
        atomically and the version bumped.
    """
    target = Path(path) if path else Path(NEPSE_CALENDAR_FILE)
    current = NepseCalendar.load(target) or NepseCalendar()

    problems = validate_holiday_candidate(candidate, calendar=current)
    if problems:
        return False, problems

    day = _as_date(candidate[HOLIDAY_DATE])
    htype = str(candidate[HOLIDAY_TYPE]).strip().lower()
    category = _HOLIDAY_TYPE_CATEGORY[htype]

    data = current.to_dict()
    existing = [d for d in data.get(category) or ()]
    if day.isoformat() not in existing:
        existing.append(day.isoformat())
    data[category] = sorted(existing)

    # Provenance: the operator's attribution is appended to the calendar
    # provenance string so every activation remains auditable.
    operator = candidate.get(HOLIDAY_OPERATOR) or candidate.get(HOLIDAY_SOURCE) or "operator"
    desc = str(candidate.get(HOLIDAY_DESCRIPTION) or htype)
    note = f"{day.isoformat()} ({desc}) via {operator}"
    data["operator_notes"] = str(data.get("operator_notes") or "") + (
        ("; " if data.get("operator_notes") else "") + note
    )
    data["last_validated"] = date.today().isoformat()
    data["source"] = str(candidate.get(HOLIDAY_SOURCE) or data.get("source") or "")

    # Version bump: every activation produces a new calendar version so
    # the change is auditable in version history and rollback targets
    # are unambiguous (Sprint 13.7 §12 activation step).
    next_version = _bump_version(
        str(data.get("calendar_version") or data.get("version") or DEFAULT_CALENDAR_VERSION)
    )
    data["calendar_version"] = next_version
    data["version"] = next_version

    return update_calendar(data, path=target, via=via)


class NepseCalendar:
    """Authoritative trading-calendar abstraction for NEPSE sessions.

    Args:
        holidays: Iterable of ``date``/ISO strings that are full-day
            market closures (versioned, operator-maintained).
        weekend_days: ``date.weekday()`` values treated as the weekly
            closure (default NEPSE Fri/Sat).
        observed_sessions: The set of dates that are *known* trading
            sessions (e.g. the corpus union from ``from_corpus``).  When
            provided, a weekday with no observed session is classified as
            a closure; when ``None`` such a day is ``UNKNOWN`` (no
            evidence either way — never guessed).
        provenance: Human-readable provenance for the calendar contents.
        version: Calendar version string (bumped when the contents change).
    """

    def __init__(
        self,
        holidays: Iterable[Any] | None = None,
        *,
        weekend_days: Iterable[int] | None = None,
        observed_sessions: Iterable[Any] | None = None,
        provenance: str | Mapping[str, Any] = DEFAULT_CALENDAR_PROVENANCE,
        version: str = DEFAULT_CALENDAR_VERSION,
        # ── Sprint 13.5 governed-calendar fields (all optional) ──
        special_sessions: Iterable[Any] | None = None,
        closures: Iterable[Any] | None = None,
        effective_from: Any = None,
        effective_to: Any = None,
        timezone: str = DEFAULT_TIMEZONE,
        trading_week: Iterable[int] | None = None,
        last_validated: Any = None,
        source: str = "",
        operator_notes: str = "",
    ) -> None:
        # The trading week and the weekend are complements.  When the
        # caller declares ``trading_week`` but not ``weekend_days`` (the
        # governed v2 file schema), the weekend is derived as the
        # complement of the trading week — the data file, not a Python
        # default, is authoritative (Sprint 13.5 governance).  When
        # neither is given, the NEPSE week (Sun–Thu trading, Fri–Sat
        # closed) applies.
        if weekend_days is not None:
            self._weekend = frozenset(int(w) % 7 for w in weekend_days)
        elif trading_week is not None:
            tw = {int(w) % 7 for w in trading_week}
            self._weekend = frozenset(set(range(7)) - tw)
        else:
            self._weekend = frozenset(NEPSE_WEEKEND_DAYS)
        self._holidays = frozenset(
            d for d in (_as_date(h) for h in (holidays or ())) if d is not None
        )
        # Sprint 13.5: special sessions are trading days despite falling
        # on a weekend/holiday; closures are scheduled trading days that
        # are exceptionally closed.  Both are frozensets of dates.
        self._special = frozenset(
            d for d in (_as_date(s) for s in (special_sessions or ())) if d is not None
        )
        self._closures = frozenset(
            d for d in (_as_date(c) for c in (closures or ())) if d is not None
        )
        # Effective coverage window (None = unbounded on that side).
        self.effective_from = _as_date(effective_from)
        self.effective_to = _as_date(effective_to)
        self.timezone = timezone or DEFAULT_TIMEZONE
        self.last_validated = _as_date(last_validated)
        self.source = source
        self.operator_notes = operator_notes
        if trading_week is not None:
            self._trading_week: frozenset[int] | None = frozenset(
                int(w) % 7 for w in trading_week
            )
        else:
            self._trading_week = None
        if observed_sessions is not None:
            self._observed: frozenset[date] | None = frozenset(
                d for d in (_as_date(s) for s in observed_sessions) if d is not None
            )
        else:
            self._observed = None
        # Provenance may be a plain string (v1) or a metadata mapping
        # (v2: ``{source, notes, ...}``).  Keep the human-readable form.
        if isinstance(provenance, Mapping):
            self.provenance = str(provenance.get("notes", provenance.get("source", "")))
            if not self.source:
                self.source = str(provenance.get("source", ""))
        else:
            self.provenance = str(provenance)
        self.version = version

    # ── Queries ───────────────────────────────────────────────────

    @property
    def weekend_days(self) -> tuple[int, ...]:
        return tuple(sorted(self._weekend))

    @property
    def trading_week(self) -> tuple[int, ...]:
        """The scheduled trading weekday numbers (Sun–Thu for NEPSE)."""
        if self._trading_week is not None:
            return tuple(sorted(self._trading_week))
        return NEPSE_TRADING_WEEK

    @property
    def holidays(self) -> tuple[date, ...]:
        return tuple(sorted(self._holidays))

    @property
    def special_sessions(self) -> tuple[date, ...]:
        """Dates that are trading sessions despite weekend/holiday (Sprint 13.5)."""
        return tuple(sorted(self._special))

    @property
    def closures(self) -> tuple[date, ...]:
        """Scheduled trading days that are exceptionally closed (Sprint 13.5)."""
        return tuple(sorted(self._closures))

    @property
    def observed_sessions(self) -> frozenset[date] | None:
        return self._observed

    def is_weekend(self, day: Any) -> bool:
        d = _as_date(day)
        return d is not None and d.weekday() in self._weekend

    def is_holiday(self, day: Any) -> bool:
        d = _as_date(day)
        return d is not None and d in self._holidays

    def is_special_session(self, day: Any) -> bool:
        """True when *day* is a governed special trading session."""
        d = _as_date(day)
        return d is not None and d in self._special

    def is_closure(self, day: Any) -> bool:
        """True when *day* is an exceptional closure of a scheduled day."""
        d = _as_date(day)
        return d is not None and d in self._closures

    def covers(self, day: Any) -> bool:
        """True when *day* falls inside the calendar's effective window.

        A calendar with no ``effective_from``/``effective_to`` covers
        every date.  ``None`` on either side means unbounded on that
        side (Sprint 13.5 provenance: callers can tell whether a date is
        inside the calendar's declared supported range).
        """
        d = _as_date(day)
        if d is None:
            return False
        if self.effective_from is not None and d < self.effective_from:
            return False
        if self.effective_to is not None and d > self.effective_to:
            return False
        return True

    def is_trading_day(self, day: Any) -> bool:
        """True when *day* is a scheduled trading session.

        A scheduled session is a non-weekend, non-holiday weekday that is
        not an exceptional closure, **plus** any governed special session
        (a Saturday/ holiday that the exchange designated as a trading
        day — Sprint 13.5).  ``UNKNOWN`` evidence (no corpus session)
        does not make it a non-trading day — without an authoritative
        holiday entry the engine reports ``UNKNOWN`` rather than
        asserting closure.
        """
        d = _as_date(day)
        if d is None:
            return False
        if self.is_special_session(d):
            return True
        if self.is_closure(d):
            return False
        return not self.is_weekend(d) and not self.is_holiday(d)

    def classify_date(self, day: Any) -> str:
        """Classify a single day.

        Returns one of ``TRADING_DAY / HOLIDAY / WEEKEND / UNKNOWN /
        SPECIAL_SESSION / EXCEPTIONAL_CLOSURE``.

        ``UNKNOWN`` is returned when the day is a scheduled trading day
        with no observed session in the corpus-derived calendar (we
        cannot tell a holiday from missing data) — prefer explicit
        uncertainty over false confidence.  Special sessions and
        exceptional closures are explicit governed entries and are
        reported as such (Sprint 13.5).
        """
        d = _as_date(day)
        if d is None:
            return UNKNOWN
        if self.is_special_session(d):
            return SPECIAL_SESSION
        if self.is_closure(d):
            return EXCEPTIONAL_CLOSURE
        if self.is_weekend(d):
            return WEEKEND
        if self.is_holiday(d):
            return HOLIDAY
        if self._observed is not None and d not in self._observed:
            return UNKNOWN
        return TRADING_DAY

    def previous_trading_day(self, day: Any, inclusive: bool = False) -> date | None:
        """Return the most recent trading day at-or-before *day*."""
        d = _as_date(day)
        if d is None:
            return None
        cur = d if inclusive else d - timedelta(days=1)
        while not self.is_trading_day(cur):
            cur -= timedelta(days=1)
        return cur

    def next_trading_day(self, day: Any, inclusive: bool = False) -> date | None:
        """Return the next trading day at-or-after *day*."""
        d = _as_date(day)
        if d is None:
            return None
        cur = d if inclusive else d + timedelta(days=1)
        while not self.is_trading_day(cur):
            cur += timedelta(days=1)
        return cur

    def expected_sessions(self, start: Any, end: Any) -> list[date]:
        """Return the scheduled trading days in ``[start, end]`` (inclusive).

        Includes governed special sessions and excludes exceptional
        closures (Sprint 13.5).
        """
        s = _as_date(start)
        e = _as_date(end)
        if s is None or e is None or s > e:
            return []
        out: list[date] = []
        cur = s
        while cur <= e:
            if self.is_trading_day(cur):
                out.append(cur)
            cur += timedelta(days=1)
        return out

    # ── Gap classification (Phase 12) ─────────────────────────────

    def classify_sessions(
        self,
        symbol_dates: Iterable[Any],
        start: Any | None = None,
        end: Any | None = None,
    ) -> list[SessionFinding]:
        """Classify every scheduled session in the window against *symbol_dates*.

        Each day in ``[start, end]`` (default: spanned by *symbol_dates*)
        is classified as:

        - ``EXPECTED_NON_TRADING_DAY`` — weekend, holiday, exceptional
          closure, or a weekday with no session in the corpus-derived
          calendar (closure).
        - ``TRADING_DAY`` — the symbol has a record that day.
        - ``MISSING_TRADING_SESSION`` — scheduled trading day with no
          record, where the corpus confirms the market traded.
        - ``UNKNOWN`` — scheduled trading day, no corpus evidence (the
          engine reports uncertainty instead of guessing).

        A governed **special session** (weekend/holiday designated as a
        trading day) with a record classifies as ``TRADING_DAY``; without
        a record it is ``MISSING_TRADING_SESSION`` when the corpus
        confirms the session, else ``UNKNOWN``.  An **exceptional
        closure** classifies as ``EXPECTED_NON_TRADING_DAY`` (Sprint 13.5
        governance).  Never fabricates missing records — classification
        only.

        Note on evidence: a *base* calendar (``observed_sessions=None``)
        cannot distinguish a holiday from missing data, so every
        unobserved weekday classifies as ``UNKNOWN`` — ``missing_sessions``
        therefore reports zero until the calendar carries corpus-derived
        sessions (``NepseCalendar.from_corpus``) or explicit holidays.
        Continuity callers should pass the corpus calendar when they want
        trading-session-accurate gap detection.
        """
        dates = {d for d in (_as_date(x) for x in symbol_dates) if d is not None}
        if start is None and end is None:
            if not dates:
                return []
            start, end = min(dates), max(dates)
        s = _as_date(start)
        e = _as_date(end)
        if s is None or e is None or s > e:
            return []

        findings: list[SessionFinding] = []
        cur = s
        while cur <= e:
            if self.is_special_session(cur):
                # A special session is a trading day even on a
                # weekend/holiday.  With a record → TRADING_DAY; without
                # a record the corpus decides missing-vs-unknown.
                if cur in dates:
                    findings.append(
                        SessionFinding(cur, TRADING_DAY, "observed record on special session")
                    )
                elif self._observed is not None and cur in self._observed:
                    findings.append(
                        SessionFinding(
                            cur,
                            MISSING_TRADING_SESSION,
                            "special session traded; symbol has no record",
                        )
                    )
                elif self._observed is not None:
                    findings.append(
                        SessionFinding(
                            cur, EXPECTED_NON_TRADING_DAY, "special session not observed in corpus"
                        )
                    )
                else:
                    findings.append(
                        SessionFinding(
                            cur,
                            UNKNOWN,
                            "special session; no corpus evidence for the symbol",
                        )
                    )
            elif self.is_closure(cur):
                findings.append(
                    SessionFinding(cur, EXPECTED_NON_TRADING_DAY, "exceptional closure")
                )
            elif self.is_weekend(cur):
                findings.append(
                    SessionFinding(cur, EXPECTED_NON_TRADING_DAY, "weekend closure")
                )
            elif self.is_holiday(cur):
                findings.append(
                    SessionFinding(cur, EXPECTED_NON_TRADING_DAY, "scheduled holiday")
                )
            elif cur in dates:
                findings.append(SessionFinding(cur, TRADING_DAY, "observed record"))
            elif self._observed is not None:
                if cur in self._observed:
                    findings.append(
                        SessionFinding(
                            cur,
                            MISSING_TRADING_SESSION,
                            "corpus confirms market traded; symbol has no record",
                        )
                    )
                else:
                    findings.append(
                        SessionFinding(
                            cur, EXPECTED_NON_TRADING_DAY, "corpus-verified closure"
                        )
                    )
            else:
                findings.append(
                    SessionFinding(
                        cur,
                        UNKNOWN,
                        "no corpus evidence; cannot distinguish holiday from missing data",
                    )
                )
            cur += timedelta(days=1)
        return findings

    def missing_sessions(
        self,
        symbol_dates: Iterable[Any],
        start: Any | None = None,
        end: Any | None = None,
    ) -> list[SessionFinding]:
        """Return only the MISSING_TRADING_SESSION findings in the window."""
        return [
            f
            for f in self.classify_sessions(symbol_dates, start=start, end=end)
            if f.classification == MISSING_TRADING_SESSION
        ]

    # ── Serialization (versioned, auditable) ──────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Versioned, provenance-aware JSON representation (Sprint 13.5).

        The v2 schema adds ``calendar_version``, ``effective_from`` /
        ``effective_to``, ``timezone``, ``trading_week``,
        ``special_sessions``, ``closures``, ``last_validated``,
        ``source`` and ``operator_notes`` alongside the v1 ``version`` /
        ``provenance`` / ``weekend_days`` / ``holidays`` /
        ``observed_sessions`` keys.  ``version`` is kept as an alias of
        ``calendar_version`` for backward compatibility.
        """
        return {
            "version": self.version,
            "calendar_version": self.version,
            "provenance": self.provenance,
            "source": self.source,
            "operator_notes": self.operator_notes,
            "effective_from": self.effective_from.isoformat() if self.effective_from else None,
            "effective_to": self.effective_to.isoformat() if self.effective_to else None,
            "timezone": self.timezone,
            "trading_week": sorted(self.trading_week),
            "weekend_days": sorted(self._weekend),
            "holidays": sorted(d.isoformat() for d in self._holidays),
            "special_sessions": sorted(d.isoformat() for d in self._special),
            "closures": sorted(d.isoformat() for d in self._closures),
            "last_validated": self.last_validated.isoformat() if self.last_validated else None,
            "observed_sessions": (
                sorted(d.isoformat() for d in self._observed) if self._observed is not None else None
            ),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "NepseCalendar":
        """Build a calendar from the versioned data-file representation.

        Accepts both the v1 schema (``version``) and the v2 schema
        (``calendar_version``; ``effective_from/effective_to``,
        ``timezone``, ``trading_week``, ``special_sessions``,
        ``closures``, ``last_validated``, ``source``,
        ``operator_notes``).  ``calendar_version`` wins when both keys
        are present (a v2 file must be loaded by its own version).
        """
        version = data.get("calendar_version") or data.get("version") or DEFAULT_CALENDAR_VERSION
        # ``weekend_days`` is passed through untouched (None when absent)
        # so the constructor can derive the weekend from ``trading_week``
        # when the file declares only the trading week (Sprint 13.5
        # governed schema — the file is authoritative).
        # Dates are extracted from BOTH flat ISO strings (v1) and dict
        # entries with a ``date`` key (v2, optional name/reason/source
        # metadata) via ``_extract_dates``.  Sprint 13.9: an operator
        # holiday candidate in the documented dict form is validated and
        # accepted by ``update_calendar``, so activation must not
        # silently drop it — pass the extracted dates (never the raw
        # dicts) into the constructor.
        holidays, _ = _extract_dates(data.get("holidays"))
        special_sessions, _ = _extract_dates(data.get("special_sessions"))
        closures, _ = _extract_dates(data.get("closures"))
        return cls(
            holidays=holidays,
            weekend_days=data.get("weekend_days"),
            observed_sessions=data.get("observed_sessions"),
            provenance=data.get("provenance") or DEFAULT_CALENDAR_PROVENANCE,
            version=str(version),
            special_sessions=special_sessions,
            closures=closures,
            effective_from=data.get("effective_from"),
            effective_to=data.get("effective_to"),
            timezone=data.get("timezone") or DEFAULT_TIMEZONE,
            trading_week=data.get("trading_week"),
            last_validated=data.get("last_validated"),
            source=data.get("source") or "",
            operator_notes=data.get("operator_notes") or "",
        )

    def save(self, path: str | Path | None = None) -> Path:
        """Persist the versioned calendar to JSON (atomic, fsync-backed)."""
        from src.utils.json_store import save_json  # noqa: PLC0415 - lazy

        target = Path(path) if path else Path(NEPSE_CALENDAR_FILE)
        # fsync=True: the governed calendar is critical, low-frequency
        # state — durability before the atomic replace is worth the
        # cost here (Sprint 13.9 §14).
        save_json(target, self.to_dict(), log_name="NepseCalendar", fsync=True)
        return target

    @classmethod
    def load(cls, path: str | Path | None = None) -> "NepseCalendar | None":
        """Load a persisted calendar; ``None`` when absent or corrupt.

        Sprint 13.5 governance: the file is **validated** before use
        (``validate_calendar_data``).  A malformed / unsupported /
        contradictory calendar is rejected with logged diagnostics and
        ``None`` is returned so callers fall back to the base weekend
        rule — a broken calendar file can never silently turn an
        exchange closure into a normal trading day.
        """
        from src.utils.json_store import load_json  # noqa: PLC0415 - lazy

        target = Path(path) if path else Path(NEPSE_CALENDAR_FILE)
        data = load_json(target, None, log_name="NepseCalendar")
        if not isinstance(data, dict):
            return None
        problems = validate_calendar_data(data)
        if problems:
            logger.warning(
                "[NepseCalendar] Rejecting calendar %s (%d problem(s)): %s",
                target,
                len(problems),
                "; ".join(problems[:5]),
            )
            return None
        try:
            return cls.from_dict(data)
        except Exception as exc:  # noqa: BLE001 - a corrupt calendar must not crash callers
            logger.warning("[NepseCalendar] Cannot parse calendar %s: %s", target, exc)
            return None

    def to_status(self) -> dict[str, Any]:
        """Bounded calendar status/health representation (Sprint 13.5).

        Exposes exactly the metadata needed to answer the provenance
        questions — which version, what coverage, when last validated,
        what source, is a date covered, are counts bounded — without
        dumping raw holiday/session lists (safe for ``/metrics``).
        """
        return {
            "version": self.version,
            "timezone": self.timezone,
            "effective_from": self.effective_from.isoformat() if self.effective_from else None,
            "effective_to": self.effective_to.isoformat() if self.effective_to else None,
            "last_validated": self.last_validated.isoformat() if self.last_validated else None,
            "source": self.source,
            "trading_week": sorted(self.trading_week),
            "weekend_days": sorted(self._weekend),
            "holidays": len(self._holidays),
            "special_sessions": len(self._special),
            "closures": len(self._closures),
            "observed_sessions": (
                len(self._observed) if self._observed is not None else None
            ),
            "has_provenance": bool(self.provenance or self.source),
        }

    def coverage_for(self, start: Any, end: Any) -> dict[str, Any]:
        """Bounded coverage summary for the range ``[start, end]``.

        Tells consumers whether the requested range is fully inside the
        calendar's effective window and how many scheduled sessions it
        spans (counts only — never raw dates).  Returns ``{}`` for an
        invalid range.
        """
        s = _as_date(start)
        e = _as_date(end)
        if s is None or e is None or s > e:
            return {}
        fully_inside = self.covers(s) and self.covers(e)
        sessions = self.expected_sessions(s, e)
        return {
            "start": s.isoformat(),
            "end": e.isoformat(),
            "fully_inside_effective_window": fully_inside,
            "scheduled_sessions": len(sessions),
            "covered": fully_inside,
        }

    @classmethod
    def from_corpus(
        cls,
        data_dir: str | Path | None = None,
        *,
        holidays: Iterable[Any] | None = None,
        provenance: str | Mapping[str, Any] | None = None,
        version: str = DEFAULT_CALENDAR_VERSION,
        # ── Sprint 13.5 governed-calendar pass-throughs ──
        special_sessions: Iterable[Any] | None = None,
        closures: Iterable[Any] | None = None,
        effective_from: Any = None,
        effective_to: Any = None,
        timezone: str = DEFAULT_TIMEZONE,
        last_validated: Any = None,
        source: str = "",
        operator_notes: str = "",
    ) -> "NepseCalendar":
        """Derive observed trading sessions from the validated corpus.

        The union of all dates across every CSV under *data_dir* is the
        strongest available evidence for what NEPSE actually traded
        (Phase 11 preference #3: existing validated historical sessions).
        A weekday absent from the union is then classified as a closure
        instead of a guess.
        """
        from src.loaders.csv_loader import load_csv  # noqa: PLC0415 - lazy

        base = Path(data_dir) if data_dir else Path(DATA_DIRECTORY)
        observed: set[date] = set()
        for f in sorted(base.glob("*.csv")):
            if f.stem.lower() == "sample":
                continue
            try:
                df = load_csv(f)
                if df is None or df.empty or "Date" not in df.columns:
                    continue
                for raw in pd_to_dates(df["Date"]):
                    observed.add(raw)
            except Exception:  # noqa: BLE001 - one broken file must not abort derivation
                continue
        if provenance is None:
            provenance = (
                "Observed sessions derived from the validated corpus at "
                f"{base} (NEPSE week: Sun–Thu; Fri–Sat closed)."
            )
        return cls(
            holidays=holidays,
            observed_sessions=observed,
            provenance=provenance,
            version=version,
            special_sessions=special_sessions,
            closures=closures,
            effective_from=effective_from,
            effective_to=effective_to,
            timezone=timezone,
            last_validated=last_validated,
            source=source,
            operator_notes=operator_notes,
        )


def pd_to_dates(series: Any) -> Iterable[date]:
    """Yield ``datetime.date`` values from a pandas date Series."""
    import pandas as pd  # noqa: PLC0415 - lazy

    for ts in pd.to_datetime(series, errors="coerce").dropna():
        yield ts.date()


# Module-level defaults: the base calendar (weekend rule + operator
# holidays, no corpus evidence) and a corpus-derived instance built
# lazily on first use so tests never trigger a full corpus read.
_default_calendar: NepseCalendar | None = None
_corpus_calendar: NepseCalendar | None = None


def default_calendar() -> NepseCalendar:
    """Return the shared base calendar (weekend rule + persisted holidays)."""
    global _default_calendar
    if _default_calendar is None:
        persisted = NepseCalendar.load()
        _default_calendar = persisted or NepseCalendar()
    return _default_calendar


def corpus_calendar(data_dir: str | Path | None = None) -> NepseCalendar:
    """Return the shared corpus-derived calendar (lazily built once).

    Tests that need a controlled calendar should construct their own
    ``NepseCalendar`` directly; this is the production default.
    """
    global _corpus_calendar
    if _corpus_calendar is None:
        _corpus_calendar = NepseCalendar.from_corpus(data_dir)
    return _corpus_calendar


def reset_calendar_cache() -> None:
    """Drop cached module-level calendars (used by tests for isolation)."""
    global _default_calendar, _corpus_calendar
    _default_calendar = None
    _corpus_calendar = None


def is_nepse_trading_day(day: Any) -> bool:
    """Convenience: is *day* a scheduled NEPSE trading day (base calendar)?"""
    return default_calendar().is_trading_day(day)
