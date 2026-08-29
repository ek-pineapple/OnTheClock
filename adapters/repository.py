"""The storage interface every service talks to.

Defined as a `Protocol` rather than a base class so implementations do not
inherit from anything — an in-memory fake and a Firestore client are both
just objects with these methods.

The point of this indirection is not abstraction for its own sake. It is
that `services/monitor` should be testable without credentials, and that the
Firestore document shape should be swappable without touching a caller.
Every method here takes and returns domain objects from `core.models`;
nothing above this line ever sees a dict.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from core.models import CallGroup, Person, Production, Shift, Violation

Subject = Person | CallGroup


@runtime_checkable
class Repository(Protocol):
    """Read and write the operational store."""

    # -- productions -------------------------------------------------------
    def put_production(self, production: Production) -> None: ...
    def get_production(self, production_id: str) -> Production | None: ...
    def list_productions(self) -> list[Production]: ...

    # -- subjects ----------------------------------------------------------
    def put_person(self, person: Person) -> None: ...
    def get_person(self, production_id: str, person_id: str) -> Person | None: ...
    def list_people(self, production_id: str) -> list[Person]: ...

    def put_call_group(self, group: CallGroup) -> None: ...
    def get_call_group(self, production_id: str, group_id: str) -> CallGroup | None: ...
    def list_call_groups(self, production_id: str) -> list[CallGroup]: ...

    def get_subject(self, production_id: str, subject_id: str) -> Subject | None:
        """Resolve a subject id without the caller knowing which kind it is.

        The rule engine treats a person and a call group interchangeably, so
        callers should not have to branch either.
        """
        ...

    # -- shifts ------------------------------------------------------------
    def put_shift(self, shift: Shift) -> None: ...

    def list_shifts(self, production_id: str, subject_id: str) -> list[Shift]:
        """This subject's shifts, **chronologically ordered**.

        Ordering is part of the contract, not a convenience. Every rule in
        `core.rules` indexes into this sequence to look backwards; an
        unordered result produces a confident wrong answer rather than an
        error.
        """
        ...

    # -- violations --------------------------------------------------------
    def put_violation(self, violation: Violation) -> None: ...
    def list_violations(self, production_id: str) -> list[Violation]: ...
