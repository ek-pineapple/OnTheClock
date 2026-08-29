"""Domain types — the nouns of the problem.

Every other ring speaks this vocabulary. These are plain data holders:
they carry no compliance logic and know nothing about Grafana, Firestore,
or the agent.

Two conventions that hold throughout:

* **All datetimes are timezone-aware and stored in UTC.** A naive datetime
  is rejected at construction. A production's local timezone lives on
  `Production.timezone` and is applied only at display time. Getting this
  wrong is the classic way a clock-based system quietly produces garbage.

* **All money is `Decimal`, never `float`.** Meal penalties accumulate in
  $7.50 steps; binary floats cannot represent that exactly, and this is a
  payroll-adjacent tool where the number is the product.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

# --------------------------------------------------------------------------
# Enumerations
# --------------------------------------------------------------------------


class PerformerCategory(StrEnum):
    PRINCIPAL = "principal"
    BACKGROUND = "background"
    STUNT_COORDINATOR = "stunt_coordinator"
    STUNT_PERFORMER = "stunt_performer"


class ContractType(StrEnum):
    """Drives which forced-call cap applies ($900 vs $950)."""

    DAY = "day"
    THREE_DAY = "three_day"
    WEEKLY = "weekly"


class Zone(StrEnum):
    """Where the work happens. Drives which rest reduction is available."""

    STUDIO = "studio"
    DISTANT = "distant"
    OVERNIGHT = "overnight"


class ProductionType(StrEnum):
    """Theatrical and television have *different* overnight rest rules.

    Theatrical permits a 12h -> 11h reduction on two non-consecutive days
    per workweek. Television permits no overnight reduction at all.
    """

    THEATRICAL = "theatrical"
    TELEVISION = "television"


class BudgetTier(StrEnum):
    """Sets scale daily rates, which in turn cap forced-call penalties.

    Named to avoid colliding with `ProductionType.THEATRICAL` — the
    industry uses the word "theatrical" for both the medium and the
    top budget tier.
    """

    BASIC_THEATRICAL = "basic_theatrical"
    LOW_BUDGET = "low_budget"
    MODERATE_LOW_BUDGET = "moderate_low_budget"
    ULTRA_LOW_BUDGET = "ultra_low_budget"
    STUDENT_SHORT = "student_short"


class SubjectType(StrEnum):
    """What a shift or violation attaches to.

    Principals are tracked individually. Background performers are called
    and released in batches, so the monitored unit is the call group —
    though each individual still exists as a `Person` for the audit trail.
    """

    PERSON = "person"
    CALL_GROUP = "call_group"


class ShiftStatus(StrEnum):
    SCHEDULED = "scheduled"
    ON_CLOCK = "on_clock"
    WRAPPED = "wrapped"


class ViolationType(StrEnum):
    FORCED_CALL = "forced_call"
    MEAL_PENALTY = "meal_penalty"
    SCHEDULE_MISMATCH = "schedule_mismatch"


class ComplianceState(StrEnum):
    """The *live verdict* from a rule evaluation.

    Distinct from `ViolationStatus`, which is the lifecycle of a stored
    record. A subject can be CLEAR right now while a VIOLATED record from
    two hours ago still stands.
    """

    CLEAR = "clear"
    APPROACHING = "approaching"
    VIOLATED = "violated"


class ViolationStatus(StrEnum):
    """The lifecycle of a persisted violation record.

    CLEARED is a success state — the crew was warned and fixed it before
    the threshold was crossed. Surfacing those is how the product proves
    it is worth running.
    """

    APPROACHING = "approaching"
    VIOLATED = "violated"
    CLEARED = "cleared"
    RESOLVED = "resolved"


# Known SAG-AFTRA contract schedules relevant to the misclassification check.
# Stunt performers work under the weekly Schedule H-II; K-III is the stunt
# *coordinator's* flat-deal schedule. A stunt performer tagged K-III is the
# documented misclassification pattern.
SCHEDULE_H_II = "H-II"
SCHEDULE_K_III = "K-III"


# --------------------------------------------------------------------------
# Validation helper
# --------------------------------------------------------------------------


def _require_utc(value: datetime | None, field_name: str) -> None:
    """Reject naive datetimes at construction rather than at 2am mid-demo."""
    if value is None:
        return
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(
            f"{field_name} must be timezone-aware (UTC). "
            f"Got naive datetime {value!r}."
        )


# --------------------------------------------------------------------------
# Entities
# --------------------------------------------------------------------------


@dataclass
class Production:
    production_id: str
    title: str
    production_type: ProductionType
    budget_tier: BudgetTier
    # IANA, e.g. "America/Los_Angeles". Used for display, and for the one
    # rule that is stated in local terms: the weekly-rest reduction requires
    # the new workweek's first call to be no earlier than 6am *locally*.
    timezone: str
    shoot_start: date
    shoot_end: date

    # Which weekday the production's workweek starts on (0 = Monday).
    # Weekly rest is measured between workweeks, so the boundary has to be
    # declared rather than guessed.
    workweek_start_weekday: int = 0

    is_synthetic: bool = True


@dataclass(frozen=True)
class ContractRate:
    """What a subject is paid, and over how many days.

    A forced call costs "one day's pay, or the cap, whichever is less" — so
    the system needs one day's pay, which is *not* the same thing as a daily
    scale rate for anyone on a multi-day contract. A weekly performer at
    Basic Theatrical scale earns $4,456/week, so one day's pay is $891.20 and
    the penalty is $891.20 — not the $950 cap, and not the $1,283 day rate.

    Modelled as a rate over a period rather than as separate `daily_rate` /
    `weekly_rate` fields for two reasons: it collapses day, three-day, and
    weekly contracts into one uniform calculation, and it mirrors how an
    agreement actually states terms ("weekly performer: $4,456/week") — which
    is the shape an uploaded-contract extraction will need to produce.
    """

    period_rate_usd: Decimal
    days_in_period: int

    def __post_init__(self) -> None:
        if self.days_in_period < 1:
            raise ValueError("ContractRate.days_in_period must be >= 1")

    @property
    def one_days_pay_usd(self) -> Decimal:
        """The basis for forced-call pricing, rounded to cents."""
        return (self.period_rate_usd / self.days_in_period).quantize(Decimal("0.01"))

    @classmethod
    def daily(cls, rate_usd: Decimal) -> ContractRate:
        return cls(period_rate_usd=rate_usd, days_in_period=1)

    @classmethod
    def weekly(cls, rate_usd: Decimal, days_in_workweek: int = 5) -> ContractRate:
        return cls(period_rate_usd=rate_usd, days_in_period=days_in_workweek)

    @classmethod
    def three_day(cls, rate_usd: Decimal) -> ContractRate:
        return cls(period_rate_usd=rate_usd, days_in_period=3)


@dataclass
class Person:
    person_id: str
    production_id: str
    name: str
    performer_category: PerformerCategory
    contract_type: ContractType

    # Required to price a forced call, which is min(one day's pay, cap).
    # An Ultra Low Budget performer on $256.60/day incurs $256.60 — not the
    # $900 headline figure. Without this the penalty is uncomputable, which
    # is why it is not optional.
    contract_rate: ContractRate

    # e.g. "H-II" / "K-III". Only meaningful for stunt roles.
    contract_schedule: str | None = None

    # Set for background performers; identifies which batch they were
    # called with. Their clock is tracked at the group level.
    call_group_id: str | None = None

    active: bool = True

    @property
    def one_days_pay_usd(self) -> Decimal:
        """Forced-call pricing basis. Correct for day, three-day, and weekly."""
        return self.contract_rate.one_days_pay_usd


@dataclass
class CallGroup:
    """A batch of background performers sharing one clock.

    The group is what gets monitored; `headcount` multiplies the per-capita
    penalty into total exposure.
    """

    call_group_id: str
    production_id: str
    label: str
    headcount: int
    contract_rate: ContractRate
    performer_category: PerformerCategory = PerformerCategory.BACKGROUND
    contract_type: ContractType = ContractType.DAY

    @property
    def one_days_pay_usd(self) -> Decimal:
        return self.contract_rate.one_days_pay_usd


@dataclass
class MealBreak:
    index: int  # 1 = first meal, 2 = second meal
    out_at: datetime
    in_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_utc(self.out_at, "MealBreak.out_at")
        _require_utc(self.in_at, "MealBreak.in_at")

    @property
    def is_complete(self) -> bool:
        """A break that has been returned from. An open break stops the clock."""
        return self.in_at is not None


@dataclass
class NonDeductibleMeal:
    """The 15-minute NDB taken near call time.

    Modelled as its own record rather than a boolean because the rule
    engine needs its *window* to avoid false-positiving on early arrivals.
    """

    start_at: datetime
    end_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_utc(self.start_at, "NonDeductibleMeal.start_at")
        _require_utc(self.end_at, "NonDeductibleMeal.end_at")


@dataclass
class Shift:
    """One subject, one shoot day. The core record.

    Note what is *not* here: rest hours, violation state, or any computed
    field. Rest is a pairwise computation across two consecutive shifts
    (see Ring 1), and storing a derived value would let it drift the moment
    any record is corrected.
    """

    shift_id: str
    production_id: str
    subject_type: SubjectType
    subject_id: str
    shoot_day: int  # 1-based; never derive this from a date, since a
    # shoot day routinely crosses midnight
    date: date  # production-local calendar date, for display
    zone: Zone

    # Known in advance, from the call sheet. This is what makes the product
    # predictive rather than forensic: a forced call is detectable the
    # moment the schedule is published, hours before it happens.
    scheduled_call_at: datetime

    actual_call_at: datetime | None = None
    dismissed_at: datetime | None = None

    # Required for the distant-location reduction, which is only available
    # when exterior photography happens on the day before AND the day after.
    exterior_photography: bool = False

    non_deductible_meal: NonDeductibleMeal | None = None
    meals: list[MealBreak] = field(default_factory=list)

    # Meal periods (by index) for which a legitimate extension was granted.
    # Lives on the shift rather than on `MealBreak` because an extension is
    # granted *before* the break is taken — at which point no MealBreak
    # record exists yet to carry the flag.
    meal_extensions_granted: set[int] = field(default_factory=set)

    # Only set when subject_type is CALL_GROUP.
    headcount: int | None = None

    status: ShiftStatus = ShiftStatus.SCHEDULED

    def __post_init__(self) -> None:
        _require_utc(self.scheduled_call_at, "Shift.scheduled_call_at")
        _require_utc(self.actual_call_at, "Shift.actual_call_at")
        _require_utc(self.dismissed_at, "Shift.dismissed_at")

    @property
    def effective_call_at(self) -> datetime:
        """When the meal clock actually starts.

        Falls back to the scheduled call for shifts that have not begun,
        so a countdown can be displayed before anyone arrives.
        """
        return self.actual_call_at or self.scheduled_call_at

    @property
    def subject_headcount(self) -> int:
        """1 for an individual, N for a call group.

        Unifying these means exposure is always `per_capita x headcount`
        with no branching anywhere downstream — a person is just a call
        group of one.
        """
        return self.headcount if self.subject_type is SubjectType.CALL_GROUP else 1


# --------------------------------------------------------------------------
# Outputs
# --------------------------------------------------------------------------


@dataclass
class ComplianceCheck:
    """The result of evaluating one rule against one subject, right now.

    This is what Ring 1 returns, what the emitter turns into metrics, and
    what the agent narrates. It is deliberately not persisted — it is a
    snapshot of a live verdict.
    """

    subject_type: SubjectType
    subject_id: str
    production_id: str
    violation_type: ViolationType
    state: ComplianceState

    # The instant the line is (or was) crossed. Emitted to Prometheus as an
    # absolute unix timestamp so the dashboard can compute `deadline - now()`
    # and tick smoothly at display refresh rate rather than at emit rate.
    deadline_at: datetime | None = None

    # Seconds from `now` to `deadline_at` — i.e. the countdown the UI shows.
    # Negative means the deadline is already behind us.
    margin_seconds: float | None = None

    # How badly the rule is breached, independent of the countdown. For a
    # forced call this is how far short of the required rest the schedule
    # falls, which is what a 2nd AD needs in order to fix it ("move the call
    # 2h 15m later"). Zero or None when compliant.
    shortfall_seconds: float | None = None

    half_hour_increments: int = 0
    per_capita_penalty_usd: Decimal = Decimal("0.00")
    headcount: int = 1

    # Provenance, carried from the rulebook so the agent can cite itself
    # and the UI can flag unverified rules.
    rule_version: str = ""
    source_url: str = ""
    rule_confidence: str = ""

    explanation: str = ""

    @property
    def total_penalty_usd(self) -> Decimal:
        return self.per_capita_penalty_usd * self.headcount


@dataclass
class Violation:
    """A persisted violation record — the audit trail.

    Rates and provenance are snapshotted at detection time. If a future
    agreement changes the schedule, historical violations must not silently
    revalue themselves.
    """

    violation_id: str
    production_id: str
    violation_type: ViolationType
    subject_type: SubjectType
    subject_id: str
    shift_id: str
    shoot_day: int
    status: ViolationStatus

    threshold_at: datetime
    detected_at: datetime
    computed_at: datetime

    half_hour_increments: int = 0
    headcount_at_detection: int = 1
    per_capita_penalty_usd: Decimal = Decimal("0.00")
    total_penalty_usd: Decimal = Decimal("0.00")

    rule_version: str = ""
    source_url: str = ""
    narrative: str = ""

    def __post_init__(self) -> None:
        _require_utc(self.threshold_at, "Violation.threshold_at")
        _require_utc(self.detected_at, "Violation.detected_at")
        _require_utc(self.computed_at, "Violation.computed_at")
