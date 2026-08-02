from research_brain import db
from research_brain.llm import get_llm
from research_brain.models import SignalMatch
from research_brain.prompts import MATCH_SYSTEM_PROMPT, MATCH_USER_TEMPLATE
from research_brain.scoring import EvidenceLinkRow, confidence_score
from research_brain.state import ExtractedSignal, GraphState


def _build_existing_context(conn) -> tuple[str, str]:
    insights = db.list_active_insights(conn)
    problem_areas = db.list_problem_areas(conn)

    if insights:
        by_area: dict[int, list] = {}
        for row in insights:
            by_area.setdefault(row["problem_area_id"], []).append(row)
        area_titles = {row["id"]: row["title"] for row in problem_areas}
        lines = []
        for area_id, rows in by_area.items():
            lines.append(f"[Problem area {area_id}: {area_titles.get(area_id, 'unknown')}]")
            for row in rows:
                lines.append(f"  {row['id']} | {row['title']} | {row['summary']}")
        existing_insights_block = "\n".join(lines)
    else:
        existing_insights_block = "(none yet)"

    if problem_areas:
        existing_problem_areas_block = "\n".join(
            f"{row['id']} | {row['title']}" for row in problem_areas
        )
    else:
        existing_problem_areas_block = "(none yet)"

    return existing_insights_block, existing_problem_areas_block


def _match_one_signal(conn, signal: ExtractedSignal) -> SignalMatch:
    existing_insights_block, existing_problem_areas_block = _build_existing_context(conn)

    llm = get_llm().with_structured_output(SignalMatch)
    user_msg = MATCH_USER_TEMPLATE.format(
        quote=signal["quote"],
        signal_summary=signal["signal_summary"],
        segment=signal["segment"],
        severity=signal["severity"],
        existing_insights_block=existing_insights_block,
        existing_problem_areas_block=existing_problem_areas_block,
    )
    return llm.invoke([("system", MATCH_SYSTEM_PROMPT), ("user", user_msg)])


def _recompute_confidence(conn, insight_id: int) -> tuple[float, int, int]:
    rows = db.get_evidence_links_for_insight(conn, insight_id)
    links = [
        EvidenceLinkRow(
            stance=row["stance"],
            severity=row["severity"],
            segment=row["segment"],
            method=row["method"],
            created_at=row["created_at"],
            directness=row["directness"],
        )
        for row in rows
    ]
    support_count = sum(1 for row in rows if row["stance"] == "support")
    contradict_count = sum(1 for row in rows if row["stance"] == "contradict")
    return confidence_score(links), support_count, contradict_count


def match_and_apply_signals(state: GraphState) -> dict:
    """Match each extracted signal to the insight library and apply the resulting
    evidence, one signal at a time. This runs sequentially (not fanned out in
    parallel) so that later signals in the same capture can see insights/problem
    areas created by earlier signals in the same run — otherwise several distinct
    signals about the same underlying issue (common within a single interview)
    would each spawn their own near-duplicate problem area.
    """
    capture_id = state["capture_id"]
    method = state["method"]
    insight_deltas = []
    affected_problem_area_ids: set[int] = set()

    for signal in state.get("signals", []):
        with db.connect() as conn:
            match = _match_one_signal(conn, signal)

            insight_id = match.matched_insight_id
            is_new = insight_id is None

            if is_new:
                problem_area_id = match.existing_problem_area_id
                if problem_area_id is None:
                    new_area = match.new_problem_area
                    problem_area_id = db.insert_problem_area(
                        conn, title=new_area.title, description=new_area.description
                    )
                insight_id = db.insert_insight(
                    conn,
                    problem_area_id=problem_area_id,
                    title=match.new_insight_title,
                    summary=match.new_insight_summary,
                )
                insight_title = match.new_insight_title
                old_confidence = None
            else:
                insight_row = db.get_insight(conn, insight_id)
                problem_area_id = insight_row["problem_area_id"]
                insight_title = insight_row["title"]
                old_confidence = insight_row["confidence_score"]

            db.insert_evidence_link(
                conn,
                insight_id=insight_id,
                capture_id=capture_id,
                stance=match.stance,
                quote=signal["quote"],
                signal_summary=signal["signal_summary"],
                severity=signal["severity"],
                segment=signal["segment"],
                method=method,
                directness=signal["directness"],
            )

            new_score, support_count, contradict_count = _recompute_confidence(conn, insight_id)
            db.update_insight_scores(
                conn,
                insight_id,
                support_count=support_count,
                contradict_count=contradict_count,
                confidence_score=new_score,
            )

        insight_deltas.append(
            {
                "insight_id": insight_id,
                "insight_title": insight_title,
                "is_new": is_new,
                "stance": match.stance,
                "old_confidence": old_confidence,
                "new_confidence": new_score,
            }
        )
        affected_problem_area_ids.add(problem_area_id)

    return {
        "insight_deltas": insight_deltas,
        "affected_problem_area_ids": sorted(affected_problem_area_ids),
    }


def route_after_extract(state: GraphState) -> str:
    if not state.get("signals"):
        return "generate_report"
    return "match_and_apply_signals"
