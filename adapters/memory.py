"""In-memory `Repository` — the fake that keeps the test suite cloud-free.

Not a toy. It round-trips every object through the same serializers the
Firestore adapter uses, so a `Decimal` that would degrade to a float, or a
naive datetime that would be silently accepted, fails here — in a test that
runs in milliseconds with no credentials — rather than in a deployed monitor
loop at 2am.

Storing dicts rather than the objects themselves is the entire point. Holding
live references would let a caller mutate stored state by accident and would
exercise none of the encoding.
"""

from __future__ import annotations

from copy import deepcopy

from core.models import CallGroup, Person, Production, Shift, Violation
from core.serialization import (
    call_group_from_dict,
    call_group_to_dict,
    person_from_dict,
    person_to_dict,
    production_from_dict,
    production_to_dict,
    shift_from_dict,
    shift_to_dict,
    violation_from_dict,
    violation_to_dict,
)

Subject = Person | CallGroup


class InMemoryRepository:
    """A `Repository` backed by dictionaries."""

    def __init__(self) -> None:
        self._productions: dict[str, dict] = {}
        self._people: dict[tuple[str, str], dict] = {}
        self._call_groups: dict[tuple[str, str], dict] = {}
        self._shifts: dict[str, dict] = {}
        self._violations: dict[str, dict] = {}

    # -- productions -------------------------------------------------------

    def put_production(self, production: Production) -> None:
        self._productions[production.production_id] = production_to_dict(production)

    def get_production(self, production_id: str) -> Production | None:
        doc = self._productions.get(production_id)
        return production_from_dict(deepcopy(doc)) if doc else None

    def list_productions(self) -> list[Production]:
        return [production_from_dict(deepcopy(d)) for d in self._productions.values()]

    # -- people ------------------------------------------------------------

    def put_person(self, person: Person) -> None:
        self._people[(person.production_id, person.person_id)] = person_to_dict(person)

    def get_person(self, production_id: str, person_id: str) -> Person | None:
        doc = self._people.get((production_id, person_id))
        return person_from_dict(deepcopy(doc)) if doc else None

    def list_people(self, production_id: str) -> list[Person]:
        return [
            person_from_dict(deepcopy(d))
            for (pid, _), d in self._people.items()
            if pid == production_id
        ]

    # -- call groups -------------------------------------------------------

    def put_call_group(self, group: CallGroup) -> None:
        self._call_groups[(group.production_id, group.call_group_id)] = (
            call_group_to_dict(group)
        )

    def get_call_group(self, production_id: str, group_id: str) -> CallGroup | None:
        doc = self._call_groups.get((production_id, group_id))
        return call_group_from_dict(deepcopy(doc)) if doc else None

    def list_call_groups(self, production_id: str) -> list[CallGroup]:
        return [
            call_group_from_dict(deepcopy(d))
            for (pid, _), d in self._call_groups.items()
            if pid == production_id
        ]

    def get_subject(self, production_id: str, subject_id: str) -> Subject | None:
        return self.get_person(production_id, subject_id) or self.get_call_group(
            production_id, subject_id
        )

    # -- shifts ------------------------------------------------------------

    def put_shift(self, shift: Shift) -> None:
        self._shifts[shift.shift_id] = shift_to_dict(shift)

    def list_shifts(self, production_id: str, subject_id: str) -> list[Shift]:
        shifts = [
            shift_from_dict(deepcopy(d))
            for d in self._shifts.values()
            if d["production_id"] == production_id and d["subject_id"] == subject_id
        ]
        # Ordering is part of the Repository contract — the rules index into
        # this sequence to look backwards.
        shifts.sort(key=lambda s: s.scheduled_call_at)
        return shifts

    # -- violations --------------------------------------------------------

    def put_violation(self, violation: Violation) -> None:
        self._violations[violation.violation_id] = violation_to_dict(violation)

    def list_violations(self, production_id: str) -> list[Violation]:
        return [
            violation_from_dict(deepcopy(d))
            for d in self._violations.values()
            if d["production_id"] == production_id
        ]
