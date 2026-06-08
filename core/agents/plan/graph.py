import json
from typing import Any, TypedDict
from langgraph.graph import END, StateGraph
from langgraph.types import RetryPolicy
from core.contracts.enums import PlanStatus
from core.contracts.plan import PlanStep
from core.contracts.triage import TriageResult
from pydantic import ValidationError
from langchain_core.exceptions import OutputParserException

class PlanState(TypedDict):
    triage_result: TriageResult
    repo_map: str
    strategy: str
    steps: list[PlanStep]
    assumptions: list[str]
    open_questions: list[str]
    status: PlanStatus
    final_output: dict[str, Any]

def is_output_parse_error(exc: Exception) -> bool:
    if isinstance(exc, (OutputParserException, ValidationError, json.JSONDecodeError)):
        return True
    msg = str(exc).lower()
    return "parse" in msg and "json" in msg

PARSER_RETRY_POLICY = RetryPolicy(
    initial_interval=0.5,
    backoff_factor=2.0,
    max_interval=8.0,
    max_attempts=3,
    jitter=True,
    retry_on=is_output_parse_error,
)

def build_plan_graph(nodes) -> StateGraph[PlanState]:
    graph = StateGraph(PlanState)
    graph.add_node("draft_plan", nodes.draft_plan, retry_policy=PARSER_RETRY_POLICY)
    graph.add_node("map_dependencies", nodes.map_dependencies, retry_policy=PARSER_RETRY_POLICY)
    graph.add_node("detect_ambiguity", nodes.detect_ambiguity, retry_policy=PARSER_RETRY_POLICY)
    graph.add_node("finalize", nodes.finalize, retry_policy=PARSER_RETRY_POLICY)

    graph.set_entry_point("draft_plan")

    graph.add_edge("draft_plan", "map_dependencies")
    graph.add_edge("map_dependencies", "detect_ambiguity")
    graph.add_edge("detect_ambiguity", "finalize")
    graph.add_edge("finalize", END)
    
    return graph.compile()
