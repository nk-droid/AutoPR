from typing import Any

from core.contracts.enums import RiskLevel
from core.orchestrator.models import StageStatus
from core.contracts.triage import AmbiguityResult
from core.contracts.triage import Risk
from core.contracts.triage import TaskSpec
from core.contracts.triage import TriageResult

import core.agents.plan.nodes as plan_nodes
import core.agents.triage.nodes as triage_nodes


def test_triage_nodes_flow_with_dict_inputs(monkeypatch) -> None:
    def fake_invoke_chain(*, output_model, **kwargs):
        del kwargs
        if output_model is TaskSpec:
            return TaskSpec(
                problem="Fix parsing",
                acceptance_criteria=["returns structured output"],
                constraints=["no API break"],
                out_of_scope=["ui"],
            )
        if output_model is Risk:
            return Risk(level=RiskLevel.MEDIUM, reasons=["parser changes"])
        if output_model is AmbiguityResult:
            return AmbiguityResult(
                status=StageStatus.NEEDS_REVIEW, questions=["Should we support XML?"]
            )
        raise AssertionError("unexpected output model")

    monkeypatch.setattr(triage_nodes, "invoke_chain", fake_invoke_chain)
    state: dict[str, Any] = {"issue": {"title": "Bug", "body": "Details"}}
    state = triage_nodes.extract_task(state)
    state = triage_nodes.assess_risk(state)
    state = triage_nodes.detect_ambiguity(state)
    state = triage_nodes.finalize(state)
    assert state["status"] == StageStatus.NEEDS_REVIEW
    assert state["task_spec"].problem == "Fix parsing"
    assert state["risk"].level == RiskLevel.MEDIUM
    assert state["final_output"]["ambiguity"]["questions"] == ["Should we support XML?"]
    assert state["final_output"]["questions"] == ["Should we support XML?"]


def test_plan_nodes_flow_merges_dependencies_and_questions(monkeypatch) -> None:
    def fake_invoke_chain(*, output_model, **kwargs):
        del kwargs
        if output_model is plan_nodes.DraftPlanModel:
            return plan_nodes.DraftPlanModel(
                status=StageStatus.OK,
                strategy="small iterative rollout",
                assumptions=["feature flag enabled"],
                open_questions=["who owns rollout?"],
                steps=[
                    plan_nodes.DraftPlanStepModel(
                        title="Step A",
                        objective="touch module A",
                        files=["a.py"],
                        tests=["tests/test_a.py"],
                    ),
                    plan_nodes.DraftPlanStepModel(
                        title="Step B",
                        objective="touch module B",
                        files=["b.py"],
                        tests=["tests/test_b.py"],
                    ),
                ],
            )
        if output_model is plan_nodes.DependencyMapModel:
            return plan_nodes.DependencyMapModel(
                steps=[
                    plan_nodes.DependencyItem(title="Step A", depends_on_titles=[]),
                    plan_nodes.DependencyItem(title="Step B", depends_on_titles=["Step A"]),
                ]
            )
        if output_model is plan_nodes.PlanAmbiguityModel:
            return plan_nodes.PlanAmbiguityModel(
                status=StageStatus.NEEDS_REVIEW,
                open_questions=["who owns rollout?", "what about rollback?"],
            )
        raise AssertionError("unexpected output model")

    monkeypatch.setattr(plan_nodes, "invoke_chain", fake_invoke_chain)
    triage_result = TriageResult(
        task_spec=TaskSpec(problem="p", acceptance_criteria=["a"], constraints=[], out_of_scope=[]),
        risk=Risk(level=RiskLevel.LOW, reasons=["small"]),
        ambiguity=AmbiguityResult(status=StageStatus.OK, questions=[]),
        questions=[],
    )
    state: dict[str, Any] = {
        "triage_result": triage_result.model_dump(mode="json"),
        "strategy": "",
        "steps": [],
        "assumptions": [],
        "open_questions": [],
        "status": StageStatus.OK,
        "final_output": {},
    }
    state = plan_nodes.draft_plan(state)
    assert state["strategy"] == "small iterative rollout"
    first_step_id = state["steps"][0].id
    second_step_id = state["steps"][1].id
    state = plan_nodes.map_dependencies(state)
    assert state["steps"][0].dependencies == []
    assert state["steps"][1].dependencies == [first_step_id]
    assert second_step_id != first_step_id
    state = plan_nodes.detect_ambiguity(state)
    assert state["status"] == StageStatus.NEEDS_REVIEW
    assert state["open_questions"] == ["who owns rollout?", "what about rollback?"]
    state = plan_nodes.finalize(state)
    assert state["final_output"]["strategy"] == "small iterative rollout"
    assert len(state["final_output"]["steps"]) == 2
