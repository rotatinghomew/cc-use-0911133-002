"""Registration completeness analysis.

Public entry points:

``registry_delta_hub.completeness.analyse(fixture_dict)``
    Run the full estimation chain on an in-memory fixture.

``python3 -m registry_delta_hub.completeness <fixture> --output <dir>``
    Run the chain and write machine-readable artifacts plus a Chinese summary.

The analysis answers "how complete is the merged registry, and where should a
small back-entry audit go?"  It does not decide anyone's service eligibility.
"""
from __future__ import annotations

from registry_delta_hub.completeness.pipeline import (
    RunResult,
    analyse,
    analyse_followup_included,
)

__all__ = ["RunResult", "analyse", "analyse_followup_included"]
