"""Optimizer core: candidate store, selection, gate, scheduling, merge, loop."""
from optimizer.candidate import (
    Candidate,
    CandidateStore,
    candidate_id_for,
    make_candidate,
    normalize_source,
)
from optimizer.feedback_sampler import (
    Scenario,
    ScenarioSchedule,
    disjoint_minibatch,
    map_to_feedback_scenarios,
    select_reflection_instances,
)
from optimizer.gate import GateConfig, GateDecision, accept_child
from optimizer.greedy import select_parent_greedy
from optimizer.loop import ARMS, ArmConfig, BudgetExhausted, LoopConfig, OptimizerLoop
from optimizer.merge import attempt_merge, changed_components_since
from optimizer.pareto import (
    frontier_members,
    macro_average,
    macro_averages,
    per_instance_best_sets,
    select_parent,
)

__all__ = [
    "Candidate",
    "CandidateStore",
    "candidate_id_for",
    "make_candidate",
    "normalize_source",
    "Scenario",
    "ScenarioSchedule",
    "disjoint_minibatch",
    "map_to_feedback_scenarios",
    "select_reflection_instances",
    "GateConfig",
    "GateDecision",
    "accept_child",
    "select_parent_greedy",
    "ARMS",
    "ArmConfig",
    "BudgetExhausted",
    "LoopConfig",
    "OptimizerLoop",
    "attempt_merge",
    "changed_components_since",
    "frontier_members",
    "macro_average",
    "macro_averages",
    "per_instance_best_sets",
    "select_parent",
]
