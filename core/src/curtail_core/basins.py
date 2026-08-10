"""The two watersheds, and their compliance gages. Single source of truth.

This module exists because `Basin` was originally declared twice, once in
`flow_minimums` and once in `adjudications`. Both were `StrEnum` with identical
values, so `==` compared True across them and `is` compared False. Every
routing decision in `priority.py` uses `is`.

The consequence was not theoretical. A Scott right constructed with the
flow_minimums copy of the enum fell through the `is Basin.SCOTT` check and was
placed on the SHASTA ladder, returning Tier B with citation 23 CCR
875.5(b)(1)(B) and a reason asserting it was held under the Shasta
Adjudication. No exception, no warning, and a false statement in a record a
state official signs.

Type checkers do not catch this. mypy treats the two classes as distinct types
but `is` comparison between them is legal, so it passes silently.

One enum, one home, imported everywhere.
"""

from __future__ import annotations

from enum import StrEnum


class Basin(StrEnum):
    """The two watersheds under the Scott and Shasta emergency regulation."""

    SCOTT = "scott"
    SHASTA = "shasta"


#: Baseline compliance gage per basin.
#:
#: These are the compliance points named in the regulation, not the only
#: legally possible ones: sections 875(c)(1)(B) and 875(f)(3) permit
#: alternative flow points and additional approved gages.
COMPLIANCE_GAGE: dict[Basin, str] = {
    Basin.SCOTT: "USGS-11519500",  # Scott River near Fort Jones
    Basin.SHASTA: "USGS-11517500",  # Shasta River near Yreka
}
