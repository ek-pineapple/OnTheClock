"""Product settings — deliberately *not* part of the rulebook.

There is a real distinction between two kinds of number in this system:

* **Contract values** — the 6-hour meal clock, the 12-hour rest period, the
  $900 cap. These come from an agreement, belong in `core.rulebook`, and
  must be replaceable by an uploaded contract.

* **Product settings** — how far in advance we warn, how we round a partial
  half-hour. No agreement mentions these; they are our choices about how the
  tool behaves.

Conflating the two would mean an uploaded contract could silently change our
alerting behaviour, or that a UI preference would look like a contractual
term. They are separated so neither can happen.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta


@dataclass(frozen=True)
class EvaluationConfig:
    """How the monitor behaves. Not contract terms."""

    #: How far ahead of a meal deadline to start warning. Product choice:
    #: long enough for a 2nd AD to actually call a break, short enough that
    #: the Ops view is not permanently amber.
    meal_warning_lead: timedelta = timedelta(minutes=30)

    #: How far ahead of a *dismissal* deadline to warn, when someone is
    #: still on the clock and their next call is already scheduled.
    dismissal_warning_lead: timedelta = timedelta(minutes=60)

    #: Penalty increments are half-hour blocks, and a partial block counts
    #: as a whole one ("or portion thereof"). This value is the block size,
    #: not a rounding preference — but the *decision to round up* is ours to
    #: state explicitly rather than leave implicit in a ceil() call.
    penalty_increment: timedelta = timedelta(minutes=30)


DEFAULT_CONFIG = EvaluationConfig()
