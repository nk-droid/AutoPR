from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from core.contracts.enums import PlanStatus, RiskLevel
from core.contracts.plan import PlanOutput, PlanStep
from core.contracts.triage import TriageResult
from infra.llm.chains import invoke_chain
from infra.llm.client import create_client
from infra.llm.prompts import load_prompt_catalog, require_prompt

from observability.tracing import traced, langgraph_node_attrs

client = create_client()
_PROMPTS_PATH = Path(__file__).with_name("prompts.yaml")
_PROMPTS = load_prompt_catalog(_PROMPTS_PATH)
PLAN_DRAFT_PROMPT = require_prompt(_PROMPTS, "plan_draft", source=_PROMPTS_PATH)
DEPENDENCY_MAPPING_PROMPT = require_prompt(_PROMPTS, "dependency_mapping", source=_PROMPTS_PATH)
PLAN_AMBIGUITY_PROMPT = require_prompt(_PROMPTS, "plan_ambiguity", source=_PROMPTS_PATH)

class DraftPlanStepModel(BaseModel):
    title: str
    objective: str
    rationale: str = ""
    files: list[str] = Field(default_factory=list)
    tests: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.LOW

class DraftPlanModel(BaseModel):
    status: PlanStatus = PlanStatus.OK
    strategy: str
    assumptions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    steps: list[DraftPlanStepModel] = Field(default_factory=list)

class DependencyItem(BaseModel):
    title: str
    depends_on_titles: list[str] = Field(default_factory=list)

class DependencyMapModel(BaseModel):
    steps: list[DependencyItem] = Field(default_factory=list)

class PlanAmbiguityModel(BaseModel):
    status: PlanStatus = PlanStatus.OK
    open_questions: list[str] = Field(default_factory=list)

def _as_triage_result(value: Any) -> TriageResult:
    if isinstance(value, TriageResult):
        return value
    return TriageResult.model_validate(value)

def _as_plan_steps(values: Any) -> list[PlanStep]:
    if not isinstance(values, list):
        return []
    return [item if isinstance(item, PlanStep) else PlanStep.model_validate(item) for item in values]

@traced(
    "plan_step.draft_plan",
    attributes=langgraph_node_attrs("plan", "draft_plan"),
)
def draft_plan(state: dict[str, Any]) -> dict[str, Any]:
    triage_result = _as_triage_result(state.get("triage_result"))
    
    response = invoke_chain(
        template=PLAN_DRAFT_PROMPT.template,
        input_vars=PLAN_DRAFT_PROMPT.input_vars,
        output_model=DraftPlanModel,
        variables={"triage_result": triage_result.model_dump(mode="json")},
        client=client,
        include_format_instructions=PLAN_DRAFT_PROMPT.include_format_instructions,
    )

    state["status"] = response.status
    state["strategy"] = response.strategy
    state["assumptions"] = response.assumptions
    state["open_questions"] = response.open_questions
    state["steps"] = [
        PlanStep(
            title=step.title,
            objective=step.objective,
            rationale=step.rationale,
            files=step.files,
            tests=step.tests,
            acceptance_criteria=step.acceptance_criteria,
            risk_level=step.risk_level,
        )
        for step in response.steps
    ]
    return state

@traced(
    "plan_step.map_dependencies",
    attributes=langgraph_node_attrs("plan", "map_dependencies"),
)
def map_dependencies(state: dict[str, Any]) -> dict[str, Any]:
    plan_steps = _as_plan_steps(state.get("steps"))
    
    response = invoke_chain(
        template=DEPENDENCY_MAPPING_PROMPT.template,
        input_vars=DEPENDENCY_MAPPING_PROMPT.input_vars,
        output_model=DependencyMapModel,
        variables={"steps": [step.model_dump(mode="json") for step in plan_steps]},
        client=client,
        include_format_instructions=DEPENDENCY_MAPPING_PROMPT.include_format_instructions,
    )

    dependency_titles_by_step_title = {
        item.title: item.depends_on_titles
        for item in response.steps
    }
    step_id_by_title = {step.title: step.id for step in plan_steps}
    state["steps"] = [
        step.model_copy(
            update={
                "dependencies": [
                    step_id_by_title[dep_title]
                    for dep_title in dependency_titles_by_step_title.get(step.title, [])
                    if dep_title in step_id_by_title
                ]
            }
        )
        for step in plan_steps
    ]
    return state

@traced(
    "plan_step.detect_ambiguity",
    attributes=langgraph_node_attrs("plan", "detect_ambiguity"),
)
def detect_ambiguity(state: dict[str, Any]) -> dict[str, Any]:
    plan_steps = _as_plan_steps(state.get("steps"))
    current_status = state.get("status", PlanStatus.OK)
    if not isinstance(current_status, PlanStatus):
        current_status = PlanStatus.OK

    response = invoke_chain(
        template=PLAN_AMBIGUITY_PROMPT.template,
        input_vars=PLAN_AMBIGUITY_PROMPT.input_vars,
        output_model=PlanAmbiguityModel,
        variables={
            "plan": {
                "strategy": state.get("strategy", ""),
                "status": current_status.value,
                "assumptions": state.get("assumptions", []),
                "open_questions": state.get("open_questions", []),
                "steps": [step.model_dump(mode="json") for step in plan_steps],
            }
        },
        client=client,
        include_format_instructions=PLAN_AMBIGUITY_PROMPT.include_format_instructions,
    )

    state["status"] = response.status

    current_questions = state.get("open_questions", [])
    if not isinstance(current_questions, list):
        current_questions = []

    merged_questions = list(current_questions)
    for question in response.open_questions:
        if question not in merged_questions:
            merged_questions.append(question)

    state["open_questions"] = merged_questions
    return state

@traced(
    "plan_step.finalize",
    attributes=langgraph_node_attrs("plan", "finalize"),
)
def finalize(state: dict[str, Any]) -> dict[str, Any]:
    plan_steps = _as_plan_steps(state.get("steps"))
    assumptions = state.get("assumptions", [])
    open_questions = state.get("open_questions", [])

    result = PlanOutput(
        strategy=state.get("strategy", ""),
        steps=plan_steps,
        assumptions=assumptions if isinstance(assumptions, list) else [],
        open_questions=open_questions if isinstance(open_questions, list) else [],
    )
    
    state["final_output"] = result.model_dump(mode="json")
    return state
