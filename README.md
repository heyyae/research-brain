# research-brain

**A first-pass triage system for product prioritisation, built on a research evidence base.**

Raw research captures (interview transcripts, verbatim feedback, support tickets) go in.
Each capture runs through a LangGraph pipeline that extracts candidate pain points, matches
them against the existing insight library, updates confidence/priority scores, and drafts
hypotheses for the highest-priority problem areas.

What it is: an automated first cut. It ranks what to look at first and drafts hypotheses so
nobody starts from a blank page.

What it is not: a verdict. Scores are pointers, not conclusions — they tell you where to
look, not what is true. Every ranked view prints the evidence underneath the score for this
reason. **Read the quotes before acting on a hypothesis.** Anything resting on thin evidence
(a single source, no behavioural data, only stale or contradicting evidence) is labelled
inline as a question to test rather than a finding.

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
Each area shows its priority split into `prevalence` (how widely the problem shows up) and
`severity` (how badly it hurts), because a single number conflates "widespread but mild"
with "rare but severe" — different product decisions. Add `--brief` for scores only.

### Drill into what's driving a specific problem area

```
research-brain list-insights --problem-area <id> --sort confidence
```

Use the `<id>` shown in brackets from `list-problem-areas`. Drop `--problem-area` to list
insights across all problem areas.

### Review hypotheses for product to validate

```
research-brain list-hypotheses
research-brain list-hypotheses --problem-area <id>
research-brain list-hypotheses --status validated   # or invalidated, retired
```

Lists every active hypothesis, grouped by problem area (highest priority first) — the
handoff view for product to pick what to validate or test next. Hypotheses are generated
and revised automatically by the pipeline for the top-priority problem areas touched by
each new capture; there's no separate step to trigger this.

Each hypothesis prints the evidence underneath it, inline: the motivating insights, what
each insight's confidence score is made of, the strongest supporting quotes, and **every**
contradicting quote (supporting quotes are capped at two; contradictions never are —
hiding disconfirming evidence would make this advocacy rather than triage). Hypotheses
resting on thin evidence carry a `⚠` line saying so.

Add `--brief` to collapse to hypotheses and insight titles only. The `⚠` warnings still
print in brief mode.

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
Display helpers that decompose those scores and flag thin evidence live in
`src/research_brain/display.py` — they're pure functions over existing rows, so changing
what gets surfaced never touches the pipeline.

### Reading a confidence breakdown

```
3 sources · 3 segments · interview · triangulated NO · newest 5d
```

`triangulated` is the one to watch. Behavioural methods (`analytics`, `observation`,
`usability_test`) show *what* users do; explanatory methods (`interview`, `usability_test`)
surface *why*. An insight with only one side runs at half strength in the confidence
formula — a large lever that a bare score hides. An interview-only evidence base will show
`triangulated NO` everywhere, which is accurate: it means nobody has observed the behaviour
yet, only heard it described.

## Structure

- `data/` — SQLite database (`research_brain.db`): captures, insights, problem_areas,
  evidence_links, hypotheses
- `transcripts/` — drop `.txt` files here to reference with `capture --file`
- `outputs/` — generated synthesis reports (`synthesis_<capture_id>_<timestamp>.md`)
- `src/research_brain/` — pipeline source (see `graph.py` for the node wiring)
