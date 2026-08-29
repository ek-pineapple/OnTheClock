"""Ring 1 — compliance evaluation.

Pure functions over `core.models`, reading every threshold and rate from
`core.rulebook`. No I/O, no network, no cloud.

**The one hard rule in this package: never write a rule value here.**

If the meal clock's `6` or the studio zone's `12` appears in this code, it
is trapped where no uploaded contract can reach it — and the long-term
goal is that a user supplies their own agreement and the system adapts.
Read values from the `RuleBook`; dispatch only on its machine-readable
condition keys (`ReductionCondition`, `ReductionLimit`).

A condition key with no matching predicate must fail loudly as
unsupported. Silently skipping an unevaluated rule means reporting
"all clear" on a check that never ran, which is the worst failure mode
this product has.

Nothing in this package calls `datetime.now()`. Evaluation time is always
passed in, so every check is deterministic and testable.
"""

from .classification import check_contract_schedule
from .config import DEFAULT_CONFIG, EvaluationConfig
from .evaluate import active, evaluate_subject, total_exposure_usd
from .meals import check_meal_period
from .rest import UnsupportedRuleError, check_rest_period
from .weekly_rest import check_weekly_rest, workweek_start

__all__ = [
    "DEFAULT_CONFIG",
    "EvaluationConfig",
    "UnsupportedRuleError",
    "active",
    "check_contract_schedule",
    "check_meal_period",
    "check_rest_period",
    "check_weekly_rest",
    "evaluate_subject",
    "total_exposure_usd",
    "workweek_start",
]
