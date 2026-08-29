"""Synthetic production data generator.

Produces two fictional productions across three shoot weeks, with violations
**planted at known coordinates** rather than left to chance. Random generation
will not reliably produce a stunt performer on the wrong contract schedule, or
a distant-location stretch that exercises the once-every-fourth-day reduction.
The demo needs those to exist on purpose.

Two properties are deliberate:

* **Deterministic.** Seeded, so the same command produces the same shoot every
  time. A demo that reshuffles itself between rehearsal and recording is not
  a demo.
* **Validated on the way out.** Every shift goes through `core.validation`
  before it is written. Ring 1 testing showed how easily a shift's `date` and
  its UTC timestamps drift apart, and how convincingly wrong the resulting
  verdict looks.

All productions, performers, and schedules are fictional.

    python -m services.ingestion.generate            # summary
    python -m services.ingestion.generate --verbose  # every planted anomaly
"""

from __future__ import annotations

import random
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from adapters.memory import InMemoryRepository
from adapters.repository import Repository
from core.models import (
    BudgetTier,
    CallGroup,
    ContractRate,
    ContractType,
    MealBreak,
    NonDeductibleMeal,
    PerformerCategory,
    Person,
    Production,
    ProductionType,
    Shift,
    ShiftStatus,
    SubjectType,
    Zone,
    SCHEDULE_H_II,
    SCHEDULE_K_III,
)
from core.validation import validate_sequence

LA = ZoneInfo("America/Los_Angeles")

#: Three workweeks, weekends off: 7-11, 14-18, 21-24 September.
SHOOT_DAYS: list[date] = (
    [date(2026, 9, d) for d in range(7, 12)]
    + [date(2026, 9, d) for d in range(14, 19)]
    + [date(2026, 9, d) for d in range(21, 25)]
)

#: "Now" for the demo — Tuesday of week three, mid-afternoon. Late enough that
#: two weekly-rest boundaries and a distant-location stretch are already
#: behind us, so history exists to reason about.
NOW = datetime(2026, 9, 22, 12, 40, tzinfo=LA).astimezone(ZoneInfo("UTC"))
TODAY = date(2026, 9, 22)


def at(day: date, hour: int, minute: int = 0) -> datetime:
    """A production-local wall clock time, stored as UTC."""
    return datetime.combine(day, time(hour, minute), tzinfo=LA).astimezone(
        ZoneInfo("UTC")
    )


# ---------------------------------------------------------------------------
# Planted anomalies
# ---------------------------------------------------------------------------


@dataclass
class Plant:
    """One deliberate anomaly, so the demo is reproducible and explainable."""

    subject_id: str
    on: date
    kind: str
    note: str


PLANTS: list[Plant] = [
    Plant("p_dana", TODAY, "no_meal",
          "Called 06:00, no meal break taken — penalty accruing by 12:40."),
    Plant("p_kwame", TODAY, "turnaround_tonight",
          "Wrapped 11:00, called back 21:00 for a night exterior — 10h "
          "turnaround against a 12h entitlement, still fixable."),
    Plant("p_tobias", TODAY, "ndb_only",
          "Took a 15-minute non-deductible meal at 07:30 and nothing since; "
          "the NDB must not reset the six-hour clock."),
    Plant("p_nina", TODAY, "schedule_mismatch",
          "Stunt performer booked on K-III, the coordinator's flat-deal "
          "schedule, instead of weekly H-II."),
    Plant("bg_diner", TODAY, "no_meal",
          "85 background performers, called 06:00, no meal break."),
    # Weekly rest is evaluated at the *first* shift of a new workweek, so the
    # plant belongs on the Monday, not on the Friday that precedes it.
    Plant("p_marcus", date(2026, 9, 21), "short_weekly_rest",
          "Wrapped Friday 22:00, called back Monday 05:00 — inside the 56h "
          "weekly rest period."),
    Plant("p_sam", TODAY, "forced_call_past",
          "Wrapped 01:00, called 09:00 — four hours short, call already "
          "passed, no longer preventable."),
]


# ---------------------------------------------------------------------------
# Rosters
# ---------------------------------------------------------------------------

