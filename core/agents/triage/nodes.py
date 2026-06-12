from pathlib import Path
from typing import Any

from core.contracts.run_context import TriageIssueInput
from core.contracts.triage import AmbiguityResult, Risk, TaskSpec, TriageResult
from infra.llm.chains import invoke_chain
from infra.llm.client import create_client
from infra.llm.prompts import load_prompt_catalog, require_prompt

from observability.tracing import traced, langgraph_node_attrs

client = create_client()
_PROMPTS_PATH = Path(__file__).with_name("prompts.yaml")
_PROMPTS = load_prompt_catalog(_PROMPTS_PATH)
TASK_EXTRACTION_PROMPT = require_prompt(_PROMPTS, "task_extraction", source=_PROMPTS_PATH)
RISK_ASSESSMENT_PROMPT = require_prompt(_PROMPTS, "risk_assessment", source=_PROMPTS_PATH)
AMBIGUITY_DETECTION_PROMPT = require_prompt(_PROMPTS, "ambiguity_detection", source=_PROMPTS_PATH)


@traced(
    "triage_step.extract_task",
    attributes=langgraph_node_attrs("triage", "extract_task"),
)
def extract_task(state: dict[str, Any]) -> dict[str, Any]:
    """
    Extract the task specification from the triage issue using an LLM.

    Args:
        state: A dictionary containing the current state of the triage process, including the triage issue.
        ```
        {
            "issue": TriageIssueInput(...),
            // other state variables...
        }
        ```

    Returns:
        An updated state dictionary with the extracted task specification added.
    """

    issue_value = state.get("issue")
    if isinstance(issue_value, TriageIssueInput):
        issue = issue_value
    else:
        issue = TriageIssueInput.model_validate(issue_value)

    response = invoke_chain(
        template=TASK_EXTRACTION_PROMPT.template,
        input_vars=TASK_EXTRACTION_PROMPT.input_vars,
        output_model=TaskSpec,
        variables={"issue": f"{issue.title}\n{issue.body}"},
        agent="triage_agent",
        node="extract_task",
        client=client,
        include_format_instructions=TASK_EXTRACTION_PROMPT.include_format_instructions,
    )

    state["task_spec"] = response
    return state


@traced(
    "triage_step.assess_risk",
    attributes=langgraph_node_attrs("triage", "assess_risk"),
)
def assess_risk(state: dict[str, Any]) -> dict[str, Any]:
    """
    Assess the risk associated with the task using an LLM.

    Args:
        state: A dictionary containing the current state of the triage process, including the extracted task specification.
        ```
        {
            "task_spec": TaskSpec(...),
            // other state variables...
        }
        ```

    Returns:
        An updated state dictionary with the assessed risk added.
    """

    task_spec_value = state.get("task_spec")
    task_spec = (
        task_spec_value
        if isinstance(task_spec_value, TaskSpec)
        else TaskSpec.model_validate(task_spec_value)
    )

    response = invoke_chain(
        template=RISK_ASSESSMENT_PROMPT.template,
        input_vars=RISK_ASSESSMENT_PROMPT.input_vars,
        output_model=Risk,
        variables={
            "task_spec": (
                f"Problem: {task_spec.problem}\n"
                f"Acceptance Criteria: {', '.join(task_spec.acceptance_criteria)}\n"
                f"Constraints: {', '.join(task_spec.constraints)}\n"
                f"Out of Scope: {', '.join(task_spec.out_of_scope)}"
            )
        },
        agent="triage_agent",
        node="assess_risk",
        client=client,
        include_format_instructions=RISK_ASSESSMENT_PROMPT.include_format_instructions,
    )

    state["risk"] = response
    return state


@traced(
    "triage_step.detect_ambiguity",
    attributes=langgraph_node_attrs("triage", "detect_ambiguity"),
)
def detect_ambiguity(state: dict[str, Any]) -> dict[str, Any]:
    """
    Detect any ambiguity in the task specification and risk assessment using an LLM.

    Args:
        state: A dictionary containing the current state of the triage process, including the extracted task specification and assessed risk.
        ```
        {
            "task_spec": TaskSpec(...),
            "risk": Risk(...),
            // other state variables...
        }
        ```

    Returns:
        An updated state dictionary with the detected ambiguity and its status added.
    """

    task_spec_value = state.get("task_spec")
    risk_value = state.get("risk")
    task_spec = (
        task_spec_value
        if isinstance(task_spec_value, TaskSpec)
        else TaskSpec.model_validate(task_spec_value)
    )
    risk = risk_value if isinstance(risk_value, Risk) else Risk.model_validate(risk_value)

    response = invoke_chain(
        template=AMBIGUITY_DETECTION_PROMPT.template,
        input_vars=AMBIGUITY_DETECTION_PROMPT.input_vars,
        output_model=AmbiguityResult,
        variables={
            "task_spec": (
                f"Problem: {task_spec.problem}\n"
                f"Acceptance Criteria: {', '.join(task_spec.acceptance_criteria)}\n"
                f"Constraints: {', '.join(task_spec.constraints)}\n"
                f"Out of Scope: {', '.join(task_spec.out_of_scope)}"
            ),
            "risk": (f"Level: {risk.level.value}\nReasons: {', '.join(risk.reasons)}"),
        },
        agent="triage_agent",
        node="detect_ambiguity",
        client=client,
        include_format_instructions=AMBIGUITY_DETECTION_PROMPT.include_format_instructions,
    )

    state["ambiguity"] = response
    state["status"] = response.status
    return state


@traced(
    "triage_step.finalize",
    attributes=langgraph_node_attrs("triage", "finalize"),
)
def finalize(state: dict[str, Any]) -> dict[str, Any]:
    """
    Finalize the triage process by compiling the results into a TriageResult object.

    Args:
        state: A dictionary containing the current state of the triage process, including the extracted task specification, assessed risk, and detected ambiguity.
        ```
        {
            "task_spec": TaskSpec(...),
            "risk": Risk(...),
            "ambiguity": AmbiguityResult(...),
            // other state variables...
        }
        ```

    Returns:
        An updated state dictionary with the final triage result added.
    """

    task_spec_value = state.get("task_spec")
    risk_value = state.get("risk")
    ambiguity_value = state.get("ambiguity")

    task_spec = (
        task_spec_value
        if isinstance(task_spec_value, TaskSpec)
        else TaskSpec.model_validate(task_spec_value)
    )
    risk = risk_value if isinstance(risk_value, Risk) else Risk.model_validate(risk_value)
    ambiguity = (
        ambiguity_value
        if isinstance(ambiguity_value, AmbiguityResult)
        else AmbiguityResult.model_validate(ambiguity_value)
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
