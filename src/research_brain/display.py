"""Display helpers that make scores auditable at the point of decision.

The scores in `scoring.py` are composites — several multiplicative factors
collapsed into one number. That's fine for ranking, but it means a bare score
can't be sanity-checked: "confidence 62" could be many weak sources or few
strong triangulated ones, and those call for different decisions.

Everything here exists to keep the tool honest about that: show what a score is
made of, show the quotes underneath it, and say so plainly when the evidence is
too thin to be treated as a finding. All functions are pure and read only
existing rows — no schema or pipeline changes needed.
"""
from research_brain.scoring import (
    BEHAVIORAL_METHODS,
    EXPLANATORY_METHODS,
    PREVALENCE_WEIGHT,
    SEVERITY_WEIGHT,
    EvidenceLinkRow,
    _normalize_method,
    age_days,
    decay_weight,
    directness_weight,
    method_weight,
    priority_score,
)

TRIAGE_HEADER = (
    "First-pass triage from research evidence — drafted automatically, not reviewed.\n"
    "Scores rank what to look at; they don't tell you what's true. Check the quotes "
    "before acting."
)

# How many supporting quotes to show per insight. Contradicting quotes are never
# capped — hiding disconfirming evidence would make this advocacy, not triage.
TOP_SUPPORTING_QUOTES = 2

# An insight backed by fewer than this many distinct (segment, method) sources is
# flagged thin regardless of what its confidence score says.
THIN_SOURCE_THRESHOLD = 2

# Evidence older than this with nothing newer means the insight may be stale.
STALE_EVIDENCE_DAYS = 365.0


def _to_link_rows(links: list) -> list[EvidenceLinkRow]:
    """Adapt sqlite3.Row evidence links into the scoring dataclass."""
    return [
        EvidenceLinkRow(
            stance=link["stance"],
            severity=link["severity"],
            segment=link["segment"],
            method=link["method"],
            created_at=link["created_at"],
            directness=link["directness"],
        )
        for link in links
    ]


def evidence_strength(link) -> float:
    """Rank a single evidence link the way the confidence score weighs it.

    Reuses the same factors as `confidence_score` so the quotes we surface are
    the ones actually driving the number, not an arbitrary first-two.
    """
    return (
        decay_weight(age_days(link["created_at"]))
        * method_weight(link["method"])
        * directness_weight(link["directness"])
    )


def select_quotes(links: list) -> tuple[list, list]:
    """Return (top supporting links, all contradicting links).

    Supporting quotes are capped at TOP_SUPPORTING_QUOTES and ordered by the
    weight the scoring formula gives them. Contradictions are returned in full.
    """
    supporting = [link for link in links if link["stance"] == "support"]
    contradicting = [link for link in links if link["stance"] == "contradict"]
    supporting.sort(key=evidence_strength, reverse=True)
    return supporting[:TOP_SUPPORTING_QUOTES], contradicting


def confidence_breakdown(links: list) -> str:
    """One-line decomposition of what a confidence score is made of.

    Surfaces the triangulation flag in particular: an untriangulated insight
    runs at half strength (NO_TRIANGULATION_MULTIPLIER), the single biggest
    lever in the formula, and it is otherwise invisible.
    """
    if not links:
        return "no evidence"

    non_contradict = [link for link in links if link["stance"] != "contradict"]
    considered = non_contradict or links

    segments = {link["segment"] for link in considered}
    methods = {_normalize_method(link["method"]) for link in considered}
    sources = {(link["segment"], _normalize_method(link["method"])) for link in considered}

    triangulated = bool(methods & BEHAVIORAL_METHODS) and bool(methods & EXPLANATORY_METHODS)
    newest_age = min(age_days(link["created_at"]) for link in links)

    parts = [
        f"{len(sources)} source{'s' if len(sources) != 1 else ''}",
        f"{len(segments)} segment{'s' if len(segments) != 1 else ''}",
        "+".join(sorted(methods)),
        f"triangulated {'yes' if triangulated else 'NO'}",
        f"newest {newest_age:.0f}d",
    ]
    return " · ".join(parts)


def priority_breakdown(links: list) -> str:
    """Split a priority score into its prevalence and severity halves.

    A single priority number conflates 'widespread but mild' with 'rare but
    severe' — different product decisions with the same score.
    """
    if not links:
        return "no evidence"
    rows = _to_link_rows(links)
    total = priority_score(rows)
    # Recover each half from the weighted total by scoring the components the
    # same way priority_score does.
    prevalence_part, severity_part = _priority_halves(rows)
    return f"prevalence {prevalence_part:.0f} · severity {severity_part:.0f} (total {total:.1f})"


def _priority_halves(rows: list[EvidenceLinkRow]) -> tuple[float, float]:
    """Recompute the two halves of priority_score independently."""
    from research_brain.scoring import PREVALENCE_SOURCE_CAP

    non_contradict = [r for r in rows if r.stance != "contradict"]
    most_recent_decay_by_source: dict[tuple[str, str], float] = {}
    for r in non_contradict:
        source = (r.segment, r.method)
        decay = decay_weight(age_days(r.created_at))
        if decay > most_recent_decay_by_source.get(source, 0.0):
            most_recent_decay_by_source[source] = decay
    weighted_sources = sum(most_recent_decay_by_source.values())
    prevalence = min(weighted_sources, PREVALENCE_SOURCE_CAP) / PREVALENCE_SOURCE_CAP * 100.0

    decayed = [r.severity * decay_weight(age_days(r.created_at)) for r in rows]
    severity = (sum(decayed) / len(decayed)) * 100.0 if decayed else 0.0
    return prevalence, severity


def thinness_warning(links: list) -> str | None:
    """Return a plain-language warning when evidence is too thin to act on.

    Hypotheses are generated for the top-N problem areas by *relative* priority,
    so on a young database a hypothesis can rest on a single interview and look
    identical in shape to a well-evidenced one. This is what distinguishes them.
    """
    if not links:
        return "no evidence linked — treat as a question to test, not a finding"

    non_contradict = [link for link in links if link["stance"] != "contradict"]
    if not non_contradict:
        return "all evidence contradicts this — treat as likely wrong"

    sources = {(link["segment"], _normalize_method(link["method"])) for link in non_contradict}
    methods = {_normalize_method(link["method"]) for link in non_contradict}
    has_behavioral = bool(methods & BEHAVIORAL_METHODS)
    has_explanatory = bool(methods & EXPLANATORY_METHODS)
    newest_age = min(age_days(link["created_at"]) for link in links)

    reasons = []
    if len(sources) < THIN_SOURCE_THRESHOLD:
        reasons.append(f"{len(sources)} source")
    if not has_behavioral:
        reasons.append("no behavioural evidence")
    elif not has_explanatory:
        reasons.append("no explanatory evidence")
    if newest_age > STALE_EVIDENCE_DAYS:
        reasons.append(f"nothing newer than {newest_age:.0f}d")

    if not reasons:
        return None
    return ", ".join(reasons) + " — treat as a question to test, not a finding"


def format_quote_line(link, indent: str = "      ") -> list[str]:
    """Render one evidence link as displayable lines."""
    stance_label = link["stance"].upper()
    meta = (
        f"{link['segment']} · {link['method']} · {link['directness']} · "
        f"severity {link['severity']:.1f} · {age_days(link['created_at']):.0f}d ago"
    )
    return [
        f"{indent}[{stance_label}] {meta}",
        f'{indent}  "{link["quote"]}"',
    ]
