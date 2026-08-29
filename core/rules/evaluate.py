"""Combined evaluation — every rule, one subject, one moment.

Lives in Ring 1 rather than in the monitor service so that stacking can be
tested with no cloud. The monitor just calls this.

**Rules are evaluated independently and never short-circuit.** A performer
can incur a forced call *and* a meal penalty on the same day; those are
separate obligations that stack, and finding one is not a reason to stop
looking for the others. Anything that returned "the violation" rather than
"the violations" would silently under-report.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from ..models import (
    CallGroup,
    ComplianceCheck,
    ComplianceState,
    Person,
    Production,
    Shift,
)
from ..rulebook import RuleBook
from .classification import check_contract_schedule
from .config import DEFAULT_CONFIG, EvaluationConfig
from .meals import check_meal_period
from .rest import check_rest_period
from .weekly_rest import check_weekly_rest

Subject = Person | CallGroup


def evaluate_subject(
    subject: Subject,
    production: Production,
    shifts: Sequence[Shift],
    index: int,
    now: datetime,
    rulebook: RuleBook,
    config: EvaluationConfig = DEFAULT_CONFIG,
) -> list[ComplianceCheck]:
    """Run every applicable rule against `shifts[index]`.

    Returns all checks including CLEAR ones, so a caller can render a full
    picture ("3 clear, 1 approaching") rather than only exceptions. Filter
    with `active(...)` when only problems matter.

    `shifts` must be this subject's shifts in chronological order.
    """
    checks = [
        check_rest_period(subject, production, shifts, index, now, rulebook, config),
        check_weekly_rest(subject, production, shifts, index, now, rulebook, config),
        check_meal_period(subject, shifts[index], now, rulebook, config),
    ]

    # Classification applies to individuals only — a call group has no
    # contract schedule of its own.
    if isinstance(subject, Person):
        checks.append(check_contract_schedule(subject, now, rulebook.weekly_rest.rule_version))

    return checks


def active(checks: Sequence[ComplianceCheck]) -> list[ComplianceCheck]:
    """Only the checks that need someone's attention."""
    return [c for c in checks if c.state is not ComplianceState.CLEAR]


def total_exposure_usd(checks: Sequence[ComplianceCheck]) -> Decimal:
    """Summed dollar exposure across checks, headcount included.

    CLEAR checks carry a zero penalty, so they contribute nothing and do not
    need filtering out first.
    """
    return sum(
        (c.total_penalty_usd for c in checks),
        Decimal("0.00"),
    )
