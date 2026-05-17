from typing import Any

from langchain_core.output_parsers import PydanticOutputParser
from pydantic import BaseModel, Field

from core.contracts.code import CodeOutput, CodeStep
from core.contracts.enums import PlanStatus
from core.contracts.plan import PlanStep
from infra.llm.client import create_client, create_prompt

client = create_client()

FILE_GENERATION_PROMPT = """
You are implementing one coding step. Generate the full resulting content for each changed file.

Objective:
{objective}

Target files:
{target_files}

Planned tests:
{tests}

Repository map:
{repo_map}

Existing file contents:
{file_context}

Return JSON only. For each file you output:
1. path: relative file path
2. content: full file content after applying changes

Rules:
1. Only return files relevant to this step.
2. content must be complete file contents, not a patch or snippet.
3. If a target file does not exist in context, create full content from scratch.
4. Keep output deterministic and concise.
5. If target files include tests, generate those test files too.

{format_instructions}
"""

class GeneratedFile(BaseModel):
    path: str = Field(..., description="Relative path of the changed file.")
    content: str = Field(..., description="Full file content after changes.")

class GeneratedFilesPayload(BaseModel):
    files: list[GeneratedFile] = Field(default_factory=list, description="Changed files with full contents.")
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
            files=[
                item.strip()
                for item in step_value.files
                if item.strip()
            ],
            tests=[
                item.strip()
                for item in step_value.tests
                if item.strip()
            ],
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
            chunks.append(
                f"### FILE: {path}\n<content not provided>"
            )
            continue

        chunks.append(
            f"### FILE: {path}\n"
            f"{_truncate_text(raw_content, max_chars_per_file)}"
        )

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

    state["status"] = PlanStatus.BLOCKED
    state["notes"] = notes

    return state

def understand_task(state: dict[str, Any]) -> dict[str, Any]:
    code_step = _resolve_code_step(state)

    notes = dict(state.get("notes", {}))
    notes["code_step"] = code_step

    state["notes"] = notes

    return state

def locate_files(state: dict[str, Any]) -> dict[str, Any]:
    code_step = _resolve_code_step(state)

    files = _dedupe_preserve_order(code_step.files)

    test_files = _dedupe_preserve_order(
        [
            normalized
            for normalized in (
                _normalize_test_target(test)
                for test in code_step.tests
            )
            if normalized
        ]
    )

    target_files = _dedupe_preserve_order(
        files + test_files
    )

    state["target_files"] = target_files

    notes = dict(state.get("notes", {}))
    notes["target_files"] = target_files
    notes["code_files"] = files
    notes["test_files"] = test_files
    notes["has_target_files"] = bool(target_files)

    state["notes"] = notes

    return state


