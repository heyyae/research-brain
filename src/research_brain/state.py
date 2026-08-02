import operator
from typing import Annotated, Literal, TypedDict


class ExtractedSignal(TypedDict):
    quote: str
    signal_summary: str
    segment: str
    severity: float
    directness: Literal["direct_quote", "secondhand"]


class NewProblemArea(TypedDict):
    title: str
    description: str


class InsightDelta(TypedDict):
    insight_id: int
    insight_title: str
    is_new: bool
    stance: Literal["support", "contradict", "neutral"]
    old_confidence: float | None
    new_confidence: float


class ProblemAreaDelta(TypedDict):
    problem_area_id: int
    title: str
    old_priority: float | None
    new_priority: float


class HypothesisUpdate(TypedDict):
    problem_area_id: int
    statement: str
    is_new: bool
    motivating_insight_ids: list[int]
    insight_stance_notes: list[dict]


class GraphState(TypedDict, total=False):
    # ---- input ----
    capture_id: int
    raw_text: str
    capture_title: str
    capture_date: str
    method: str
    segment: str

    # ---- node 1 output ----
    signals: list[ExtractedSignal]

    # ---- node 2 output (sequential match + apply per signal) ----
    insight_deltas: list[InsightDelta]
    affected_problem_area_ids: list[int]

    # ---- node 4 output (deterministic) ----
    problem_area_deltas: list[ProblemAreaDelta]

    # ---- node 5 output (LLM, fan-out via Send) ----
    hypothesis_updates: Annotated[list[HypothesisUpdate], operator.add]

    # ---- node 6 output ----
    report_path: str | None
    report_text: str | None

    # ---- error handling ----
    errors: Annotated[list[str], operator.add]


class HypothesisNodeInput(TypedDict):
    """Per-Send input for the update_hypotheses node."""

    problem_area_id: int
