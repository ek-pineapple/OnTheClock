"""Domain objects <-> plain dictionaries.

Lives in `core/` because a dict is not a cloud dependency — this module
imports nothing outside the standard library, and the same output feeds
Firestore, a JSON API response, or a test fixture.

Three types need deliberate handling, and getting any of them wrong is
quiet rather than loud:

* **`Decimal` is written as a string, never a float.** Firestore has no
  decimal type. `float(Decimal("7.50"))` looks harmless and then background
  penalties accumulate wrong across 85 people. Ring 0 chose Decimal on
  purpose; this is where that choice is kept or lost.
* **`datetime` is written as an ISO-8601 string with its offset**, and
  parsed back timezone-aware. A naive datetime is rejected on the way out,
  so a bad record cannot be written in the first place.
* **`set[int]` becomes a sorted list**, because JSON has no set. Sorted so
  the encoding is stable and two equal shifts produce identical documents.

Every decoder is the exact inverse of its encoder. The round-trip tests are
what make that claim true rather than aspirational.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from .models import (
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
    Violation,
    ViolationStatus,
    ViolationType,
    Zone,
)


class SerializationError(ValueError):
    """A value could not be encoded or decoded safely."""


# --------------------------------------------------------------------------
# Primitives
# --------------------------------------------------------------------------


def enc_money(value: Decimal) -> str:
    if not isinstance(value, Decimal):
        raise SerializationError(
            f"Money must be Decimal, got {type(value).__name__}: {value!r}. "
            "Floats are rejected here because the loss is silent."
        )
    return str(value)


def dec_money(value: Any) -> Decimal:
    if isinstance(value, float):
        raise SerializationError(
            f"Refusing to decode money from a float ({value!r}). "
            "The document was written incorrectly."
        )
    return Decimal(str(value))


def enc_dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise SerializationError(f"Refusing to encode naive datetime {value!r}.")
    return value.isoformat()


def dec_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        # Firestore's client returns real datetimes for timestamp fields.
        if value.tzinfo is None:
            raise SerializationError(f"Stored datetime {value!r} is naive.")
        return value
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise SerializationError(f"Decoded datetime {value!r} has no offset.")
    return parsed


def enc_date(value: date) -> str:
    return value.isoformat()


def dec_date(value: Any) -> date:
    return value if isinstance(value, date) and not isinstance(value, datetime) \
        else date.fromisoformat(str(value)[:10])


# --------------------------------------------------------------------------
# Value objects
# --------------------------------------------------------------------------


def contract_rate_to_dict(r: ContractRate) -> dict:
    return {
        "period_rate_usd": enc_money(r.period_rate_usd),
        "days_in_period": r.days_in_period,
    }


def contract_rate_from_dict(d: dict) -> ContractRate:
    return ContractRate(
        period_rate_usd=dec_money(d["period_rate_usd"]),
        days_in_period=int(d["days_in_period"]),
    )


def meal_to_dict(m: MealBreak) -> dict:
    return {"index": m.index, "out_at": enc_dt(m.out_at), "in_at": enc_dt(m.in_at)}


def meal_from_dict(d: dict) -> MealBreak:
    return MealBreak(index=int(d["index"]), out_at=dec_dt(d["out_at"]), in_at=dec_dt(d["in_at"]))


def ndb_to_dict(n: NonDeductibleMeal) -> dict:
    return {"start_at": enc_dt(n.start_at), "end_at": enc_dt(n.end_at)}


def ndb_from_dict(d: dict) -> NonDeductibleMeal:
    return NonDeductibleMeal(start_at=dec_dt(d["start_at"]), end_at=dec_dt(d["end_at"]))


# --------------------------------------------------------------------------
# Entities
# --------------------------------------------------------------------------


def production_to_dict(p: Production) -> dict:
    return {
        "production_id": p.production_id,
        "title": p.title,
        "production_type": p.production_type.value,
        "budget_tier": p.budget_tier.value,
        "timezone": p.timezone,
        "shoot_start": enc_date(p.shoot_start),
        "shoot_end": enc_date(p.shoot_end),
        "workweek_start_weekday": p.workweek_start_weekday,
        "is_synthetic": p.is_synthetic,
    }


def production_from_dict(d: dict) -> Production:
    return Production(
        production_id=d["production_id"],
        title=d["title"],
        production_type=ProductionType(d["production_type"]),
        budget_tier=BudgetTier(d["budget_tier"]),
        timezone=d["timezone"],
        shoot_start=dec_date(d["shoot_start"]),
        shoot_end=dec_date(d["shoot_end"]),
        workweek_start_weekday=int(d.get("workweek_start_weekday", 0)),
        is_synthetic=bool(d.get("is_synthetic", True)),
    )


def person_to_dict(p: Person) -> dict:
    return {
        "person_id": p.person_id,
        "production_id": p.production_id,
        "name": p.name,
        "performer_category": p.performer_category.value,
        "contract_type": p.contract_type.value,
        "contract_rate": contract_rate_to_dict(p.contract_rate),
        "contract_schedule": p.contract_schedule,
        "call_group_id": p.call_group_id,
        "active": p.active,
    }


def person_from_dict(d: dict) -> Person:
    return Person(
        person_id=d["person_id"],
        production_id=d["production_id"],
        name=d["name"],
        performer_category=PerformerCategory(d["performer_category"]),
        contract_type=ContractType(d["contract_type"]),
        contract_rate=contract_rate_from_dict(d["contract_rate"]),
        contract_schedule=d.get("contract_schedule"),
        call_group_id=d.get("call_group_id"),
        active=bool(d.get("active", True)),
    )


def call_group_to_dict(g: CallGroup) -> dict:
    return {
        "call_group_id": g.call_group_id,
        "production_id": g.production_id,
        "label": g.label,
        "headcount": g.headcount,
        "contract_rate": contract_rate_to_dict(g.contract_rate),
        "performer_category": g.performer_category.value,
        "contract_type": g.contract_type.value,
    }


def call_group_from_dict(d: dict) -> CallGroup:
    return CallGroup(
        call_group_id=d["call_group_id"],
        production_id=d["production_id"],
        label=d["label"],
        headcount=int(d["headcount"]),
        contract_rate=contract_rate_from_dict(d["contract_rate"]),
        performer_category=PerformerCategory(d["performer_category"]),
        contract_type=ContractType(d["contract_type"]),
    )


def shift_to_dict(s: Shift) -> dict:
    return {
        "shift_id": s.shift_id,
        "production_id": s.production_id,
        "subject_type": s.subject_type.value,
        "subject_id": s.subject_id,
        "shoot_day": s.shoot_day,
        "date": enc_date(s.date),
        "zone": s.zone.value,
        "scheduled_call_at": enc_dt(s.scheduled_call_at),
        "actual_call_at": enc_dt(s.actual_call_at),
        "dismissed_at": enc_dt(s.dismissed_at),
        "exterior_photography": s.exterior_photography,
        "non_deductible_meal": (
            ndb_to_dict(s.non_deductible_meal) if s.non_deductible_meal else None
        ),
        "meals": [meal_to_dict(m) for m in s.meals],
        # JSON has no set. Sorted so equal shifts encode identically.
        "meal_extensions_granted": sorted(s.meal_extensions_granted),
        "headcount": s.headcount,
        "status": s.status.value,
    }


def shift_from_dict(d: dict) -> Shift:
    return Shift(
        shift_id=d["shift_id"],
        production_id=d["production_id"],
        subject_type=SubjectType(d["subject_type"]),
        subject_id=d["subject_id"],
        shoot_day=int(d["shoot_day"]),
        date=dec_date(d["date"]),
        zone=Zone(d["zone"]),
        scheduled_call_at=dec_dt(d["scheduled_call_at"]),
        actual_call_at=dec_dt(d.get("actual_call_at")),
        dismissed_at=dec_dt(d.get("dismissed_at")),
        exterior_photography=bool(d.get("exterior_photography", False)),
        non_deductible_meal=(
            ndb_from_dict(d["non_deductible_meal"])
            if d.get("non_deductible_meal")
            else None
        ),
        meals=[meal_from_dict(m) for m in d.get("meals", [])],
        meal_extensions_granted=set(d.get("meal_extensions_granted", [])),
        headcount=d.get("headcount"),
        status=ShiftStatus(d.get("status", ShiftStatus.SCHEDULED.value)),
    )


def violation_to_dict(v: Violation) -> dict:
    return {
        "violation_id": v.violation_id,
        "production_id": v.production_id,
        "violation_type": v.violation_type.value,
        "subject_type": v.subject_type.value,
        "subject_id": v.subject_id,
        "shift_id": v.shift_id,
        "shoot_day": v.shoot_day,
        "status": v.status.value,
        "threshold_at": enc_dt(v.threshold_at),
        "detected_at": enc_dt(v.detected_at),
        "computed_at": enc_dt(v.computed_at),
        "half_hour_increments": v.half_hour_increments,
        "headcount_at_detection": v.headcount_at_detection,
        "per_capita_penalty_usd": enc_money(v.per_capita_penalty_usd),
        "total_penalty_usd": enc_money(v.total_penalty_usd),
        "rule_version": v.rule_version,
        "source_url": v.source_url,
        "narrative": v.narrative,
    }


def violation_from_dict(d: dict) -> Violation:
    return Violation(
        violation_id=d["violation_id"],
        production_id=d["production_id"],
        violation_type=ViolationType(d["violation_type"]),
        subject_type=SubjectType(d["subject_type"]),
        subject_id=d["subject_id"],
        shift_id=d["shift_id"],
        shoot_day=int(d["shoot_day"]),
        status=ViolationStatus(d["status"]),
        threshold_at=dec_dt(d["threshold_at"]),
        detected_at=dec_dt(d["detected_at"]),
        computed_at=dec_dt(d["computed_at"]),
        half_hour_increments=int(d.get("half_hour_increments", 0)),
        headcount_at_detection=int(d.get("headcount_at_detection", 1)),
        per_capita_penalty_usd=dec_money(d.get("per_capita_penalty_usd", "0.00")),
        total_penalty_usd=dec_money(d.get("total_penalty_usd", "0.00")),
        rule_version=d.get("rule_version", ""),
        source_url=d.get("source_url", ""),
        narrative=d.get("narrative", ""),
    )