def generate_patch(state: dict[str, Any]) -> dict[str, Any]:
    code_step = _resolve_code_step(state)

    objective = (
        code_step.objective.strip()
        or "Implement planned changes"
    )

    raw_target_files = state.get("target_files", [])

    target_files = (
        _dedupe_preserve_order(raw_target_files)
        if isinstance(raw_target_files, list)
        else []
    )

    tests = [
        item.strip()
        for item in code_step.tests
        if item.strip()
    ]

    if not target_files:
        return _block_state(
            state,
            reason="no_target_files",
            extra_notes={"objective": objective},
        )

    parser = PydanticOutputParser(pydantic_object=GeneratedFilesPayload)

    prompt = create_prompt(
        FILE_GENERATION_PROMPT,
        [
            "objective",
            "target_files",
            "tests",
            "repo_map",
            "file_context",
            "format_instructions",
        ],
    )

    chain = prompt | client | parser

    repo_map_value = state.get("repo_map", "")
    repo_map = (
        repo_map_value
        if isinstance(repo_map_value, str)
        else ""
    )

    file_contents_value = state.get("file_contents", {})
    file_contents = (
        file_contents_value
        if isinstance(file_contents_value, dict)
        else {}
    )

    file_context = _build_file_context(
        target_files,
        file_contents,
    )

    try:
        response = chain.invoke(
            {
                "objective": objective,
                "target_files": "\n".join(target_files),
                "tests": (
                    "\n".join(tests)
                    if tests
                    else "No tests specified."
                ),
                "repo_map": (
                    _truncate_text(repo_map, 12000)
                    if repo_map
                    else "No repo map provided."
                ),
                "file_context": file_context,
                "format_instructions": (
                    parser.get_format_instructions()
                ),
            }
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

    files_changed = _dedupe_preserve_order(
        list(generated_files.keys())
    )

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

    state["status"] = PlanStatus.OK

    return state

def validate_patch(state: dict[str, Any]) -> dict[str, Any]:
    notes = dict(state.get("notes", {}))

    if (
        state.get("status") == PlanStatus.BLOCKED
        and notes.get("blocking_reason")
    ):
        return state

    files_payload_value = state.get("files", {})

    files_payload = (
        files_payload_value
        if isinstance(files_payload_value, dict)
        else {}
    )

    if not files_payload:
        return _block_state(
            state,
            reason="no_generated_files",
        )

    raw_files_changed = state.get("files_changed", [])

    files_changed = (
        _dedupe_preserve_order(raw_files_changed)
        if isinstance(raw_files_changed, list)
        else []
    )

    if not files_changed:
        files_changed = _dedupe_preserve_order(
            list(files_payload.keys())
        )

    raw_target_files = state.get("target_files", [])

    target_files = (
        _dedupe_preserve_order(raw_target_files)
        if isinstance(raw_target_files, list)
        else []
    )

    unexpected_paths = (
        [
            path
            for path in files_changed
            if path not in target_files
        ]
        if target_files
        else []
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

    if (
        target_files
        and not any(
            path in files_changed
            for path in target_files
        )
    ):
        return _block_state(
            state,
            reason="generated_files_missing_target",
            extra_notes={
                "validated_files_changed": files_changed,
            },
        )

    planned_tests = [
        item.strip()
        for item in _resolve_code_step(state).tests
        if item.strip()
    ]

    planned_test_paths = [
        normalized
        for normalized in (
            _normalize_test_target(test)
            for test in planned_tests
        )
        if normalized
    ]

    missing_test_paths = [
        path
        for path in planned_test_paths
        if path not in files_changed
    ]

    if missing_test_paths:
        return _block_state(
            state,
            reason="generated_files_missing_tests",
            extra_notes={
                "missing_test_paths": missing_test_paths,
                "validated_files_changed": files_changed,
            },
        )

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

def finalize(state: dict[str, Any]) -> dict[str, Any]:
    notes = dict(state.get("notes", {}))

    files_payload_value = state.get("files", {})

    files_payload = (
        files_payload_value
        if isinstance(files_payload_value, dict)
        else {}
    )

    generated_files = {
        path: data
        for path, data in files_payload.items()
        if isinstance(path, str)
        and isinstance(data, str)
    }

    raw_files_changed = state.get("files_changed", [])

    files_changed = (
        _dedupe_preserve_order(raw_files_changed)
        if isinstance(raw_files_changed, list)
        else []
    )

    if not files_changed and generated_files:
        files_changed = _dedupe_preserve_order(
            list(generated_files.keys())
        )

    planned_tests = [
        item.strip()
        for item in _resolve_code_step(state).tests
        if item.strip()
    ]

    planned_test_paths = {
        normalized
        for normalized in (
            _normalize_test_target(test)
            for test in planned_tests
        )
        if normalized
    }

    files_map: dict[str, str] = {}
    tests_map: dict[str, str] = {}

    for path, data in generated_files.items():
        normalized_path = path.strip()

        if (
            normalized_path in planned_test_paths
            or _is_probable_test_path(normalized_path)
        ):
            tests_map[normalized_path] = data
            continue

        files_map[normalized_path] = data

    result = CodeOutput(
        files_map=files_map,
        tests_map=tests_map,
    )

    notes["files_changed"] = files_changed
    notes["tests_generated"] = _dedupe_preserve_order(
        list(tests_map.keys())
    )

    state["notes"] = notes
    state["final_output"] = result.model_dump(mode="json")

    return state