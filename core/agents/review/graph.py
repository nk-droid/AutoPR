from typing import Any, TypedDict
from langgraph.graph import END, StateGraph
from langgraph.types import RetryPolicy
from core.agents.review.nodes import MergeabilityUnknownError
from core.contracts.review import ReviewCheck
from core.contracts.review import LLMMergeRiskReview
from core.contracts.run_context import PRToMergeContext
from core.orchestrator.models import StageStatus


class ReviewState(TypedDict):
    context: PRToMergeContext
    status: StageStatus
    summary: str
    checks: list[ReviewCheck]
    required_actions: list[str]
    notes: dict[str, Any]
    llm_review: LLMMergeRiskReview | None
    final_output: dict[str, Any]
    allow_unknown: bool


def is_mergeable_status_unknown(exc: Exception) -> bool:
    return isinstance(exc, MergeabilityUnknownError)


# Evaluate review -> LLM merge-risk review -> Finalize
def build_review_graph(nodes) -> StateGraph[ReviewState]:
    graph = StateGraph(ReviewState)
    graph.add_node(
        "evaluate_review",
        nodes.evaluate_review,
        retry_policy=RetryPolicy(
            max_attempts=3,
            initial_interval=1.0,
            backoff_factor=2.0,
            retry_on=is_mergeable_status_unknown,
        ),
    )
    graph.add_node("llm_merge_risk_review", nodes.llm_merge_risk_review)
    graph.add_node("finalize", nodes.finalize)

    graph.set_entry_point("evaluate_review")

    graph.add_edge("evaluate_review", "llm_merge_risk_review")
    graph.add_edge("llm_merge_risk_review", "finalize")
    graph.add_edge("finalize", END)

    return graph.compile()
