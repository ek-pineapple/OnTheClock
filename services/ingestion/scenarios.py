"""Hand-built demo scenarios.

Random generation will not reliably produce a person who is 40 minutes into a
meal penalty while another is eight hours from a fixable forced call. The demo
needs those states to exist on purpose, so this module builds them.

Everything here is fictional. Names, productions, and schedules are invented
to reflect realistic industry patterns; no real production's data is used.

Run it to see the rule engine's actual output:

    python -m services.ingestion.scenarios          # human-readable
    python -m services.ingestion.scenarios --json   # for the frontend
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from core.models import (
    BudgetTier,
    CallGroup,
    ComplianceState,
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
from core.rulebook import RuleBook
from core.rules import evaluate_subject

LA = ZoneInfo("America/Los_Angeles")

#: The demo is anchored to a fixed instant so the numbers are reproducible.
#: 12:40 local on the second day of the shoot — mid-afternoon, meal clocks
#: running, tomorrow's call sheet already published.
NOW = datetime(2026, 9, 15, 19, 40, tzinfo=timezone.utc)

MON = date(2026, 9, 14)
TUE = date(2026, 9, 15)
WED = date(2026, 9, 16)


def local(day: date, hour: int, minute: int = 0) -> datetime:
    """A production-local wall-clock time, stored as UTC."""
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=LA).astimezone(
        timezone.utc
    )


@dataclass
class Booking:
    """One subject plus their shift sequence."""

    subject: Person | CallGroup
    shifts: list[Shift]
    index: int  # which shift is "current"


# ---------------------------------------------------------------------------
# Production A — studio picture, top budget tier
# ---------------------------------------------------------------------------

PROD_A = Production(
    production_id="prod_long_night",
    title="The Long Night",
    production_type=ProductionType.THEATRICAL,
    budget_tier=BudgetTier.BASIC_THEATRICAL,
    timezone="America/Los_Angeles",
    shoot_start=date(2026, 9, 7),
    shoot_end=date(2026, 10, 16),
)

# Production B — micro-budget indie. Same rules, very different prices.
PROD_B = Production(
    production_id="prod_paper_boats",
    title="Paper Boats",
    production_type=ProductionType.THEATRICAL,
    budget_tier=BudgetTier.ULTRA_LOW_BUDGET,
    timezone="America/Los_Angeles",
    shoot_start=date(2026, 9, 9),
    shoot_end=date(2026, 9, 30),
)

SCALE_A = Decimal("1283.00")
WEEKLY_A = Decimal("4456.00")
SCALE_B = Decimal("256.60")


def _person(pid, prod, name, category, contract, rate, schedule=None) -> Person:
    return Person(
        person_id=pid,
        production_id=prod.production_id,
        name=name,
        performer_category=category,
        contract_type=contract,
        contract_rate=rate,
        contract_schedule=schedule,
    )


def _shift(prod, sid, subject_id, day_no, on, call, dismissed=None, **kw) -> Shift:
    return Shift(
        shift_id=sid,
        production_id=prod.production_id,
        subject_type=kw.pop("subject_type", SubjectType.PERSON),
        subject_id=subject_id,
        shoot_day=day_no,
        date=on,
        zone=kw.pop("zone", Zone.STUDIO),
        scheduled_call_at=call,
        actual_call_at=kw.pop("actual_call_at", call),
        dismissed_at=dismissed,
        **kw,
    )


def bookings() -> list[tuple[Production, Booking]]:
    out: list[tuple[Production, Booking]] = []

    # -- Dana Reyes — meal penalty already running -------------------------
    # Weekly performer, so her forced-call exposure is one day's pay
    # ($891.20), not the $950 cap. Called 06:00, no meal taken by 12:40.
    dana = _person(
        "p_dana", PROD_A, "Dana Reyes", PerformerCategory.PRINCIPAL,
        ContractType.WEEKLY, ContractRate.weekly(WEEKLY_A),
    )
    out.append((PROD_A, Booking(dana, [
        _shift(PROD_A, "sh_dana_1", "p_dana", 6, MON, local(MON, 6),
               dismissed=local(MON, 18), status=ShiftStatus.WRAPPED),
        _shift(PROD_A, "sh_dana_2", "p_dana", 7, TUE, local(TUE, 6),
               status=ShiftStatus.ON_CLOCK),
    ], 1)))

    # -- Kwame Adeyemi — forced call scheduled, still fixable --------------
    # Wrapped at 11:00 from a day shoot, called back 21:00 the same evening
    # for a night exterior. Ten hours' turnaround against a 12-hour
    # entitlement. The call has not happened, so someone can still move it.
    kwame = _person(
        "p_kwame", PROD_A, "Kwame Adeyemi", PerformerCategory.PRINCIPAL,
        ContractType.DAY, ContractRate.daily(SCALE_A),
    )
    out.append((PROD_A, Booking(kwame, [
        _shift(PROD_A, "sh_kwame_1", "p_kwame", 7, TUE, local(TUE, 6),
               dismissed=local(TUE, 11), status=ShiftStatus.WRAPPED),
        _shift(PROD_A, "sh_kwame_2", "p_kwame", 7, TUE, local(TUE, 21)),
    ], 1)))

    # -- Priya Raghunathan — fully compliant -------------------------------
    priya = _person(
        "p_priya", PROD_A, "Priya Raghunathan", PerformerCategory.PRINCIPAL,
        ContractType.DAY, ContractRate.daily(SCALE_A),
    )
    out.append((PROD_A, Booking(priya, [
        _shift(PROD_A, "sh_priya_1", "p_priya", 6, MON, local(MON, 7),
               dismissed=local(MON, 17), status=ShiftStatus.WRAPPED),
        _shift(PROD_A, "sh_priya_2", "p_priya", 7, TUE, local(TUE, 7),
               status=ShiftStatus.ON_CLOCK,
               meals=[MealBreak(1, local(TUE, 11, 30), local(TUE, 12))]),
    ], 1)))

    # -- Tobias Vane — stunt coordinator, and an NDB that must not count ---
    # Took a 15-minute non-deductible meal at 07:30. If that were treated as
    # his first meal his deadline would slide to 13:45 and the approaching
    # penalty would be missed entirely.
    tobias = _person(
        "p_tobias", PROD_A, "Tobias Vane", PerformerCategory.STUNT_COORDINATOR,
        ContractType.DAY, ContractRate.daily(SCALE_A),
    )
    out.append((PROD_A, Booking(tobias, [
        _shift(PROD_A, "sh_tobias_1", "p_tobias", 6, MON, local(MON, 8),
               dismissed=local(MON, 22), status=ShiftStatus.WRAPPED),
        _shift(PROD_A, "sh_tobias_2", "p_tobias", 7, TUE, local(TUE, 7),
               status=ShiftStatus.ON_CLOCK,
               non_deductible_meal=NonDeductibleMeal(local(TUE, 7, 30), local(TUE, 7, 45)),
               meals=[MealBreak(1, local(TUE, 7, 30), local(TUE, 7, 45))]),
    ], 1)))

    # -- Nina Okafor — misclassified stunt performer -----------------------
    # Booked on K-III, the coordinator's flat-deal schedule, rather than the
    # weekly H-II. Her rest threshold is also flagged unverified.
    nina = _person(
        "p_nina", PROD_A, "Nina Okafor", PerformerCategory.STUNT_PERFORMER,
        ContractType.WEEKLY, ContractRate.weekly(WEEKLY_A), schedule=SCHEDULE_K_III,
    )
    out.append((PROD_A, Booking(nina, [
        _shift(PROD_A, "sh_nina_1", "p_nina", 6, MON, local(MON, 8),
               dismissed=local(MON, 19), status=ShiftStatus.WRAPPED),
        _shift(PROD_A, "sh_nina_2", "p_nina", 7, TUE, local(TUE, 8),
               status=ShiftStatus.ON_CLOCK),
    ], 1)))

    # -- Background call group — where volume changes the arithmetic -------
    bg = CallGroup(
        call_group_id="bg_diner", production_id=PROD_A.production_id,
        label="Background — Diner Ext.", headcount=85,
        contract_rate=ContractRate.daily(Decimal("221.00")),
    )
    out.append((PROD_A, Booking(bg, [
        _shift(PROD_A, "sh_bg_1", "bg_diner", 6, MON, local(MON, 6),
               dismissed=local(MON, 17), subject_type=SubjectType.CALL_GROUP,
               headcount=85, status=ShiftStatus.WRAPPED),
        _shift(PROD_A, "sh_bg_2", "bg_diner", 7, TUE, local(TUE, 6),
               subject_type=SubjectType.CALL_GROUP, headcount=85,
               status=ShiftStatus.ON_CLOCK),
    ], 1)))

    # -- Production B — same violation, one fifth the price ----------------
    sam = _person(
        "p_sam", PROD_B, "Sam Okonkwo", PerformerCategory.PRINCIPAL,
        ContractType.DAY, ContractRate.daily(SCALE_B),
    )
    out.append((PROD_B, Booking(sam, [
        _shift(PROD_B, "sh_sam_1", "p_sam", 4, MON, local(MON, 9),
               dismissed=local(TUE, 1), status=ShiftStatus.WRAPPED),
        _shift(PROD_B, "sh_sam_2", "p_sam", 5, TUE, local(TUE, 9),
               status=ShiftStatus.ON_CLOCK),
    ], 1)))

    ruth = _person(
        "p_ruth", PROD_B, "Ruth Vela", PerformerCategory.PRINCIPAL,
        ContractType.DAY, ContractRate.daily(SCALE_B),
    )
    out.append((PROD_B, Booking(ruth, [
        _shift(PROD_B, "sh_ruth_1", "p_ruth", 4, MON, local(MON, 9),
               dismissed=local(MON, 20), status=ShiftStatus.WRAPPED),
        _shift(PROD_B, "sh_ruth_2", "p_ruth", 5, TUE, local(TUE, 10),
               status=ShiftStatus.ON_CLOCK,
               meals=[MealBreak(1, local(TUE, 14), local(TUE, 14, 30))]),
    ], 1)))

    return out


def evaluate_all(now: datetime = NOW) -> dict:
    """Run every booking through the rule engine and shape it for a UI."""
    rb = RuleBook()
    subjects, productions = [], {}

    for prod, bk in bookings():
        productions.setdefault(prod.production_id, {
            "production_id": prod.production_id,
            "title": prod.title,
            "budget_tier": prod.budget_tier.value,
            "production_type": prod.production_type.value,
            "timezone": prod.timezone,
        })
        checks = evaluate_subject(bk.subject, prod, bk.shifts, bk.index, now, rb)
        shift = bk.shifts[bk.index]

        subjects.append({
            "subject_id": shift.subject_id,
            "subject_type": shift.subject_type.value,
            "production_id": prod.production_id,
            "name": getattr(bk.subject, "name", getattr(bk.subject, "label", "")),
            "performer_category": bk.subject.performer_category.value,
            "contract_type": bk.subject.contract_type.value,
            "headcount": shift.subject_headcount,
            "call_at": shift.scheduled_call_at.isoformat(),
            "call_local": shift.scheduled_call_at.astimezone(LA).strftime("%H:%M"),
            "zone": shift.zone.value,
            "status": shift.status.value,
            "worst_state": _worst(checks),
            "checks": [_check_json(c, now) for c in checks],
        })

    return {
        "generated_at": now.isoformat(),
        "now_local": now.astimezone(LA).strftime("%H:%M"),
        "day_label": now.astimezone(LA).strftime("%A %d %B %Y"),
        "productions": list(productions.values()),
        "subjects": subjects,
        "unverified_rules": RuleBook().unverified_rules(),
    }


_ORDER = {
    ComplianceState.VIOLATED: 3,
    ComplianceState.APPROACHING: 2,
    ComplianceState.CLEAR: 1,
}


def _worst(checks) -> str:
    return max(checks, key=lambda c: _ORDER[c.state]).state.value


def _check_json(c, now: datetime) -> dict:
    return {
        "type": c.violation_type.value,
        "state": c.state.value,
        "deadline_at": c.deadline_at.isoformat() if c.deadline_at else None,
        "deadline_local": (
            c.deadline_at.astimezone(LA).strftime("%H:%M") if c.deadline_at else None
        ),
        "margin_seconds": c.margin_seconds,
        "shortfall_seconds": c.shortfall_seconds,
        "increments": c.half_hour_increments,
        "per_capita_usd": str(c.per_capita_penalty_usd),
        "total_usd": str(c.total_penalty_usd),
        "headcount": c.headcount,
        "confidence": c.rule_confidence,
        "source_url": c.source_url,
        "explanation": c.explanation,
    }


def _human(data: dict) -> None:
    print(f"\n  ON THE CLOCK — {data['day_label']} · {data['now_local']} local\n")
    for p in data["productions"]:
        print(f"  {p['title']}  ({p['budget_tier']})")
        rows = [s for s in data["subjects"] if s["production_id"] == p["production_id"]]
        for s in rows:
            head = f" x{s['headcount']}" if s["headcount"] > 1 else ""
            print(f"    {s['worst_state'].upper():<12} {s['name']}{head}")
            for c in s["checks"]:
                if c["state"] == "clear":
                    continue
                money = f"  ${c['total_usd']}" if c["total_usd"] != "0.00" else ""
                print(f"        - {c['type']}{money}")
                print(f"          {c['explanation']}")
        print()


if __name__ == "__main__":
    result = evaluate_all()
    if "--json" in sys.argv:
        json.dump(result, sys.stdout, indent=2)
    else:
        _human(result)
