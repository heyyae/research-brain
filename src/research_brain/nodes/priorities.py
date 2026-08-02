from research_brain import db
from research_brain.scoring import EvidenceLinkRow, priority_score
from research_brain.state import GraphState


def recompute_priorities(state: GraphState) -> dict:
    problem_area_deltas = []

    with db.connect() as conn:
        for problem_area_id in state.get("affected_problem_area_ids", []):
            area_row = db.get_problem_area(conn, problem_area_id)
            old_priority = area_row["priority_score"]

            rows = db.get_evidence_links_for_problem_area(conn, problem_area_id)
            links = [
                EvidenceLinkRow(
                    stance=row["stance"],
                    severity=row["severity"],
                    segment=row["segment"],
                    method=row["method"],
                    created_at=row["created_at"],
                )
                for row in rows
            ]
            new_priority = priority_score(links)
            db.update_problem_area_priority(conn, problem_area_id, new_priority)

            problem_area_deltas.append(
                {
                    "problem_area_id": problem_area_id,
                    "title": area_row["title"],
                    "old_priority": old_priority,
                    "new_priority": new_priority,
                }
            )

    return {"problem_area_deltas": problem_area_deltas}
