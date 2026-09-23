"""Solver-agnostic LP/MILP problem representation.

Kept deliberately minimal (no general-purpose LP DSL) -- just enough to describe a
hauling/market-making problem and hand it to a backend. `Variable.upper` covers simple
per-variable capacity bounds (e.g. `x <= volume_remain`) without needing a `Constraint` row
per variable, which matters at the scale a real order book produces.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class Variable:
    name: str
    lower: float = 0.0
    upper: float | None = None
    integer: bool = False


@dataclass(frozen=True)
class Constraint:
    name: str
    coeffs: dict[str, float]
    sense: Literal["<=", ">=", "=="]
    rhs: float


@dataclass(frozen=True)
class Objective:
    coeffs: dict[str, float]
    sense: Literal["max", "min"] = "max"


@dataclass(frozen=True)
class Problem:
    variables: list[Variable]
    objective: Objective
    constraints: list[Constraint] = field(default_factory=list)


@dataclass(frozen=True)
class Solution:
    status: str
    objective_value: float
    values: dict[str, float]
