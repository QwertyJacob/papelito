"""Orchestrates a full papelito run: identify -> snowball -> merge ->
confirm -> flag known titles -> render the Markdown report. Every stage
checkpoints its raw output to <output_dir>/raw/ so a crash or a rate-limit
wall never loses more than the stage in flight."""
import json
import os
import re

from ..clients.arxiv_client import ArxivClient
from ..clients.dblp_client import DblpClient
from ..clients.s2_client import S2Client, resolve_api_key
from ..clients.openalex_client import OpenAlexClient
from ..venues import VenueMatcher
from . import identify, merge as merge_stage, report as report_stage, snowball as snowball_stage


def _norm_title(t):
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def _surname(name):
    return name.strip().split()[-1].lower()


def _filter_by_authors(pool, author_names):
    if not author_names:
        return pool
    surnames = {_surname(n) for n in author_names}

    def matches(c):
        for a in (c.get("authors") or []):
            words = re.findall(r"[a-z]+", (a or "").lower())
            if any(s in words for s in surnames):
                return True
        return False

    return {k: c for k, c in pool.items() if matches(c)}


def _checkpoint(outdir, name, data):
    raw_dir = os.path.join(outdir, "raw")
    os.makedirs(raw_dir, exist_ok=True)
    with open(os.path.join(raw_dir, name), "w") as f:
        json.dump(data, f, indent=2)


def run(config, log=print):
    os.makedirs(config.output_dir, exist_ok=True)
    venue_matcher = VenueMatcher(config.venues)

    api_key = resolve_api_key(config)
    arxiv_client = ArxivClient()
    dblp_client = DblpClient()
    if api_key:
        graph_client = S2Client(api_key=api_key)
        client_name = "Semantic Scholar"
    else:
        graph_client = OpenAlexClient()
        client_name = "OpenAlex"
        log("No Semantic Scholar API key found -- using OpenAlex for graph search & citation snowballing.")

    log(f"\n=== Identifying via arXiv ({len(config.topics)} topic(s)) ===")
    arxiv_pool = identify.identify_arxiv(config.topics, arxiv_client, venue_matcher, dblp_client, config.options, log=log)
    _checkpoint(config.output_dir, "01_arxiv_candidates.json", arxiv_pool)
    log(f"  {len(arxiv_pool)} unique arXiv candidates")

    s2_pool, author_pool, snowball_pool = {}, {}, {}
    log(f"\n=== Identifying via {client_name} ({len(config.topics)} topic(s)) ===")
    s2_pool = identify.identify_s2(config.topics, graph_client, config.options, log=log)
    _checkpoint(config.output_dir, "02_s2_candidates.json", s2_pool)
    log(f"  {len(s2_pool)} unique {client_name} candidates")

    if config.authors and api_key:
        log(f"\n=== Identifying via author search ({len(config.authors)} author(s)) ===")
        author_pool = identify.identify_by_authors(config.authors, config.topics, graph_client, log=log)
        _checkpoint(config.output_dir, "02b_author_candidates.json", author_pool)
        log(f"  {len(author_pool)} unique author-based candidates")

    if config.options.snowball and config.seeds:
        log(f"\n=== Resolving {len(config.seeds)} seed(s) via {client_name} ===")
        seeds = snowball_stage.resolve_seeds(config.seeds, graph_client, log=log)
        _checkpoint(config.output_dir, "03_resolved_seeds.json", seeds)

        log(f"\n=== Snowballing from {len(seeds)} resolved seed(s) via {client_name} ===")
        snowball_pool = snowball_stage.snowball(
            seeds, config.topics, graph_client, venue_matcher, config.options.snowball_limit, log=log
        )
        _checkpoint(config.output_dir, "04_snowball_candidates.json", snowball_pool)
        log(f"  {len(snowball_pool)} unique snowball candidates")
    elif config.options.snowball and not config.seeds:
        log("\n(snowballing enabled but no seeds configured -- skipped)")

    if config.authors:
        log(f"\n=== Restricting arXiv/S2 topic-search hits to configured author(s) ===")
        before = len(arxiv_pool) + len(s2_pool)
        arxiv_pool = _filter_by_authors(arxiv_pool, config.authors)
        s2_pool = _filter_by_authors(s2_pool, config.authors)
        log(f"  {before} -> {len(arxiv_pool) + len(s2_pool)} (author-profile pull is unaffected)")

    log("\n=== Merging and deduplicating ===")
    pool = merge_stage.merge_pools(arxiv_pool, s2_pool, author_pool, snowball_pool)
    log(f"  {len(pool)} unique candidates total")

    if api_key:
        log("\n=== Confirming venues via Semantic Scholar batch lookup ===")
        _confirm_venues_batch(pool, graph_client, venue_matcher, log=log)
    elif not venue_matcher.is_empty:
        log("\n=== Confirming venues via OpenAlex structured venue matching ===")
        for c in pool.values():
            if not c.get("_venue") and c.get("_raw_venue"):
                mv = venue_matcher.match_text(c.get("_raw_venue"))
                if mv:
                    c["_venue"] = mv
                    c["_venue_source"] = "openalex_venue"

    _checkpoint(config.output_dir, "05_merged_pool.json", pool)

    if venue_matcher.is_empty:
        in_scope, excluded = pool, {}
    else:
        in_scope = {k: c for k, c in pool.items() if c.get("_venue")}
        excluded = {k: c for k, c in pool.items() if not c.get("_venue")}
    log(f"\nIn scope: {len(in_scope)}  |  Excluded: {len(excluded)}")

    known_titles = _load_known_titles(config.known_titles_file)
    if known_titles:
        for c in in_scope.values():
            c["_already_known"] = _norm_title(c["title"]) in known_titles

    _checkpoint(config.output_dir, "06_in_scope.json", in_scope)
    _checkpoint(config.output_dir, "07_excluded.json", excluded)

    log("\n=== Writing report ===")
    report_path = report_stage.write_report(config, in_scope, excluded, venue_matcher, s2_authenticated=bool(api_key))
    log(f"Report written to {report_path}")
    return report_path


