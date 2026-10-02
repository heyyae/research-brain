"""One-off: re-cluster existing insights into sharper problem areas.

Reads every non-retired insight, asks the LLM to propose a focused taxonomy and
assign each insight to exactly one new problem area, then rewrites the
problem_areas table: creates the new areas, re-parents insights, re-points
hypotheses to the new area of their motivating insight, recomputes priorities
with the real scoring function, and deletes the now-empty old areas.

Run with: python scripts/recluster_problem_areas.py            (dry-run: prints plan)
          python scripts/recluster_problem_areas.py --apply    (writes changes)
"""
import argparse
import json
import sys

from pydantic import BaseModel, Field

from research_brain import db
from research_brain.llm import get_llm
from research_brain.scoring import EvidenceLinkRow, priority_score


class ProblemAreaCluster(BaseModel):
    title: str = Field(description="Short, specific problem-area title (4-8 words)")
    description: str = Field(description="One sentence describing the problem area")
    insight_ids: list[int] = Field(description="IDs of insights that belong to this area")


class Taxonomy(BaseModel):
    clusters: list[ProblemAreaCluster]


SYSTEM_PROMPT = """You are a UX research lead reorganizing an insight library for a \
scam-protection mobile app (ScamShield). You are given the full list of insights, each \
with an id, title, and summary. They are currently filed under only two over-broad \
problem areas, which makes prioritization useless.

Re-cluster ALL insights into a focused set of problem areas. Aim for about 8 areas \
(do not exceed 8) such that:
- Each problem area names a distinct, actionable problem a product team could own \
(e.g. activation/device-compatibility failures, reporting friction, localization & \
accessibility, trust & false signals, feature discoverability, coverage gaps for new \
scam types, post-report feedback/visibility).
- Areas are mutually exclusive and roughly comparable in grain — not one giant bucket \
plus several tiny ones. Avoid tiny areas of only 2-3 insights: fold a narrow theme into \
the nearest broader area rather than giving it its own bucket, UNLESS it is genuinely \
unrelated to everything else.
- EVERY insight id appears in exactly one cluster. Do not drop or duplicate any id.
Return titles that are specific enough that a new insight either clearly fits or clearly \
does not."""


def build_user_msg(insights: list[dict]) -> str:
    lines = [f"{r['id']} | {r['title']} | {r['summary']}" for r in insights]
    return (
        "Here are all the insights to re-cluster (id | title | summary):\n\n"
        + "\n".join(lines)
        + "\n\nReturn the new taxonomy. Remember: every id must appear exactly once."
    )


def load_insights(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT id, title, summary, problem_area_id FROM insights "
        "WHERE status IS NULL OR status != 'retired' ORDER BY id"
    ).fetchall()
    return [dict(r) for r in rows]


def links_for_insight(conn, insight_id: int) -> list[EvidenceLinkRow]:
    rows = db.get_evidence_links_for_insight(conn, insight_id)
    return [
        EvidenceLinkRow(
            stance=r["stance"],
            severity=r["severity"],
            segment=r["segment"],
            method=r["method"],
            created_at=r["created_at"],
            directness=r["directness"],
        )
        for r in rows
    ]


def diagnose(taxonomy: Taxonomy, all_ids: set[int]) -> tuple[list[int], set[int], set[int]]:
    assigned = [i for c in taxonomy.clusters for i in c.insight_ids]
    assigned_set = set(assigned)
    dupes = sorted({i for i in assigned if assigned.count(i) > 1})
    missing = all_ids - assigned_set
    extra = assigned_set - all_ids
    return dupes, missing, extra


