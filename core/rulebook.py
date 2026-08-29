"""The encoded SAG-AFTRA rulebook.

Every number the system relies on lives here as *data*, not as branching
logic, so that it can be inspected, cited, versioned, and replaced.

Three things this buys us:

1. The agent can cite itself. "$110 accrued across 3 half-hour increments,
   per the schedule effective 2026-07-01" — with a link — reads very
   differently from an unsourced number.
2. Changing an agreement means editing one table, never control flow.
3. The Week 5 agreement-upload feature has a concrete target shape:
   Gemini extracts values into these structures, presented for
   confirmation, never silently applied.

**On `Confidence`.** Not every number below was verified against a primary
source. sagaftra.org blocks automated fetching, so some values come from
their public support KB and some from payroll-industry secondary sources.
Rather than flatten that distinction and present guesses as facts, each
rule carries its provenance grade. `NEEDS_SOURCE_CHECK` entries are meant
to be visibly flagged in the UI, not quietly relied upon — for a
compliance tool, "we have not verified this" is information, not a gap.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from .models import (
    BudgetTier,
    ContractType,
    PerformerCategory,
    ProductionType,
    Zone,
)

RULE_VERSION = "sag-2026.1"

SRC_MEAL_PERIODS = "https://www.sagaftra.org/meal-periods"
SRC_REST_THEATRICAL = (
    "https://servicesagaftra.custhelp.com/app/answers/detail/a_id/1001/"
    "~/how-do-rest-periods-work-for-a-theatrical-production"
)
SRC_REST_TELEVISION = (
    "https://servicesagaftra.custhelp.com/app/answers/detail/a_id/958/"
)
SRC_MEAL_PENALTY_SCHEDULE = "https://greenslate.com/blog/sag-aftra-tv-theatrical-agreement"
SRC_SCALE_RATES = "https://www.wrapbook.com/blog/essential-guide-sag-rates"


class Confidence(StrEnum):
    """How well-sourced an encoded rule is."""

    #: Retrieved this session from SAG-AFTRA's own support KB.
    CONFIRMED = "confirmed"
    #: Multiple independent payroll-industry sources agree, but not
    #: verified against SAG-AFTRA primary text.
    CORROBORATED = "corroborated"
    #: Asserted in project notes or widely repeated, but not verified.
    #: Must be surfaced to the user before being relied on.
    NEEDS_SOURCE_CHECK = "needs_source_check"


# --------------------------------------------------------------------------
# Meal penalties
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PenaltyStep:
    """One tier of an escalating penalty schedule.

    `to_increment=None` means the tier is unbounded above.
    """

    from_increment: int
    to_increment: int | None
    amount_usd: Decimal


@dataclass(frozen=True)
class PenaltySchedule:
    """An escalating, cumulative meal-penalty schedule.

    This is *not* a flat per-increment rate, which is the single most
    common way this rule is stated incorrectly. Each half-hour increment
    is charged at its own tier's rate and the charges accumulate.

    For a principal, five increments is::

        25 + 35 + 50 + 50 + 75  =  $235

    not `5 x 75 = $375` (the last tier applied to all), and not
    `5 x 25 = $125` (the first tier applied to all).

    Because this is a piecewise cumulative sum, it cannot be expressed in
    PromQL. The emitter computes the dollar figure and pushes the result;
    Grafana stores an answer, not an equation.
    """

    steps: tuple[PenaltyStep, ...]
    source_url: str
    effective_from: date
    rule_version: str
    confidence: Confidence = Confidence.CONFIRMED

    def rate_for_increment(self, n: int) -> Decimal:
        """The rate charged for the nth half-hour increment (1-based)."""
        if n < 1:
            return Decimal("0.00")
        for step in self.steps:
            upper = step.to_increment
            if n >= step.from_increment and (upper is None or n <= upper):
                return step.amount_usd
        return Decimal("0.00")

    def cumulative_for(self, increments: int) -> Decimal:
        """Total owed after `increments` half-hour increments, per person."""
        if increments < 1:
            return Decimal("0.00")
        return sum(
            (self.rate_for_increment(n) for n in range(1, increments + 1)),
            Decimal("0.00"),
        )


#: Effective 2026-07-01. Verified against payroll-industry reporting of the
#: 2026 TV/Theatrical agreement.
MEAL_PENALTY_PRINCIPAL = PenaltySchedule(
    steps=(
        PenaltyStep(1, 1, Decimal("25.00")),
        PenaltyStep(2, 2, Decimal("35.00")),
        PenaltyStep(3, 4, Decimal("50.00")),
        PenaltyStep(5, None, Decimal("75.00")),
    ),
    source_url=SRC_MEAL_PENALTY_SCHEDULE,
    effective_from=date(2026, 7, 1),
    rule_version=RULE_VERSION,
    confidence=Confidence.CORROBORATED,
)

MEAL_PENALTY_BACKGROUND = PenaltySchedule(
    steps=(
        PenaltyStep(1, 1, Decimal("7.50")),
        PenaltyStep(2, 2, Decimal("10.00")),
        PenaltyStep(3, 4, Decimal("12.50")),
        PenaltyStep(5, None, Decimal("15.00")),
    ),
    source_url=SRC_MEAL_PENALTY_SCHEDULE,
    effective_from=date(2026, 7, 1),
    rule_version=RULE_VERSION,
    confidence=Confidence.CORROBORATED,
)

#: Stunt roles are assumed to follow the principal schedule. Not verified —
#: the stunt schedules (H-II, K-III) may carry their own meal terms.
MEAL_PENALTY_BY_CATEGORY: dict[PerformerCategory, PenaltySchedule] = {
    PerformerCategory.PRINCIPAL: MEAL_PENALTY_PRINCIPAL,
    PerformerCategory.BACKGROUND: MEAL_PENALTY_BACKGROUND,
    PerformerCategory.STUNT_COORDINATOR: MEAL_PENALTY_PRINCIPAL,
    PerformerCategory.STUNT_PERFORMER: MEAL_PENALTY_PRINCIPAL,
}


@dataclass(frozen=True)
class MealTimingRule:
    """When meals are owed, and what does *not* count as a violation."""

    first_meal_within_hours: Decimal
    subsequent_meal_within_hours: Decimal
    grace_period_minutes: int
    extension_minutes: int
    ndb_within_hours_of_call: Decimal
    ndb_duration_minutes: int
    source_url: str
    rule_version: str
    confidence: Confidence


#: The 6-hour rolling clock is near-universally reported. The grace period,
#: extension, and non-deductible-meal terms come from project notes and were
#: NOT verified this session — sagaftra.org returns 403 to automated
#: fetching. Treat the sub-rules as provisional; they exist to prevent
#: false positives, so an unverified value here produces over-alerting
#: rather than silent under-reporting.
MEAL_TIMING = MealTimingRule(
    first_meal_within_hours=Decimal("6"),
    subsequent_meal_within_hours=Decimal("6"),
    grace_period_minutes=12,
    extension_minutes=30,
    ndb_within_hours_of_call=Decimal("2"),
    ndb_duration_minutes=15,
    source_url=SRC_MEAL_PERIODS,
    rule_version=RULE_VERSION,
    confidence=Confidence.NEEDS_SOURCE_CHECK,
)


# --------------------------------------------------------------------------
# Rest periods (forced calls)
# --------------------------------------------------------------------------


class ReductionCondition(StrEnum):
    """What must be true for a rest reduction to be permitted.

    Machine-readable so Ring 1 can dispatch on it; each value has a
    corresponding predicate in the rule engine.
    """

    #: Exterior photography required on the day before AND the day after.
    EXTERIOR_PHOTOGRAPHY_ADJACENT_DAYS = "exterior_photography_adjacent_days"
    #: No additional condition beyond the usage limit.
    NONE = "none"


class ReductionLimit(StrEnum):
    """How often a permitted reduction may actually be used."""

    ONCE_PER_FOUR_CONSECUTIVE_DAYS = "once_per_four_consecutive_days"
    TWO_NON_CONSECUTIVE_DAYS_PER_WORKWEEK = "two_non_consecutive_days_per_workweek"


@dataclass(frozen=True)
class RestReduction:
    reduced_to_hours: Decimal
    condition: ReductionCondition
    limit: ReductionLimit
    description: str


@dataclass(frozen=True)
class RestRule:
    """Base rest requirement plus the reduction available, if any."""

    performer_category: PerformerCategory | None  # None = all categories
    zone: Zone | None  # None = all zones
    production_type: ProductionType | None  # None = both
    base_hours: Decimal
    reduction: RestReduction | None
    source_url: str
    rule_version: str
    confidence: Confidence


#: Ordered most-specific first. `RuleBook.rest_rule` returns the first match,
#: so the stunt-coordinator override precedes the zone-based defaults.
REST_RULES: tuple[RestRule, ...] = (
    # --- Category overrides (apply regardless of zone) --------------------
    RestRule(
        performer_category=PerformerCategory.STUNT_COORDINATOR,
        zone=None,
        production_type=None,
        base_hours=Decimal("9"),
        reduction=None,
        source_url=SRC_REST_THEATRICAL,
        rule_version=RULE_VERSION,
        confidence=Confidence.CORROBORATED,
    ),
    # NOTE: stunt *performers* are deliberately absent here. Their rest
    # threshold could not be verified in any source, and plan.md explicitly
    # warns against assuming it matches the coordinator's 9 hours. They
    # therefore fall through to the standard zone rules below, and
    # `RuleBook.rest_rule` downgrades the confidence of whatever it returns
    # for them. Fail loud, not silent.

    # --- Studio zone ------------------------------------------------------
    RestRule(
        performer_category=None,
        zone=Zone.STUDIO,
        production_type=None,
        base_hours=Decimal("12"),
        reduction=None,
        source_url=SRC_REST_THEATRICAL,
        rule_version=RULE_VERSION,
        confidence=Confidence.CONFIRMED,
    ),
    # --- Distant location (outside the studio zone) -----------------------
    # Reducible to 10h, but ONLY where exterior photography is required on
    # both the day before and the day after, and only once every fourth
    # consecutive day. Both conditions must hold — project notes previously
    # recorded only the second.
    RestRule(
        performer_category=None,
        zone=Zone.DISTANT,
        production_type=None,
        base_hours=Decimal("12"),
        reduction=RestReduction(
            reduced_to_hours=Decimal("10"),
            condition=ReductionCondition.EXTERIOR_PHOTOGRAPHY_ADJACENT_DAYS,
            limit=ReductionLimit.ONCE_PER_FOUR_CONSECUTIVE_DAYS,
            description=(
                "Reducible to 10 hours where exterior photography is required "
                "on the day before and the day after, once every fourth "
                "consecutive day."
            ),
        ),
        source_url=SRC_REST_THEATRICAL,
        rule_version=RULE_VERSION,
        confidence=Confidence.CONFIRMED,
    ),
    # --- Overnight location: THEATRICAL only -------------------------------
    RestRule(
        performer_category=None,
        zone=Zone.OVERNIGHT,
        production_type=ProductionType.THEATRICAL,
        base_hours=Decimal("12"),
        reduction=RestReduction(
            reduced_to_hours=Decimal("11"),
            condition=ReductionCondition.NONE,
            limit=ReductionLimit.TWO_NON_CONSECUTIVE_DAYS_PER_WORKWEEK,
            description=(
                "Reducible to 11 hours on any two non-consecutive days in a "
                "workweek. Theatrical only."
            ),
        ),
        source_url=SRC_REST_THEATRICAL,
        rule_version=RULE_VERSION,
        confidence=Confidence.CONFIRMED,
    ),
    # --- Overnight location: TELEVISION — no reduction permitted ----------
    RestRule(
        performer_category=None,
        zone=Zone.OVERNIGHT,
        production_type=ProductionType.TELEVISION,
        base_hours=Decimal("12"),
        reduction=None,
        source_url=SRC_REST_TELEVISION,
        rule_version=RULE_VERSION,
        confidence=Confidence.CONFIRMED,
    ),
)


@dataclass(frozen=True)
class WeeklyRestRule:
    base_hours: Decimal
    reduced_hours_early_call_exception: Decimal
    reduced_hours_six_day_location: Decimal
    early_call_no_earlier_than_hour: int
    #: How many shifts in a workweek make it a "six-day" workweek. A rule
    #: value, so it lives here rather than as a literal in the rule engine.
    six_day_workweek_shift_count: int
    source_url: str
    rule_version: str
    confidence: Confidence


WEEKLY_REST = WeeklyRestRule(
    base_hours=Decimal("56"),
    # 56 -> 54 provided the first call of the new workweek is no earlier
    # than 6am.
    reduced_hours_early_call_exception=Decimal("54"),
    # A six-day location workweek permits as little as 36 hours. This case
    # was missing from the project notes entirely.
    reduced_hours_six_day_location=Decimal("36"),
    early_call_no_earlier_than_hour=6,
    six_day_workweek_shift_count=6,
    source_url=SRC_REST_THEATRICAL,
    rule_version=RULE_VERSION,
    confidence=Confidence.CONFIRMED,
)


# --------------------------------------------------------------------------
# Forced-call penalties
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ForcedCallPenalty:
    """A forced call costs the *lesser* of one day's pay or a fixed cap.

    This is why `Person.contract_rate` is mandatory. The widely-quoted
    "$900 per violation" is a ceiling, not a price: an Ultra Low Budget
    performer on $256.60/day incurs $256.60. Only productions paying at or
    above the cap ever reach it.

    Note the argument is *one day's pay*, not a daily scale rate — for a
    weekly performer those differ. Use `Person.one_days_pay_usd`, which
    derives it correctly from the contract period.
    """

    cap_usd: Decimal
    basis: str
    source_url: str
    rule_version: str
    confidence: Confidence

    def amount_for(self, one_days_pay_usd: Decimal) -> Decimal:
        return min(one_days_pay_usd, self.cap_usd)


FORCED_CALL_PENALTIES: dict[ContractType, ForcedCallPenalty] = {
    ContractType.DAY: ForcedCallPenalty(
        cap_usd=Decimal("900.00"),
        basis="daily rate or $900, whichever is less, per violation",
        source_url=SRC_REST_THEATRICAL,
        rule_version=RULE_VERSION,
        confidence=Confidence.CONFIRMED,
    ),
    ContractType.THREE_DAY: ForcedCallPenalty(
        cap_usd=Decimal("950.00"),
        basis="one day's pay or $950, whichever is less, per violation",
        source_url=SRC_REST_THEATRICAL,
        rule_version=RULE_VERSION,
        confidence=Confidence.CONFIRMED,
    ),
    ContractType.WEEKLY: ForcedCallPenalty(
        cap_usd=Decimal("950.00"),
        basis="one day's pay or $950, whichever is less, per violation",
        source_url=SRC_REST_THEATRICAL,
        rule_version=RULE_VERSION,
        confidence=Confidence.CONFIRMED,
    ),
}


# --------------------------------------------------------------------------
# Scale rates by budget tier
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ScaleRate:
    """Minimum daily/weekly scale for a budget tier.

    Budget tier reaches forced-call penalties *through* this rate rather
    than via a separate penalty table — which is what makes a two-production
    demo meaningful: the same violation prices differently because the
    performers are paid differently.
    """

    budget_tier: BudgetTier
    daily_usd: Decimal
    weekly_usd: Decimal | None
    budget_range: str
    source_url: str
    rule_version: str
    confidence: Confidence


SCALE_RATES: dict[BudgetTier, ScaleRate] = {
    BudgetTier.BASIC_THEATRICAL: ScaleRate(
        budget_tier=BudgetTier.BASIC_THEATRICAL,
        daily_usd=Decimal("1283.00"),
        weekly_usd=Decimal("4456.00"),
        budget_range="greater than $2MM",
        source_url=SRC_SCALE_RATES,
        rule_version=RULE_VERSION,
        confidence=Confidence.CORROBORATED,
    ),
    BudgetTier.LOW_BUDGET: ScaleRate(
        budget_tier=BudgetTier.LOW_BUDGET,
        daily_usd=Decimal("834.00"),
        weekly_usd=Decimal("2896.00"),
        budget_range="$700K-$2MM",
        source_url=SRC_SCALE_RATES,
        rule_version=RULE_VERSION,
        confidence=Confidence.CORROBORATED,
    ),
    BudgetTier.MODERATE_LOW_BUDGET: ScaleRate(
        budget_tier=BudgetTier.MODERATE_LOW_BUDGET,
        daily_usd=Decimal("449.05"),
        weekly_usd=Decimal("1559.60"),
        budget_range="$300K-$700K",
        source_url=SRC_SCALE_RATES,
        rule_version=RULE_VERSION,
        confidence=Confidence.CORROBORATED,
    ),
    BudgetTier.ULTRA_LOW_BUDGET: ScaleRate(
        budget_tier=BudgetTier.ULTRA_LOW_BUDGET,
        daily_usd=Decimal("256.60"),
        weekly_usd=None,
        budget_range="$300K or less",
        source_url=SRC_SCALE_RATES,
        rule_version=RULE_VERSION,
        confidence=Confidence.CORROBORATED,
    ),
    # No verified rate found for Student/Short. Placeholder mirrors Ultra
    # Low Budget so the system runs, but it is flagged and must not be
    # presented as authoritative.
    BudgetTier.STUDENT_SHORT: ScaleRate(
        budget_tier=BudgetTier.STUDENT_SHORT,
        daily_usd=Decimal("256.60"),
        weekly_usd=None,
        budget_range="student / short film",
        source_url=SRC_SCALE_RATES,
        rule_version=RULE_VERSION,
        confidence=Confidence.NEEDS_SOURCE_CHECK,
    ),
}


# --------------------------------------------------------------------------
# Lookup surface
# --------------------------------------------------------------------------


class RuleBook:
    """The single entry point Ring 1 uses to ask the agreement a question.

    Kept as a class rather than loose functions so it can later be backed
    by Firestore (or by user-confirmed values from an uploaded agreement)
    without changing a single call site in the rule engine.
    """

    def __init__(
        self,
        rest_rules: tuple[RestRule, ...] = REST_RULES,
        meal_penalties: dict[PerformerCategory, PenaltySchedule] | None = None,
        forced_call: dict[ContractType, ForcedCallPenalty] | None = None,
        scale_rates: dict[BudgetTier, ScaleRate] | None = None,
        meal_timing: MealTimingRule = MEAL_TIMING,
        weekly_rest: WeeklyRestRule = WEEKLY_REST,
    ) -> None:
        self.rest_rules = rest_rules
        self.meal_penalties = meal_penalties or MEAL_PENALTY_BY_CATEGORY
        self.forced_call = forced_call or FORCED_CALL_PENALTIES
        self.scale_rates = scale_rates or SCALE_RATES
        self.meal_timing = meal_timing
        self.weekly_rest = weekly_rest

    def rest_rule(
        self,
        category: PerformerCategory,
        zone: Zone,
        production_type: ProductionType,
    ) -> RestRule:
        """Most-specific matching rest rule.

        Stunt performers are downgraded to NEEDS_SOURCE_CHECK: they fall
        through to the standard zone rule, but we have no source confirming
        that is correct, and the UI should say so rather than imply
        certainty we do not have.
        """
        for rule in self.rest_rules:
            if rule.performer_category is not None and rule.performer_category is not category:
                continue
            if rule.zone is not None and rule.zone is not zone:
                continue
            if rule.production_type is not None and rule.production_type is not production_type:
                continue

            if category is PerformerCategory.STUNT_PERFORMER:
                return RestRule(
                    performer_category=category,
                    zone=rule.zone,
                    production_type=rule.production_type,
                    base_hours=rule.base_hours,
                    reduction=rule.reduction,
                    source_url=rule.source_url,
                    rule_version=rule.rule_version,
                    confidence=Confidence.NEEDS_SOURCE_CHECK,
                )
            return rule

        raise LookupError(
            f"No rest rule for category={category}, zone={zone}, "
            f"production_type={production_type}"
        )

    def meal_penalty_schedule(self, category: PerformerCategory) -> PenaltySchedule:
        return self.meal_penalties[category]

    def forced_call_penalty(self, contract_type: ContractType) -> ForcedCallPenalty:
        return self.forced_call[contract_type]

    def scale_rate(self, budget_tier: BudgetTier) -> ScaleRate:
        return self.scale_rates[budget_tier]

    def unverified_rules(self) -> list[str]:
        """Everything currently flagged NEEDS_SOURCE_CHECK.

        Intended to be surfaced in the UI and mentioned in the demo — an
        honest compliance tool states the limits of what it knows.
        """
        flagged: list[str] = []
        if self.meal_timing.confidence is Confidence.NEEDS_SOURCE_CHECK:
            flagged.append("meal timing sub-rules (grace period, extension, NDB)")
        for tier, rate in self.scale_rates.items():
            if rate.confidence is Confidence.NEEDS_SOURCE_CHECK:
                flagged.append(f"scale rate for {tier}")
        flagged.append("stunt performer rest threshold (falls back to zone default)")
        return flagged
