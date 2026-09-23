"""PuLP/CBC implementation of the `optimize.problem` interface."""

from __future__ import annotations

import pulp

from eve.optimize.problem import Problem, Solution


def solve(problem: Problem) -> Solution:
    lp_vars = {
        var.name: pulp.LpVariable(
            var.name,
            lowBound=var.lower,
            upBound=var.upper,
            cat=pulp.LpInteger if var.integer else pulp.LpContinuous,
        )
        for var in problem.variables
    }

    sense = pulp.LpMaximize if problem.objective.sense == "max" else pulp.LpMinimize
    prob = pulp.LpProblem("eve_optimize", sense)
    prob += pulp.lpSum(coeff * lp_vars[name] for name, coeff in problem.objective.coeffs.items())

    sense_ops = {"<=": pulp.LpConstraintLE, ">=": pulp.LpConstraintGE, "==": pulp.LpConstraintEQ}
    for constraint in problem.constraints:
        expr = pulp.lpSum(coeff * lp_vars[name] for name, coeff in constraint.coeffs.items())
        prob += pulp.LpConstraint(
            expr, sense=sense_ops[constraint.sense], name=constraint.name, rhs=constraint.rhs
        )

    prob.solve(pulp.PULP_CBC_CMD(msg=False))

    return Solution(
        status=pulp.LpStatus[prob.status],
        objective_value=pulp.value(prob.objective) or 0.0,
        values={name: var.value() or 0.0 for name, var in lp_vars.items()},
    )