PROD_A = Production(
    production_id="prod_long_night",
    title="The Long Night",
    production_type=ProductionType.THEATRICAL,
    budget_tier=BudgetTier.BASIC_THEATRICAL,
    timezone="America/Los_Angeles",
    shoot_start=SHOOT_DAYS[0],
    shoot_end=SHOOT_DAYS[-1],
)

PROD_B = Production(
    production_id="prod_paper_boats",
    title="Paper Boats",
    production_type=ProductionType.TELEVISION,
    budget_tier=BudgetTier.ULTRA_LOW_BUDGET,
    timezone="America/Los_Angeles",
    shoot_start=SHOOT_DAYS[0],
    shoot_end=SHOOT_DAYS[-1],
)

DAY_A = ContractRate.daily(Decimal("1283.00"))
WEEK_A = ContractRate.weekly(Decimal("4456.00"))
DAY_B = ContractRate.daily(Decimal("256.60"))

P = PerformerCategory
C = ContractType


@dataclass
class Cast:
    person: Person
    call_hour: int = 6
    #: Local dates this performer works. Defaults to the whole shoot.
    days: list[date] = field(default_factory=lambda: list(SHOOT_DAYS))


def _p(pid, prod, name, cat, ct, rate, sched=None) -> Person:
    return Person(
        person_id=pid, production_id=prod.production_id, name=name,
        performer_category=cat, contract_type=ct, contract_rate=rate,
        contract_schedule=sched,
    )


def roster_a() -> list[Cast]:
    return [
        Cast(_p("p_dana", PROD_A, "Dana Reyes", P.PRINCIPAL, C.WEEKLY, WEEK_A)),
        Cast(_p("p_kwame", PROD_A, "Kwame Adeyemi", P.PRINCIPAL, C.DAY, DAY_A)),
        Cast(_p("p_priya", PROD_A, "Priya Raghunathan", P.PRINCIPAL, C.DAY, DAY_A), 7),
        Cast(_p("p_marcus", PROD_A, "Marcus Ellery", P.PRINCIPAL, C.WEEKLY, WEEK_A), 7),
        Cast(_p("p_ines", PROD_A, "Inés Barrantes", P.PRINCIPAL, C.THREE_DAY,
                ContractRate.three_day(Decimal("3249.00"))), 8),
        Cast(_p("p_tobias", PROD_A, "Tobias Vane", P.STUNT_COORDINATOR, C.DAY, DAY_A), 7),
        Cast(_p("p_nina", PROD_A, "Nina Okafor", P.STUNT_PERFORMER, C.WEEKLY, WEEK_A,
                SCHEDULE_K_III), 8),
        Cast(_p("p_ravi", PROD_A, "Ravi Sundaram", P.STUNT_PERFORMER, C.WEEKLY, WEEK_A,
                SCHEDULE_H_II), 8),
    ]


def roster_b() -> list[Cast]:
    return [
        Cast(_p("p_sam", PROD_B, "Sam Okonkwo", P.PRINCIPAL, C.DAY, DAY_B), 9),
        Cast(_p("p_ruth", PROD_B, "Ruth Vela", P.PRINCIPAL, C.DAY, DAY_B), 10),
        Cast(_p("p_yusuf", PROD_B, "Yusuf Demirtaş", P.PRINCIPAL, C.WEEKLY,
                ContractRate.weekly(Decimal("1283.00"))), 9),
    ]


def call_groups() -> list[tuple[CallGroup, int, list[date]]]:
    """(group, call hour, days worked) — background is called in batches."""
    return [
        (CallGroup("bg_diner", PROD_A.production_id, "Background — Diner Ext.",
                   85, ContractRate.daily(Decimal("221.00"))), 6, [TODAY]),
        (CallGroup("bg_precinct", PROD_A.production_id, "Background — Precinct Int.",
                   120, ContractRate.daily(Decimal("221.00"))), 7,
         [date(2026, 9, 16), date(2026, 9, 17)]),
        (CallGroup("bg_market", PROD_B.production_id, "Background — Market Sq.",
                   40, ContractRate.daily(Decimal("187.00"))), 9,
         [date(2026, 9, 21), TODAY]),
    ]


# ---------------------------------------------------------------------------
# Shift construction
# ---------------------------------------------------------------------------


