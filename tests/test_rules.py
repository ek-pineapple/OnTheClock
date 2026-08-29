"""Ring 1 tests.

These run with no cloud, no credentials, and no network — which is the whole
point of keeping `core/` pure. Every test pins `now` explicitly, so nothing
here depends on when it is run.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from core.models import (
    CallGroup,
    ComplianceState,
    ContractRate,
    ContractType,
    MealBreak,
    PerformerCategory,
    Person,
    Production,
    ProductionType,
    BudgetTier,
    Shift,
    ShiftStatus,
    SubjectType,
    ViolationType,
    Zone,
    SCHEDULE_H_II,
    SCHEDULE_K_III,
)
from core.rulebook import (
    MEAL_PENALTY_BACKGROUND,
    MEAL_PENALTY_PRINCIPAL,
    Confidence,
    RuleBook,
)
from core.rules import (
    active,
    check_contract_schedule,
    check_meal_period,
    check_rest_period,
    check_weekly_rest,
    evaluate_subject,
    total_exposure_usd,
    workweek_start,
)

RB = RuleBook()
DAY = date(2026, 9, 14)


def utc(h: int, m: int = 0, day: int = 14) -> datetime:
    return datetime(2026, 9, day, h, m, tzinfo=timezone.utc)


def production(ptype=ProductionType.THEATRICAL) -> Production:
    return Production(
        production_id="prod_a",
        title="The Long Night",
        production_type=ptype,
        budget_tier=BudgetTier.BASIC_THEATRICAL,
        timezone="America/Los_Angeles",
        shoot_start=date(2026, 9, 1),
        shoot_end=date(2026, 10, 10),
    )


def performer(
    category=PerformerCategory.PRINCIPAL,
    contract=ContractType.DAY,
    rate=ContractRate.daily(Decimal("1283.00")),
    schedule=None,
) -> Person:
    return Person(
        person_id="p_07",
        production_id="prod_a",
        name="Dana Reyes",
        performer_category=category,
        contract_type=contract,
        contract_rate=rate,
        contract_schedule=schedule,
    )


def shift(
    day: int,
    call: datetime,
    dismissed: datetime | None = None,
    zone=Zone.STUDIO,
    exterior: bool = False,
    meals: list[MealBreak] | None = None,
    status=ShiftStatus.SCHEDULED,
    subject_type=SubjectType.PERSON,
    subject_id="p_07",
    headcount: int | None = None,
    on: date | None = None,
) -> Shift:
    return Shift(
        shift_id=f"sh_{day}",
        production_id="prod_a",
        subject_type=subject_type,
        subject_id=subject_id,
        shoot_day=day,
        date=on or DAY,
        zone=zone,
        scheduled_call_at=call,
        actual_call_at=call,
        dismissed_at=dismissed,
        exterior_photography=exterior,
        meals=meals or [],
        status=status,
        headcount=headcount,
    )


# ==========================================================================
# The corrected penalty math — the error that started all this
# ==========================================================================


class TestPenaltySchedule:
    def test_principal_schedule_is_cumulative_not_flat(self):
        # 25 + 35 + 50 + 50 + 75
        assert MEAL_PENALTY_PRINCIPAL.cumulative_for(5) == Decimal("235.00")
        # The two wrong answers this replaces:
        assert MEAL_PENALTY_PRINCIPAL.cumulative_for(5) != Decimal("375.00")  # 5 x 75
        assert MEAL_PENALTY_PRINCIPAL.cumulative_for(5) != Decimal("125.00")  # 5 x 25

    @pytest.mark.parametrize(
        "n,expected",
        [(1, "25.00"), (2, "60.00"), (3, "110.00"), (4, "160.00"), (5, "235.00")],
    )
    def test_principal_steps(self, n, expected):
        assert MEAL_PENALTY_PRINCIPAL.cumulative_for(n) == Decimal(expected)

    @pytest.mark.parametrize(
        "n,expected",
        [(1, "7.50"), (2, "17.50"), (3, "30.00"), (4, "42.50"), (5, "57.50")],
    )
    def test_background_steps(self, n, expected):
        assert MEAL_PENALTY_BACKGROUND.cumulative_for(n) == Decimal(expected)

    def test_fifth_tier_is_unbounded(self):
        sixth = MEAL_PENALTY_PRINCIPAL.cumulative_for(6) - MEAL_PENALTY_PRINCIPAL.cumulative_for(5)
        tenth = MEAL_PENALTY_PRINCIPAL.cumulative_for(10) - MEAL_PENALTY_PRINCIPAL.cumulative_for(9)
        assert sixth == tenth == Decimal("75.00")

    def test_zero_and_negative_increments_cost_nothing(self):
        assert MEAL_PENALTY_PRINCIPAL.cumulative_for(0) == Decimal("0.00")
        assert MEAL_PENALTY_PRINCIPAL.cumulative_for(-3) == Decimal("0.00")


class TestForcedCallPricing:
    def test_penalty_is_lesser_of_days_pay_or_cap(self):
        day = RB.forced_call_penalty(ContractType.DAY)
        # Above the cap -> capped.
        assert day.amount_for(Decimal("1283.00")) == Decimal("900.00")
        # Below the cap -> actual pay. The "$900 per violation" headline is a
        # ceiling, not a price.
        assert day.amount_for(Decimal("256.60")) == Decimal("256.60")

    def test_weekly_performer_uses_one_days_pay_not_the_day_rate(self):
        weekly = ContractRate.weekly(Decimal("4456.00"))
        assert weekly.one_days_pay_usd == Decimal("891.20")
        penalty = RB.forced_call_penalty(ContractType.WEEKLY)
        # Not the $950 cap — one day's pay is less.
        assert penalty.amount_for(weekly.one_days_pay_usd) == Decimal("891.20")


# ==========================================================================
# Rest periods
# ==========================================================================


class TestRestPeriod:
    def test_twelve_hours_clear_in_studio_zone(self):
        shifts = [
            shift(1, utc(7), dismissed=utc(19)),
            shift(2, utc(7, day=15)),
        ]
        r = check_rest_period(performer(), production(), shifts, 1, utc(20), RB)
        assert r.state is ComplianceState.CLEAR

    def test_eleven_hour_gap_in_studio_zone_is_a_forced_call(self):
        shifts = [
            shift(1, utc(7), dismissed=utc(20)),
            shift(2, utc(7, day=15)),  # 11h gap
        ]
        r = check_rest_period(performer(), production(), shifts, 1, utc(21), RB)
        assert r.state is ComplianceState.APPROACHING  # call hasn't happened
        assert r.shortfall_seconds == 3600
        assert r.per_capita_penalty_usd == Decimal("900.00")

    def test_approaching_becomes_violated_once_the_call_passes(self):
        shifts = [
            shift(1, utc(7), dismissed=utc(20)),
            shift(2, utc(7, day=15)),
        ]
        after = utc(8, day=15)
        r = check_rest_period(performer(), production(), shifts, 1, after, RB)
        assert r.state is ComplianceState.VIOLATED

    def test_approaching_countdown_is_time_left_to_fix(self):
        """A forced call is not a countdown to harm — it is a countdown to
        the last moment a human can still move the call."""
        shifts = [
            shift(1, utc(7), dismissed=utc(20)),
            shift(2, utc(7, day=15)),
        ]
        now = utc(22)  # 9 hours before the call
        r = check_rest_period(performer(), production(), shifts, 1, now, RB)
        assert r.state is ComplianceState.APPROACHING
        assert r.deadline_at == utc(7, day=15)
        assert r.margin_seconds == 9 * 3600

    def test_first_shift_has_nothing_to_measure_against(self):
        shifts = [shift(1, utc(7))]
        r = check_rest_period(performer(), production(), shifts, 0, utc(6), RB)
        assert r.state is ComplianceState.CLEAR
        assert r.per_capita_penalty_usd == Decimal("0.00")

    def test_clear_check_carries_no_penalty(self):
        shifts = [shift(1, utc(7), dismissed=utc(19)), shift(2, utc(7, day=15))]
        r = check_rest_period(performer(), production(), shifts, 1, utc(20), RB)
        assert r.per_capita_penalty_usd == Decimal("0.00")

    def test_stunt_coordinator_gets_nine_hours(self):
        shifts = [
            shift(1, utc(7), dismissed=utc(21)),
            shift(2, utc(7, day=15)),  # 10h gap — short for 12h, fine for 9h
        ]
        coord = performer(category=PerformerCategory.STUNT_COORDINATOR)
        r = check_rest_period(coord, production(), shifts, 1, utc(22), RB)
        assert r.state is ComplianceState.CLEAR

    def test_stunt_performer_is_flagged_unverified(self):
        """We could not source their threshold, so the check must say so
        rather than quietly borrowing the coordinator's 9 hours."""
        shifts = [shift(1, utc(7), dismissed=utc(19)), shift(2, utc(7, day=15))]
        sp = performer(category=PerformerCategory.STUNT_PERFORMER)
        r = check_rest_period(sp, production(), shifts, 1, utc(20), RB)
        assert r.rule_confidence == Confidence.NEEDS_SOURCE_CHECK.value


