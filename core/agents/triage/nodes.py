from typing import Any

from langchain_core.output_parsers import PydanticOutputParser

from core.contracts.run_context import TriageIssueInput
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

def extract_task(state: dict[str, Any]) -> dict[str, Any]:
    parser = PydanticOutputParser(pydantic_object=TaskSpec)
    prompt = create_prompt(TASK_EXTRACTION_PROMPT, ["issue"])
    chain = prompt | client | parser

    issue_value = state.get("issue")
    if isinstance(issue_value, TriageIssueInput):
        issue = issue_value
    else:
        issue = TriageIssueInput.model_validate(issue_value)

    response = chain.invoke({
        "issue": f"{issue.title}\n{issue.body}",
    })

    state["task_spec"] = response

    return state

def assess_risk(state: dict[str, Any]) -> dict[str, Any]:
    parser = PydanticOutputParser(pydantic_object=Risk)
    prompt = create_prompt(RISK_ASSESSMENT_PROMPT, ["task_spec"])
    chain = prompt | client | parser

    task_spec_value = state.get("task_spec")
    task_spec = task_spec_value if isinstance(task_spec_value, TaskSpec) else TaskSpec.model_validate(task_spec_value)
    response = chain.invoke(
        {
            "task_spec": (
                f"Problem: {task_spec.problem}\n"
                f"Acceptance Criteria: {', '.join(task_spec.acceptance_criteria)}\n"
                f"Constraints: {', '.join(task_spec.constraints)}\n"
                f"Out of Scope: {', '.join(task_spec.out_of_scope)}"
            )
        }
    )

    state["risk"] = response
    
    return state

def detect_ambiguity(state: dict[str, Any]) -> dict[str, Any]:
    parser = PydanticOutputParser(pydantic_object=AmbiguityResult)
    prompt = create_prompt(AMBIGUITY_DETECTION_PROMPT, ["task_spec", "risk"])
    chain = prompt | client | parser

    task_spec_value = state.get("task_spec")
    risk_value = state.get("risk")
    task_spec = task_spec_value if isinstance(task_spec_value, TaskSpec) else TaskSpec.model_validate(task_spec_value)
    risk = risk_value if isinstance(risk_value, Risk) else Risk.model_validate(risk_value)
    response = chain.invoke(
        {
            "task_spec": (
                f"Problem: {task_spec.problem}\n"
                f"Acceptance Criteria: {', '.join(task_spec.acceptance_criteria)}\n"
                f"Constraints: {', '.join(task_spec.constraints)}\n"
                f"Out of Scope: {', '.join(task_spec.out_of_scope)}"
            ),
            "risk": (
                f"Level: {risk.level.value}\n"
                f"Reasons: {', '.join(risk.reasons)}"
            ),
        }
    )

    state["ambiguity"] = response
    state["status"] = response.status

    return state

def finalize(state: dict[str, Any]) -> dict[str, Any]:
    task_spec_value = state.get("task_spec")
    risk_value = state.get("risk")
    ambiguity_value = state.get("ambiguity")

    task_spec = task_spec_value if isinstance(task_spec_value, TaskSpec) else TaskSpec.model_validate(task_spec_value)
    risk = risk_value if isinstance(risk_value, Risk) else Risk.model_validate(risk_value)
    ambiguity = (
        ambiguity_value if isinstance(ambiguity_value, AmbiguityResult) else AmbiguityResult.model_validate(ambiguity_value)
    )

    result = TriageResult(
        task_spec=task_spec,
        risk=risk,
        ambiguity=ambiguity,
        questions=list(ambiguity.questions),
    )

    state["status"] = ambiguity.status
    state["final_output"] = result.model_dump(mode="json")
    
    return state
