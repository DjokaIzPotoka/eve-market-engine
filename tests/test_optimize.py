"""Tests for the PuLP/CBC solver backend against hand-verified optima."""

from __future__ import annotations

from eve.optimize.problem import Constraint, Objective, Problem, Variable
from eve.optimize.solver import solve


def test_simple_continuous_lp_matches_hand_computed_optimum() -> None:
    # maximize 3x + 2y, s.t. x + y <= 4, x <= 2, x,y >= 0
    # optimal at x=2, y=2 -> objective 10 (x=2 exhausts its own bound, y takes the rest)
    problem = Problem(
        variables=[Variable("x", upper=2.0), Variable("y")],
        objective=Objective({"x": 3.0, "y": 2.0}, "max"),
        constraints=[Constraint("cap", {"x": 1.0, "y": 1.0}, "<=", 4.0)],
    )

    solution = solve(problem)

    assert solution.status == "Optimal"
    assert round(solution.objective_value, 6) == 10.0
    assert round(solution.values["x"], 6) == 2.0
    assert round(solution.values["y"], 6) == 2.0


def test_integer_variable_forces_away_from_fractional_relaxation() -> None:
    # maximize 5x, s.t. x <= 2.5 -- continuous optimum is x=2.5 (obj 12.5),
    # integer optimum is x=2 (obj 10.0).
    problem = Problem(
        variables=[Variable("x", upper=2.5, integer=True)],
        objective=Objective({"x": 5.0}, "max"),
        constraints=[],
    )

    solution = solve(problem)

    assert solution.status == "Optimal"
    assert solution.values["x"] == 2.0
    assert solution.objective_value == 10.0


def test_trivially_feasible_problem_reports_optimal_status() -> None:
    problem = Problem(
        variables=[Variable("x", upper=1.0)],
        objective=Objective({"x": 1.0}, "max"),
        constraints=[],
    )

    solution = solve(problem)

    assert solution.status == "Optimal"
