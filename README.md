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

### Add a new raw capture

From a file (drop the `.txt` into `transcripts/` first, or point `--file` at it wherever it lives):

```
research-brain capture --file transcripts/interview_01.txt --title "..." --date 2026-08-01 --method interview --segment applicant
```

Or paste it directly, no file needed:

```
research-brain capture
```

This prompts for title/date/method/segment, then lets you paste the transcript (type `END`
on its own line when done). Either way, it immediately runs the full pipeline and
prints + saves a synthesis report — no separate "process" step.

`method` should be one of `interview`, `usability_test`, `observation`, `support_ticket`,
`analytics` — these exact words matter, since they're weighted differently in scoring
(an observed behavior counts for more than a support ticket mention). Keep `segment`
names consistent across captures so prevalence scoring can tell when the same problem
shows up across multiple distinct segments.

### Pull the top 3 (or all) problem areas

```
research-brain list-problem-areas
```

Sorted by priority score, highest first — this is the "what should I look at next" view.

### Drill into what's driving a specific problem area

```
research-brain list-insights --problem-area <id> --sort confidence
```

Use the `<id>` shown in brackets from `list-problem-areas`. Drop `--problem-area` to list
insights across all problem areas.

### Fetch the full narrative for a capture

```
research-brain show-report <capture_id>
```

Reprints the saved synthesis report (signals extracted, insights reinforced/created,
priority changes, hypotheses) for that capture. Capture IDs are printed when you run
`capture`, or shown in each insight's evidence trail.

### Other commands

```
research-brain replay <capture_id>   # re-run the pipeline for an existing capture
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
