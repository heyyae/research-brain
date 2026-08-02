from datetime import datetime, timezone

from research_brain import db
from research_brain.config import OUTPUTS_DIR
from research_brain.state import GraphState


def _fmt_delta(old: float | None, new: float) -> str:
    if old is None:
        return f"new -> {new:.1f}"
    arrow = "up" if new > old else ("down" if new < old else "flat")
    return f"{old:.1f} -> {new:.1f} ({arrow})"


def render_report(state: GraphState) -> str:
    lines = [f"# Synthesis report — {state['capture_title']}", ""]
    lines.append(f"- Capture ID: {state['capture_id']}")
    lines.append(f"- Date: {state['capture_date']} | Method: {state['method']} | Segment: {state['segment']}")
    lines.append("")

    signals = state.get("signals", [])
    lines.append(f"## Signals extracted ({len(signals)})")
    if not signals:
        lines.append("No signals were extracted from this capture.")
    else:
        for s in signals:
            lines.append(
                f"- [{s['segment']}, severity {s['severity']:.1f}, {s['directness']}] "
                f"{s['signal_summary']} — \"{s['quote']}\""
            )
    lines.append("")

    insight_deltas = state.get("insight_deltas", [])
    lines.append(f"## Insights updated ({len(insight_deltas)})")
    for d in insight_deltas:
        tag = "NEW" if d["is_new"] else d["stance"].upper()
        lines.append(
            f"- [{tag}] **{d['insight_title']}** (id {d['insight_id']}) — confidence "
            f"{_fmt_delta(d['old_confidence'], d['new_confidence'])}"
        )
    if not insight_deltas:
        lines.append("(none)")
    lines.append("")

    pa_deltas = state.get("problem_area_deltas", [])
    lines.append(f"## Problem area priorities ({len(pa_deltas)})")
    for d in sorted(pa_deltas, key=lambda x: x["new_priority"], reverse=True):
        lines.append(
            f"- **{d['title']}** (id {d['problem_area_id']}) — priority "
            f"{_fmt_delta(d['old_priority'], d['new_priority'])}"
        )
    if not pa_deltas:
        lines.append("(none)")
    lines.append("")

    hyp_updates = state.get("hypothesis_updates", [])
    lines.append(f"## Hypotheses ({len(hyp_updates)})")
    seen_notes = set()
    for h in hyp_updates:
        tag = "NEW" if h["is_new"] else "REVISED"
        lines.append(
            f"- [{tag}] {h['statement']} (motivated by insights: "
            f"{', '.join(str(i) for i in h['motivating_insight_ids']) or 'none'})"
        )
        for note in h.get("insight_stance_notes", []):
            key = (note["insight_id"], note["verdict"], note["note"])
            if key in seen_notes:
                continue
            seen_notes.add(key)
            lines.append(f"    - insight {note['insight_id']}: {note['verdict']} — {note['note']}")
    if not hyp_updates:
        lines.append("(none)")
    lines.append("")

    return "\n".join(lines)


def save_report(state: GraphState, report_text: str) -> str:
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = OUTPUTS_DIR / f"synthesis_{state['capture_id']}_{timestamp}.md"
    path.write_text(report_text)
    return str(path)


def generate_report(state: GraphState) -> dict:
    text = render_report(state)
    path = save_report(state, text)
    print(text)
    with db.connect() as conn:
        db.set_capture_status(conn, state["capture_id"], "completed")
    return {"report_text": text, "report_path": path}
