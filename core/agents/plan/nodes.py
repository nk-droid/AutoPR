from typing import Any

from langchain_core.output_parsers import PydanticOutputParser
from pydantic import BaseModel, Field

from core.contracts.enums import PlanStatus, RiskLevel
from core.contracts.plan import PlanOutput, PlanStep
from core.contracts.triage import TriageResult
from infra.llm.client import create_client, create_prompt

client = create_client()

PLAN_DRAFT_PROMPT = """
You are the planning stage of an issue-to-PR pipeline.
Your plan is consumed by downstream coding automation:
- The orchestrator picks one step (default index 0) and passes it to coding.
- Coding reads `objective`, `files`, and `tests` from that selected step.
- Missing or unclear file targets should result in `status=needs_review` with explicit open questions.

Here is the triage result:
```
{triage_result}
```

Return ONLY a JSON object with this shape:
{{
  "status": "ok | needs_review | blocked",
  "strategy": "high-level implementation strategy and execution order",
  "assumptions": ["assumptions used while planning"],
  "open_questions": ["blocking or quality-critical questions"],
  "steps": [
    {{
      "title": "step name",
      "objective": "single concrete coding outcome",
      "rationale": "why this step is needed",
      "files": ["specific repository file path(s) for this step"],
      "tests": ["specific tests to add/run for this step"],
      "acceptance_criteria": ["observable completion conditions"],
      "risk_level": "low | medium | high"
    }}
  ]
}}

Rules:
1. Do not return markdown, code fences, function definitions, or JSON schema objects.
2. Do not return keys like `properties`, `type`, `items`, `definitions`, `parameters`, or `function`.
3. Keep steps ordered and independently actionable.
4. If target files are unknown, set `status=needs_review` and include open questions.
"""

DEPENDENCY_MAPPING_PROMPT = """
You are a planning agent that adds dependencies between already drafted plan steps.
Do not add, remove, or rename steps.

Here are current steps:
```
{steps}
```

Return ONLY JSON:
{{
  "steps": [
    {{
      "title": "step name",
      "depends_on_titles": ["title of prerequisite step"]
    }}
  ]
}}
"""

PLAN_AMBIGUITY_PROMPT = """
You are a planning ambiguity detector.

Here is the current plan:
```
{plan}
```

Return JSON:
{{
  "status": "ok | needs_review | blocked",
  "open_questions": ["question 1", "question 2"]
}}

Rules:
1. Use "needs_review" only when ambiguity can block implementation quality or safety.
2. Use "blocked" only when work cannot proceed.
3. If status is "ok", prefer open_questions=[].
"""

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

def draft_plan(state: dict[str, Any]) -> dict[str, Any]:
    parser = PydanticOutputParser(pydantic_object=DraftPlanModel)
    prompt = create_prompt(PLAN_DRAFT_PROMPT, ["triage_result"])
    chain = prompt | client | parser

    triage_result = _as_triage_result(state.get("triage_result"))
    response = chain.invoke({"triage_result": triage_result.model_dump(mode="json")})

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

def map_dependencies(state: dict[str, Any]) -> dict[str, Any]:
    parser = PydanticOutputParser(pydantic_object=DependencyMapModel)
    prompt = create_prompt(DEPENDENCY_MAPPING_PROMPT, ["steps"])
    chain = prompt | client | parser

    plan_steps = _as_plan_steps(state.get("steps"))
    response = chain.invoke({"steps": [step.model_dump(mode="json") for step in plan_steps]})

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

def detect_ambiguity(state: dict[str, Any]) -> dict[str, Any]:
    parser = PydanticOutputParser(pydantic_object=PlanAmbiguityModel)
    prompt = create_prompt(PLAN_AMBIGUITY_PROMPT, ["plan"])
    chain = prompt | client | parser

    plan_steps = _as_plan_steps(state.get("steps"))
    current_status = state.get("status", PlanStatus.OK)

    if not isinstance(current_status, PlanStatus):
        current_status = PlanStatus.OK

    response = chain.invoke(
        {
            "plan": {
                "strategy": state.get("strategy", ""),
                "status": current_status.value,
                "assumptions": state.get("assumptions", []),
                "open_questions": state.get("open_questions", []),
                "steps": [step.model_dump(mode="json") for step in plan_steps],
            }
        }
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
