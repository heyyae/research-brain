"""Pure, deterministic scoring functions — no DB/LLM dependencies.

The constants below are starting guesses, not derived from any standard
research-ops formula. Tune them once real capture data shows how the scores
feel in practice.
"""
from dataclasses import dataclass
from datetime import datetime, timezone

HALF_LIFE_DAYS = 90.0  # decay half-life shared by confidence + priority severity
CONFIDENCE_K = 3.0  # squashing constant in raw / (raw + k)
SEGMENT_DIVERSITY_BONUS = 0.15  # per additional distinct segment beyond the first
METHOD_DIVERSITY_BONUS = 0.10  # per additional distinct method beyond the first
DIVERSITY_BONUS_CAP = 1.75
PREVALENCE_WEIGHT = 0.5
SEVERITY_WEIGHT = 0.5
PREVALENCE_SOURCE_CAP = 8  # distinct (segment, method) pairs for max prevalence score

# Method quality weights: not all research methods are equally strong evidence.
# Observed behavior > analytics > usability test > interview > support ticket,
# per the original data model doc. Unrecognized/free-text methods default to 1.0
# (treated as interview-strength) rather than erroring, since `method` is free text.
METHOD_WEIGHTS = {
    "analytics": 2.0,
    "observation": 1.8,
    "usability_test": 1.5,
    "interview": 1.0,
    "support_ticket": 0.8,
}
DEFAULT_METHOD_WEIGHT = 1.0

# Directness weight: a verbatim quote is stronger evidence than a paraphrase/hearsay.
DIRECTNESS_WEIGHTS = {
    "direct_quote": 1.2,
    "secondhand": 0.8,
}
DEFAULT_DIRECTNESS_WEIGHT = 1.0


def method_weight(method: str) -> float:
    return METHOD_WEIGHTS.get(method.strip().lower().replace(" ", "_"), DEFAULT_METHOD_WEIGHT)


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
    if raw <= 0:
        return 0.0
    score = 100.0 * raw / (raw + CONFIDENCE_K)
    return max(0.0, min(100.0, score))


def priority_score(links: list[EvidenceLinkRow], now: datetime | None = None) -> float:
    if not links:
        return 0.0
    now = now or datetime.now(timezone.utc)

    non_contradict = [link for link in links if link.stance != "contradict"]
    distinct_sources = len({(link.segment, link.method) for link in non_contradict})
    prevalence = min(distinct_sources, PREVALENCE_SOURCE_CAP) / PREVALENCE_SOURCE_CAP * 100.0

    decayed_severities = [
        link.severity * decay_weight(age_days(link.created_at, now)) for link in links
    ]
    recency_weighted_severity = sum(decayed_severities) / len(decayed_severities)

    return PREVALENCE_WEIGHT * prevalence + SEVERITY_WEIGHT * (recency_weighted_severity * 100.0)
