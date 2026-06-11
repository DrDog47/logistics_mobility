"""Driver document requirements ruleset + completeness check (PRD §11–12).

Three layers (§12.1): the *facts* (a driver's documents) stay in the DB; the
*type catalogue* stays in the DB; the *rules* — which types are required, under
which conditions, and the expiry thresholds — live in a git-versioned YAML
ruleset loaded into an immutable registry at startup (mirrors ``RateRegistry`` /
Polish params). A malformed file refuses boot.

``check_completeness`` is a pure function (no DB access) so it can be registered
as a Jinja global and drive the summary straight from the driver card, exactly
like :func:`app.documents.status.document_status`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

from app.docs.status import EXPIRED, NO_DATE, OK, SOON, URGENT

# Requirement modes (§12.4).
REQUIRED = "required"
CONDITIONAL = "conditional"   # v1: no predicate data yet → treated as required-for-all
PRESENT_ONLY = "present_only"
_MODES = frozenset({REQUIRED, CONDITIONAL, PRESENT_ONLY})

# Levels that mean "present and fine" vs "present but needs attention".
_OK_LEVELS = frozenset({OK, NO_DATE})


class RequirementsError(RuntimeError):
    """Raised when the requirements YAML is missing or malformed (refuses boot)."""


@dataclass(frozen=True)
class RequirementRule:
    type: str
    mode: str
    thresholds: tuple[int, ...]
    satisfied_by: tuple[str, ...] = ()
    label: str | None = None

    @property
    def display_label(self) -> str:
        return self.label or self.type.replace("_", " ")

    @property
    def accepts(self) -> tuple[str, ...]:
        """Document types that satisfy this requirement (itself + alternatives)."""
        return (self.type, *self.satisfied_by)

    @property
    def must_have(self) -> bool:
        """Whether absence counts as 'missing' (required / conditional, not present_only)."""
        return self.mode in (REQUIRED, CONDITIONAL)


@dataclass(frozen=True)
class RequirementItem:
    """One satisfied requirement: the controlling document and its expiry status."""

    rule: RequirementRule
    document: object
    level: str
    days_left: int | None


@dataclass(frozen=True)
class CompletenessReport:
    ok: list[RequirementItem] = field(default_factory=list)
    expiring: list[RequirementItem] = field(default_factory=list)
    missing: list[RequirementRule] = field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        return not self.missing and not self.expiring


@dataclass(frozen=True)
class RequirementsRuleset:
    version: int
    rules: tuple[RequirementRule, ...]

    @classmethod
    def load(cls, path: str | Path) -> RequirementsRuleset:
        path = Path(path)
        if not path.exists():
            raise RequirementsError(f"Requirements file does not exist: {path}")
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise RequirementsError(f"YAML parse error: {exc}") from exc
        if not isinstance(raw, dict):
            raise RequirementsError("Root must be a mapping")

        rules_raw = raw.get("rules")
        if not isinstance(rules_raw, list) or not rules_raw:
            raise RequirementsError("`rules` must be a non-empty list")

        rules: list[RequirementRule] = []
        seen: set[str] = set()
        for i, r in enumerate(rules_raw):
            if not isinstance(r, dict):
                raise RequirementsError(f"rule #{i} must be a mapping")
            t = r.get("type")
            if not isinstance(t, str) or not t:
                raise RequirementsError(f"rule #{i}: `type` is required")
            if t in seen:
                raise RequirementsError(f"duplicate rule type: {t!r}")
            seen.add(t)

            mode = r.get("mode", REQUIRED)
            if mode not in _MODES:
                raise RequirementsError(
                    f"rule {t!r}: invalid mode {mode!r} (one of {sorted(_MODES)})"
                )
            thr = r.get("thresholds", [120, 60])
            if (
                not isinstance(thr, list)
                or not thr
                or not all(isinstance(x, int) and x >= 0 for x in thr)
            ):
                raise RequirementsError(
                    f"rule {t!r}: thresholds must be a non-empty list of non-negative ints"
                )
            sb = r.get("satisfied_by", [])
            if not isinstance(sb, list) or not all(isinstance(x, str) for x in sb):
                raise RequirementsError(
                    f"rule {t!r}: satisfied_by must be a list of type codes"
                )
            label = r.get("label")
            if label is not None and not isinstance(label, str):
                raise RequirementsError(f"rule {t!r}: label must be a string")

            rules.append(
                RequirementRule(
                    type=t,
                    mode=mode,
                    thresholds=tuple(sorted(thr, reverse=True)),
                    satisfied_by=tuple(sb),
                    label=label,
                )
            )

        version = raw.get("version", 1)
        if not isinstance(version, int):
            raise RequirementsError("`version` must be an integer")
        return cls(version=version, rules=tuple(rules))


def _expiry_level(
    end_date: date | None, thresholds: tuple[int, ...], today: date
) -> tuple[str, int | None]:
    """Classify a document by its ``end_date`` using this rule's thresholds.

    Mirrors :func:`app.documents.status.document_status` (§6) but driven by the
    ruleset (§12.3). ``thresholds`` is sorted most-distant-first, e.g. (120, 60):
    ``<=60`` → urgent, ``<=120`` → soon, beyond → ok.
    """
    if end_date is None:
        return NO_DATE, None
    days = (end_date - today).days
    if days < 0:
        return EXPIRED, days
    urgent = thresholds[-1]   # smallest = most urgent
    soon = thresholds[0]      # largest = first warning
    if days <= urgent:
        return URGENT, days
    if days <= soon:
        return SOON, days
    return OK, days


def check_completeness(
    documents,
    ruleset: RequirementsRuleset,
    today: date | None = None,
) -> CompletenessReport:
    """Pure (driver's documents, ruleset) → {in order | expiring soon | missing}.

    ``documents`` should be the driver's *active* documents (non-deleted,
    non-archived). Each rule is satisfied by a document of its ``type`` or any
    ``satisfied_by`` alternative; the controlling document is the one expiring
    latest. ``present_only`` rules are never reported as missing (§12.4).
    """
    today = today or date.today()

    by_type: dict[str, list] = {}
    for d in documents:
        if getattr(d, "is_deleted", False) or getattr(d, "archived_at", None):
            continue
        by_type.setdefault(d.document_type, []).append(d)

    report = CompletenessReport(ok=[], expiring=[], missing=[])
    for rule in ruleset.rules:
        docs = [d for code in rule.accepts for d in by_type.get(code, [])]
        if not docs:
            if rule.must_have:
                report.missing.append(rule)
            continue
        # Controlling document = the one valid the longest (latest end_date;
        # open-ended docs — no end_date — count as the most current).
        controlling = max(
            docs, key=lambda d: (d.end_date is None, d.end_date or date.min)
        )
        level, days = _expiry_level(controlling.end_date, rule.thresholds, today)
        item = RequirementItem(rule=rule, document=controlling, level=level, days_left=days)
        (report.ok if level in _OK_LEVELS else report.expiring).append(item)

    report.expiring.sort(
        key=lambda it: it.days_left if it.days_left is not None else 1 << 30
    )
    return report


# --- App-factory wiring (mirrors app.rates.services) --------------------------


def init_requirements(app) -> None:
    """Load the driver + vehicle requirements YAML at startup; malformed → refuse boot."""
    for kind, config_key in (
        ("driver", "DRIVER_REQUIREMENTS_FILE"),
        ("vehicle", "VEHICLE_REQUIREMENTS_FILE"),
    ):
        path = app.config[config_key]
        ruleset = RequirementsRuleset.load(path)
        app.extensions[f"{kind}_requirements"] = ruleset
        app.logger.info(
            "%s requirements loaded: %d rules from %s",
            kind.capitalize(),
            len(ruleset.rules),
            path,
        )


def _get_ruleset(kind: str) -> RequirementsRuleset:
    from flask import current_app

    ruleset = current_app.extensions.get(f"{kind}_requirements")
    if ruleset is None:
        raise RuntimeError(
            f"{kind.capitalize()} requirements not initialized. "
            "Did you call init_requirements()?"
        )
    return ruleset


def get_ruleset() -> RequirementsRuleset:
    return _get_ruleset("driver")


def get_vehicle_ruleset() -> RequirementsRuleset:
    return _get_ruleset("vehicle")


def driver_completeness(driver, today: date | None = None) -> CompletenessReport:
    """Jinja-global entry point: completeness report for one driver (§12.5)."""
    return check_completeness(driver.active_documents, get_ruleset(), today)


def vehicle_completeness(vehicle, today: date | None = None) -> CompletenessReport:
    """Jinja-global entry point: completeness report for one vehicle (vehicle PRD §12)."""
    return check_completeness(vehicle.active_documents, get_vehicle_ruleset(), today)