#: Week two goes on location, which is what makes the distant-location
#: reduction (10h, exterior photography either side, once every fourth
#: consecutive day) reachable at all.
LOCATION_WEEK = [date(2026, 9, d) for d in range(14, 19)]


def _zone_for(day: date) -> Zone:
    return Zone.DISTANT if day in LOCATION_WEEK else Zone.STUDIO


#: Only these plants need the *previous* day to wrap late. The others build
#: their violation inside their own day, so exempting the day before them
#: would let a random long shift add a forced call nobody planted.
_NEEDS_SHORT_TURNAROUND = {"forced_call_past", "short_weekly_rest"}


def _plants_for(subject_id: str, day: date) -> list[Plant]:
    return [p for p in PLANTS if p.subject_id == subject_id and p.on == day]


def _build_shifts(
    subject_id: str,
    subject_type: SubjectType,
    production: Production,
    call_hour: int,
    days: list[date],
    rng: random.Random,
    headcount: int | None = None,
) -> list[Shift]:
    shifts: list[Shift] = []

    # First pass: fix every call time, so a dismissal can be constrained by
    # the call that follows it.
    calls: list[datetime] = []
    for day in days:
        kinds = {p.kind for p in _plants_for(subject_id, day)}
        call_h = call_hour
        if "forced_call_past" in kinds:
            call_h = 9
        if "short_weekly_rest" in kinds:
            call_h = 5
        # Ordinary days get a few minutes of jitter so the data does not look
        # machine-stamped. Planted days get exact times — a 30-minute drift
        # is enough to slide a deliberate 40-minute overrun back inside the
        # grace period and quietly disarm the scenario.
        minute = 0 if kinds else rng.choice([0, 0, 15, 30])
        calls.append(at(day, call_h, minute))

    for day_no, (day, call) in enumerate(zip(days, calls), start=1):
        kinds = {p.kind for p in _plants_for(subject_id, day)}
        zone = _zone_for(day)
        is_today = day == TODAY
        future = day > TODAY

        # -- dismissal -----------------------------------------------------
        if future or is_today:
            dismissed = None
            status = ShiftStatus.SCHEDULED if future else ShiftStatus.ON_CLOCK
        else:
            hours = rng.choice([10, 11, 12, 12, 13, 14])
            dismissed = call + timedelta(hours=hours)

            # Friday of week two runs long, which is what sets up the tight
            # Monday turnaround planted on Marcus.
            if day == date(2026, 9, 18):
                dismissed = at(day, 22)

            # Compliant by default. A generated shoot should only violate
            # where a Plant says so — otherwise random long days collide
            # with next-day calls and the demo grows violations nobody can
            # explain. The planted anomalies are exempt, since producing a
            # short turnaround is precisely their job.
            if day_no < len(days):
                next_kinds = {p.kind for p in _plants_for(subject_id, days[day_no])}
                latest_legal = calls[day_no] - timedelta(hours=12)

                if "forced_call_past" in next_kinds:
                    # The plant lives on tomorrow, but the violation is made
                    # here: wrap late enough that tomorrow's call is four
                    # hours short of the entitlement.
                    dismissed = calls[day_no] - timedelta(hours=8)
                elif not (next_kinds & _NEEDS_SHORT_TURNAROUND):
                    floor = call + timedelta(hours=6)
                    dismissed = max(floor, min(dismissed, latest_legal))

            status = ShiftStatus.WRAPPED

        if "turnaround_tonight" in kinds:
            # Two shifts on one calendar day: a day block that wraps early,
            # then a night exterior called back too soon.
            shifts.append(_shift(production, subject_id, subject_type, day_no, day,
                                 zone, call, at(day, 11), ShiftStatus.WRAPPED,
                                 headcount=headcount))
            shifts.append(_shift(production, subject_id, subject_type, day_no, day,
                                 zone, at(day, 21), None, ShiftStatus.SCHEDULED,
                                 headcount=headcount))
            continue

        meals, ndb, extensions = _meals_for(kinds, call, dismissed, is_today, rng)

        shifts.append(_shift(
            production, subject_id, subject_type, day_no, day, zone, call,
            dismissed, status, meals=meals, ndb=ndb, extensions=extensions,
            exterior=zone is Zone.DISTANT, headcount=headcount,
        ))

    return shifts