class TestOvernightDiffersByProductionType:
    """The dimension that was missing from the plan entirely."""

    def _shifts(self):
        return [
            shift(1, utc(7), dismissed=utc(20), zone=Zone.OVERNIGHT),
            shift(2, utc(7, day=15), zone=Zone.OVERNIGHT),  # 11h gap
        ]

    def test_theatrical_permits_eleven_hours(self):
        r = check_rest_period(
            performer(), production(ProductionType.THEATRICAL), self._shifts(), 1, utc(21), RB
        )
        assert r.state is ComplianceState.CLEAR

    def test_television_does_not(self):
        r = check_rest_period(
            performer(), production(ProductionType.TELEVISION), self._shifts(), 1, utc(21), RB
        )
        assert r.state is ComplianceState.APPROACHING


class TestDistantLocationReduction:
    """10h requires exterior photography on BOTH adjacent days — the
    conjunction the project notes had lost."""

    def test_reduction_applies_when_both_days_are_exterior(self):
        shifts = [
            shift(1, utc(7), dismissed=utc(21), zone=Zone.DISTANT, exterior=True),
            shift(2, utc(7, day=15), zone=Zone.DISTANT, exterior=True),  # 10h
        ]
        r = check_rest_period(performer(), production(), shifts, 1, utc(22), RB)
        assert r.state is ComplianceState.CLEAR

    def test_reduction_denied_when_adjacent_day_is_not_exterior(self):
        shifts = [
            shift(1, utc(7), dismissed=utc(21), zone=Zone.DISTANT, exterior=True),
            shift(2, utc(7, day=15), zone=Zone.DISTANT, exterior=False),
        ]
        r = check_rest_period(performer(), production(), shifts, 1, utc(22), RB)
        assert r.state is ComplianceState.APPROACHING
        assert "condition not met" in r.explanation


