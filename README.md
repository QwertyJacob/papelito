# papelito

A self-contained, scoped literature search. You give it topics/keywords
and (optionally) a venue allow-list; it searches arXiv, Semantic Scholar,
and DBLP, snowballs the citation graph from any seed papers you give it,
and writes a single readable Markdown report — with abstracts, grouped by
venue and by topic — instead of a pile of JSON.

It grew out of a one-off scoping-review pipeline for a specific paper;
this is the same method, generalized into a config-driven library so it
can be pointed at any topic and any venue list.

## How it works

1. **Identify** — one search per topic against arXiv and against Semantic
   Scholar's search endpoint.
2. **Snowball** *(optional)* — from seed papers you name, pull backward
   references and forward citations, keeping only results that are both
   on an allow-listed venue and topically on-theme (precision over
   recall — this step is meant to extend the pool, not flood it).
3. **Merge & deduplicate** — across all three sources, by arXiv id / S2
   paper id / normalized title.
4. **Confirm venues** — arXiv's self-reported `comment`/`journal-ref`
   text, DBLP title lookup, and a final authoritative batch check against
   Semantic Scholar's structured venue fields for the whole pool.
5. **Report** — a Markdown file with summary counts, a by-venue and
   by-topic breakdown, and every in-scope paper with its abstract.

Every stage checkpoints its raw output as JSON under `<output_dir>/raw/`,
so a crash or a rate-limit wall never loses more than the stage in
flight.

No venue allow-list? Leave `venues: []` in your config and every paper
found is kept — venue is then reported for information only.

No Semantic Scholar key? The tool still runs, using arXiv + DBLP only
(Semantic Scholar topic search, snowballing, and batch venue
confirmation are skipped) — see below for how to add one.

## Install

```bash
git clone https://github.com/QwertyJacob/papelito.git
cd papelito
pip install -e .
```

## Semantic Scholar API key

Snowballing and Semantic Scholar's own topic search need an API key
(request one free at
[semanticscholar.org/product/api](https://www.semanticscholar.org/product/api)).
**Never commit the key.** Put it in an unversioned, gitignored location —
`unversioned/` at the root of wherever you run `papelito` from is already
covered by this repo's `.gitignore`:

```bash
mkdir -p unversioned
echo "your-key-here" > unversioned/.semantic_scholar_api_key
```

papelito looks for the key in this order:

1. `semantic_scholar_api_key_file:` set in your review config, if present.
2. the `SEMANTIC_SCHOLAR_API_KEY` environment variable.
3. `./unversioned/.semantic_scholar_api_key`, relative to wherever you
   run `papelito` from.

If Semantic Scholar's terms matter for your use — if you publish results
produced with it, they ask for attribution to the Semantic Scholar Open
Data Platform (see their
[API tutorial](https://www.semanticscholar.org/product/api/tutorial)).

## Usage

Copy and edit the example config, then run it:

```bash
cp examples/example_config.yaml my_review.yaml
$EDITOR my_review.yaml
papelito run my_review.yaml
```

This writes `output/report.md` (path configurable via `output_dir:`).

### Config reference

See [`examples/example_config.yaml`](examples/example_config.yaml) for a
fully-commented example. The shape:

```yaml
project_name: "My review"
topics:
  - label: "short label for grouping"
    query: "plain-language search query"
    arxiv_query: 'all:"optional arXiv boolean query"'   # optional
venues:                          # optional; omit/empty = keep everything
  - name: "NeurIPS"
    aliases: ["NIPS", "Neural Information Processing Systems"]
seeds:                           # optional, for snowballing
  - "a seed paper title or close paraphrase"
authors:                         # optional, for author-scoped identification
  - "Donald D. Hoffman"
known_titles_file: known.txt     # optional, one title per line
options:
  arxiv_results_per_topic: 25
  s2_results_per_topic: 25
  snowball: true
  snowball_limit: 500
  dblp_venue_confirm: true
output_dir: output
```

### As a library

```python
from papelito.config import load_config
from papelito.pipeline.runner import run

config = load_config("my_review.yaml")
report_path = run(config)
```

## Notes on eligibility decisions

Venue matching is exact/substring matching (plus regex for arXiv's free
text) against your allow-list — no NLP/relevance model. Title
deduplication and seed resolution use fixed word-overlap thresholds. This
keeps every inclusion/exclusion decision auditable: it always reduces to
string matching and conditional logic over structured API fields, and
every intermediate step is saved to `raw/` for you to inspect.

DBLP's coverage is CS-centric — community venues outside computer science
won't be indexed there, so DBLP-only venue confirmation will legitimately
miss them.

## Author-scoped identification (non-CS fields, prolific authors)

`arxiv`+`s2 topic search`+`dblp` are all CS-leaning: arXiv is preprint-server
coverage for physics/CS/math-adjacent fields, and DBLP indexes CS venues
only. For an author who publishes mainly in other disciplines (psychology,
philosophy, cognitive science, biology, ...), those sources will
legitimately miss most of their work, and a topic search alone can't
reliably distinguish "the right person's paper" from someone with the same
byline. Requires a Semantic Scholar API key.

Add `authors:` to your config with one or more author names. For each,
papelito resolves the name to a Semantic Scholar author id (most-prolific
matching profile wins, logged for you to sanity-check), pulls that
profile's *entire* publication list, and keeps only the papers matching a
configured topic's keywords — there's no per-author search query to lean
on for relevance, so filtering happens after the full list is fetched.

This also guards (imperfectly) against a known S2 data quality issue:
author disambiguation sometimes merges unrelated same-name people into one
profile. The topic filter incidentally screens out most such noise, but
skim the `[author] matched ...` log line for the resolved id and paper
count, and spot-check `raw/02b_author_candidates.json` if a name is
ambiguous.
