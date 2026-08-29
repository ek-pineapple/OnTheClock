"""Contract-schedule classification check.

Unlike the other two rules this is not clock-based and carries no dollar
penalty — it is a data-integrity flag against a documented misclassification
pattern that SAG-AFTRA has actively pursued.

Stunt *performers* work under the weekly Schedule H-II. Schedule K-III is
the stunt *coordinator's* flat-deal schedule. Tagging a stunt performer as
K-III avoids proper weekly pay and residual terms, so the combination is a
red flag on its own.

Cheap to evaluate, distinct from the clock rules, and it exercises the
`performer_category` / `contract_schedule` fields that would otherwise only
exist for the demo's benefit.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from ..models import (
    CallGroup,
    ComplianceCheck,
    ComplianceState,
    PerformerCategory,
    Person,
    SubjectType,
    ViolationType,
    SCHEDULE_H_II,
    SCHEDULE_K_III,
)

Subject = Person | CallGroup

_SOURCE = "https://www.sagaftra.org/"


def check_contract_schedule(
    subject: Subject,
    now: datetime,
    rule_version: str = "sag-2026.1",
) -> ComplianceCheck:
    """Flag a stunt performer booked on the coordinator's flat-deal schedule.

    Returns a check with no penalty amount — the cost here is legal and
    contractual exposure, not a per-violation charge, and inventing a dollar
    figure would misrepresent it.
    """
    subject_type = (
        SubjectType.PERSON if isinstance(subject, Person) else SubjectType.CALL_GROUP
    )
    subject_id = (
        subject.person_id if isinstance(subject, Person) else subject.call_group_id
    )
    schedule = getattr(subject, "contract_schedule", None)

    def _check(state: ComplianceState, explanation: str) -> ComplianceCheck:
        return ComplianceCheck(
            subject_type=subject_type,
            subject_id=subject_id,
            production_id=subject.production_id,
            violation_type=ViolationType.SCHEDULE_MISMATCH,
            state=state,
            per_capita_penalty_usd=Decimal("0.00"),
            headcount=1,
            rule_version=rule_version,
            source_url=_SOURCE,
            rule_confidence="corroborated",
            explanation=explanation,
        )

    if subject.performer_category is not PerformerCategory.STUNT_PERFORMER:
        return _check(
            ComplianceState.CLEAR,
            "Not a stunt performer — schedule check does not apply.",
        )

    if schedule is None:
        return _check(
            ComplianceState.APPROACHING,
            (
                "Stunt performer has no contract schedule recorded. Expected "
                f"{SCHEDULE_H_II}; cannot confirm correct classification."
            ),
        )

    if schedule == SCHEDULE_K_III:
        return _check(
            ComplianceState.VIOLATED,
            (
                f"Classification mismatch: stunt performer booked on "
                f"{SCHEDULE_K_III}, the stunt coordinator's flat-deal "
                f"schedule. Stunt performers work under the weekly "
                f"{SCHEDULE_H_II}. This pattern avoids proper weekly pay and "
                f"residual terms and has been actively pursued by SAG-AFTRA."
            ),
        )

    return _check(
        ComplianceState.CLEAR,
        f"Stunt performer correctly booked on schedule {schedule}.",
    )
