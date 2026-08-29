"""Ring 0 — the shared vocabulary and the encoded rulebook.

This package contains no decisions. It defines *what things are*
(`models`) and *what the SAG-AFTRA agreement says* (`rulebook`).

All compliance reasoning lives in Ring 1 (`agent/rules/`), which imports
from here. Nothing in this package imports from anywhere else in the
project, and nothing here touches the network, Firestore, or Grafana.
"""