class TestStillOnTheClock:
    """The most actionable alert the product makes: nobody has been dismissed
    yet, no violation exists, and the deadline to prevent one is known."""

    def _shifts(self):
        return [
            shift(1, utc(7), dismissed=None, status=ShiftStatus.ON_CLOCK),
            shift(2, utc(7, day=15)),
        ]

    def test_warns_before_the_latest_legal_dismissal(self):
        # Call 07:00 next day, 12h rest -> must wrap by 19:00.
        r = check_rest_period(performer(), production(), self._shifts(), 1, utc(18, 30), RB)
        assert r.state is ComplianceState.APPROACHING
        assert r.deadline_at == utc(19)
        assert r.margin_seconds == 30 * 60

    def test_clear_while_there_is_still_room(self):
        r = check_rest_period(performer(), production(), self._shifts(), 1, utc(14), RB)
        assert r.state is ComplianceState.CLEAR

    def test_violated_once_the_dismissal_deadline_passes(self):
        r = check_rest_period(performer(), production(), self._shifts(), 1, utc(20), RB)
        assert r.state is ComplianceState.VIOLATED
        assert r.shortfall_seconds == 3600


# ==========================================================================
# Meal periods
# ==========================================================================


class TestMealPeriod:
    def test_clear_early_in_the_window(self):
        s = shift(1, utc(7), status=ShiftStatus.ON_CLOCK)
        r = check_meal_period(performer(), s, utc(10), RB)
        assert r.state is ComplianceState.CLEAR
        assert r.deadline_at == utc(13)  # call + 6h

    def test_approaching_inside_the_warning_lead(self):
        s = shift(1, utc(7), status=ShiftStatus.ON_CLOCK)
        r = check_meal_period(performer(), s, utc(12, 45), RB)
        assert r.state is ComplianceState.APPROACHING
        assert r.margin_seconds == 15 * 60

    def test_grace_period_suppresses_the_penalty(self):
        s = shift(1, utc(7), status=ShiftStatus.ON_CLOCK)
        r = check_meal_period(performer(), s, utc(13, 10), RB)  # 10 min over
        assert r.state is ComplianceState.APPROACHING
        assert r.half_hour_increments == 0
        assert r.per_capita_penalty_usd == Decimal("0.00")

    def test_penalty_starts_once_grace_is_exceeded(self):
        s = shift(1, utc(7), status=ShiftStatus.ON_CLOCK)
        r = check_meal_period(performer(), s, utc(13, 20), RB)  # 20 min over
        assert r.state is ComplianceState.VIOLATED
        assert r.half_hour_increments == 1
        assert r.per_capita_penalty_usd == Decimal("25.00")

    def test_partial_half_hour_counts_as_a_whole_one(self):
        s = shift(1, utc(7), status=ShiftStatus.ON_CLOCK)
        r = check_meal_period(performer(), s, utc(13, 31), RB)  # 31 min over
        assert r.half_hour_increments == 2
        assert r.per_capita_penalty_usd == Decimal("60.00")  # 25 + 35

    def test_second_meal_clock_resets_on_return_not_on_departure(self):
        meals = [MealBreak(index=1, out_at=utc(12), in_at=utc(12, 30))]
        s = shift(1, utc(7), meals=meals, status=ShiftStatus.ON_CLOCK)
        r = check_meal_period(performer(), s, utc(17), RB)
        assert r.deadline_at == utc(18, 30)  # 12:30 + 6h, not 12:00 + 6h
        assert r.state is ComplianceState.CLEAR

    def test_open_break_stops_the_clock(self):
        meals = [MealBreak(index=1, out_at=utc(12), in_at=None)]
        s = shift(1, utc(7), meals=meals, status=ShiftStatus.ON_CLOCK)
        r = check_meal_period(performer(), s, utc(14), RB)
        assert r.state is ComplianceState.CLEAR
        assert "clock stopped" in r.explanation

    def test_granted_extension_prevents_a_penalty(self):
        """At 13:20 the same moment is a violation without an extension and
        merely approaching with one — the extension is a legitimate delay."""
        without = shift(1, utc(7), status=ShiftStatus.ON_CLOCK)
        r1 = check_meal_period(performer(), without, utc(13, 20), RB)
        assert r1.state is ComplianceState.VIOLATED
        assert r1.half_hour_increments == 1

        granted = shift(1, utc(7), status=ShiftStatus.ON_CLOCK)
        granted.meal_extensions_granted = {1}
        r2 = check_meal_period(performer(), granted, utc(13, 20), RB)
        assert r2.state is ComplianceState.APPROACHING  # deadline moved to 13:30
        assert r2.half_hour_increments == 0
        assert r2.per_capita_penalty_usd == Decimal("0.00")

    def test_wrapped_shift_has_no_clock(self):
        s = shift(1, utc(7), dismissed=utc(19), status=ShiftStatus.WRAPPED)
        r = check_meal_period(performer(), s, utc(20), RB)
        assert r.state is ComplianceState.CLEAR
        assert r.deadline_at is None


