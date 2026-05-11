from typing import Dict, Any
from langchain_core.prompts import PromptTemplate

from core.contracts.triage import TaskSpec, Risk, AmbiguityResult, TriageResult
from infra.llm.client import create_client, create_prompt

client = create_client()

TASK_EXTRACTION_PROMPT = """
You are a task extraction agent responsible for analyzing a given issue and extracting a clear and concise task specification.
Given an issue with its title and body, you will analyze the content to identify the problem statement, acceptance criteria, 
constraints, and out-of-scope items to create a well-defined task specification.

Here is the issue you need to evaluate:
```
{issue}
```

Please provide your task specification in the following JSON format:
```
{{
  "problem": "A brief description of the problem to be solved.",
  "acceptance_criteria": [
    "A list of specific conditions that must be met for the task to be considered complete."
  ],
  "constraints": [
    "A list of limitations or restrictions that must be taken into account when working on the task."
  ],
  "out_of_scope": [
    "A list of items or aspects that are explicitly not part of the task."
  ]
}}
```
"""

RISK_ASSESSMENT_PROMPT = """
You are a risk assessment agent responsible for evaluating the risk level of a given task based on its specifications and other relevant factors.
Given a task specification, you will analyze the problem, acceptance criteria, constraints, and out-of-scope items to determine the appropriate
risk level (low, medium, high) associated with the task. Additionally, you will provide reasons for your assessment.

Here is the task specification you need to evaluate:
```
{task_spec}
```

Please provide your risk assessment in the following JSON format:
```
{{
  "level": "low | medium | high",
  "reasons": [
    "A list of reasons that justify the assigned risk level."
  ]
}}
```
"""

AMBIGUITY_DETECTION_PROMPT = """
You are an ambiguity detection agent responsible for identifying any ambiguous or unclear aspects of a given task specification and risk assessment.
Given a task specification and its associated risk assessment, you will analyze the information to detect any potential ambiguities, such as unclear
problem statements, vague acceptance criteria, or insufficiently defined constraints. Your goal is to ensure that the task specification is clear and actionable.

Here is the task specification you need to evaluate:
```
{task_spec}
```

Here is the risk assessment associated with the task:
```
{risk}
```

Please provide your ambiguity detection result in the following JSON format:
```
{{
  "questions": [
    "A list of questions (in string format) that highlight potential ambiguities in the task specification."
  ],
  "status": "ok | needs_review" ('ok' indicates that the task specification is clear and actionable, while 'needs_human' indicates that there are ambiguities that require human intervention.)
}}
```

Rules:
1. Set status="needs_review" ONLY if a blocking ambiguity exists (implementation cannot proceed safely).
2. Set status="ok" for non-blocking uncertainty.
3. If status="ok", prefer questions=[].
"""

def _normalize_questions(raw_questions: Any) -> list[str]:
    if not isinstance(raw_questions, list):
        return []
    cleaned: list[str] = []
    for item in raw_questions:
        if not isinstance(item, str):
            continue
        value = item.strip()
        if not value:
            continue
        if value.lower() in {"none", "n/a", "na", "null", "nil", "no questions", "no question"}:
            continue
        cleaned.append(value)
    return cleaned

def _coerce_ambiguity_status(raw_status: Any, questions: list[str]) -> str:
    status = str(raw_status).strip().lower()
    if status not in {"ok", "needs_human"}:
        status = "ok"
    if status == "needs_human" and not questions:
        return "ok"
    return status

def extract_task(state: Dict[str, Any]) -> TaskSpec:
    from langchain_core.output_parsers import PydanticOutputParser
    parser = PydanticOutputParser(pydantic_object=TaskSpec)
    prompt = create_prompt(TASK_EXTRACTION_PROMPT, ["issue"])
    chain = prompt | client | parser
    issue = f"{state['issue']['title']}\n{state['issue']['body']}"
    response = chain.invoke({
       "issue": issue
    })
    
    state["task_spec"] = response.model_dump()
    return state

def assess_risk(state: Dict[str, Any]) -> Risk:
    from langchain_core.output_parsers import PydanticOutputParser
    parser = PydanticOutputParser(pydantic_object=Risk)
    prompt = create_prompt(RISK_ASSESSMENT_PROMPT, ["task_spec"])
    chain = prompt | client | parser
    task_spec = f"""
Problem: {state['task_spec']['problem']}
Acceptance Criteria: {', '.join(state['task_spec']['acceptance_criteria'])}
Constraints: {', '.join(state['task_spec']['constraints'])}
Out of Scope: {', '.join(state['task_spec']['out_of_scope'])}
    """
    response = chain.invoke({
       "task_spec": task_spec
    })
    
    state["risk"] = response.model_dump()
    return state

def detect_ambiguity(state: Dict[str, Any]) -> AmbiguityResult:
    from langchain_core.output_parsers import PydanticOutputParser
    parser = PydanticOutputParser(pydantic_object=AmbiguityResult)
    prompt = create_prompt(AMBIGUITY_DETECTION_PROMPT, ["task_spec", "risk"])
    chain = prompt | client | parser
    task_spec = f"""Problem: {state['task_spec']['problem']}
Acceptance Criteria: {', '.join(state['task_spec']['acceptance_criteria'])}
Constraints: {', '.join(state['task_spec']['constraints'])}
Out of Scope: {', '.join(state['task_spec']['out_of_scope'])}
"""
    risk = f"""Level: {state['risk']['level']}
Reasons: {', '.join(state['risk']['reasons'])}
"""

    response = chain.invoke({
       "task_spec": task_spec,
       "risk": risk
    })
    payload = response.model_dump()
    questions = _normalize_questions(payload.get("questions", []))
    status = _coerce_ambiguity_status(payload.get("status", "ok"), questions)
    state["questions"] = questions
    state["status"] = status
    return state

def finalize(state: Dict[str, Any]) -> TriageResult:
    ambiguity_status = state.get("status", "ok")
    questions = state.get("questions", [])
    triage_status = "accepted"
    if ambiguity_status == "needs_review" and questions:
        triage_status = "needs_review"

    result = TriageResult(
        task_spec=TaskSpec(**state["task_spec"]),
        risk=Risk(**state["risk"]),
        ambiguity=AmbiguityResult(status=ambiguity_status, questions=questions),
        questions=questions
    )

    state["final_output"] = result.model_dump()
    return state
