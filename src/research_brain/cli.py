import argparse
import sys
from pathlib import Path

from research_brain import db
from research_brain.config import TRANSCRIPTS_DIR
from research_brain.display import (
    TRIAGE_HEADER,
    confidence_breakdown,
    format_quote_line,
    priority_breakdown,
    select_quotes,
    thinness_warning,
)


def _read_transcript_interactive() -> tuple[str, str | None]:
    print("\nPaste transcript below, type END on its own line when finished.")
    print(f"Or enter a path to a file (absolute, or relative to {TRANSCRIPTS_DIR}) and press enter.\n")

    first_line = input()
    candidate = Path(first_line.strip())
    if first_line.strip() and not candidate.is_file() and not candidate.is_absolute():
        candidate = TRANSCRIPTS_DIR / first_line.strip()
    if first_line.strip() and candidate.is_file():
        return candidate.read_text(), str(candidate)

    lines = [first_line]
    while True:
        line = input()
        if line == "END":
            break
        lines.append(line)
    return "\n".join(lines), None


def cmd_capture(args: argparse.Namespace) -> None:
    db.init_db()

    if args.file:
        path = Path(args.file)
        if not path.is_absolute() and not path.is_file():
            path = TRANSCRIPTS_DIR / args.file
        raw_text = path.read_text()
        source_path = str(path)
        title = args.title or path.stem
        capture_date = args.date
        method = args.method
        segment = args.segment
        if not (capture_date and method and segment):
            print("--date, --method, and --segment are required with --file", file=sys.stderr)
            sys.exit(1)
    else:
        title = args.title or input("Interview title: ")
        capture_date = args.date or input("Date: ")
        method = args.method or input("Method: ")
        segment = args.segment or input("User segment: ")
        raw_text, source_path = _read_transcript_interactive()

    with db.connect() as conn:
        capture_id = db.insert_capture(
            conn,
            title=title,
            capture_date=capture_date,
            method=method,
            segment=segment,
            raw_text=raw_text,
            source_path=source_path,
        )

    print(f"\nCapture #{capture_id} saved. Running synthesis pipeline...\n")
    _run_pipeline(capture_id, title, capture_date, method, segment, raw_text)


def _run_pipeline(capture_id: int, title: str, capture_date: str, method: str, segment: str, raw_text: str) -> None:
    from research_brain.graph import build_graph

    graph = build_graph()
    try:
        graph.invoke(
            {
                "capture_id": capture_id,
                "capture_title": title,
                "capture_date": capture_date,
                "method": method,
                "segment": segment,
                "raw_text": raw_text,
            }
        )
    except Exception:
        with db.connect() as conn:
            db.set_capture_status(conn, capture_id, "failed")
        raise


def _recompute_scores(conn, insight_ids: list[int]) -> None:
    """Recompute confidence for the given insights and priority for their problem
    areas from whatever evidence currently remains. Used after evidence links are
    deleted (e.g. a replay clears a capture's old links) so stale scores don't linger."""
    from research_brain.scoring import EvidenceLinkRow, confidence_score, priority_score

    def _rows(links):
        return [
            EvidenceLinkRow(
                stance=r["stance"], severity=r["severity"], segment=r["segment"],
                method=r["method"], created_at=r["created_at"], directness=r["directness"],
            )
            for r in links
        ]

    affected_area_ids: set[int] = set()
    for insight_id in insight_ids:
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


def cmd_replay(args: argparse.Namespace) -> None:
    db.init_db()
    with db.connect() as conn:
        capture = db.get_capture(conn, args.capture_id)
        if capture is None:
            print(f"No capture with id {args.capture_id}", file=sys.stderr)
            sys.exit(1)
        # Clear this capture's prior evidence first, then recompute the scores of
        # everything it used to touch — otherwise replaying appends a second copy
        # of every quote on top of the old run, double-counting the evidence.
        affected_insight_ids = [
            row["insight_id"]
            for row in conn.execute(
                "SELECT DISTINCT insight_id FROM evidence_links WHERE capture_id = ?",
                (args.capture_id,),
            ).fetchall()
        ]
        removed = db.delete_evidence_links_for_capture(conn, args.capture_id)
        _recompute_scores(conn, affected_insight_ids)
        conn.commit()
    if removed:
        print(f"Cleared {removed} prior evidence link(s) from capture #{args.capture_id} before replay.")
    _run_pipeline(
        capture["id"], capture["title"], capture["capture_date"], capture["method"], capture["segment"], capture["raw_text"]
    )