class TestBackgroundCallGroup:
    """A call group is priced through the same code path as one person."""

    def test_headcount_multiplies_the_per_capita_penalty(self):
        group = CallGroup(
            call_group_id="bg_1",
            production_id="prod_a",
            label="Diner Ext.",
            headcount=85,
            contract_rate=ContractRate.daily(Decimal("221.00")),
        )
        s = shift(
            1, utc(7),
            status=ShiftStatus.ON_CLOCK,
            subject_type=SubjectType.CALL_GROUP,
            subject_id="bg_1",
            headcount=85,
        )
        # Deadline 13:00; at 14:29 the overrun is 89 min -> 3 increments.
        r = check_meal_period(group, s, utc(14, 29), RB)
        assert r.half_hour_increments == 3
        assert r.per_capita_penalty_usd == Decimal("30.00")  # 7.50+10+12.50
        assert r.total_penalty_usd == Decimal("2550.00")


# ==========================================================================
# Classification
# ==========================================================================


class TestContractSchedule:
    def test_stunt_performer_on_k_iii_is_flagged(self):
        p = performer(category=PerformerCategory.STUNT_PERFORMER, schedule=SCHEDULE_K_III)
        r = check_contract_schedule(p, utc(12))
        assert r.state is ComplianceState.VIOLATED
        assert r.per_capita_penalty_usd == Decimal("0.00")

    def test_stunt_performer_on_h_ii_is_clear(self):
        p = performer(category=PerformerCategory.STUNT_PERFORMER, schedule=SCHEDULE_H_II)
        r = check_contract_schedule(p, utc(12))
        assert r.state is ComplianceState.CLEAR

    def test_missing_schedule_is_flagged_but_not_a_violation(self):
        p = performer(category=PerformerCategory.STUNT_PERFORMER, schedule=None)
        r = check_contract_schedule(p, utc(12))
        assert r.state is ComplianceState.APPROACHING

    def test_coordinator_on_k_iii_is_correct(self):
        p = performer(category=PerformerCategory.STUNT_COORDINATOR, schedule=SCHEDULE_K_III)
        r = check_contract_schedule(p, utc(12))
        assert r.state is ComplianceState.CLEAR


