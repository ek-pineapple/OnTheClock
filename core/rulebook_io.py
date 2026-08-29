"""RuleBook <-> plain dictionaries.

Separate from `serialization.py` because it serves a different purpose. That
module persists *what happened*. This one persists *what the rules are* — and
it is the hinge the whole upload-your-own-agreement direction turns on.

Three things depend on this existing:

1. A rulebook can be stored in Firestore and edited without a deploy.
2. Gemini's extraction from an uploaded PDF has a concrete target shape to
   produce, rather than free-form text someone has to translate into code.
3. The extracted values can be rendered for human confirmation before going
   live — which is the whole safety story. Nothing extracted is ever applied
   silently.

For (3) to work the encoding has to stay legible to a person reading it in a
review screen, not just parseable by a machine. That is why steps are written
as explicit `from`/`to` bounds rather than a packed array, and why every rule
carries its `source_url` and `confidence` through the round trip. A reviewer
needs to see where a number came from and how sure we are of it.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from .models import BudgetTier, ContractType, PerformerCategory, ProductionType, Zone
from .rulebook import (
    Confidence,
    ForcedCallPenalty,
    MealTimingRule,
    PenaltySchedule,
    PenaltyStep,
    ReductionCondition,
    ReductionLimit,
    RestReduction,
    RestRule,
    RuleBook,
    ScaleRate,
    WeeklyRestRule,
)
from .serialization import SerializationError, dec_money, enc_money


def _enc_date(d: date) -> str:
    return d.isoformat()


def _dec_date(v: Any) -> date:
    return v if isinstance(v, date) else date.fromisoformat(str(v)[:10])


# --------------------------------------------------------------------------
# Meal penalties
# --------------------------------------------------------------------------


def penalty_schedule_to_dict(s: PenaltySchedule) -> dict:
    return {
        "steps": [
            {
                "from_increment": step.from_increment,
                "to_increment": step.to_increment,  # null = unbounded above
                "amount_usd": enc_money(step.amount_usd),
            }
            for step in s.steps
        ],
        "source_url": s.source_url,
        "effective_from": _enc_date(s.effective_from),
        "rule_version": s.rule_version,
        "confidence": s.confidence.value,
    }


def penalty_schedule_from_dict(d: dict) -> PenaltySchedule:
    steps = tuple(
        PenaltyStep(
            from_increment=int(st["from_increment"]),
            to_increment=(
                None if st.get("to_increment") is None else int(st["to_increment"])
            ),
            amount_usd=dec_money(st["amount_usd"]),
        )
        for st in d["steps"]
    )
    _validate_steps(steps)
    return PenaltySchedule(
        steps=steps,
        source_url=d["source_url"],
        effective_from=_dec_date(d["effective_from"]),
        rule_version=d["rule_version"],
        confidence=Confidence(d.get("confidence", Confidence.NEEDS_SOURCE_CHECK.value)),
    )


def _validate_steps(steps: tuple[PenaltyStep, ...]) -> None:
    """Reject a schedule with holes or overlaps.

    An extracted schedule that skips increment 3, or charges two rates for
    it, would silently mis-price every violation past that point. Better to
    refuse the document than to serve wrong money.
    """
    if not steps:
        raise SerializationError("Penalty schedule has no steps.")
    ordered = sorted(steps, key=lambda s: s.from_increment)
    if ordered[0].from_increment != 1:
        raise SerializationError(
            f"Penalty schedule must start at increment 1, "
            f"starts at {ordered[0].from_increment}."
        )
    for prev, nxt in zip(ordered, ordered[1:]):
        if prev.to_increment is None:
            raise SerializationError(
                "An unbounded step must be last; found one followed by "
                f"increment {nxt.from_increment}."
            )
        if nxt.from_increment != prev.to_increment + 1:
            raise SerializationError(
                f"Penalty schedule is not contiguous: step ends at "
                f"{prev.to_increment}, next starts at {nxt.from_increment}."
            )
    if ordered[-1].to_increment is not None:
        raise SerializationError(
            "Penalty schedule must end with an unbounded step, otherwise a "
            "long enough overrun has no defined rate."
        )


def meal_timing_to_dict(t: MealTimingRule) -> dict:
    return {
        "first_meal_within_hours": enc_money(t.first_meal_within_hours),
        "subsequent_meal_within_hours": enc_money(t.subsequent_meal_within_hours),
        "grace_period_minutes": t.grace_period_minutes,
        "extension_minutes": t.extension_minutes,
        "ndb_within_hours_of_call": enc_money(t.ndb_within_hours_of_call),
        "ndb_duration_minutes": t.ndb_duration_minutes,
        "source_url": t.source_url,
        "rule_version": t.rule_version,
        "confidence": t.confidence.value,
    }


def meal_timing_from_dict(d: dict) -> MealTimingRule:
    return MealTimingRule(
        first_meal_within_hours=dec_money(d["first_meal_within_hours"]),
        subsequent_meal_within_hours=dec_money(d["subsequent_meal_within_hours"]),
        grace_period_minutes=int(d["grace_period_minutes"]),
        extension_minutes=int(d["extension_minutes"]),
        ndb_within_hours_of_call=dec_money(d["ndb_within_hours_of_call"]),
        ndb_duration_minutes=int(d["ndb_duration_minutes"]),
        source_url=d["source_url"],
        rule_version=d["rule_version"],
        confidence=Confidence(d["confidence"]),
    )


# --------------------------------------------------------------------------
# Rest rules
# --------------------------------------------------------------------------


def rest_rule_to_dict(r: RestRule) -> dict:
    return {
        "performer_category": (
            r.performer_category.value if r.performer_category else None
        ),
        "zone": r.zone.value if r.zone else None,
        "production_type": r.production_type.value if r.production_type else None,
        "base_hours": enc_money(r.base_hours),
        "reduction": (
            {
                "reduced_to_hours": enc_money(r.reduction.reduced_to_hours),
                "condition": r.reduction.condition.value,
                "limit": r.reduction.limit.value,
                "description": r.reduction.description,
            }
            if r.reduction
            else None
        ),
        "source_url": r.source_url,
        "rule_version": r.rule_version,
        "confidence": r.confidence.value,
    }


def rest_rule_from_dict(d: dict) -> RestRule:
    red = d.get("reduction")
    if red is not None:
        # An unknown key here must fail at load time, not at evaluation time.
        # A contract naming a condition we cannot evaluate has to be visibly
        # rejected rather than quietly treated as "no reduction".
        try:
            condition = ReductionCondition(red["condition"])
            limit = ReductionLimit(red["limit"])
        except ValueError as exc:
            raise SerializationError(
                f"Unsupported reduction rule: {exc}. This agreement names a "
                "condition or limit the rule engine has no predicate for."
            ) from exc
        reduction = RestReduction(
            reduced_to_hours=dec_money(red["reduced_to_hours"]),
            condition=condition,
            limit=limit,
            description=red.get("description", ""),
        )
    else:
        reduction = None

    return RestRule(
        performer_category=(
            PerformerCategory(d["performer_category"]) if d.get("performer_category") else None
        ),
        zone=Zone(d["zone"]) if d.get("zone") else None,
        production_type=(
            ProductionType(d["production_type"]) if d.get("production_type") else None
        ),
        base_hours=dec_money(d["base_hours"]),
        reduction=reduction,
        source_url=d["source_url"],
        rule_version=d["rule_version"],
        confidence=Confidence(d["confidence"]),
    )


def weekly_rest_to_dict(w: WeeklyRestRule) -> dict:
    return {
        "base_hours": enc_money(w.base_hours),
        "reduced_hours_early_call_exception": enc_money(
            w.reduced_hours_early_call_exception
        ),
        "reduced_hours_six_day_location": enc_money(w.reduced_hours_six_day_location),
        "early_call_no_earlier_than_hour": w.early_call_no_earlier_than_hour,
        "six_day_workweek_shift_count": w.six_day_workweek_shift_count,
        "source_url": w.source_url,
        "rule_version": w.rule_version,
        "confidence": w.confidence.value,
    }


def weekly_rest_from_dict(d: dict) -> WeeklyRestRule:
    return WeeklyRestRule(
        base_hours=dec_money(d["base_hours"]),
        reduced_hours_early_call_exception=dec_money(
            d["reduced_hours_early_call_exception"]
        ),
        reduced_hours_six_day_location=dec_money(d["reduced_hours_six_day_location"]),
        early_call_no_earlier_than_hour=int(d["early_call_no_earlier_than_hour"]),
        six_day_workweek_shift_count=int(d["six_day_workweek_shift_count"]),
        source_url=d["source_url"],
        rule_version=d["rule_version"],
        confidence=Confidence(d["confidence"]),
    )


# --------------------------------------------------------------------------
# Penalties and rates
# --------------------------------------------------------------------------


def forced_call_to_dict(f: ForcedCallPenalty) -> dict:
    return {
        "cap_usd": enc_money(f.cap_usd),
        "basis": f.basis,
        "source_url": f.source_url,
        "rule_version": f.rule_version,
        "confidence": f.confidence.value,
    }


def forced_call_from_dict(d: dict) -> ForcedCallPenalty:
    return ForcedCallPenalty(
        cap_usd=dec_money(d["cap_usd"]),
        basis=d["basis"],
        source_url=d["source_url"],
        rule_version=d["rule_version"],
        confidence=Confidence(d["confidence"]),
    )


def scale_rate_to_dict(s: ScaleRate) -> dict:
    return {
        "budget_tier": s.budget_tier.value,
        "daily_usd": enc_money(s.daily_usd),
        "weekly_usd": enc_money(s.weekly_usd) if s.weekly_usd is not None else None,
        "budget_range": s.budget_range,
        "source_url": s.source_url,
        "rule_version": s.rule_version,
        "confidence": s.confidence.value,
    }


def scale_rate_from_dict(d: dict) -> ScaleRate:
    return ScaleRate(
        budget_tier=BudgetTier(d["budget_tier"]),
        daily_usd=dec_money(d["daily_usd"]),
        weekly_usd=(
            dec_money(d["weekly_usd"]) if d.get("weekly_usd") is not None else None
        ),
        budget_range=d.get("budget_range", ""),
        source_url=d["source_url"],
        rule_version=d["rule_version"],
        confidence=Confidence(d["confidence"]),
    )


# --------------------------------------------------------------------------
# The whole book
# --------------------------------------------------------------------------


def rulebook_to_dict(rb: RuleBook) -> dict:
    return {
        "schema": "on-the-clock/rulebook/1",
        "rest_rules": [rest_rule_to_dict(r) for r in rb.rest_rules],
        "weekly_rest": weekly_rest_to_dict(rb.weekly_rest),
        "meal_timing": meal_timing_to_dict(rb.meal_timing),
        "meal_penalties": {
            cat.value: penalty_schedule_to_dict(sched)
            for cat, sched in rb.meal_penalties.items()
        },
        "forced_call": {
            ct.value: forced_call_to_dict(fc) for ct, fc in rb.forced_call.items()
        },
        "scale_rates": {
            bt.value: scale_rate_to_dict(sr) for bt, sr in rb.scale_rates.items()
        },
    }


def rulebook_from_dict(d: dict) -> RuleBook:
    schema = d.get("schema")
    if schema != "on-the-clock/rulebook/1":
        raise SerializationError(f"Unknown rulebook schema: {schema!r}")

    return RuleBook(
        rest_rules=tuple(rest_rule_from_dict(r) for r in d["rest_rules"]),
        meal_penalties={
            PerformerCategory(k): penalty_schedule_from_dict(v)
            for k, v in d["meal_penalties"].items()
        },
        forced_call={
            ContractType(k): forced_call_from_dict(v)
            for k, v in d["forced_call"].items()
        },
        scale_rates={
            BudgetTier(k): scale_rate_from_dict(v) for k, v in d["scale_rates"].items()
        },
        meal_timing=meal_timing_from_dict(d["meal_timing"]),
        weekly_rest=weekly_rest_from_dict(d["weekly_rest"]),
    )
