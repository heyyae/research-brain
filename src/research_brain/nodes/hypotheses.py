from langgraph.types import Send

from research_brain import db
from research_brain.config import HYPOTHESIS_TOP_N
from research_brain.llm import get_llm
from research_brain.models import HypothesisUpdateResult
from research_brain.prompts import HYPOTHESES_SYSTEM_PROMPT, HYPOTHESES_USER_TEMPLATE
from research_brain.state import GraphState, HypothesisNodeInput


def fan_out_to_hypotheses(state: GraphState) -> list[Send] | str:
    deltas = state.get("problem_area_deltas") or []
    if not deltas:
        return "generate_report"
    top = sorted(deltas, key=lambda d: d["new_priority"], reverse=True)[:HYPOTHESIS_TOP_N]
    return [Send("update_hypotheses", {"problem_area_id": d["problem_area_id"]}) for d in top]


def update_hypotheses(input: HypothesisNodeInput) -> dict:
    problem_area_id = input["problem_area_id"]

    with db.connect() as conn:
        area_row = db.get_problem_area(conn, problem_area_id)
        insight_rows = db.list_insights(conn, problem_area_id=problem_area_id)
        existing_hyp_rows = db.list_hypotheses(conn, problem_area_id)

        insights_block = "\n".join(
            f"{r['id']} | {r['title']} | {r['summary']} | confidence={r['confidence_score']:.1f} "
            f"| support={r['support_count']} contradict={r['contradict_count']}"
            for r in insight_rows
        ) or "(none)"

        existing_hypotheses_block = "\n".join(
            f"{r['id']} | {r['statement']}" for r in existing_hyp_rows
        ) or "(none yet)"

    llm = get_llm().with_structured_output(HypothesisUpdateResult)
    user_msg = HYPOTHESES_USER_TEMPLATE.format(
        title=area_row["title"],
        description=area_row["description"] or "(no description)",
        priority_score=area_row["priority_score"],
        insights_block=insights_block,
        recent_touches_block=insights_block,
        existing_hypotheses_block=existing_hypotheses_block,
    )
    result: HypothesisUpdateResult = llm.invoke(
        [("system", HYPOTHESES_SYSTEM_PROMPT), ("user", user_msg)]
    )

    with db.connect() as conn:
        for proposal in result.hypotheses:
            if not proposal.is_new and proposal.existing_hypothesis_id is not None:
                db.update_hypothesis_statement(
                    conn, proposal.existing_hypothesis_id, proposal.statement
                )
                hyp_id = proposal.existing_hypothesis_id
            else:
                hyp_id = db.insert_hypothesis(
                    conn, problem_area_id=problem_area_id, statement=proposal.statement
                )
            for insight_id in proposal.motivating_insight_ids:
                db.link_hypothesis_insight(conn, hyp_id, insight_id)

    hypothesis_updates = [
        {
            "problem_area_id": problem_area_id,
            "statement": proposal.statement,
            "is_new": proposal.is_new,
            "motivating_insight_ids": proposal.motivating_insight_ids,
            "insight_stance_notes": [note.model_dump() for note in result.insight_stance_notes],
        }
        for proposal in result.hypotheses
    ]
    return {"hypothesis_updates": hypothesis_updates}
