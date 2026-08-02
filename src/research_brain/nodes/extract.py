from research_brain.llm import get_llm
from research_brain.models import ExtractedSignals
from research_brain.prompts import EXTRACT_SYSTEM_PROMPT, EXTRACT_USER_TEMPLATE
from research_brain.state import GraphState


def extract_signals(state: GraphState) -> dict:
    llm = get_llm().with_structured_output(ExtractedSignals)
    user_msg = EXTRACT_USER_TEMPLATE.format(
        title=state["capture_title"],
        date=state["capture_date"],
        method=state["method"],
        segment=state["segment"],
        raw_text=state["raw_text"],
    )
    result: ExtractedSignals = llm.invoke(
        [("system", EXTRACT_SYSTEM_PROMPT), ("user", user_msg)]
    )
    signals = [s.model_dump() for s in result.signals]
    return {"signals": signals}