def _meals_for(kinds, call, dismissed, is_today, rng):
    """Meal records for one shift. Planted anomalies win over normal patterns."""
    if "no_meal" in kinds:
        return [], None, set()

    if "ndb_only" in kinds:
        ndb = NonDeductibleMeal(call + timedelta(minutes=30), call + timedelta(minutes=45))
        # Deliberately *also* logged as an ordinary break — the realistic data
        # error the NDB check exists to survive.
        return [MealBreak(1, ndb.start_at, ndb.end_at)], ndb, set()

    meals = [MealBreak(1, call + timedelta(hours=5, minutes=rng.choice([0, 20, 40])),
                       None)]
    meals[0].in_at = meals[0].out_at + timedelta(minutes=30)

    if dismissed and dismissed - meals[0].in_at > timedelta(hours=6):
        second_out = meals[0].in_at + timedelta(hours=5, minutes=30)
        meals.append(MealBreak(2, second_out, second_out + timedelta(minutes=30)))

    if is_today:
        # Mid-afternoon: the first meal is done, the second is not yet due.
        meals = [m for m in meals if m.out_at <= NOW]

    extensions = {1} if rng.random() < 0.08 else set()
    return meals, None, extensions


def _shift(production, subject_id, subject_type, day_no, day, zone, call,
           dismissed, status, meals=None, ndb=None, extensions=None,
           exterior=False, headcount=None) -> Shift:
    suffix = "n" if call.astimezone(LA).hour >= 18 else "d"
    return Shift(
        shift_id=f"sh_{subject_id}_{day.isoformat()}_{suffix}",
        production_id=production.production_id,
        subject_type=subject_type,
        subject_id=subject_id,
        shoot_day=day_no,
        date=day,
        zone=zone,
        scheduled_call_at=call,
        actual_call_at=call if status is not ShiftStatus.SCHEDULED else None,
        dismissed_at=dismissed,
        exterior_photography=exterior,
        non_deductible_meal=ndb,
        meals=meals or [],
        meal_extensions_granted=extensions or set(),
        headcount=headcount,
        status=status,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def generate(repo: Repository, seed: int = 7) -> dict:
    """Populate `repo` with the full synthetic shoot. Returns a summary."""
    rng = random.Random(seed)
    counts = {"productions": 0, "people": 0, "call_groups": 0, "shifts": 0}

    for production, roster in ((PROD_A, roster_a()), (PROD_B, roster_b())):
        repo.put_production(production)
        counts["productions"] += 1

        for cast in roster:
            repo.put_person(cast.person)
            counts["people"] += 1
            shifts = _build_shifts(
                cast.person.person_id, SubjectType.PERSON, production,
                cast.call_hour, cast.days, rng,
            )
            # Fail here rather than let a malformed shift reach the rules.
            validate_sequence(shifts, production)
            for s in shifts:
                repo.put_shift(s)
            counts["shifts"] += len(shifts)

    for group, call_hour, days in call_groups():
        production = PROD_A if group.production_id == PROD_A.production_id else PROD_B
        repo.put_call_group(group)
        counts["call_groups"] += 1
        shifts = _build_shifts(
            group.call_group_id, SubjectType.CALL_GROUP, production,
            call_hour, days, rng, headcount=group.headcount,
        )
        validate_sequence(shifts, production)
        for s in shifts:
            repo.put_shift(s)
        counts["shifts"] += len(shifts)

    counts["tracked_people"] = sum(
        1 for _, r in ((PROD_A, roster_a()), (PROD_B, roster_b())) for _ in r
    ) + sum(g.headcount for g, _, _ in call_groups())
    return counts


if __name__ == "__main__":
    repo = InMemoryRepository()
    summary = generate(repo)

    print(f"\n  Synthetic shoot generated — {len(SHOOT_DAYS)} shoot days, "
          f"{SHOOT_DAYS[0]} to {SHOOT_DAYS[-1]}")
    print(f"  now = {NOW.astimezone(LA):%A %d %B %Y, %H:%M} local\n")
    for k, v in summary.items():
        print(f"    {k:<16} {v}")

    print("\n  Planted anomalies:")
    for p in PLANTS:
        print(f"    {p.on}  {p.subject_id:<14} {p.kind}")
        if "--verbose" in sys.argv:
            print(f"      {p.note}")
    print()
