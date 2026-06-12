from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from core.contracts.code import CodeOutput, CodeStep
from core.orchestrator.models import StageStatus
from core.contracts.plan import PlanStep
from infra.llm.chains import invoke_chain
from infra.llm.client import create_client
from infra.llm.prompts import load_prompt_catalog, require_prompt

from observability.tracing import traced, langgraph_node_attrs

client = create_client()
_PROMPTS_PATH = Path(__file__).with_name("prompts.yaml")
_PROMPTS = load_prompt_catalog(_PROMPTS_PATH)
FILE_GENERATION_PROMPT = require_prompt(_PROMPTS, "file_generation", source=_PROMPTS_PATH)


class GeneratedFile(BaseModel):
    path: str = Field(..., description="Relative path of the changed file.")
    content: str = Field(..., description="Full file content after changes.")


class GeneratedFilesPayload(BaseModel):
    files: list[GeneratedFile] = Field(
        default_factory=list, description="Changed files with full contents."
    )
    summary: str = Field(default="", description="Short summary of overall implementation.")


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []

    for value in values:
        item = value.strip()

        if not item or item in seen:
            continue

        seen.add(item)
        result.append(item)

    return result


def _truncate_text(value: str, max_chars: int = 6000) -> str:
    if len(value) <= max_chars:
        return value

    return value[:max_chars] + "\n...<truncated>..."


def _normalize_test_target(value: str) -> str:
    text = value.strip()
    if not text:
        return ""

    if "::" in text:
        text = text.split("::", 1)[0].strip()

    return text


def _is_probable_test_path(path: str) -> bool:
    normalized = path.strip().replace("\\", "/").lower()

    if not normalized:
        return False

    filename = normalized.rsplit("/", 1)[-1]

    return (
        normalized.startswith("tests/")
        or "/tests/" in normalized
        or filename.startswith("test_")
        or filename.endswith("_test.py")
    )


def _resolve_code_step(state: dict[str, Any]) -> CodeStep:
    step_value = state.get("step")

    if isinstance(step_value, PlanStep):
        return CodeStep(
            objective=step_value.objective,
            files=[item.strip() for item in step_value.files if item.strip()],
            tests=[item.strip() for item in step_value.tests if item.strip()],
        )

    notes = state.get("notes", {})

    if isinstance(notes, dict):
        note_step = notes.get("code_step")

        if isinstance(note_step, CodeStep):
            return note_step

        if isinstance(note_step, dict):
            return CodeStep.model_validate(note_step)

    return CodeStep(objective="", files=[], tests=[])


def _build_file_context(
    target_files: list[str],
    file_contents: dict[str, str],
    *,
    max_files: int = 10,
    max_chars_per_file: int = 7000,
) -> str:
    if not file_contents:
        return "No file contents provided."

    selected_files = target_files or list(file_contents.keys())
    chunks: list[str] = []

    for path in selected_files[:max_files]:
        raw_content = file_contents.get(path)

        if raw_content is None:
            chunks.append(f"### FILE: {path}\n<content not provided>")
            continue

        chunks.append(f"### FILE: {path}\n{_truncate_text(raw_content, max_chars_per_file)}")

    return "\n\n".join(chunks)


def _build_dependency_context(
    dependency_files: dict[str, str],
    *,
    max_files: int = 10,
    max_chars_per_file: int = 7000,
) -> str:
    if not dependency_files:
        return ""

    chunks: list[str] = []

    for path, content in list(dependency_files.items())[:max_files]:
        if not isinstance(path, str) or not isinstance(content, str):
            continue

        chunks.append(f"### FILE: {path}\n{_truncate_text(content, max_chars_per_file)}")

    return "\n\n".join(chunks)


