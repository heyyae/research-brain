"""Pure, deterministic scoring functions — no DB/LLM dependencies.

The constants below are starting guesses, not derived from any standard
research-ops formula. Tune them once real capture data shows how the scores
feel in practice.
"""
from dataclasses import dataclass
from datetime import datetime, timezone

HALF_LIFE_DAYS = 180.0  # decay half-life shared by confidence + priority severity
CONFIDENCE_K = 3.0  # squashing constant in raw / (raw + k)
SEGMENT_DIVERSITY_BONUS = 0.15  # per additional distinct segment beyond the first
METHOD_DIVERSITY_BONUS = 0.10  # per additional distinct method beyond the first
DIVERSITY_BONUS_CAP = 1.75
PREVALENCE_WEIGHT = 0.5
SEVERITY_WEIGHT = 0.5
PREVALENCE_SOURCE_CAP = 8  # distinct (segment, method) pairs for max prevalence score

# Method quality weights now express within-category reliability only — the
# cross-category "behavioral vs. explanatory" question is handled separately
# by triangulation_multiplier(), so these no longer need a single ladder that
# ranks e.g. analytics against interview. Unrecognized/free-text methods
# default to 1.0 (treated as interview-strength) rather than erroring, since
# `method` is free text.
#
# Behavioral bucket: analytics > observation > usability_test (large-sample,
# unbiased analytics outranks smaller-sample real-world observation, which
# outranks the more artificial lab-task setting of a usability test).
# Explanatory bucket: usability_test == interview (equally credited for
# surfacing why; usability_test also covers the behavioral side at once, per
# BEHAVIORAL_METHODS below). Since usability_test needs to equal interview
# for the explanatory comparison, its value is set by that constraint rather
# than by the behavioral ranking alone.
# support_ticket stays its own low-reliability signal: unsolicited,
# unstructured, self-selected toward unhappy users.
METHOD_WEIGHTS = {
    "analytics": 1.5,
    "observation": 1.3,
    "usability_test": 1.0,
    "interview": 1.0,
    "support_ticket": 0.8,
}
DEFAULT_METHOD_WEIGHT = 1.0

# Methods answer different questions and aren't comparable on one reliability
# ladder: behavioral methods show *what* users do, explanatory methods surface
# *why*. Confidence requires triangulating both; usability_test does both at
# once (think-aloud alongside observed behavior), so it satisfies either side.
# support_ticket is neither — real signal, but not confirmatory on its own —
# so it doesn't count toward triangulation, though it still adds to raw score.
BEHAVIORAL_METHODS = {"analytics", "observation", "usability_test"}
EXPLANATORY_METHODS = {"interview", "usability_test"}
TRIANGULATION_BONUS_MULTIPLIER = 1.0  # both what + why present
NO_TRIANGULATION_MULTIPLIER = 0.5  # only one side (or neither) present

# Directness weight: a verbatim quote is stronger evidence than a paraphrase/hearsay.
DIRECTNESS_WEIGHTS = {
    "direct_quote": 1.2,
    "secondhand": 0.8,
}
DEFAULT_DIRECTNESS_WEIGHT = 1.0


def _normalize_method(method: str) -> str:
    return method.strip().lower().replace(" ", "_")


def method_weight(method: str) -> float:
    return METHOD_WEIGHTS.get(_normalize_method(method), DEFAULT_METHOD_WEIGHT)


def triangulation_multiplier(links: list[EvidenceLinkRow]) -> float:
    non_contradict = [link for link in links if link.stance != "contradict"]
    methods = {_normalize_method(link.method) for link in non_contradict}
    has_behavioral = bool(methods & BEHAVIORAL_METHODS)
    has_explanatory = bool(methods & EXPLANATORY_METHODS)
    if has_behavioral and has_explanatory:
        return TRIANGULATION_BONUS_MULTIPLIER
    return NO_TRIANGULATION_MULTIPLIER


def directness_weight(directness: str | None) -> float:
    if not directness:
        return DEFAULT_DIRECTNESS_WEIGHT
    return DIRECTNESS_WEIGHTS.get(directness, DEFAULT_DIRECTNESS_WEIGHT)


@dataclass(frozen=True)
class EvidenceLinkRow:
    stance: str  # 'support' | 'contradict' | 'neutral'
    severity: float  # 0.0-1.0
    segment: str
    method: str
    created_at: str  # ISO datetime
    directness: str | None = None  # 'direct_quote' | 'secondhand' | None


def _parse_iso(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def age_days(created_at: str, now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    delta = now - _parse_iso(created_at)
    return max(delta.total_seconds() / 86400.0, 0.0)


def decay_weight(age_in_days: float, half_life_days: float = HALF_LIFE_DAYS) -> float:
    return 0.5 ** (age_in_days / half_life_days)


def _signed(stance: str) -> int:
    if stance == "support":
        return 1
    if stance == "contradict":
        return -1
    return 0


def diversity_bonus(links: list[EvidenceLinkRow]) -> float:
    non_contradict = [link for link in links if link.stance != "contradict"]
    distinct_segments = len({link.segment for link in non_contradict})
    distinct_methods = len({link.method for link in non_contradict})
    bonus = 1.0
    if distinct_segments > 0:
        bonus += SEGMENT_DIVERSITY_BONUS * (distinct_segments - 1)
    if distinct_methods > 0:
        bonus += METHOD_DIVERSITY_BONUS * (distinct_methods - 1)
    return min(bonus, DIVERSITY_BONUS_CAP)


def confidence_score(links: list[EvidenceLinkRow], now: datetime | None = None) -> float:
    if not links:
        return 0.0
    now = now or datetime.now(timezone.utc)
    raw = sum(
        _signed(link.stance)
        * decay_weight(age_days(link.created_at, now))
        * method_weight(link.method)
        * directness_weight(link.directness)
        for link in links
    )
    raw *= diversity_bonus(links)
    raw *= triangulation_multiplier(links)
    if raw <= 0:
        return 0.0
    score = 100.0 * raw / (raw + CONFIDENCE_K)
    return max(0.0, min(100.0, score))


def priority_score(links: list[EvidenceLinkRow], now: datetime | None = None) -> float:
    if not links:
        return 0.0
    now = now or datetime.now(timezone.utc)

    non_contradict = [link for link in links if link.stance != "contradict"]
    # Weight each distinct (segment, method) source by its most-recent touch, so a
    # source with only stale evidence contributes less prevalence than a fresh one.
    most_recent_decay_by_source: dict[tuple[str, str], float] = {}
    for link in non_contradict:
        source = (link.segment, link.method)
        decay = decay_weight(age_days(link.created_at, now))
        if decay > most_recent_decay_by_source.get(source, 0.0):
            most_recent_decay_by_source[source] = decay
    weighted_sources = sum(most_recent_decay_by_source.values())
    prevalence = min(weighted_sources, PREVALENCE_SOURCE_CAP) / PREVALENCE_SOURCE_CAP * 100.0

    decayed_severities = [
        link.severity * decay_weight(age_days(link.created_at, now)) for link in links
    ]
    recency_weighted_severity = sum(decayed_severities) / len(decayed_severities)

    return PREVALENCE_WEIGHT * prevalence + SEVERITY_WEIGHT * (recency_weighted_severity * 100.0)
