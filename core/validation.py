"""Consistency checks that a single dataclass cannot make about itself.

`Shift` carries both a `date` (the production-local shoot day) and UTC
timestamps, and nothing forces them to agree. During Ring 1 testing this bit
me repeatedly: a shift dated Mon 7 Sept with a call timestamp on Mon 14 Sept
is accepted silently, then produces a confident and completely wrong verdict
— a weekly-rest violation invented out of a typo.

A generator will reproduce that at scale. So the invariants live here, and
anything writing shifts runs them first.

These cannot go in `Shift.__post_init__` because the local-date check needs
the production's timezone, which a shift does not carry. That is a real
consequence of storing UTC: correctness of a *local* claim requires a second
object, so the check has to live one level up.
"""

from __future__ import annotations

from datetime import timedelta
from zoneinfo import ZoneInfo

from .models import Production, Shift, SubjectType

#: A shift longer than this is almost certainly a data error rather than a
#: brutal day. Long enough to allow genuinely extreme days without nagging.
MAX_SHIFT_LENGTH = timedelta(hours=24)


class ShiftValidationError(ValueError):
    """A shift's fields contradict each other."""


def shift_problems(shift: Shift, production: Production) -> list[str]:
    """Every inconsistency found, as human-readable strings. Empty = valid."""
    problems: list[str] = []
    tz = ZoneInfo(production.timezone)

    if shift.production_id != production.production_id:
        problems.append(
            f"shift belongs to {shift.production_id!r} but was validated "
            f"against {production.production_id!r}"
        )

    # The call starts the shoot day, so it must fall on that local date even
    # for a night shoot that wraps the following morning.
    call_local = shift.scheduled_call_at.astimezone(tz)
    if call_local.date() != shift.date:
        problems.append(
            f"scheduled_call_at is {call_local.date()} in {production.timezone} "
            f"but the shift is dated {shift.date}"
        )

    if shift.actual_call_at is not None:
        drift = abs(shift.actual_call_at - shift.scheduled_call_at)
        if drift > timedelta(hours=12):
            problems.append(
                f"actual_call_at is {drift} away from scheduled_call_at — "
                "likely the wrong day"
            )

    if shift.dismissed_at is not None:
        if shift.dismissed_at <= shift.effective_call_at:
            problems.append("dismissed_at is at or before the call")
        elif shift.dismissed_at - shift.effective_call_at > MAX_SHIFT_LENGTH:
            problems.append(
                f"shift runs {shift.dismissed_at - shift.effective_call_at}, "
                f"longer than the {MAX_SHIFT_LENGTH} sanity limit"
            )

    problems.extend(_meal_problems(shift))

    if shift.subject_type is SubjectType.CALL_GROUP:
        if not shift.headcount or shift.headcount < 1:
            problems.append("call group shift has no headcount")
    elif shift.headcount is not None:
        problems.append("headcount is set on an individual's shift")

    return problems


def _meal_problems(shift: Shift) -> list[str]:
    problems: list[str] = []
    seen: set[int] = set()

    for meal in shift.meals:
        if meal.index in seen:
            problems.append(f"duplicate meal index {meal.index}")
        seen.add(meal.index)

        if meal.out_at < shift.effective_call_at:
            problems.append(f"meal {meal.index} starts before the call")
        if shift.dismissed_at and meal.out_at > shift.dismissed_at:
            problems.append(f"meal {meal.index} starts after dismissal")
        if meal.in_at is not None and meal.in_at <= meal.out_at:
            problems.append(f"meal {meal.index} returns at or before it starts")

    # Open breaks must be the last thing that happened — two simultaneous
    # open breaks means the records are wrong, and the meal clock would
    # silently stop on whichever one was found first.
    open_meals = [m.index for m in shift.meals if not m.is_complete]
    if len(open_meals) > 1:
        problems.append(f"more than one open meal break: {open_meals}")

    for index in shift.meal_extensions_granted:
        if index < 1:
            problems.append(f"meal extension granted for invalid index {index}")

    return problems


def validate_shift(shift: Shift, production: Production) -> None:
    """Raise if the shift is internally inconsistent."""
    problems = shift_problems(shift, production)
    if problems:
        raise ShiftValidationError(
            f"Shift {shift.shift_id!r} is inconsistent:\n  - "
            + "\n  - ".join(problems)
        )


def validate_sequence(shifts: list[Shift], production: Production) -> None:
    """Validate each shift, plus the ordering invariant the rules depend on.

    Every rule in `core.rules` indexes into a chronological sequence. An
    out-of-order list does not fail loudly — it produces a plausible wrong
    answer, which is the failure mode this project can least afford.
    """
    for shift in shifts:
        validate_shift(shift, production)

    for prev, curr in zip(shifts, shifts[1:]):
        if curr.scheduled_call_at < prev.scheduled_call_at:
            raise ShiftValidationError(
                f"Shifts are not in chronological order: {prev.shift_id!r} "
                f"({prev.scheduled_call_at}) precedes {curr.shift_id!r} "
                f"({curr.scheduled_call_at})"
            )
        if prev.dismissed_at and curr.scheduled_call_at < prev.dismissed_at:
            raise ShiftValidationError(
                f"{curr.shift_id!r} is called at {curr.scheduled_call_at}, "
                f"before {prev.shift_id!r} was dismissed at {prev.dismissed_at} "
                "— overlapping shifts for one subject"
            )
