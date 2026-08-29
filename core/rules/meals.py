"""Meal period evaluation.

Unlike a forced call, this *is* a live countdown: the clock runs against
someone currently working, and every half-hour past the deadline costs more
than the last.

The rolling clock:

* The first meal is due within N hours of call.
* Each subsequent meal is due within N hours of returning from the previous
  one — so the clock resets on `in_at`, not on `out_at`.
* An open break (out, not yet back) stops the clock entirely.
* A non-deductible meal does not reset the clock; it is not a meal period.

Three mechanisms exist to avoid false positives, and all three are currently
flagged `NEEDS_SOURCE_CHECK` in the rulebook — they come from project notes
and could not be verified, since sagaftra.org blocks automated fetching.
They fail in the safe direction: a wrong grace period causes over-alerting,
never a silent miss.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

from ..models import (
    CallGroup,
    ComplianceCheck,
    ComplianceState,
    MealBreak,
    Person,
    Shift,
    ShiftStatus,
    ViolationType,
)
from ..rulebook import RuleBook
from .config import DEFAULT_CONFIG, EvaluationConfig

Subject = Person | CallGroup


def _is_non_deductible(meal: MealBreak, shift: Shift, timing) -> bool:
    """Does this break qualify as a non-deductible meal rather than a meal period?

    An NDB is a short break taken near call time. It is *not* a meal period:
    it does not satisfy the meal requirement and does not reset the six-hour
    clock. `Shift.non_deductible_meal` is the proper place to record one, but
    a set logging it as an ordinary break is entirely plausible — and if we
    accepted that as the first meal, the clock would reset to a later time and
    we would under-alert on a real violation.

    So the check is defensive rather than trusting: any break that *looks*
    like an NDB (short, and close to call) is excluded from clock resets. It
    errs toward earlier deadlines, which is the safe direction.
    """
    if not meal.is_complete:
        return False
    within_call_window = meal.out_at - shift.effective_call_at <= timedelta(
        hours=float(timing.ndb_within_hours_of_call)
    )
    is_short = (meal.in_at - meal.out_at) <= timedelta(  # type: ignore[operator]
        minutes=timing.ndb_duration_minutes
    )
    return within_call_window and is_short


def _meal_periods(shift: Shift, timing) -> list[MealBreak]:
    """Breaks that count as meal periods, NDB-like ones excluded."""
    return [m for m in shift.meals if not _is_non_deductible(m, shift, timing)]


def _last_completed_meal(shift: Shift, timing) -> MealBreak | None:
    completed = [m for m in _meal_periods(shift, timing) if m.is_complete]
    return max(completed, key=lambda m: m.in_at) if completed else None  # type: ignore[arg-type]


def _open_meal(shift: Shift) -> MealBreak | None:
    for meal in shift.meals:
        if not meal.is_complete:
            return meal
    return None


def check_meal_period(
    subject: Subject,
    shift: Shift,
    now: datetime,
    rulebook: RuleBook,
    config: EvaluationConfig = DEFAULT_CONFIG,
) -> ComplianceCheck:
    """Evaluate the meal clock currently running against `shift`.

    `now` is passed in rather than read from the clock, so evaluation is
    deterministic and testable.
    """
    timing = rulebook.meal_timing
    schedule = rulebook.meal_penalty_schedule(subject.performer_category)

    def _check(
        state: ComplianceState,
        deadline_at: datetime | None,
        increments: int,
        explanation: str,
    ) -> ComplianceCheck:
        return ComplianceCheck(
            subject_type=shift.subject_type,
            subject_id=shift.subject_id,
            production_id=shift.production_id,
            violation_type=ViolationType.MEAL_PENALTY,
            state=state,
            deadline_at=deadline_at,
            margin_seconds=(
                (deadline_at - now).total_seconds() if deadline_at else None
            ),
            half_hour_increments=increments,
            # Cumulative across the step schedule — NOT increments x flat rate.
            per_capita_penalty_usd=schedule.cumulative_for(increments),
            headcount=shift.subject_headcount,
            rule_version=timing.rule_version,
            source_url=timing.source_url,
            rule_confidence=timing.confidence.value,
            explanation=explanation,
        )

    if shift.status is ShiftStatus.WRAPPED:
        return _check(
            ComplianceState.CLEAR, None, 0,
            "Wrapped — no meal clock running.",
        )

    # An open break stops the clock. Nobody owes a meal penalty while
    # actually eating.
    open_meal = _open_meal(shift)
    if open_meal is not None:
        return _check(
            ComplianceState.CLEAR, None, 0,
            (
                f"On meal {open_meal.index} since "
                f"{open_meal.out_at:%H:%M UTC} — clock stopped."
            ),
        )

    # Where the current clock started, and which meal is next. A recorded
    # non-deductible meal is deliberately ignored here — it is not a meal
    # period and does not reset the clock.
    last = _last_completed_meal(shift, timing)
    if last is None:
        clock_start = shift.effective_call_at
        meal_index = 1
        window = timing.first_meal_within_hours
        origin = f"call at {clock_start:%H:%M UTC}"
    else:
        clock_start = last.in_at  # type: ignore[assignment]
        meal_index = last.index + 1
        window = timing.subsequent_meal_within_hours
        origin = f"return from meal {last.index} at {clock_start:%H:%M UTC}"

    deadline = clock_start + timedelta(hours=float(window))

    # A granted extension is a legitimate delay, not a violation.
    extended = meal_index in shift.meal_extensions_granted
    if extended:
        deadline += timedelta(minutes=timing.extension_minutes)

    overrun = now - deadline

    # --- Still inside the window ----------------------------------------
    if overrun <= timedelta(0):
        remaining = -overrun
        state = (
            ComplianceState.APPROACHING
            if remaining <= config.meal_warning_lead
            else ComplianceState.CLEAR
        )
        note = " (30m extension granted)" if extended else ""
        return _check(
            state, deadline, 0,
            (
                f"Meal {meal_index} due {deadline:%H:%M UTC}{note} — "
                f"{_fmt(remaining)} remaining, measured from {origin}."
            ),
        )

    # --- Past the deadline, inside the grace period ----------------------
    grace = timedelta(minutes=timing.grace_period_minutes)
    if overrun <= grace:
        return _check(
            ComplianceState.APPROACHING, deadline, 0,
            (
                f"Meal {meal_index} is {_fmt(overrun)} late but within the "
                f"{timing.grace_period_minutes}-minute grace period — no "
                f"penalty yet. Breaking now still avoids a charge."
            ),
        )

    # --- Penalty accruing -------------------------------------------------
    # Grace forgives the overrun entirely only if the meal starts within it.
    # Once exceeded, increments are counted from the original deadline, and a
    # partial half-hour counts as a whole one ("or portion thereof").
    #
    # This reading is not verified — see the module docstring. It is the
    # stricter of the two plausible interpretations, which for a
    # cost-avoidance tool is the correct direction to err in.
    increments = math.ceil(
        overrun.total_seconds() / config.penalty_increment.total_seconds()
    )
    accrued = schedule.cumulative_for(increments)
    next_increment_at = deadline + config.penalty_increment * increments
    step_rate = schedule.rate_for_increment(increments + 1)

    return _check(
        ComplianceState.VIOLATED, deadline, increments,
        (
            f"Meal {meal_index} penalty accruing — {_fmt(overrun)} past the "
            f"{deadline:%H:%M UTC} deadline. {increments} half-hour "
            f"increment{'s' if increments != 1 else ''}, ${accrued} per person"
            + (
                f" x{shift.subject_headcount} = ${accrued * shift.subject_headcount}"
                if shift.subject_headcount > 1
                else ""
            )
            + f". Next increment at {next_increment_at:%H:%M UTC} adds ${step_rate}."
        ),
    )


def _fmt(delta: timedelta) -> str:
    total = int(abs(delta).total_seconds())
    hours, remainder = divmod(total, 3600)
    minutes = remainder // 60
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"
