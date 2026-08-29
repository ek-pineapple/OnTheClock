"""Weekly rest period evaluation.

Separate from the daily rest check because it measures a different gap: not
between two consecutive shifts, but between the *last dismissal of one
workweek* and the *first call of the next*.

Base entitlement is 56 hours, with two exceptions that lower the bar:

* 54 hours, if the new workweek's first call is no earlier than 6am.
* 36 hours, following a six-day location workweek.

A breach is a forced call and carries the same penalty as a daily-rest
breach, so a production can be perfectly compliant day-to-day and still owe
a full day's pay at the week boundary. That is exactly the kind of thing a
2nd AD tracking individual clocks would not catch.

The 6am threshold is the one rule in the system stated in *local* terms, so
this module converts to the production's timezone before comparing. Storing
UTC and comparing UTC here would silently shift the boundary by the offset.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from ..models import (
    CallGroup,
    ComplianceCheck,
    ComplianceState,
    Person,
    Production,
    Shift,
    ViolationType,
    Zone,
)
from ..rulebook import RuleBook
from .config import DEFAULT_CONFIG, EvaluationConfig

Subject = Person | CallGroup

_LOCATION_ZONES = (Zone.DISTANT, Zone.OVERNIGHT)


def workweek_start(day: date, start_weekday: int) -> date:
    """The date the workweek containing `day` began."""
    return day - timedelta(days=(day.weekday() - start_weekday) % 7)


def _is_first_shift_of_workweek(
    shifts: Sequence[Shift], index: int, start_weekday: int
) -> bool:
    if index == 0:
        return False
    this_week = workweek_start(shifts[index].date, start_weekday)
    prev_week = workweek_start(shifts[index - 1].date, start_weekday)
    return this_week != prev_week


def _previous_workweek_shifts(
    shifts: Sequence[Shift], index: int, start_weekday: int
) -> list[Shift]:
    prev_week = workweek_start(shifts[index - 1].date, start_weekday)
    return [
        s
        for s in shifts[:index]
        if workweek_start(s.date, start_weekday) == prev_week
    ]


def check_weekly_rest(
    subject: Subject,
    production: Production,
    shifts: Sequence[Shift],
    index: int,
    now: datetime,
    rulebook: RuleBook,
    config: EvaluationConfig = DEFAULT_CONFIG,
) -> ComplianceCheck:
    """Evaluate the weekly rest period preceding `shifts[index]`.

    Returns CLEAR when `shifts[index]` is not the first shift of a workweek —
    the rule simply does not apply mid-week.
    """
    rule = rulebook.weekly_rest
    upcoming = shifts[index]
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

    if not _is_first_shift_of_workweek(shifts, index, production.workweek_start_weekday):
        return _check(
            ComplianceState.CLEAR, None, None,
            "Not a workweek boundary — weekly rest does not apply.",
        )

    previous = shifts[index - 1]
    if previous.dismissed_at is None:
        return _check(
            ComplianceState.CLEAR, None, None,
            "Previous workweek not yet wrapped — weekly rest not measurable.",
        )

    required, reason = _required_weekly_rest(production, shifts, index, rulebook)
    earliest_legal_call = previous.dismissed_at + required
    margin = upcoming.scheduled_call_at - earliest_legal_call

    if margin >= timedelta(0):
        return _check(
            ComplianceState.CLEAR, earliest_legal_call, None,
            (
                f"Weekly rest satisfied. {reason}. Last dismissal "
                f"{previous.dismissed_at:%a %H:%M UTC}, new workweek called "
                f"{upcoming.scheduled_call_at:%a %H:%M UTC} "
                f"({_fmt(margin)} of margin)."
            ),
        )

    shortfall = -margin

    if now < upcoming.scheduled_call_at:
        return _check(
            ComplianceState.APPROACHING,
            upcoming.scheduled_call_at,
            shortfall,
            (
                f"Weekly rest short by {_fmt(shortfall)} — still fixable. "
                f"{reason}. Last dismissal {previous.dismissed_at:%a %H:%M UTC} "
                f"means the earliest legal call is "
                f"{earliest_legal_call:%a %H:%M UTC}, but the new workweek is "
                f"called for {upcoming.scheduled_call_at:%a %H:%M UTC}."
            ),
        )

    return _check(
        ComplianceState.VIOLATED,
        upcoming.scheduled_call_at,
        shortfall,
        (
            f"Weekly rest violation (forced call). {reason}. Rest was short "
            f"by {_fmt(shortfall)} between "
            f"{previous.dismissed_at:%a %H:%M UTC} and "
            f"{upcoming.scheduled_call_at:%a %H:%M UTC}."
        ),
    )


def _required_weekly_rest(
    production: Production,
    shifts: Sequence[Shift],
    index: int,
    rulebook: RuleBook,
) -> tuple[timedelta, str]:
    """Resolve the weekly entitlement, applying whichever exception qualifies.

    Both exceptions lower the requirement, so when more than one applies the
    lowest governs.
    """
    rule = rulebook.weekly_rest
    upcoming = shifts[index]

    options: list[tuple[float, str]] = [
        (float(rule.base_hours), f"{rule.base_hours}h weekly rest required")
    ]

    # Exception 1 — first call of the new workweek no earlier than 6am LOCAL.
    local_call = upcoming.scheduled_call_at.astimezone(ZoneInfo(production.timezone))
    if local_call.hour >= rule.early_call_no_earlier_than_hour:
        options.append(
            (
                float(rule.reduced_hours_early_call_exception),
                (
                    f"{rule.reduced_hours_early_call_exception}h required "
                    f"(reduced from {rule.base_hours}h — new workweek called "
                    f"{local_call:%H:%M} local, no earlier than "
                    f"{rule.early_call_no_earlier_than_hour}:00)"
                ),
            )
        )

    # Exception 2 — following a six-day location workweek.
    prev_shifts = _previous_workweek_shifts(
        shifts, index, production.workweek_start_weekday
    )
    on_location = [s for s in prev_shifts if s.zone in _LOCATION_ZONES]
    if (
        len(prev_shifts) >= rule.six_day_workweek_shift_count
        and len(on_location) >= rule.six_day_workweek_shift_count
    ):
        options.append(
            (
                float(rule.reduced_hours_six_day_location),
                (
                    f"{rule.reduced_hours_six_day_location}h required "
                    f"(reduced from {rule.base_hours}h — "
                    f"{len(prev_shifts)}-day location workweek)"
                ),
            )
        )

    hours, reason = min(options, key=lambda o: o[0])
    return timedelta(hours=hours), reason


def _fmt(delta: timedelta) -> str:
    total = int(abs(delta).total_seconds())
    hours, remainder = divmod(total, 3600)
    minutes = remainder // 60
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"