# ==========================================================================
# Provenance
# ==========================================================================


# ==========================================================================
# Weekly rest — the rule a day-by-day tracker cannot see
# ==========================================================================

MON_7, SAT_12, SUN_13, MON_14 = (
    date(2026, 9, 7), date(2026, 9, 12), date(2026, 9, 13), date(2026, 9, 14)
)


class TestWeeklyRest:
    def test_workweek_start_anchors_to_monday(self):
        assert workweek_start(date(2026, 9, 12), 0) == MON_7  # Saturday -> Mon 7
        assert workweek_start(date(2026, 9, 14), 0) == MON_14

    def test_does_not_apply_mid_week(self):
        shifts = [
            shift(1, utc(7, day=7), dismissed=utc(19, day=7), on=MON_7),
            shift(2, utc(7, day=8), on=date(2026, 9, 8)),
        ]
        r = check_weekly_rest(performer(), production(), shifts, 1, utc(20, day=7), RB)
        assert r.state is ComplianceState.CLEAR
        assert "Not a workweek boundary" in r.explanation

    def test_fifty_six_hours_clear_across_the_boundary(self):
        # Wrap Fri 18:00, back Mon 07:00 = 61h.
        shifts = [
            shift(1, utc(7, day=11), dismissed=utc(18, day=11), on=date(2026, 9, 11)),
            shift(2, utc(7, day=14), on=MON_14),
        ]
        r = check_weekly_rest(performer(), production(), shifts, 1, utc(19, day=11), RB)
        assert r.state is ComplianceState.CLEAR

    def test_short_weekly_rest_is_a_forced_call(self):
        # Wrap Sat 20:00, back Mon 05:00 = 33h — well short of 56h.
        shifts = [
            shift(1, utc(7, day=12), dismissed=utc(20, day=12), on=SAT_12),
            shift(2, utc(12, day=14), on=MON_14),  # 05:00 local
        ]
        r = check_weekly_rest(performer(), production(), shifts, 1, utc(21, day=12), RB)
        assert r.state is ComplianceState.APPROACHING
        assert r.per_capita_penalty_usd == Decimal("900.00")

    def test_six_am_local_exception_reduces_to_fifty_four(self):
        """The one rule stated in local time. 13:00 UTC is 06:00 PDT, so the
        reduction applies; 12:00 UTC is 05:00 PDT and it does not."""
        # Night shoot wraps Sat 06:00 UTC. A Mon 13:00 UTC call is a 55h gap:
        # short of 56h, but fine under the 54h exception. A Mon 12:00 UTC call
        # is 54h and gets no exception, so 56h stands and it fails.
        wrap = utc(6, day=12)
        at_six = [
            shift(1, utc(20, day=11), dismissed=wrap, on=date(2026, 9, 11)),
            shift(2, utc(13, day=14), on=MON_14),  # 06:00 local -> 54h allowed
        ]
        r1 = check_weekly_rest(performer(), production(), at_six, 1, utc(7, day=12), RB)
        assert r1.state is ComplianceState.CLEAR
        assert "54" in r1.explanation

        before_six = [
            shift(1, utc(20, day=11), dismissed=wrap, on=date(2026, 9, 11)),
            shift(2, utc(12, day=14), on=MON_14),  # 05:00 local -> 56h stands
        ]
        r2 = check_weekly_rest(performer(), production(), before_six, 1, utc(7, day=12), RB)
        assert r2.state is ComplianceState.APPROACHING

    def test_six_day_location_workweek_reduces_to_thirty_six(self):
        prior = [
            shift(i, utc(7, day=6 + i), dismissed=utc(19, day=6 + i),
                  zone=Zone.DISTANT, on=date(2026, 9, 6 + i))
            for i in range(1, 7)  # Mon 7 .. Sat 12, all on location
        ]
        shifts = prior + [shift(7, utc(12, day=14), on=MON_14)]  # 05:00 local
        # Sat wrap 19:00 -> Mon 12:00 UTC = 41h. Short of 56h, fine under 36h.
        r = check_weekly_rest(performer(), production(), shifts, 6, utc(20, day=12), RB)
        assert r.state is ComplianceState.CLEAR
        assert "location workweek" in r.explanation

    def test_five_day_location_workweek_gets_no_reduction(self):
        # Tue-Sat instead of Mon-Sat: same 41h gap into the new workweek, but
        # only five days, so the 36h reduction does not apply and 56h stands.
        prior = [
            shift(i, utc(7, day=7 + i), dismissed=utc(19, day=7 + i),
                  zone=Zone.DISTANT, on=date(2026, 9, 7 + i))
            for i in range(1, 6)  # Tue 8 .. Sat 12
        ]
        shifts = prior + [shift(6, utc(12, day=14), on=MON_14)]
        r = check_weekly_rest(performer(), production(), shifts, 5, utc(20, day=12), RB)
        assert r.state is ComplianceState.APPROACHING


