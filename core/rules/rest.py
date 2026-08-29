"""Rest period (forced call) evaluation.

A forced call behaves unlike a meal penalty, and the difference drives the
whole design of this module:

* A meal penalty is a live countdown against someone currently working.
* A forced call is **determined the moment the call sheet is published** —
  potentially twelve hours before it happens — and stays wrong until a human
  edits the schedule.

So `APPROACHING` here does not mean "about to go wrong". It means *"the
schedule is already non-compliant, and there is still time to fix it."* The
countdown is time-left-to-act, not time-until-harm. Once the call time
passes unchanged, it becomes `VIOLATED` and the money is owed.

There is a second, subtler case this module handles: a performer still on
the clock whose next call is already scheduled. Their dismissal time is not
yet known, but the *latest* dismissal that keeps the schedule legal is
computable right now. That produces the most actionable alert the product
can make — "wrap them within 40 minutes or tomorrow's call is a forced
call" — hours before any violation exists.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from ..models import (
    CallGroup,
    ComplianceCheck,
    ComplianceState,
    Person,
    Production,
    Shift,
    ViolationType,
)
from ..rulebook import (
    ReductionCondition,
    ReductionLimit,
    RestRule,
    RuleBook,
)
from .config import DEFAULT_CONFIG, EvaluationConfig


class UnsupportedRuleError(Exception):
    """A rulebook condition has no predicate implementing it.

    Raised rather than skipped. Silently ignoring an unevaluated condition
    would mean reporting "all clear" on a check that never ran — which, for
    a compliance tool, is worse than crashing. Callers (the monitor service)
    must surface this as an error state, never swallow it.

    This matters most for the uploaded-agreement feature: a contract naming
    a condition we cannot evaluate must be visibly rejected, not quietly
    treated as compliant.
    """


Subject = Person | CallGroup


# --------------------------------------------------------------------------
# Reduction predicates
#
# Each machine-readable key in the rulebook gets exactly one predicate here.
# The rulebook says *what* is required; these say *how to check it*. No
# threshold values appear in this file.
# --------------------------------------------------------------------------


def _condition_none(shifts: Sequence[Shift], index: int) -> bool:
    """No precondition beyond the usage limit."""
    return True


def _condition_exterior_photography_adjacent(
    shifts: Sequence[Shift], index: int
) -> bool:
    """Exterior photography required on the day before *and* the day after.

    The reduced rest period sits between the previous shift's dismissal and
    this shift's call, so "day before" is `shifts[index - 1]` and "day after"
    is `shifts[index]`. Both must qualify — project notes previously recorded
    only the usage limit and missed this condition entirely.
    """
    if index < 1:
        return False
    return shifts[index - 1].exterior_photography and shifts[index].exterior_photography


_CONDITION_PREDICATES = {
    ReductionCondition.NONE: _condition_none,
    ReductionCondition.EXTERIOR_PHOTOGRAPHY_ADJACENT_DAYS: (
        _condition_exterior_photography_adjacent
    ),
}


def _reduction_uses_before(
    shifts: Sequence[Shift], index: int, base_hours: float
) -> list[int]:
    """Indices of earlier shifts whose rest gap fell below the base entitlement.

    Whether a reduction was "used" is derived from the schedule rather than
    stored on a shift. A stored flag would drift the moment any timestamp was
    corrected; the gap itself is the ground truth.
    """
    uses: list[int] = []
    for i in range(1, index):
        prev, curr = shifts[i - 1], shifts[i]
        if prev.dismissed_at is None:
            continue
        gap_hours = (
            curr.scheduled_call_at - prev.dismissed_at
        ).total_seconds() / 3600.0
        if gap_hours < base_hours:
            uses.append(i)
    return uses


def _limit_once_per_four_consecutive_days(
    shifts: Sequence[Shift], index: int, base_hours: float
) -> bool:
    """Permitted only if no reduction was used in the preceding three days.

    Simplification: "consecutive day" is taken to mean consecutive entries in
    this subject's shift sequence. A subject with a day off mid-sequence will
    therefore be treated slightly conservatively (the gap counts as a day).
    Erring toward *more* rest is the safe direction for a compliance tool.
    """
    uses = _reduction_uses_before(shifts, index, base_hours)
    return not any(index - used < 4 for used in uses)


def _limit_two_non_consecutive_days_per_workweek(
    shifts: Sequence[Shift], index: int, base_hours: float
) -> bool:
    """At most two uses per workweek, and never on back-to-back days.

    Simplification: the workweek is a rolling seven-entry window ending at
    `index`, rather than a calendar workweek anchored to the production's
    week start. Refining this needs a workweek boundary on `Production`,
    which the demo data does not yet carry.
    """
    window_start = max(0, index - 7)
    uses = [u for u in _reduction_uses_before(shifts, index, base_hours) if u >= window_start]
    if len(uses) >= 2:
        return False
    # "Non-consecutive" — the immediately preceding day cannot also be a use.
    return not any(index - used <= 1 for used in uses)


_LIMIT_PREDICATES = {
    ReductionLimit.ONCE_PER_FOUR_CONSECUTIVE_DAYS: _limit_once_per_four_consecutive_days,
    ReductionLimit.TWO_NON_CONSECUTIVE_DAYS_PER_WORKWEEK: (
        _limit_two_non_consecutive_days_per_workweek
    ),
}


def _required_rest(
    rule: RestRule, shifts: Sequence[Shift], index: int
) -> tuple[timedelta, str]:
    """Resolve the rest entitlement, applying a reduction only if permitted.

    Returns the requirement and a human-readable reason, so the agent can
    explain *why* a given number of hours applied rather than asserting it.
    """
    base = timedelta(hours=float(rule.base_hours))
    if rule.reduction is None:
        return base, f"{rule.base_hours}h required, no reduction available"

    condition = _CONDITION_PREDICATES.get(rule.reduction.condition)
    if condition is None:
        raise UnsupportedRuleError(
            f"No predicate implements reduction condition "
            f"{rule.reduction.condition!r}. Refusing to evaluate rather than "
            f"report a possibly-false all-clear."
        )

    limit = _LIMIT_PREDICATES.get(rule.reduction.limit)
    if limit is None:
        raise UnsupportedRuleError(
            f"No predicate implements reduction limit {rule.reduction.limit!r}. "
            f"Refusing to evaluate rather than report a possibly-false all-clear."
        )

    if not condition(shifts, index):
        return base, (
            f"{rule.base_hours}h required — reduction to "
            f"{rule.reduction.reduced_to_hours}h not available "
            f"(condition not met: {rule.reduction.condition.value})"
        )

    if not limit(shifts, index, float(rule.base_hours)):
        return base, (
            f"{rule.base_hours}h required — reduction to "
            f"{rule.reduction.reduced_to_hours}h already used "
            f"(limit: {rule.reduction.limit.value})"
        )

    return timedelta(hours=float(rule.reduction.reduced_to_hours)), (
        f"{rule.reduction.reduced_to_hours}h required "
        f"(reduced from {rule.base_hours}h — {rule.reduction.description})"
    )


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def check_rest_period(
    subject: Subject,
    production: Production,
    shifts: Sequence[Shift],
    index: int,
    now: datetime,
    rulebook: RuleBook,
    config: EvaluationConfig = DEFAULT_CONFIG,
) -> ComplianceCheck:
    """Evaluate the rest period preceding `shifts[index]`.

    `shifts` must be this subject's shifts in chronological order. History is
    required, not just the adjacent pair, because the reduction limits
    ("once every fourth consecutive day", "two non-consecutive days per
    workweek") can only be evaluated by looking back.

    `now` is passed in rather than read from the clock so that evaluation is
    deterministic and testable. Nothing in this package calls `datetime.now()`.
    """
    upcoming = shifts[index]
    rule = rulebook.rest_rule(
        subject.performer_category, upcoming.zone, production.production_type
    )
    penalty_rule = rulebook.forced_call_penalty(subject.contract_type)
    penalty = penalty_rule.amount_for(subject.one_days_pay_usd)

    def _check(
        state: ComplianceState,
        deadline_at: datetime | None,
        shortfall: timedelta | None,
        explanation: str,
    ) -> ComplianceCheck:
        return ComplianceCheck(
            subject_type=upcoming.subject_type,
            subject_id=upcoming.subject_id,
            production_id=upcoming.production_id,
            violation_type=ViolationType.FORCED_CALL,
            state=state,
            deadline_at=deadline_at,
            margin_seconds=(
                (deadline_at - now).total_seconds() if deadline_at else None
            ),
            shortfall_seconds=shortfall.total_seconds() if shortfall else None,
            per_capita_penalty_usd=(
                penalty if state is not ComplianceState.CLEAR else penalty * 0
            ),
            headcount=upcoming.subject_headcount,
            rule_version=rule.rule_version,
            source_url=rule.source_url,
            rule_confidence=rule.confidence.value,
            explanation=explanation,
        )

    # First shift of the engagement — no prior dismissal to measure against.
    if index == 0:
        return _check(
            ComplianceState.CLEAR, None, None,
            "First shift — no preceding dismissal to measure rest against.",
        )

    previous = shifts[index - 1]
    required, reason = _required_rest(rule, shifts, index)

    # --- Case 1: still on the clock -------------------------------------
    # Dismissal has not happened, so the rest period has not started. The
    # violation does not exist yet and is entirely preventable — but the
    # deadline for preventing it is computable right now.
    if previous.dismissed_at is None:
        latest_legal_dismissal = upcoming.scheduled_call_at - required
        remaining = latest_legal_dismissal - now

        if remaining <= timedelta(0):
            overdue = -remaining
            return _check(
                ComplianceState.VIOLATED,
                latest_legal_dismissal,
                overdue,
                (
                    f"Still on the clock past the latest legal dismissal. "
                    f"{reason}. Next call {upcoming.scheduled_call_at:%H:%M UTC}; "
                    f"dismissal is already {_fmt(overdue)} late, so the call is "
                    f"now a forced call unless the schedule moves."
                ),
            )

        if remaining <= config.dismissal_warning_lead:
            return _check(
                ComplianceState.APPROACHING,
                latest_legal_dismissal,
                None,
                (
                    f"Must be dismissed within {_fmt(remaining)} to protect the "
                    f"{upcoming.scheduled_call_at:%H:%M UTC} call. {reason}."
                ),
            )

        return _check(
            ComplianceState.CLEAR, latest_legal_dismissal, None,
            (
                f"On the clock. {reason}. Latest legal dismissal "
                f"{latest_legal_dismissal:%H:%M UTC} ({_fmt(remaining)} away)."
            ),
        )

    # --- Case 2: dismissed — the schedule can be judged outright ---------
    earliest_legal_call = previous.dismissed_at + required
    margin = upcoming.scheduled_call_at - earliest_legal_call

    if margin >= timedelta(0):
        return _check(
            ComplianceState.CLEAR, earliest_legal_call, None,
            (
                f"Compliant. {reason}. Dismissed "
                f"{previous.dismissed_at:%H:%M UTC}, earliest legal call "
                f"{earliest_legal_call:%H:%M UTC}, scheduled "
                f"{upcoming.scheduled_call_at:%H:%M UTC} "
                f"({_fmt(margin)} of margin)."
            ),
        )

    shortfall = -margin

    # The schedule is non-compliant. Whether it is still fixable depends
    # only on whether the call has happened yet.
    if now < upcoming.scheduled_call_at:
        return _check(
            ComplianceState.APPROACHING,
            upcoming.scheduled_call_at,
            shortfall,
            (
                f"Scheduled call breaches the rest period by {_fmt(shortfall)} — "
                f"still fixable. {reason}. Dismissed "
                f"{previous.dismissed_at:%H:%M UTC}, so the earliest legal call "
                f"is {earliest_legal_call:%H:%M UTC}, but the call is set for "
                f"{upcoming.scheduled_call_at:%H:%M UTC}. Moving the call "
                f"{_fmt(shortfall)} later clears it."
            ),
        )

    return _check(
        ComplianceState.VIOLATED,
        upcoming.scheduled_call_at,
        shortfall,
        (
            f"Forced call. {reason}. Rest was short by {_fmt(shortfall)} — "
            f"dismissed {previous.dismissed_at:%H:%M UTC}, called "
            f"{upcoming.scheduled_call_at:%H:%M UTC} against an earliest legal "
            f"call of {earliest_legal_call:%H:%M UTC}."
        ),
    )


def _fmt(delta: timedelta) -> str:
    """Render a duration the way a 2nd AD would say it."""
    total = int(abs(delta).total_seconds())
    hours, remainder = divmod(total, 3600)
    minutes = remainder // 60
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"
