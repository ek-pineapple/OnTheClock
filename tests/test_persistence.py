"""Ring 2 tests — serialization, validation, and the repository contract.

Runs with no credentials and no network. The in-memory repository encodes and
decodes through exactly the same serializers Firestore will use, so the
round-trip guarantees proved here carry over to the real adapter.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from adapters.memory import InMemoryRepository
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
    Violation,
    ViolationStatus,
    ViolationType,
    Zone,
    SCHEDULE_K_III,
)
from core.rulebook import RuleBook
from core.rulebook_io import (
    penalty_schedule_from_dict,
    rulebook_from_dict,
    rulebook_to_dict,
)
from core.rules import evaluate_subject, total_exposure_usd
from core.serialization import (
    SerializationError,
    person_from_dict,
    person_to_dict,
    production_from_dict,
    production_to_dict,
    shift_from_dict,
    shift_to_dict,
    violation_from_dict,
    violation_to_dict,
)
from core.validation import (
    ShiftValidationError,
    shift_problems,
    validate_sequence,
    validate_shift,
)

LA = "America/Los_Angeles"


def utc(day: int, h: int, m: int = 0) -> datetime:
    return datetime(2026, 9, day, h, m, tzinfo=timezone.utc)


def prod() -> Production:
    return Production(
        production_id="prod_a",
        title="The Long Night",
        production_type=ProductionType.THEATRICAL,
        budget_tier=BudgetTier.BASIC_THEATRICAL,
        timezone=LA,
        shoot_start=date(2026, 9, 7),
        shoot_end=date(2026, 10, 16),
    )


def person() -> Person:
    return Person(
        person_id="p_07",
        production_id="prod_a",
        name="Dana Reyes",
        performer_category=PerformerCategory.PRINCIPAL,
        contract_type=ContractType.WEEKLY,
        contract_rate=ContractRate.weekly(Decimal("4456.00")),
        contract_schedule=SCHEDULE_K_III,
    )


def group() -> CallGroup:
    return CallGroup(
        call_group_id="bg_1",
        production_id="prod_a",
        label="Background — Diner Ext.",
        headcount=85,
        contract_rate=ContractRate.daily(Decimal("221.00")),
    )


def shift(**kw) -> Shift:
    base = dict(
        shift_id="sh_1",
        production_id="prod_a",
        subject_type=SubjectType.PERSON,
        subject_id="p_07",
        shoot_day=7,
        date=date(2026, 9, 15),
        zone=Zone.STUDIO,
        # 13:00 UTC == 06:00 PDT on 15 Sept
        scheduled_call_at=utc(15, 13),
        actual_call_at=utc(15, 13),
        dismissed_at=utc(16, 1),
        status=ShiftStatus.WRAPPED,
    )
    base.update(kw)
    return Shift(**base)


# ==========================================================================
# Serialization round-trips
# ==========================================================================


class TestRoundTrips:
    def test_production(self):
        p = prod()
        assert production_from_dict(production_to_dict(p)) == p

    def test_person_preserves_decimal_exactly(self):
        p = person()
        back = person_from_dict(person_to_dict(p))
        assert back == p
        assert isinstance(back.contract_rate.period_rate_usd, Decimal)
        assert back.one_days_pay_usd == Decimal("891.20")

    def test_shift_with_everything_set(self):
        s = shift(
            meals=[
                MealBreak(1, utc(15, 19), utc(15, 19, 30)),
                MealBreak(2, utc(16, 0), None),
            ],
            non_deductible_meal=NonDeductibleMeal(utc(15, 13, 30), utc(15, 13, 45)),
            meal_extensions_granted={2, 1},
            exterior_photography=True,
        )
        assert shift_from_dict(shift_to_dict(s)) == s

    def test_set_is_encoded_as_a_sorted_list(self):
        s = shift(meal_extensions_granted={3, 1, 2})
        assert shift_to_dict(s)["meal_extensions_granted"] == [1, 2, 3]
        assert shift_from_dict(shift_to_dict(s)).meal_extensions_granted == {1, 2, 3}

    def test_violation(self):
        v = Violation(
            violation_id="v_1",
            production_id="prod_a",
            violation_type=ViolationType.MEAL_PENALTY,
            subject_type=SubjectType.CALL_GROUP,
            subject_id="bg_1",
            shift_id="sh_1",
            shoot_day=7,
            status=ViolationStatus.VIOLATED,
            threshold_at=utc(15, 19),
            detected_at=utc(15, 19, 40),
            computed_at=utc(15, 19, 40),
            half_hour_increments=2,
            headcount_at_detection=85,
            per_capita_penalty_usd=Decimal("17.50"),
            total_penalty_usd=Decimal("1487.50"),
        )
        assert violation_from_dict(violation_to_dict(v)) == v


class TestSerializationRefusals:
    def test_money_as_float_is_rejected_on_write(self):
        p = person()
        p.contract_rate = ContractRate(period_rate_usd=4456.00, days_in_period=5)  # type: ignore[arg-type]
        with pytest.raises(SerializationError, match="must be Decimal"):
            person_to_dict(p)

    def test_money_as_float_is_rejected_on_read(self):
        doc = person_to_dict(person())
        doc["contract_rate"]["period_rate_usd"] = 4456.00  # a float crept in
        with pytest.raises(SerializationError, match="float"):
            person_from_dict(doc)

    def test_naive_datetime_is_rejected_on_read(self):
        doc = shift_to_dict(shift())
        doc["scheduled_call_at"] = "2026-09-15T13:00:00"  # no offset
        with pytest.raises(SerializationError, match="no offset"):
            shift_from_dict(doc)


# ==========================================================================
# Rulebook serialization — the upload-a-contract hinge
# ==========================================================================


class TestRuleBookIO:
    def test_round_trip_preserves_behaviour_not_just_shape(self):
        """The real test: rules evaluated through a round-tripped rulebook
        must produce byte-identical verdicts."""
        original = RuleBook()
        restored = rulebook_from_dict(rulebook_to_dict(original))

        shifts = [
            shift(shift_id="s1", dismissed_at=utc(15, 20), status=ShiftStatus.WRAPPED),
            shift(shift_id="s2", date=date(2026, 9, 16), shoot_day=8,
                  scheduled_call_at=utc(16, 13), actual_call_at=utc(16, 13),
                  dismissed_at=None, status=ShiftStatus.ON_CLOCK),
        ]
        now = utc(16, 20)
        a = evaluate_subject(person(), prod(), shifts, 1, now, original)
        b = evaluate_subject(person(), prod(), shifts, 1, now, restored)

        assert [c.state for c in a] == [c.state for c in b]
        assert total_exposure_usd(a) == total_exposure_usd(b)
        assert [c.explanation for c in a] == [c.explanation for c in b]

    def test_penalty_amounts_survive_exactly(self):
        restored = rulebook_from_dict(rulebook_to_dict(RuleBook()))
        sched = restored.meal_penalty_schedule(PerformerCategory.PRINCIPAL)
        assert sched.cumulative_for(5) == Decimal("235.00")
        bg = restored.meal_penalty_schedule(PerformerCategory.BACKGROUND)
        assert bg.cumulative_for(3) == Decimal("30.00")

    def test_provenance_survives(self):
        restored = rulebook_from_dict(rulebook_to_dict(RuleBook()))
        rule = restored.rest_rule(
            PerformerCategory.PRINCIPAL, Zone.DISTANT, ProductionType.THEATRICAL
        )
        assert rule.source_url.startswith("http")
        assert rule.reduction is not None
        assert rule.confidence.value == "confirmed"

    def test_unknown_reduction_condition_is_rejected_loudly(self):
        """An uploaded agreement naming a condition we cannot evaluate must
        fail at load, not be silently treated as 'no reduction'."""
        doc = rulebook_to_dict(RuleBook())
        for rule in doc["rest_rules"]:
            if rule["reduction"]:
                rule["reduction"]["condition"] = "whenever_the_producer_feels_like_it"
                break
        with pytest.raises(SerializationError, match="Unsupported reduction rule"):
            rulebook_from_dict(doc)

    @pytest.mark.parametrize(
        "steps,msg",
        [
            ([{"from_increment": 2, "to_increment": None, "amount_usd": "25.00"}],
             "must start at increment 1"),
            ([{"from_increment": 1, "to_increment": 2, "amount_usd": "25.00"},
              {"from_increment": 4, "to_increment": None, "amount_usd": "75.00"}],
             "not contiguous"),
            ([{"from_increment": 1, "to_increment": 2, "amount_usd": "25.00"}],
             "must end with an unbounded step"),
        ],
    )
    def test_malformed_schedules_are_rejected(self, steps, msg):
        with pytest.raises(SerializationError, match=msg):
            penalty_schedule_from_dict({
                "steps": steps, "source_url": "http://x", "effective_from": "2026-07-01",
                "rule_version": "v1", "confidence": "confirmed",
            })

    def test_unknown_schema_version_is_rejected(self):
        doc = rulebook_to_dict(RuleBook())
        doc["schema"] = "on-the-clock/rulebook/99"
        with pytest.raises(SerializationError, match="Unknown rulebook schema"):
            rulebook_from_dict(doc)


# ==========================================================================
# Validation — the gap that caused my own Ring 1 test bugs
# ==========================================================================


class TestShiftValidation:
    def test_a_correct_shift_has_no_problems(self):
        assert shift_problems(shift(), prod()) == []

    def test_date_disagreeing_with_the_timestamp_is_caught(self):
        """Exactly the bug that produced phantom weekly-rest violations
        during Ring 1 testing."""
        s = shift(date=date(2026, 9, 7))  # timestamps are on the 15th
        problems = shift_problems(s, prod())
        assert any("but the shift is dated" in p for p in problems)

    def test_night_shoot_wrapping_after_midnight_is_valid(self):
        # Call 21:00 local on the 15th (04:00 UTC on the 16th), wrap 06:00 local
        # on the 16th. The shift is still dated the 15th.
        s = shift(
            date=date(2026, 9, 15),
            scheduled_call_at=utc(16, 4),
            actual_call_at=utc(16, 4),
            dismissed_at=utc(16, 13),
        )
        assert shift_problems(s, prod()) == []

    def test_dismissal_before_call_is_caught(self):
        s = shift(dismissed_at=utc(15, 12))
        assert any("before the call" in p for p in shift_problems(s, prod()))

    def test_absurdly_long_shift_is_caught(self):
        s = shift(dismissed_at=utc(17, 20))
        assert any("sanity limit" in p for p in shift_problems(s, prod()))

    def test_call_group_without_headcount_is_caught(self):
        s = shift(subject_type=SubjectType.CALL_GROUP, subject_id="bg_1", headcount=None)
        assert any("no headcount" in p for p in shift_problems(s, prod()))

    def test_two_open_meals_is_caught(self):
        s = shift(meals=[MealBreak(1, utc(15, 19), None), MealBreak(2, utc(15, 21), None)])
        assert any("more than one open meal" in p for p in shift_problems(s, prod()))

    def test_duplicate_meal_index_is_caught(self):
        s = shift(meals=[
            MealBreak(1, utc(15, 19), utc(15, 19, 30)),
            MealBreak(1, utc(15, 22), utc(15, 22, 30)),
        ])
        assert any("duplicate meal index" in p for p in shift_problems(s, prod()))

    def test_validate_shift_raises_with_all_problems_listed(self):
        s = shift(date=date(2026, 9, 7), dismissed_at=utc(15, 12))
        with pytest.raises(ShiftValidationError) as exc:
            validate_shift(s, prod())
        assert "dated" in str(exc.value) and "before the call" in str(exc.value)


class TestSequenceValidation:
    def test_ordered_sequence_passes(self):
        shifts = [
            shift(shift_id="s1"),
            shift(shift_id="s2", date=date(2026, 9, 16), shoot_day=8,
                  scheduled_call_at=utc(16, 13), actual_call_at=utc(16, 13),
                  dismissed_at=utc(17, 1)),
        ]
        validate_sequence(shifts, prod())

    def test_out_of_order_is_rejected(self):
        a = shift(shift_id="s1")
        b = shift(shift_id="s2", date=date(2026, 9, 14), shoot_day=6,
                  scheduled_call_at=utc(14, 13), actual_call_at=utc(14, 13),
                  dismissed_at=utc(15, 1))
        with pytest.raises(ShiftValidationError, match="not in chronological order"):
            validate_sequence([a, b], prod())

    def test_overlapping_shifts_are_rejected(self):
        a = shift(shift_id="s1", dismissed_at=utc(16, 10))
        b = shift(shift_id="s2", date=date(2026, 9, 16), shoot_day=8,
                  scheduled_call_at=utc(16, 8), actual_call_at=utc(16, 8),
                  dismissed_at=utc(16, 20))
        with pytest.raises(ShiftValidationError, match="overlapping shifts"):
            validate_sequence([a, b], prod())


# ==========================================================================
# Repository contract
# ==========================================================================


class TestInMemoryRepository:
    def test_production_round_trip(self):
        r = InMemoryRepository()
        r.put_production(prod())
        assert r.get_production("prod_a") == prod()
        assert r.get_production("nope") is None

    def test_get_subject_resolves_either_kind(self):
        r = InMemoryRepository()
        r.put_person(person())
        r.put_call_group(group())
        assert isinstance(r.get_subject("prod_a", "p_07"), Person)
        assert isinstance(r.get_subject("prod_a", "bg_1"), CallGroup)
        assert r.get_subject("prod_a", "ghost") is None

    def test_shifts_come_back_chronological_regardless_of_write_order(self):
        """Ordering is part of the contract — the rules index into this
        sequence to look backwards."""
        r = InMemoryRepository()
        later = shift(shift_id="s2", date=date(2026, 9, 16), shoot_day=8,
                      scheduled_call_at=utc(16, 13), actual_call_at=utc(16, 13),
                      dismissed_at=utc(17, 1))
        r.put_shift(later)
        r.put_shift(shift(shift_id="s1"))
        assert [s.shift_id for s in r.list_shifts("prod_a", "p_07")] == ["s1", "s2"]

    def test_stored_objects_are_isolated_from_the_caller(self):
        """Mutating what you put in, or what you got out, must not corrupt
        the store — the real client would not share references either."""
        r = InMemoryRepository()
        p = person()
        r.put_person(p)
        p.name = "Mutated After Write"
        assert r.get_person("prod_a", "p_07").name == "Dana Reyes"

        fetched = r.get_person("prod_a", "p_07")
        fetched.name = "Mutated After Read"
        assert r.get_person("prod_a", "p_07").name == "Dana Reyes"

    def test_listing_is_scoped_to_one_production(self):
        r = InMemoryRepository()
        r.put_person(person())
        other = person()
        other.production_id = "prod_b"
        other.person_id = "p_99"
        r.put_person(other)
        assert [p.person_id for p in r.list_people("prod_a")] == ["p_07"]

    def test_the_fake_satisfies_the_protocol(self):
        from adapters.repository import Repository
        assert isinstance(InMemoryRepository(), Repository)