# ==========================================================================
# Non-deductible meals
# ==========================================================================


class TestNonDeductibleMeal:
    def test_short_early_break_does_not_reset_the_clock(self):
        """A 15-minute break an hour after call is an NDB, not a meal. If it
        reset the clock the deadline would move to 14:15 and we would miss a
        real violation at 13:00."""
        ndb = [MealBreak(index=1, out_at=utc(8), in_at=utc(8, 15))]
        s = shift(1, utc(7), meals=ndb, status=ShiftStatus.ON_CLOCK)
        r = check_meal_period(performer(), s, utc(12, 45), RB)
        assert r.deadline_at == utc(13)  # call + 6h, unchanged
        assert r.state is ComplianceState.APPROACHING

    def test_a_real_meal_does_reset_the_clock(self):
        real = [MealBreak(index=1, out_at=utc(11), in_at=utc(11, 30))]
        s = shift(1, utc(7), meals=real, status=ShiftStatus.ON_CLOCK)
        r = check_meal_period(performer(), s, utc(12, 45), RB)
        assert r.deadline_at == utc(17, 30)  # 11:30 + 6h
        assert r.state is ComplianceState.CLEAR

    def test_long_early_break_still_counts_as_a_meal(self):
        """Near call time but 45 minutes long — too long to be an NDB."""
        early = [MealBreak(index=1, out_at=utc(8), in_at=utc(8, 45))]
        s = shift(1, utc(7), meals=early, status=ShiftStatus.ON_CLOCK)
        r = check_meal_period(performer(), s, utc(12), RB)
        assert r.deadline_at == utc(14, 45)


