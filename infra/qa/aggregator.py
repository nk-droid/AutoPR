from infra.qa.models import CoverageResult, LintResult, QAResult, SecurityResult, TestResult

class QAResultAggregator:
    def aggregate(
        self,
        test_result: TestResult,
        coverage_result: CoverageResult,
        lint_result: LintResult,
        security_result: SecurityResult,
    ) -> QAResult:
        
        tests_passed = test_result.success
        coverage_passed = coverage_result.success and coverage_result.threshold_passed
        lint_passed = lint_result.success
        security_passed = security_result.success
        
        overall = all([tests_passed, coverage_passed, lint_passed, security_passed])

        return QAResult(
            passed=overall,
            tests_passed=tests_passed,
            coverage_passed=coverage_passed,
            lint_passed=lint_passed,
            security_passed=security_passed,
            summary={
                "tests": {"passed": test_result.passed, "failed": test_result.failed},
                "coverage": coverage_result.coverage_pct,
                "lint_issues": len(lint_result.issues),
                "security_issues": len(security_result.issues),
            },
        )