def _block_state(
    state: dict[str, Any],
    *,
    reason: str,
    extra_notes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    notes = dict(state.get("notes", {}))

    notes["blocking_reason"] = reason

    if extra_notes:
        notes.update(extra_notes)

    state["status"] = StageStatus.BLOCKED
    state["notes"] = notes

    return state


@traced(
    "code_step.understand_task",
    attributes=langgraph_node_attrs("code", "understand_task"),
)
def understand_task(state: dict[str, Any]) -> dict[str, Any]:
    """
    Initial node to understand the task and populate notes for downstream nodes.

    Args:
        state: The current state of the code agent, expected to contain at least a "step" key with a PlanStep.
        ```
        {
            "step": PlanStep(
                objective="Implement feature X",
                files=["path/to/file1.py", "path/to/file2.py"],
                tests=["path/to/test_file1.py::test_func1", "path/to/test_file2.py"]
            ),
            // other state variables...
        }
        ```

    Returns:
        Updated state with extracted code step and notes for downstream processing.
    """

    # Extract the code step from the state
    code_step = _resolve_code_step(state)
    notes = dict(state.get("notes", {}))
    notes["code_step"] = code_step
    state["notes"] = notes

    return state


@traced(
    "code_step.locate_files",
    attributes=langgraph_node_attrs("code", "locate_files"),
)
def locate_files(state: dict[str, Any]) -> dict[str, Any]:
    """
    Node to process the code step and determine target files for generation.

    Args:
        state: The current state of the code agent, expected to contain a "notes" key with a "code_step" of type CodeStep.
        ```
        {
            "notes": {
                "code_step": {
                    "objective": "Implement feature X",
                    "files": ["path/to/file1.py", "path/to/file2.py"],
                    "tests": ["path/to/test_file1.py::test_func1", "path/to/test_file2.py"]
                }
            }
        }
        ```

    Returns:
        Updated state with "target_files" key containing the list of files to generate, and notes about the file selection.
    """

    # Extract the code step from the state
    code_step = _resolve_code_step(state)

    # Normalize and deduplicate file paths from the code step
    files = _dedupe_preserve_order(code_step.files)
    test_files = _dedupe_preserve_order(
        [
            normalized
            for normalized in (_normalize_test_target(test) for test in code_step.tests)
            if normalized
        ]
    )

    target_files = _dedupe_preserve_order(files + test_files)

    # Store the target files in the state for downstream nodes to use
    state["target_files"] = target_files

    notes = dict(state.get("notes", {}))
    notes["target_files"] = target_files
    notes["code_files"] = files
    notes["test_files"] = test_files
    notes["has_target_files"] = bool(target_files)

    state["notes"] = notes

    return state


@traced(
    "code_step.generate_patch",
    attributes=langgraph_node_attrs("code", "generate_patch"),
)
def generate_patch(state: dict[str, Any]) -> dict[str, Any]:
    """
    Node to generate code changes based on the objective and target files.

    Args:
        state: The current state of the code agent, expected to contain:
        - "notes" key with:
            - "code_step" of type CodeStep with the objective and tests.
            - "target_files" key with the list of files to generate.
            - Optional "repo_map" key with repository structure information.
            - Optional "file_contents" key with a dict of file paths to their contents for context.

    Returns:
        Updated state with "files" key containing the generated file contents, "files_changed" key with the list of changed files, and notes about the generation process.
    """

    # Extract the code step from the state
    code_step = _resolve_code_step(state)

    objective = code_step.objective.strip() or "Implement planned changes"

    # Extract QA feedback from the state, if available
    # Used to provide addtional context to the model for re-generation after a failed validation
    raw_qa_feedback = state.get("qa_feedback", "")
    qa_feedback_text = (
        raw_qa_feedback.strip()
        if isinstance(raw_qa_feedback, str) and raw_qa_feedback.strip()
        else "No previous QA failures."
    )

    # Extract the target files from the state. Block the generation if the target files are missing or invalid
    raw_target_files = state.get("target_files", [])
    target_files = (
        _dedupe_preserve_order(raw_target_files) if isinstance(raw_target_files, list) else []
    )

    if not target_files:
        return _block_state(
            state,
            reason="no_target_files",
            extra_notes={"objective": objective},
        )

    # Extract and normalize tests from the code step
    tests = [item.strip() for item in code_step.tests if item.strip()]

    # Extract repo map from state
    repo_map_value = state.get("repo_map", "")
    repo_map = repo_map_value if isinstance(repo_map_value, str) else ""

    # Build context from file contents
    file_contents_value = state.get("file_contents", {})
    file_contents = file_contents_value if isinstance(file_contents_value, dict) else {}

    file_context = _build_file_context(
        target_files,
        file_contents,
    )

    # Build context from dependency files
    dependency_files_value = state.get("dependency_files", {})
    dependency_files = dependency_files_value if isinstance(dependency_files_value, dict) else {}

    dependency_context = _build_dependency_context(dependency_files)
    if dependency_context:
        file_context = (
            f"{file_context}\n\n"
            "## PREVIOUSLY GENERATED FILES (from dependency steps, for reference)\n"
            f"{dependency_context}"
        )

    try:
        response = invoke_chain(
            template=FILE_GENERATION_PROMPT.template,
            input_vars=FILE_GENERATION_PROMPT.input_vars,
            output_model=GeneratedFilesPayload,
            variables={
                "objective": objective,
                "qa_feedback": qa_feedback_text,
                "target_files": "\n".join(target_files),
                "tests": ("\n".join(tests) if tests else "No tests specified."),
                "repo_map": (
                    _truncate_text(repo_map, 12000) if repo_map else "No repo map provided."
                ),
                "file_context": file_context,
            },
            agent="code_agent",
            node="generate_patch",
            client=client,
            include_format_instructions=FILE_GENERATION_PROMPT.include_format_instructions,
        )

    except Exception as exc:
        state["files"] = {}

        return _block_state(
            state,
            reason="file_generation_failed",
            extra_notes={
                "objective": objective,
                "generation_error": str(exc),
            },
        )

    # Process generated files and update the state
    generated_files: dict[str, str] = {}
    ignored_files: list[str] = []

    for file_item in response.files:
        path = file_item.path.strip()

        if not path:
            continue

        if path not in target_files:
            ignored_files.append(path)
            continue

        generated_files[path] = file_item.content

    # If no files were generated, block the state
    if not generated_files:
        state["files"] = {}

        return _block_state(
            state,
            reason="no_generated_files",
            extra_notes={
                "objective": objective,
                "ignored_generated_files": ignored_files,
            },
        )

    files_changed = _dedupe_preserve_order(list(generated_files.keys()))

    state["files"] = generated_files
    state["files_changed"] = files_changed

    state["notes"] = {
        "objective": objective,
        "execution_mode": "llm_full_files",
        "repo_map_used": bool(repo_map),
        "file_contents_available": len(file_contents),
        "target_files": target_files,
        "files_changed": files_changed,
        "tests": tests,
        "summary": response.summary.strip(),
        "ignored_generated_files": ignored_files,
    }

    state["status"] = StageStatus.OK

    return state


@traced(
    "code_step.validate_patch",
    attributes=langgraph_node_attrs("code", "validate_patch"),
)
def validate_patch(state: dict[str, Any]) -> dict[str, Any]:
    """
    Node to validate the generated code changes against the objective and target files.

    Args:
        state: The current state of the code agent, expected to contain:
        - "files" key with the generated file contents as a dict of file paths to contents.
        - "target_files" key with the list of files that were intended to be changed.
        - "notes" key with details about the generation process, including the original objective and any QA feedback.

    Returns:
        Updated state with validation results. If validation fails, the state will be blocked with a reason and
        notes about the validation failure. If validation succeeds, the state will be updated with a "files_changed" key and marked as OK.
    """

    notes = dict(state.get("notes", {}))

    # If blocked, return state
    if state.get("status") == StageStatus.BLOCKED and notes.get("blocking_reason"):
        return state

    # Extract generated files
    files_payload_value = state.get("files", {})
    files_payload = files_payload_value if isinstance(files_payload_value, dict) else {}

    if not files_payload:
        return _block_state(state, reason="no_generated_files")

    raw_files_changed = state.get("files_changed", [])
    files_changed = (
        _dedupe_preserve_order(raw_files_changed) if isinstance(raw_files_changed, list) else []
    )

    # if no files_changed, assume all the files in the payload are the changed files
    if not files_changed:
        files_changed = _dedupe_preserve_order(list(files_payload.keys()))

    raw_target_files = state.get("target_files", [])
    target_files = (
        _dedupe_preserve_order(raw_target_files) if isinstance(raw_target_files, list) else []
    )

    # Validate that the generated files are the intended files
    unexpected_paths = (
        [path for path in files_changed if path not in target_files] if target_files else []
    )

    if unexpected_paths:
        return _block_state(
            state,
            reason="unexpected_generated_files",
            extra_notes={
                "unexpected_paths": unexpected_paths,
                "target_files": target_files,
            },
        )

    if target_files and not any(path in files_changed for path in target_files):
        return _block_state(
            state,
            reason="generated_files_missing_target",
            extra_notes={
                "validated_files_changed": files_changed,
            },
        )

    # Validate that all planned tests have been generated
    planned_tests = [item.strip() for item in _resolve_code_step(state).tests if item.strip()]
    planned_test_paths = [
        normalized
        for normalized in (_normalize_test_target(test) for test in planned_tests)
        if normalized
    ]
    missing_test_paths = [path for path in planned_test_paths if path not in files_changed]

    if missing_test_paths:
        return _block_state(
            state,
            reason="generated_files_missing_tests",
            extra_notes={
                "missing_test_paths": missing_test_paths,
                "validated_files_changed": files_changed,
            },
        )

    # Validate that all generated files have non-empty content
    invalid_paths = [
        path
        for path, data in files_payload.items()
        if not isinstance(data, str) or not data.strip()
    ]
    if invalid_paths:
        return _block_state(
            state,
            reason="invalid_generated_file_content",
            extra_notes={
                "invalid_paths": invalid_paths,
            },
        )

    state["files_changed"] = files_changed

    notes["validated_files_changed"] = files_changed
    state["notes"] = notes

    return state


@traced(
    "code_step.finalize",
    attributes=langgraph_node_attrs("code", "finalize"),
)
def finalize(state: dict[str, Any]) -> dict[str, Any]:
    """
    Final node to produce the final output of the code agent.

    Args:
        state: The current state of the code agent, expected to contain:
        - "files" key with the generated file contents as a dict of file paths to contents.
        - "target_files" key with the list of files that were intended to be changed.
        - "notes" key with details about the generation and validation process, including
            the original objective and any QA feedback.

    Returns:
        Updated state with a "final_output" key containing the CodeOutput with the generated files and
        tests, and marked as OK. If the state was previously blocked, it will remain unchanged.
    """

    notes = state.get("notes", {})
    files_payload_value = state.get("files", {})
    files_payload = files_payload_value if isinstance(files_payload_value, dict) else {}

    generated_files = {
        path: data
        for path, data in files_payload.items()
        if isinstance(path, str) and isinstance(data, str)
    }

    raw_files_changed = state.get("files_changed", [])
    files_changed = (
        _dedupe_preserve_order(raw_files_changed) if isinstance(raw_files_changed, list) else []
    )

    if not files_changed and generated_files:
        files_changed = _dedupe_preserve_order(list(generated_files.keys()))

    planned_tests = [item.strip() for item in _resolve_code_step(state).tests if item.strip()]
    planned_test_paths = {
        normalized
        for normalized in (_normalize_test_target(test) for test in planned_tests)
        if normalized
    }

    files_map: dict[str, str] = {}
    tests_map: dict[str, str] = {}
    for path, data in generated_files.items():
        normalized_path = path.strip()
        if normalized_path in planned_test_paths or _is_probable_test_path(normalized_path):
            tests_map[normalized_path] = data
            continue

        files_map[normalized_path] = data

    result = CodeOutput(
        files_map=files_map,
        tests_map=tests_map,
    )

    notes["files_changed"] = files_changed
    notes["tests_generated"] = _dedupe_preserve_order(list(tests_map.keys()))

    state["notes"] = notes
    state["final_output"] = result.model_dump(mode="json")

    return state
