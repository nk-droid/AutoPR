import os
import sys
import pytest

os.environ.setdefault("AUTOPR_TRACE_EXPORTER", "none")

# Modules that some tests replace with fakes via sys.modules. Restoring them
# after each test keeps those fakes from leaking into unrelated tests.
_ISOLATED_MODULES = (
    "infra.storage.artifacts",
    "infra.storage.review_requests",
    "infra.slack.notification",
    "apps.api.routes.webhooks",
    "apps.api.routes.internal",
    "core.orchestrator.coordinator",
    "core.orchestrator.resume",
    "infra.github.webhook_handler",
)

@pytest.fixture(autouse=True)
def _restore_isolated_modules():
    saved = {name: sys.modules.get(name) for name in _ISOLATED_MODULES}
    try:
        yield
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

@pytest.fixture(autouse=True)
def _disable_qa_workspace_retention(monkeypatch):
    # Tests must never retain QA sandboxes, regardless of a developer .env.
    monkeypatch.setenv("AUTOPR_KEEP_QA_WORKSPACE", "false")

def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        parts = set(item.path.parts)
        if "e2e" in parts:
            item.add_marker(pytest.mark.e2e)
            continue
        if "integration" in parts:
            item.add_marker(pytest.mark.integration)
            continue
        if "unit" in parts:
            item.add_marker(pytest.mark.unit)
