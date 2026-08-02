from typing import Literal

from pydantic import BaseModel, Field


class ExtractedSignal(BaseModel):
    """One candidate pain-point/observation pulled out of a raw capture."""

    quote: str = Field(description="Verbatim quote or excerpt from the raw capture supporting this signal")
    signal_summary: str = Field(description="One-line paraphrase of this specific signal")
    segment: str = Field(description="User segment this signal applies to (refine the capture-level segment if the quote is more specific)")
    severity: float = Field(ge=0.0, le=1.0, description="Estimated severity/pain of this signal from 0.0 (minor) to 1.0 (severe blocker)")
    directness: Literal["direct_quote", "secondhand"] = Field(
        description="'direct_quote' if the quote is the speaker's own verbatim experience/statement; "
        "'secondhand' if they are relaying someone else's experience, a general impression, or hearsay"
    )


class ExtractedSignals(BaseModel):
    signals: list[ExtractedSignal] = Field(default_factory=list)


class NewProblemArea(BaseModel):
    title: str
    description: str


class SignalMatch(BaseModel):
    """LLM's judgment on how one signal relates to the existing insight library."""

    matched_insight_id: int | None = Field(
        default=None, description="ID of an existing insight this signal matches, or null if this is a new insight"
    )
    stance: Literal["support", "contradict", "neutral"] = Field(
        description="If matched: whether this signal supports, contradicts, or is neutral toward the matched insight. If new: always 'support'."
    )
    rationale: str = Field(description="Short justification for the match/no-match and stance decision")
    new_insight_title: str | None = Field(default=None, description="Required if matched_insight_id is null: a short title for the new insight")
    new_insight_summary: str | None = Field(default=None, description="Required if matched_insight_id is null: a one-line summary for the new insight")
    existing_problem_area_id: int | None = Field(
        default=None, description="Required if matched_insight_id is null and this fits an existing problem area: its ID"
    )
    new_problem_area: NewProblemArea | None = Field(
        default=None, description="Required if matched_insight_id is null and no existing problem area fits: title + description for a new one"
    )


class HypothesisProposal(BaseModel):
    statement: str = Field(description="The hypothesis statement, phrased as testable")
    is_new: bool = Field(description="True if this is a newly proposed hypothesis, false if revising an existing one")
    existing_hypothesis_id: int | None = Field(
        default=None, description="Required if is_new is false: the ID of the existing hypothesis being revised"
    )
    motivating_insight_ids: list[int] = Field(description="IDs of insights that motivate this hypothesis")


class InsightStanceNote(BaseModel):
    insight_id: int
    verdict: Literal["REINFORCED", "CONTRADICTED", "NEUTRAL"]
    note: str = Field(description="Short explanation of why this insight was reinforced/contradicted/neutral this run")


class HypothesisUpdateResult(BaseModel):
    hypotheses: list[HypothesisProposal] = Field(default_factory=list)
    insight_stance_notes: list[InsightStanceNote] = Field(default_factory=list)
