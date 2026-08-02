from langgraph.graph import END, START, StateGraph

from research_brain.nodes.extract import extract_signals
from research_brain.nodes.hypotheses import fan_out_to_hypotheses, update_hypotheses
from research_brain.nodes.match import match_and_apply_signals, route_after_extract
from research_brain.nodes.priorities import recompute_priorities
from research_brain.report import generate_report
from research_brain.state import GraphState


def build_graph():
    graph = StateGraph(GraphState)

    graph.add_node("extract_signals", extract_signals)
    graph.add_node("match_and_apply_signals", match_and_apply_signals)
    graph.add_node("recompute_priorities", recompute_priorities)
    graph.add_node("update_hypotheses", update_hypotheses)
    graph.add_node("generate_report", generate_report)

    graph.add_edge(START, "extract_signals")
    graph.add_conditional_edges(
        "extract_signals",
        route_after_extract,
        {"generate_report": "generate_report", "match_and_apply_signals": "match_and_apply_signals"},
    )
    graph.add_edge("match_and_apply_signals", "recompute_priorities")
    # fan_out_to_hypotheses returns "generate_report" or a list of Send(...) objects
    # targeting "update_hypotheses" — both are valid conditional-edge destinations,
    # so no path_map is needed here. Hypothesis generation for independent
    # already-committed problem areas is safe to parallelize, unlike signal matching.
    graph.add_conditional_edges("recompute_priorities", fan_out_to_hypotheses)
    graph.add_edge("update_hypotheses", "generate_report")
    graph.add_edge("generate_report", END)

    return graph.compile()
