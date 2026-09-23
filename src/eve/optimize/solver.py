"""Solver entry point -- the one place callers import from, and the only line that would
change to swap in an OR-Tools backend later (see CLAUDE.md's stack note)."""

from __future__ import annotations

from eve.optimize import pulp_backend
from eve.optimize.problem import Problem, Solution


def solve(problem: Problem) -> Solution:
    return pulp_backend.solve(problem)
