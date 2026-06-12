import uuid

from core.contracts.plan import PlanStep
from core.orchestrator.steps.code import _aggregate_coding_step, _dependency_levels


def test_dependency_levels_groups_into_waves() -> None:
    a = PlanStep(title="A", objective="a")
    b = PlanStep(title="B", objective="b", dependencies=[a.id])
    c = PlanStep(title="C", objective="c", dependencies=[a.id])
    d = PlanStep(title="D", objective="d", dependencies=[b.id, c.id])
    e = PlanStep(title="E", objective="e")

    # Pass scrambled; roots and ties keep original input order.
    levels = _dependency_levels([d, c, b, a, e])
    titles = [[s.title for s in level] for level in levels]

    assert titles[0] == ["A", "E"]  # independent roots
    assert set(titles[1]) == {"B", "C"}  # both depend only on A
    assert titles[2] == ["D"]


def test_dependency_levels_detects_cycle() -> None:
    x = PlanStep(title="X", objective="x")
    y = PlanStep(title="Y", objective="y", dependencies=[x.id])
    x.dependencies = [y.id]

    assert _dependency_levels([x, y]) is None


def test_dependency_levels_ignores_dependencies_outside_the_set() -> None:
    a = PlanStep(title="A", objective="a", dependencies=[uuid.uuid4()])
    levels = _dependency_levels([a])
    assert [s.title for level in levels for s in level] == ["A"]


def test_aggregate_coding_step_unions_files_tests_objectives() -> None:
    a = PlanStep(title="A", objective="obj-a", files=["x.py"], tests=["t_x.py"])
    b = PlanStep(title="B", objective="obj-b", files=["x.py", "y.py"], tests=["t_y.py"])

    agg = _aggregate_coding_step([a, b])

    assert agg.files == ["x.py", "y.py"]  # deduped, order preserved
    assert agg.tests == ["t_x.py", "t_y.py"]
    assert "obj-a" in agg.objective and "obj-b" in agg.objective
