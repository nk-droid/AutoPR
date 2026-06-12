from infra.qa.aggregator import QAResultAggregator
from infra.qa import models as qa_models


def test_aggregate_all_checks_pass() -> None:
    aggregator = QAResultAggregator()
    result = aggregator.aggregate(
        test_result=qa_models.TestResult(success=True, total=3, passed=3, failed=0),
        coverage_result=qa_models.CoverageResult(
            success=True, coverage_pct=91.0, threshold_passed=True
        ),
        lint_result=qa_models.LintResult(success=True, issues=[]),
        security_result=qa_models.SecurityResult(success=True, issues=[]),
    )
    assert result.passed is True
    assert result.tests_passed is True
    assert result.coverage_passed is True
    assert result.lint_passed is True
    assert result.security_passed is True
    assert result.summary["tests"] == {"passed": 3, "failed": 0}
    assert result.summary["coverage"] == 91.0
    assert result.summary["lint_issues"] == 0
    assert result.summary["security_issues"] == 0


def test_aggregate_marks_failures_and_counts() -> None:
    aggregator = QAResultAggregator()
    result = aggregator.aggregate(
        test_result=qa_models.TestResult(success=False, total=4, passed=2, failed=2),
        coverage_result=qa_models.CoverageResult(
            success=True, coverage_pct=62.5, threshold_passed=False
        ),
        lint_result=qa_models.LintResult(
            success=False,
            issues=[qa_models.LintIssue(file="a.py", line=8, code="E999", message="problem")],
        ),
        security_result=qa_models.SecurityResult(
            success=False,
            issues=[qa_models.SecurityIssue(severity="HIGH", file="a.py", line=11, issue="unsafe")],
        ),
    )
    assert result.passed is False
    assert result.tests_passed is False
    assert result.coverage_passed is False
    assert result.lint_passed is False
    assert result.security_passed is False
    assert result.summary["tests"] == {"passed": 2, "failed": 2}
    assert result.summary["lint_issues"] == 1
    assert result.summary["security_issues"] == 1