def cluster_with_repair(llm, insights: list[dict], all_ids: set[int], max_tries: int = 3) -> Taxonomy:
    """Cluster, and if the model drops/dupes/invents ids, ask it to fix exactly those."""
    messages = [("system", SYSTEM_PROMPT), ("user", build_user_msg(insights))]
    taxonomy: Taxonomy = llm.invoke(messages)
    for attempt in range(max_tries):
        dupes, missing, extra = diagnose(taxonomy, all_ids)
        if not (dupes or missing or extra):
            return taxonomy
        id_to_insight = {r["id"]: r for r in insights}
        fix_lines = []
        if missing:
            fix_lines.append("These ids were not assigned to any cluster — assign each to the "
                             "single best-fitting cluster (reuse a cluster above or add one):")
            fix_lines += [f"  {i} | {id_to_insight[i]['title']}" for i in sorted(missing)]
        if dupes:
            fix_lines.append(f"These ids appear in more than one cluster — keep each in only one: {dupes}")
        if extra:
            fix_lines.append(f"These ids are not real insights — remove them: {sorted(extra)}")
        print(f"  repair attempt {attempt + 1}: {'; '.join(fix_lines[:1])}", file=sys.stderr)
        messages = [
            ("system", SYSTEM_PROMPT),
            ("user", build_user_msg(insights)),
            ("assistant", taxonomy.model_dump_json()),
            ("user", "Your taxonomy has problems. Return the COMPLETE corrected taxonomy "
                     "(all clusters, every id exactly once).\n" + "\n".join(fix_lines)),
        ]
        taxonomy = llm.invoke(messages)
    dupes, missing, extra = diagnose(taxonomy, all_ids)
    if dupes or missing or extra:
        raise SystemExit(
            f"LLM taxonomy still invalid after {max_tries} repair attempts: "
            f"dupes={dupes} missing={sorted(missing)} extra={sorted(extra)}"
        )
    return taxonomy


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Write changes (default: dry-run)")
    args = ap.parse_args()

    db.init_db()
    with db.connect() as conn:
        insights = load_insights(conn)
        all_ids = {r["id"] for r in insights}

        print(f"Re-clustering {len(insights)} insights...", file=sys.stderr)
        llm = get_llm().with_structured_output(Taxonomy)
        taxonomy = cluster_with_repair(llm, insights, all_ids)

        # Compute the priority each new area WOULD get (pool all its insights' links).
        plan = []
        for c in taxonomy.clusters:
            links: list[EvidenceLinkRow] = []
            for iid in c.insight_ids:
                links.extend(links_for_insight(conn, iid))
            plan.append((c, priority_score(links)))
        plan.sort(key=lambda x: x[1], reverse=True)

        print("\n=== Proposed taxonomy ===")
        for c, pri in plan:
            print(f"\n[{pri:5.1f}] {c.title}  ({len(c.insight_ids)} insights)")
            print(f"        {c.description}")
            print(f"        ids: {sorted(c.insight_ids)}")

        if not args.apply:
            print("\n(dry-run — re-run with --apply to write these changes)")
            return

        old_area_ids = [r["id"] for r in conn.execute("SELECT id FROM problem_areas").fetchall()]

        # 1. Create new areas, re-parent their insights, set priority.
        insight_to_new_area: dict[int, int] = {}
        for c, pri in plan:
            new_id = db.insert_problem_area(conn, title=c.title, description=c.description)
            for iid in c.insight_ids:
                conn.execute(
                    "UPDATE insights SET problem_area_id=? WHERE id=?", (new_id, iid)
                )
                insight_to_new_area[iid] = new_id
            db.update_problem_area_priority(conn, new_id, pri)

        # 2. Re-point hypotheses to the new area of (one of) their motivating insights.
        hyps = conn.execute("SELECT id, problem_area_id FROM hypotheses").fetchall()
        for h in hyps:
            motivating = db.get_motivating_insights(conn, h["id"])
            new_area = None
            for mi in motivating:
                if mi["id"] in insight_to_new_area:
                    new_area = insight_to_new_area[mi["id"]]
                    break
            if new_area is not None:
                conn.execute(
                    "UPDATE hypotheses SET problem_area_id=? WHERE id=?", (new_area, h["id"])
                )

        # 3. Delete the now-empty old areas.
        for oid in old_area_ids:
            remaining = conn.execute(
                "SELECT COUNT(*) c FROM insights WHERE problem_area_id=?", (oid,)
            ).fetchone()["c"]
            orphan_hyps = conn.execute(
                "SELECT COUNT(*) c FROM hypotheses WHERE problem_area_id=?", (oid,)
            ).fetchone()["c"]
            if remaining == 0 and orphan_hyps == 0:
                conn.execute("DELETE FROM problem_areas WHERE id=?", (oid,))
            else:
                print(
                    f"  WARNING: old area {oid} still has {remaining} insights / "
                    f"{orphan_hyps} hypotheses — leaving it in place",
                    file=sys.stderr,
                )

        conn.commit()
        print(f"\nApplied. {len(plan)} new problem areas created, old areas removed.")


if __name__ == "__main__":
    main()