def cmd_list_insights(args: argparse.Namespace) -> None:
    db.init_db()
    with db.connect() as conn:
        rows = db.list_insights(conn, problem_area_id=args.problem_area, order_by=args.sort)
        if not rows:
            print("No insights yet.")
            return
        for r in rows:
            print(
                f"[{r['id']}] {r['title']} — confidence {r['confidence_score']:.1f} "
                f"(support={r['support_count']} contradict={r['contradict_count']}) "
                f"| problem_area={r['problem_area_id']}"
            )
            if not args.brief:
                links = db.get_evidence_links_for_insight(conn, r["id"])
                print(f"    {confidence_breakdown(links)}")
                warning = thinness_warning(links)
                if warning:
                    print(f"    ⚠ {warning}")
            print(f"    {r['summary']}")


def cmd_show_insight(args: argparse.Namespace) -> None:
    db.init_db()
    with db.connect() as conn:
        insight = db.get_insight(conn, args.insight_id)
        if insight is None:
            print(f"No insight with id {args.insight_id}", file=sys.stderr)
            sys.exit(1)
        if args.problem_area is not None and insight["problem_area_id"] != args.problem_area:
            print(
                f"Insight {args.insight_id} belongs to problem_area={insight['problem_area_id']}, "
                f"not {args.problem_area}",
                file=sys.stderr,
            )
            sys.exit(1)

        links = db.get_evidence_links_for_insight(conn, args.insight_id)

    print(
        f"[{insight['id']}] {insight['title']} — confidence {insight['confidence_score']:.1f} "
        f"(support={insight['support_count']} contradict={insight['contradict_count']}) "
        f"| problem_area={insight['problem_area_id']}"
    )
    print(f"    {confidence_breakdown(links)}")
    warning = thinness_warning(links)
    if warning:
        print(f"    ⚠ {warning}")
    print(f"    {insight['summary']}\n")

    if not links:
        print("No evidence items yet.")
        return

    for link in links:
        print(f"  [{link['created_at']}] {link['stance']} | segment={link['segment']} method={link['method']} severity={link['severity']}")
        print(f"    {link['signal_summary']}")
        print(f'    "{link["quote"]}"')


def cmd_list_problem_areas(args: argparse.Namespace) -> None:
    db.init_db()
    with db.connect() as conn:
        rows = db.list_problem_areas(conn, order_by_priority=(args.sort == "priority"))
        if not rows:
            print("No problem areas yet.")
            return

        if not args.brief:
            print(TRIAGE_HEADER)
            print()

        for r in rows:
            print(f"[{r['id']}] {r['title']} — priority {r['priority_score']:.1f}")
            if not args.brief:
                links = db.get_evidence_links_for_problem_area(conn, r["id"])
                print(f"    {priority_breakdown(links)}")
            if r["description"]:
                print(f"    {r['description']}")


