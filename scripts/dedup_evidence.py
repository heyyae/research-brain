"""One-off cleanup for double-counted evidence.

Two sources of duplication were found:
  1. The support-ticket transcripts were ingested twice — captures #7 == #9 and
     #8 == #10 are byte-identical source files. Each duplicate ingestion attached
     its own evidence links (often under slightly different segment labels, which
     made the inflation worse via the diversity/prevalence bonuses).
  2. Replaying a capture appended a second copy of its evidence instead of
     replacing it, so capture #11 (Terrence Ng) has within-capture duplicate links.

This script:
  - retires the duplicate captures (#9, #10) by marking pipeline_status='duplicate'
    and deleting ALL their evidence links;
  - removes within-capture duplicate links (same insight_id + capture_id + quote),
    keeping the earliest;
  - recomputes confidence for every affected insight and priority for every
    affected problem area, using the real scoring functions.

Run: python scripts/dedup_evidence.py            (dry-run)
     python scripts/dedup_evidence.py --apply     (writes changes)
"""
import argparse
import sys

from research_brain import db
from research_brain.scoring import EvidenceLinkRow, confidence_score, priority_score

DUPLICATE_CAPTURE_IDS = [9, 10]  # same transcripts as #7 and #8 respectively
# Map each duplicate capture to its canonical original (byte-identical source file).
CANONICAL_OF = {9: 7, 10: 8}


def _rows(links):
    return [
        EvidenceLinkRow(
            stance=r["stance"], severity=r["severity"], segment=r["segment"],
            method=r["method"], created_at=r["created_at"], directness=r["directness"],
        )
        for r in links
    ]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Write changes (default: dry-run)")
    args = ap.parse_args()

    db.init_db()
    with db.connect() as conn:
        # --- Identify links on duplicate captures ---------------------------
        # A link from a duplicate capture is REDUNDANT (delete) if the canonical
        # capture already links the same quote to the same insight. Otherwise the
        # evidence is only attributed to the duplicate — REPOINT it to the
        # canonical capture (the quote is verbatim-present there too) so the signal
        # survives rather than being lost.
        dup_rows = [
            dict(r)
            for r in conn.execute(
                f"SELECT * FROM evidence_links WHERE capture_id IN "
                f"({','.join('?' * len(DUPLICATE_CAPTURE_IDS))})",
                DUPLICATE_CAPTURE_IDS,
            ).fetchall()
        ]
        dup_capture_links = []  # to delete (canonical already has this quote)
        repoint_links = []      # (link_id, new_capture_id) to reattribute
        for r in dup_rows:
            canonical = CANONICAL_OF[r["capture_id"]]
            canonical_has = conn.execute(
                "SELECT 1 FROM evidence_links WHERE insight_id=? AND capture_id=? AND quote=?",
                (r["insight_id"], canonical, r["quote"]),
            ).fetchone()
            if canonical_has:
                dup_capture_links.append(r)
            else:
                repoint_links.append((r["id"], canonical))

        # (b) within-capture duplicates (same insight+capture+quote), keep earliest
        within_dupes = []
        seen = set()
        remaining = conn.execute(
            f"SELECT * FROM evidence_links WHERE capture_id NOT IN "
            f"({','.join('?' * len(DUPLICATE_CAPTURE_IDS))}) ORDER BY created_at, id",
            DUPLICATE_CAPTURE_IDS,
        ).fetchall()
        for r in remaining:
            key = (r["insight_id"], r["capture_id"], r["quote"])
            if key in seen:
                within_dupes.append(dict(r))
            else:
                seen.add(key)

        to_delete_ids = [r["id"] for r in dup_capture_links] + [r["id"] for r in within_dupes]
        affected_insight_ids = {
            r["insight_id"] for r in dup_capture_links + within_dupes + [
                {"insight_id": r["insight_id"]}
                for r in dup_rows if r["id"] in {lid for lid, _ in repoint_links}
            ]
        }

        total_links = conn.execute("SELECT COUNT(*) c FROM evidence_links").fetchone()["c"]
        print(f"Total evidence links: {total_links}")
        print(f"Links on duplicate captures {DUPLICATE_CAPTURE_IDS}: {len(dup_rows)}")
        print(f"  - redundant (canonical already has quote) -> delete: {len(dup_capture_links)}")
        print(f"  - unique to duplicate -> repoint to canonical: {len(repoint_links)}")
        print(f"Within-capture duplicate links (replay copies) -> delete: {len(within_dupes)}")
        print(f"Total links to delete: {len(to_delete_ids)}")
        print(f"Insights whose scores will be recomputed: {len(affected_insight_ids)}")

        if not args.apply:
            print("\n(dry-run — re-run with --apply to write these changes)")
            return

        # --- Apply ----------------------------------------------------------
        for link_id, new_cap in repoint_links:
            conn.execute(
                "UPDATE evidence_links SET capture_id=? WHERE id=?", (new_cap, link_id)
            )
        conn.executemany(
            "DELETE FROM evidence_links WHERE id = ?", [(i,) for i in to_delete_ids]
        )
        for cid in DUPLICATE_CAPTURE_IDS:
            db.set_capture_status(conn, cid, "duplicate")

        # Recompute confidence for affected insights + priority for their areas.
        affected_area_ids = set()
        for insight_id in affected_insight_ids:
            insight = db.get_insight(conn, insight_id)
            if insight is None:
                continue
            affected_area_ids.add(insight["problem_area_id"])
            links = db.get_evidence_links_for_insight(conn, insight_id)
            support = sum(1 for r in links if r["stance"] == "support")
            contradict = sum(1 for r in links if r["stance"] == "contradict")
            db.update_insight_scores(
                conn, insight_id,
                support_count=support, contradict_count=contradict,
                confidence_score=confidence_score(_rows(links)),
            )
        for area_id in affected_area_ids:
            links = db.get_evidence_links_for_problem_area(conn, area_id)
            db.update_problem_area_priority(conn, area_id, priority_score(_rows(links)))

        conn.commit()
        print(
            f"\nApplied. Deleted {len(to_delete_ids)} links, retired captures "
            f"{DUPLICATE_CAPTURE_IDS}, recomputed {len(affected_insight_ids)} insights "
            f"across {len(affected_area_ids)} problem areas."
        )


if __name__ == "__main__":
    main()
