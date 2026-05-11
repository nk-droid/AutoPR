from typing import Any, Dict
from pydantic import BaseModel, Field
from langchain_core.output_parsers import PydanticOutputParser
from core.contracts.code import CodeOutput, CodeStep
from infra.llm.client import create_client, create_prompt

client = create_client()

TASK_UNDERSTANDING_PROMPT = """
You are a code understanding agent responsible for converting a task specification into an implementation-ready code step.

Here is the task specification:
```
{task_spec}
```

Please provide JSON in this shape:
{{
  "objective": "A clear implementation objective.",
  "files": ["path/to/file.py"],
  "tests": ["tests/unit/test_example.py::test_case"]
}}
"""

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

def _to_string_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    result: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        item = value.strip()
        if not item:
            continue
        result.append(item)
    return result

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

def _normalize_test_target(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if "::" in text:
        text = text.split("::", 1)[0].strip()
    return text

def _is_probable_test_path(path: str) -> bool:
    normalized = str(path or "").strip().replace("\\", "/").lower()
    if not normalized:
        return False
    filename = normalized.rsplit("/", 1)[-1]
    return (
        normalized.startswith("tests/")
        or "/tests/" in normalized
        or filename.startswith("test_")
        or filename.endswith("_test.py")
    )

def _coerce_code_step_from_plan_step(step: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(step, dict):
        return None
    if "objective" not in step:
        return None
    objective = str(step.get("objective", "")).strip()
    if not objective:
        return None
    return {
        "objective": objective,
        "files": _to_string_list(step.get("files", [])),
        "tests": _to_string_list(step.get("tests", [])),
    }

def _coerce_task_spec_text(step: dict[str, Any]) -> str:
    problem = str(step.get("problem", "")).strip()
    acceptance_criteria = _to_string_list(step.get("acceptance_criteria", []))
    constraints = _to_string_list(step.get("constraints", []))
    out_of_scope = _to_string_list(step.get("out_of_scope", []))
    return (
        f"Problem: {problem}\n"
        f"Acceptance Criteria: {', '.join(acceptance_criteria)}\n"
        f"Constraints: {', '.join(constraints)}\n"
        f"Out of Scope: {', '.join(out_of_scope)}"
    )

def _resolve_code_step(state: Dict[str, Any]) -> dict[str, Any]:
    notes = state.get("notes", {})
    if isinstance(notes, dict):
        note_step = notes.get("code_step", {})
        if isinstance(note_step, dict):
            coerced = _coerce_code_step_from_plan_step(note_step)
            if coerced is not None:
                return coerced
    step = state.get("step", {})
    if isinstance(step, dict):
        coerced = _coerce_code_step_from_plan_step(step)
        if coerced is not None:
            return coerced
    return {"objective": "", "files": [], "tests": []}

def _build_file_context(
    target_files: list[str],
    file_contents: dict[str, str],
    *,
    max_files: int = 10,
    max_chars_per_file: int = 7000,
) -> str:
    if not isinstance(file_contents, dict) or not file_contents:
        return "No file contents provided."
    selected_files = target_files or list(file_contents.keys())
    chunks: list[str] = []
    for path in selected_files[:max_files]:
        raw_content = file_contents.get(path)
        if not isinstance(raw_content, str):
            chunks.append(f"### FILE: {path}\n<content not provided>")
            continue
        chunks.append(f"### FILE: {path}\n{_truncate_text(raw_content, max_chars_per_file)}")
    return "\n\n".join(chunks)

def understand_task(state: Dict[str, Any]) -> Dict[str, Any]:
    step = state.get("step", {})
    plan_step = _coerce_code_step_from_plan_step(step)
    if plan_step is None:
        parser = PydanticOutputParser(pydantic_object=CodeStep)
        prompt = create_prompt(
            TASK_UNDERSTANDING_PROMPT + "\n\n{format_instructions}",
            ["task_spec", "format_instructions"],
        )
        chain = prompt | client | parser
        task_spec = _coerce_task_spec_text(step if isinstance(step, dict) else {})
        response = chain.invoke(
            {
                "task_spec": task_spec,
                "format_instructions": parser.get_format_instructions(),
            }
        )
        plan_step = {
            "objective": response.objective,
            "files": response.files,
            "tests": response.tests,
        }
    notes = dict(state.get("notes", {}))
    notes["code_step"] = plan_step
    state["notes"] = notes
    return state

def locate_files(state: Dict[str, Any]) -> Dict[str, Any]:
    code_step = _resolve_code_step(state)
    files = _dedupe_preserve_order(_to_string_list(code_step.get("files", [])))
    test_files = _dedupe_preserve_order(
        [
            normalized
            for normalized in (_normalize_test_target(test) for test in _to_string_list(code_step.get("tests", [])))
            if normalized
        ]
    )
    target_files = _dedupe_preserve_order(files + test_files)
    state["target_files"] = target_files
    notes = dict(state.get("notes", {}))
    notes["target_files"] = target_files
    notes["code_files"] = files
    notes["test_files"] = test_files
    notes["has_target_files"] = bool(target_files)
    state["notes"] = notes
    return state

def generate_patch(state: Dict[str, Any]) -> Dict[str, Any]:
    code_step = _resolve_code_step(state)
    objective = str(code_step.get("objective", "")).strip() or "Implement planned changes"
    target_files = _dedupe_preserve_order(_to_string_list(state.get("target_files", [])))
    tests = _to_string_list(code_step.get("tests", []))
    if not target_files:
        state["status"] = "blocked"
        notes = dict(state.get("notes", {}))
        notes["blocking_reason"] = "no_target_files"
        notes["objective"] = objective
        state["notes"] = notes
        return state
    parser = PydanticOutputParser(pydantic_object=GeneratedFilesPayload)
    prompt = create_prompt(
        FILE_GENERATION_PROMPT,
        ["objective", "target_files", "tests", "repo_map", "file_context", "format_instructions"],
    )
    chain = prompt | client | parser
    repo_map = str(state.get("repo_map", "")).strip()
    file_contents = state.get("file_contents", {})
    file_context = _build_file_context(target_files, file_contents if isinstance(file_contents, dict) else {})
    try:
        response = chain.invoke(
            {
                "objective": objective,
                "target_files": "\n".join(target_files),
                "tests": "\n".join(tests) if tests else "No tests specified.",
                "repo_map": _truncate_text(repo_map, 12000) if repo_map else "No repo map provided.",
                "file_context": file_context,
                "format_instructions": parser.get_format_instructions(),
            }
        )
    except Exception as exc:
        state["status"] = "blocked"
        notes = dict(state.get("notes", {}))
        notes["blocking_reason"] = "file_generation_failed"
        notes["generation_error"] = str(exc)
        notes["objective"] = objective
        state["notes"] = notes
        state["files"] = {}
        return state
    generated_files: dict[str, str] = {}
    ignored_files: list[str] = []
    for file_item in response.files:
        path = str(file_item.path).strip()
        if not path:
            continue
        if target_files and path not in target_files:
            ignored_files.append(path)
            continue
        generated_files[path] = str(file_item.content)
    if not generated_files:
        state["status"] = "blocked"
        notes = dict(state.get("notes", {}))
        notes["blocking_reason"] = "no_generated_files"
        notes["objective"] = objective
        notes["ignored_generated_files"] = ignored_files
        state["notes"] = notes
        state["files"] = {}
        return state
    files_changed = _dedupe_preserve_order(list(generated_files.keys()))
    state["files"] = generated_files
    state["files_changed"] = files_changed
    state["notes"] = {
        "objective": objective,
        "execution_mode": "llm_full_files",
        "repo_map_used": bool(repo_map),
        "file_contents_available": len(file_contents) if isinstance(file_contents, dict) else 0,
        "target_files": target_files,
        "files_changed": files_changed,
        "tests": tests,
        "summary": str(response.summary).strip(),
        "ignored_generated_files": ignored_files,
    }
    state["status"] = "ok"
    return state

def validate_patch(state: Dict[str, Any]) -> Dict[str, Any]:
    notes = dict(state.get("notes", {}))
    if str(state.get("status", "")).strip().lower() == "blocked" and notes.get("blocking_reason"):
        return state
    files_payload = state.get("files", {})
    if not isinstance(files_payload, dict) or not files_payload:
        state["status"] = "blocked"
        notes["blocking_reason"] = "no_generated_files"
        state["notes"] = notes
        return state
    files_changed = _dedupe_preserve_order(_to_string_list(state.get("files_changed", [])))
    if not files_changed:
        files_changed = _dedupe_preserve_order(list(files_payload.keys()))
    target_files = _to_string_list(state.get("target_files", []))
    unexpected_paths: list[str] = []
    if target_files:
        for path in files_changed:
            if path not in target_files:
                unexpected_paths.append(path)
    if unexpected_paths:
        state["status"] = "blocked"
        notes["blocking_reason"] = "unexpected_generated_files"
        notes["unexpected_paths"] = unexpected_paths
        notes["target_files"] = target_files
        state["notes"] = notes
        return state
    if target_files and not any(path in files_changed for path in target_files):
        state["status"] = "blocked"
        notes["blocking_reason"] = "generated_files_missing_target"
        notes["validated_files_changed"] = files_changed
        state["notes"] = notes
        return state
    planned_tests = _to_string_list(notes.get("tests", []))
    planned_test_paths = [
        normalized
        for normalized in (_normalize_test_target(test) for test in planned_tests)
        if normalized
    ]
    missing_test_paths = [path for path in planned_test_paths if path not in files_changed]
    if missing_test_paths:
        state["status"] = "blocked"
        notes["blocking_reason"] = "generated_files_missing_tests"
        notes["missing_test_paths"] = missing_test_paths
        notes["validated_files_changed"] = files_changed
        state["notes"] = notes
        return state
    invalid_paths: list[str] = []
    for path, data in files_payload.items():
        if not isinstance(data, str) or not data.strip():
            invalid_paths.append(path)
    if invalid_paths:
        state["status"] = "blocked"
        notes["blocking_reason"] = "invalid_generated_file_content"
        notes["invalid_paths"] = invalid_paths
        state["notes"] = notes
        return state
    state["files_changed"] = files_changed
    notes["validated_files_changed"] = files_changed
    state["notes"] = notes
    return state

def finalize(state: Dict[str, Any]) -> Dict[str, Any]:
    notes = dict(state.get("notes", {}))
    files_payload = state.get("files", {})
    generated_files: dict[str, str] = {}
    if isinstance(files_payload, dict):
        for path, data in files_payload.items():
            if not isinstance(path, str):
                continue
            if not isinstance(data, str):
                continue
            generated_files[path] = data
    files_changed = _dedupe_preserve_order(_to_string_list(state.get("files_changed", [])))
    if not files_changed and generated_files:
        files_changed = _dedupe_preserve_order(list(generated_files.keys()))
    code_step = _resolve_code_step(state)
    planned_tests = _to_string_list(code_step.get("tests", []))
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
    state["final_output"] = result.model_dump()
    return state
