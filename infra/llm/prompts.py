from pathlib import Path
from string import Formatter

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator


class PromptConfig(BaseModel):
    template: str
    input_vars: list[str] = Field(default_factory=list)
    include_format_instructions: bool = False

    @field_validator("template")
    @classmethod
    def _validate_template(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("template must be a non-empty string")
        return value

    @field_validator("input_vars")
    @classmethod
    def _validate_input_vars(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        normalized: list[str] = []
        for item in value:
            name = item.strip()
            if not name:
                raise ValueError("input_vars cannot contain empty names")
            if name in seen:
                raise ValueError(f"duplicate input var '{name}'")
            seen.add(name)
            normalized.append(name)
        return normalized


def _extract_template_fields(template: str) -> set[str]:
    fields: set[str] = set()
    for _literal_text, field_name, _format_spec, _conversion in Formatter().parse(template):
        if not field_name:
            continue
        base_name = field_name.split(".", 1)[0].split("[", 1)[0].strip()
        if base_name:
            fields.add(base_name)
    return fields


def load_prompt_catalog(path: str | Path) -> dict[str, PromptConfig]:
    catalog_path = Path(path)

    if not catalog_path.exists():
        raise FileNotFoundError(f"Prompt catalog not found: {catalog_path}")

    raw = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))

    if raw is None:
        raw = {}

    if not isinstance(raw, dict):
        raise ValueError(f"Prompt catalog must be a mapping: {catalog_path}")

    prompts: dict[str, PromptConfig] = {}

    for key, value in raw.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError(f"Prompt key must be a non-empty string: {catalog_path}")

        if not isinstance(value, dict):
            raise ValueError(
                f"Prompt '{key}' must map to an object with template/input_vars: {catalog_path}"
            )

        try:
            prompt = PromptConfig.model_validate(value)
        except ValidationError as exc:
            raise ValueError(f"Invalid prompt '{key}' in {catalog_path}: {exc}") from exc

        declared_vars = set(prompt.input_vars)
        template_fields = _extract_template_fields(prompt.template)
        missing_in_template = declared_vars - template_fields
        undeclared_in_inputs = template_fields - declared_vars

        if missing_in_template or undeclared_in_inputs:
            raise ValueError(
                f"Prompt '{key}' variable mismatch in {catalog_path}: "
                f"missing_in_template={sorted(missing_in_template)}, "
                f"undeclared_in_inputs={sorted(undeclared_in_inputs)}"
            )

        prompts[key.strip()] = prompt

    return prompts


def require_prompt(
    catalog: dict[str, PromptConfig],
    key: str,
    *,
    source: str | Path,
) -> PromptConfig:
    prompt = catalog.get(key)

    if prompt is None:
        raise KeyError(f"Prompt '{key}' not found in catalog: {source}")

    return prompt