# ==========================================================================
# Stacking — the combined entry point
# ==========================================================================


class TestStackingViolations:
    def _both_wrong(self):
        """Forced call scheduled AND a meal penalty running, same person.

        Both shifts sit inside the workweek beginning Mon 14 Sept, so the
        weekly-rest rule correctly does not fire and the stacking under test
        is exactly the two day-level violations.
        """
        return [
            shift(1, utc(7), dismissed=utc(20), on=MON_14),
            shift(
                2, utc(7, day=15),
                meals=[], status=ShiftStatus.ON_CLOCK, on=date(2026, 9, 15),
            ),
        ]

    def test_both_violations_are_reported_independently(self):
        shifts = self._both_wrong()
        # 14:00 on day 2: meal deadline was 13:00, and the 11h rest gap
        # already breached the schedule.
        checks = evaluate_subject(
            performer(), production(), shifts, 1, utc(14, day=15), RB
        )
        kinds = {c.violation_type for c in active(checks)}
        assert ViolationType.FORCED_CALL in kinds
        assert ViolationType.MEAL_PENALTY in kinds

    def test_finding_one_does_not_stop_the_others(self):
        shifts = self._both_wrong()
        checks = evaluate_subject(
            performer(), production(), shifts, 1, utc(14, day=15), RB
        )
        # rest + weekly rest + meal + classification
        assert len(checks) == 4

    def test_exposure_sums_across_stacked_violations(self):
        shifts = self._both_wrong()
        checks = evaluate_subject(
            performer(), production(), shifts, 1, utc(14, day=15), RB
        )
        # $900 forced call + $60 meal (2 increments) = $960
        assert total_exposure_usd(checks) == Decimal("960.00")

    def test_clear_subject_reports_no_active_checks(self):
        shifts = [
            shift(1, utc(7), dismissed=utc(19), on=MON_14),
            shift(2, utc(7, day=15), status=ShiftStatus.ON_CLOCK, on=date(2026, 9, 15)),
        ]
        checks = evaluate_subject(performer(), production(), shifts, 1, utc(9, day=15), RB)
        assert active(checks) == []
        assert total_exposure_usd(checks) == Decimal("0.00")

    def test_call_group_skips_the_classification_check(self):
        group = CallGroup(
            call_group_id="bg_1", production_id="prod_a", label="Diner Ext.",
            headcount=40, contract_rate=ContractRate.daily(Decimal("221.00")),
        )
        shifts = [
            shift(1, utc(7), dismissed=utc(19), subject_type=SubjectType.CALL_GROUP,
                  subject_id="bg_1", headcount=40, on=MON_14),
            shift(2, utc(7, day=15), subject_type=SubjectType.CALL_GROUP,
                  subject_id="bg_1", headcount=40, status=ShiftStatus.ON_CLOCK,
                  on=date(2026, 9, 15)),
        ]
        checks = evaluate_subject(group, production(), shifts, 1, utc(9, day=15), RB)
        assert len(checks) == 3  # no schedule-mismatch check


class TestProvenance:
    def test_every_check_carries_a_source(self):
        shifts = [shift(1, utc(7), dismissed=utc(19)), shift(2, utc(7, day=15))]
        r = check_rest_period(performer(), production(), shifts, 1, utc(20), RB)
        assert r.source_url.startswith("http")
        assert r.rule_version

    def test_unverified_rules_are_enumerable(self):
        flagged = RB.unverified_rules()
        assert any("stunt performer" in f for f in flagged)
