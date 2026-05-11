from typing import Any, Dict, Literal
from pydantic import BaseModel, Field
from langchain_core.output_parsers import PydanticOutputParser
from core.contracts.plan import PlanOutput, PlanStep
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
    risk_level: Literal["low", "medium", "high"] = "low"

class DraftPlanModel(BaseModel):
    status: Literal["ok", "needs_review", "blocked"] = "ok"
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
    status: Literal["ok", "needs_review", "blocked"] = "ok"
    open_questions: list[str] = Field(default_factory=list)

def _normalize_text_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    normalized: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        item = value.strip()
        if not item:
            continue
        normalized.append(item)
    return normalized

def draft_plan(state: Dict[str, Any]) -> Dict[str, Any]:
    parser = PydanticOutputParser(pydantic_object=DraftPlanModel)
    prompt = create_prompt(PLAN_DRAFT_PROMPT, ["triage_result"])
    chain = prompt | client | parser
    triage_result = state.get("triage_result", {})
    response = chain.invoke({"triage_result": triage_result})
    payload = response.model_dump()
    state["status"] = payload.get("status", "ok")
    state["strategy"] = payload.get("strategy", "")
    state["assumptions"] = _normalize_text_list(payload.get("assumptions", []))
    state["open_questions"] = _normalize_text_list(payload.get("open_questions", []))
    state["steps"] = [step.model_dump() for step in response.steps]
    return state

def map_dependencies(state: Dict[str, Any]) -> Dict[str, Any]:
    parser = PydanticOutputParser(pydantic_object=DependencyMapModel)
    prompt = create_prompt(DEPENDENCY_MAPPING_PROMPT, ["steps"])
    chain = prompt | client | parser
    response = chain.invoke({"steps": state.get("steps", [])})
    dependency_map = {
        item.title: _normalize_text_list(item.depends_on_titles) for item in response.steps
    }
    enriched_steps: list[dict[str, Any]] = []
    for step in state.get("steps", []):
        step_title = str(step.get("title", ""))
        enriched = dict(step)
        enriched["depends_on_titles"] = dependency_map.get(step_title, [])
        enriched_steps.append(enriched)
    state["steps"] = enriched_steps
    return state

def detect_ambiguity(state: Dict[str, Any]) -> Dict[str, Any]:
    parser = PydanticOutputParser(pydantic_object=PlanAmbiguityModel)
    prompt = create_prompt(PLAN_AMBIGUITY_PROMPT, ["plan"])
    chain = prompt | client | parser
    response = chain.invoke(
        {
            "plan": {
                "strategy": state.get("strategy", ""),
                "status": state.get("status", "ok"),
                "assumptions": state.get("assumptions", []),
                "open_questions": state.get("open_questions", []),
                "steps": state.get("steps", []),
            }
        }
    )
    payload = response.model_dump()
    new_status = payload.get("status", state.get("status", "ok"))
    state["status"] = new_status
    current_questions = _normalize_text_list(state.get("open_questions", []))
    detected_questions = _normalize_text_list(payload.get("open_questions", []))
    merged_questions = current_questions + [q for q in detected_questions if q not in current_questions]
    state["open_questions"] = merged_questions
    return state

def finalize(state: Dict[str, Any]) -> Dict[str, Any]:
    step_id_by_title: dict[str, Any] = {}
    plan_steps: list[PlanStep] = []
    for step in state.get("steps", []):
        plan_step = PlanStep(
            title=str(step.get("title", "")).strip() or "Untitled Step",
            objective=str(step.get("objective", "")).strip() or "No objective provided",
            rationale=str(step.get("rationale", "")).strip(),
            files=_normalize_text_list(step.get("files", [])),
            tests=_normalize_text_list(step.get("tests", [])),
            acceptance_criteria=_normalize_text_list(step.get("acceptance_criteria", [])),
            risk_level=step.get("risk_level", "low"),
        )
        plan_steps.append(plan_step)
        step_id_by_title[plan_step.title] = plan_step.id

    for index, step in enumerate(plan_steps):
        raw_dep_titles = state.get("steps", [])[index].get("depends_on_titles", [])
        dep_titles = _normalize_text_list(raw_dep_titles)
        plan_steps[index].dependencies = [
            step_id_by_title[dep_title] for dep_title in dep_titles if dep_title in step_id_by_title
        ]

    result = PlanOutput(
        strategy=str(state.get("strategy", "")).strip(),
        steps=plan_steps,
        assumptions=_normalize_text_list(state.get("assumptions", [])),
        open_questions=_normalize_text_list(state.get("open_questions", [])),
    )
    state["final_output"] = result.model_dump()
    return state
