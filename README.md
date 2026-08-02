# research-brain

A living repository for raw research captures (interview transcripts, verbatim feedback,
support tickets). Each new capture runs through a LangGraph pipeline that extracts
candidate pain points, matches them against your existing insight library, updates
confidence/priority scores, and proposes hypotheses for your highest-priority problem
areas.

## Setup

```
cp .env.example .env   # fill in your Anthropic-compatible endpoint + key
uv sync
```

## Usage

```
uv run research-brain capture                     # interactive: paste a transcript
uv run research-brain capture --file transcripts/interview_01.txt --title "..." --date 2026-08-01 --method interview --segment applicant
uv run research-brain list-insights --sort confidence
uv run research-brain list-problem-areas          # sorted by priority
uv run research-brain show-report <capture_id>
uv run research-brain replay <capture_id>          # re-run the pipeline for an existing capture
```

## How it works

Pipeline (`src/research_brain/graph.py`): extract signals from the raw capture -> match
each signal against existing insights (or create new ones + assign a problem area) ->
recompute confidence scores (time-decay + segment/method diversity) -> recompute
problem-area priority scores (prevalence + recency-weighted severity) -> generate/revise
hypotheses for the top-priority problem areas touched -> write a synthesis report to
`outputs/`.

Scoring formulas and their tunable constants live in `src/research_brain/scoring.py`.

## Structure

- `data/` — SQLite database (`research_brain.db`): captures, insights, problem_areas,
  evidence_links, hypotheses
- `transcripts/` — drop `.txt` files here to reference with `capture --file`
- `outputs/` — generated synthesis reports (`synthesis_<capture_id>_<timestamp>.md`)
- `src/research_brain/` — pipeline source (see `graph.py` for the node wiring)