def cmd_list_hypotheses(args: argparse.Namespace) -> None:
    db.init_db()
    with db.connect() as conn:
        rows = db.list_all_hypotheses_by_priority(
            conn, problem_area_id=args.problem_area, status=args.status
        )
        if not rows:
            print("No hypotheses yet.")
            return

        if not args.brief:
            print(TRIAGE_HEADER)

        last_problem_area_id = None
        for r in rows:
            if r["problem_area_id"] != last_problem_area_id:
                last_problem_area_id = r["problem_area_id"]
                print(
                    f"\n=== [{r['problem_area_id']}] {r['problem_area_title']} "
                    f"(priority {r['problem_area_priority']:.1f}) ==="
                )

            print(f"\n[H{r['id']}] {r['statement']}")
            print(f"    status: {r['status']}")

            insights = db.get_motivating_insights(conn, r["id"])
            if not insights:
                print("    motivated by: (no linked insights)")
                print("    ⚠ no evidence linked — treat as a question to test, not a finding")
                continue

            # Warn at the hypothesis level using every link behind it, so a
            # hypothesis resting on thin evidence says so before the reader
            # decides whether to open the detail below.
            all_links = [
                link
                for ins in insights
                for link in db.get_evidence_links_for_insight(conn, ins["id"])
            ]
            warning = thinness_warning(all_links)
            if warning:
                print(f"    ⚠ {warning}")

            print("    motivated by:")
            for ins in insights:
                links = db.get_evidence_links_for_insight(conn, ins["id"])
                print(
                    f"      - insight {ins['id']}: {ins['title']} "
                    f"(confidence {ins['confidence_score']:.1f}, "
                    f"support={ins['support_count']} contradict={ins['contradict_count']})"
                )
                if args.brief:
                    continue
                print(f"        {confidence_breakdown(links)}")
                supporting, contradicting = select_quotes(links)
                for link in supporting + contradicting:
                    for line in format_quote_line(link, indent="        "):
                        print(line)


def cmd_show_report(args: argparse.Namespace) -> None:
    from research_brain.config import OUTPUTS_DIR

    matches = sorted(OUTPUTS_DIR.glob(f"synthesis_{args.capture_id}_*.md"))
    if not matches:
        print(f"No saved report found for capture {args.capture_id}", file=sys.stderr)
        sys.exit(1)
    print(matches[-1].read_text())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="research-brain")
    sub = parser.add_subparsers(dest="command", required=True)

    p_capture = sub.add_parser("capture", help="Add a new capture and run synthesis")
    p_capture.add_argument("--file", help="Path to a transcript file (under transcripts/ or absolute)")
    p_capture.add_argument("--title")
    p_capture.add_argument("--date")
    p_capture.add_argument("--method")
    p_capture.add_argument("--segment")
    p_capture.set_defaults(func=cmd_capture)

    p_replay = sub.add_parser("replay", help="Re-run the pipeline for an existing capture")
    p_replay.add_argument("capture_id", type=int)
    p_replay.set_defaults(func=cmd_replay)

    p_list_insights = sub.add_parser("list-insights", help="List insights")
    p_list_insights.add_argument("--problem-area", type=int, dest="problem_area")
    p_list_insights.add_argument("--sort", choices=["id", "confidence", "recency"], default="id")
    p_list_insights.add_argument(
        "--brief", action="store_true", help="Scores only, without score breakdowns"
    )
    p_list_insights.set_defaults(func=cmd_list_insights)

    p_show_insight = sub.add_parser("show-insight", help="Show one insight and its evidence items")
    p_show_insight.add_argument("insight_id", type=int)
    p_show_insight.add_argument("--problem-area", type=int, dest="problem_area")
    p_show_insight.set_defaults(func=cmd_show_insight)

    p_list_pa = sub.add_parser("list-problem-areas", help="List problem areas")
    p_list_pa.add_argument("--sort", choices=["id", "priority"], default="priority")
    p_list_pa.add_argument(
        "--brief", action="store_true", help="Scores only, without prevalence/severity split"
    )
    p_list_pa.set_defaults(func=cmd_list_problem_areas)

    p_list_hyp = sub.add_parser("list-hypotheses", help="List hypotheses, grouped by problem area priority")
    p_list_hyp.add_argument("--problem-area", type=int, dest="problem_area")
    p_list_hyp.add_argument(
        "--status", choices=["active", "validated", "invalidated", "retired"], default="active"
    )
    p_list_hyp.add_argument(
        "--brief", action="store_true", help="Hypotheses only, without inline evidence quotes"
    )
    p_list_hyp.set_defaults(func=cmd_list_hypotheses)

    p_show = sub.add_parser("show-report", help="Reprint a saved synthesis report")
    p_show.add_argument("capture_id", type=int)
    p_show.set_defaults(func=cmd_show_report)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