def _confirm_venues_batch(pool, s2_client, venue_matcher, log=print):
    """Authoritatively (re)confirms venue for the WHOLE merged pool via
    Semantic Scholar's batch endpoint -- catches in-scope papers that
    arXiv's self-reported metadata and DBLP's fuzzy title search both
    missed for individual candidates."""
    keys = list(pool.keys())
    batch_ids = []
    for k in keys:
        c = pool[k]
        if c.get("arxiv_id"):
            batch_ids.append(f"ARXIV:{re.sub(r'v[0-9]+$', '', c['arxiv_id'])}")
        elif c.get("s2_paper_id"):
            batch_ids.append(c["s2_paper_id"])
        else:
            batch_ids.append(None)

    lookup_ids = [b for b in batch_ids if b]
    log(f"  batch-confirming {len(lookup_ids)}/{len(keys)} candidates")
    try:
        batch_results = s2_client.paper_batch(lookup_ids)
    except Exception as e:
        log(f"  ERROR during batch confirmation: {e}")
        return

    by_key = {}
    i = 0
    for k, bid in zip(keys, batch_ids):
        if bid is None:
            continue
        by_key[k] = batch_results[i]
        i += 1

    n_reclaimed = 0
    for k, c in pool.items():
        s2 = by_key.get(k)
        if not s2:
            continue
        raw_name = (s2.get("publicationVenue") or {}).get("name") or s2.get("venue")
        if not venue_matcher.is_empty:
            match = venue_matcher.match_structured(s2.get("venue"), raw_name)
            if match and not c.get("_venue"):
                c["_venue"] = match
                c["_venue_source"] = "s2_batch_confirmed"
                n_reclaimed += 1
        if raw_name and not c.get("_raw_venue"):
            c["_raw_venue"] = raw_name
        if not c.get("s2_paper_id") and s2.get("paperId"):
            c["s2_paper_id"] = s2.get("paperId")
        if not c.get("summary") and s2.get("abstract"):
            c["summary"] = s2.get("abstract")

    log(f"  reclaimed into in-scope via batch confirmation: {n_reclaimed}")


def _load_known_titles(path):
    if not path or not os.path.isfile(path):
        return None
    titles = set()
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                titles.add(_norm_title(line))
    return titles
