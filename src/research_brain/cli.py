import argparse
import sys
from pathlib import Path

from research_brain import db
from research_brain.config import TRANSCRIPTS_DIR


def _read_transcript_interactive() -> tuple[str, str | None]:
    print("\nPaste transcript below, type END on its own line when finished.")
    print(f"Or enter a path to a file (absolute, or relative to {TRANSCRIPTS_DIR}) and press enter.\n")

    first_line = input()
    candidate = Path(first_line.strip())
    if not candidate.is_absolute():
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
        if not path.is_absolute():
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


def cmd_replay(args: argparse.Namespace) -> None:
    db.init_db()
    with db.connect() as conn:
        capture = db.get_capture(conn, args.capture_id)
    if capture is None:
        print(f"No capture with id {args.capture_id}", file=sys.stderr)
        sys.exit(1)
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
        print(f"    {r['summary']}")


def cmd_list_problem_areas(args: argparse.Namespace) -> None:
    db.init_db()
    with db.connect() as conn:
        rows = db.list_problem_areas(conn, order_by_priority=(args.sort == "priority"))
    if not rows:
        print("No problem areas yet.")
        return
    for r in rows:
        print(f"[{r['id']}] {r['title']} — priority {r['priority_score']:.1f}")
        if r["description"]:
            print(f"    {r['description']}")


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
    p_list_insights.set_defaults(func=cmd_list_insights)

    p_list_pa = sub.add_parser("list-problem-areas", help="List problem areas")
    p_list_pa.add_argument("--sort", choices=["id", "priority"], default="priority")
    p_list_pa.set_defaults(func=cmd_list_problem_areas)

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
