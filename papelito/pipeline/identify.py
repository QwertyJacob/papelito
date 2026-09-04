"""Stage 1: identify candidate papers via arXiv and Semantic Scholar, one
search per configured topic. Each candidate carries whatever venue signal
is available at individual-candidacy time; a later batch-confirmation pass
(see runner.py) authoritatively re-checks the whole merged pool."""
import re


def identify_arxiv(topics, arxiv_client, venue_matcher, dblp_client, options, log=print):
    """Venue is confirmed against arXiv's own self-reported comment /
    journal_ref text first; DBLP title-lookup is the fallback for papers
    that don't self-report. Skipped entirely (nothing to check) when the
    config carries no venue allow-list."""
    candidates = {}
    for topic in topics:
        q = topic.resolved_arxiv_query()
        log(f"[arxiv] {topic.label!r}: {q}")
        try:
            hits = arxiv_client.search(q, max_results=options.arxiv_results_per_topic)
        except Exception as e:
            log(f"  ERROR: {e}")
            continue
        log(f"  {len(hits)} hits")
        for h in hits:
            aid = h.get("arxiv_id")
            if not aid or not h.get("title"):
                continue
            if aid not in candidates:
                venue, venue_source = None, None
                if not venue_matcher.is_empty:
                    venue = venue_matcher.match_text(h.get("comment"), h.get("journal_ref"))
                    if venue:
                        venue_source = "arxiv_self_reported"
                    elif options.dblp_venue_confirm and dblp_client is not None:
                        dblp_hit = dblp_client.best_venue_match(h["title"], venue_matcher)
                        if dblp_hit and dblp_hit != "LOOKUP_FAILED":
                            venue = dblp_hit["canonical_venue"]
                            venue_source = "dblp_title_lookup"
                candidates[aid] = {
                    "arxiv_id": aid,
                    "s2_paper_id": None,
                    "title": h["title"],
                    "published": h.get("published"),
                    "summary": h.get("summary"),
                    "authors": h.get("authors"),
                    "_venue": venue,
                    "_venue_source": venue_source,
                    "_raw_venue": h.get("journal_ref") or h.get("comment"),
                    "_topics": [],
                    "_sources": ["arxiv"],
                }
            if topic.label not in candidates[aid]["_topics"]:
                candidates[aid]["_topics"].append(topic.label)
    return candidates


def identify_s2(topics, s2_client, options, log=print):
    """Semantic Scholar's search response already carries structured
    venue / publicationVenue fields, so no extra lookup is needed here --
    just record them for the batch-confirmation pass to check."""
    candidates = {}
    for topic in topics:
        log(f"[s2] {topic.label!r}: {topic.query}")
        try:
            r = s2_client.search(topic.query, limit=options.s2_results_per_topic)
        except Exception as e:
            log(f"  ERROR: {e}")
            continue
        hits = r.get("data") or []
        log(f"  {len(hits)} hits")
        for h in hits:
            pid = h.get("paperId")
            if not pid or not h.get("title"):
                continue
            ext = h.get("externalIds") or {}
            if pid not in candidates:
                candidates[pid] = {
                    "arxiv_id": ext.get("ArXiv"),
                    "s2_paper_id": pid,
                    "title": h["title"],
                    "published": f"{h['year']}-01-01T00:00:00Z" if h.get("year") else None,
                    "summary": h.get("abstract"),
                    "authors": [a.get("name") for a in (h.get("authors") or [])],
                    "_venue": None,
                    "_venue_source": None,
                    "_raw_venue": h.get("venue") or (h.get("publicationVenue") or {}).get("name"),
                    "_topics": [],
                    "_sources": ["s2_topic_search"],
                }
            if topic.label not in candidates[pid]["_topics"]:
                candidates[pid]["_topics"].append(topic.label)
    return candidates


def _topic_patterns(topics):
    """One compiled keyword pattern per topic, built from the topic's own
    label + query words (>3 chars). Used by identify_by_authors, where
    there is no per-topic search call to lean on for relevance -- the
    whole publication list of a (possibly prolific, possibly ambiguously
    disambiguated) author has to be filtered after the fact."""
    patterns = []
    for t in topics:
        kws = {t.label.lower(), *(w.lower() for w in t.query.split())}
        kws = [k for k in kws if len(k) > 3]
        if kws:
            patterns.append((t.label, re.compile("|".join(re.escape(k) for k in kws), re.I)))
    return patterns


def _best_author_match(name, hits):
    """Prefer a candidate whose name string actually matches the configured
    name (S2 author search is fuzzy and returns many same-surname authors);
    among those, the most-prolific profile is the main one -- ties among
    unrelated same-name authors are rare enough that this is a reasonable
    default, and the choice is logged so it stays auditable."""
    low = name.strip().lower()
    exact = [h for h in hits if (h.get("name") or "").strip().lower() == low]
    pool = exact or hits
    return max(pool, key=lambda h: h.get("paperCount") or 0)


def identify_by_authors(author_names, topics, s2_client, log=print):
    """Author-scoped identification: resolve each configured author name to
    a Semantic Scholar author id, pull their ENTIRE publication list, then
    keep only the papers matching a configured topic's keywords.

    Built for authors whose on-theme work is scattered across venues a
    CS-centric pipeline (arXiv, DBLP) has no coverage of: there's no venue
    or topic search to identify them by, so relevance here is decided
    purely by keyword match against title+abstract -- the same auditable
    string-matching philosophy as the rest of papelito, just applied after
    fetching the full list instead of narrowing a search query.

    Note this can also surface FALSE POSITIVES the other direction: S2's
    author disambiguation is itself imperfect and can merge unrelated
    people who share a name into one profile. The topic filter incidentally
    guards against this too (an unrelated paper is unlikely to match), but
    it's worth spot-checking the resolved author id in the log output."""
    candidates = {}
    patterns = _topic_patterns(topics)
    for name in author_names:
        log(f"[author] resolving {name!r}")
        try:
            r = s2_client.author_search(name)
        except Exception as e:
            log(f"  ERROR: {e}")
            continue
        hits = r.get("data") or []
        if not hits:
            log(f"  no author match for {name!r}")
            continue
        chosen = _best_author_match(name, hits)
        log(
            f"  matched {chosen.get('name')!r} (authorId={chosen.get('authorId')}, "
            f"{chosen.get('paperCount')} papers, hIndex={chosen.get('hIndex')})"
        )
        try:
            papers = s2_client.author_papers(chosen["authorId"])
        except Exception as e:
            log(f"  ERROR fetching papers: {e}")
            continue
        log(f"  {len(papers)} total papers by this author profile")
        kept = 0
        for p in papers:
            pid = p.get("paperId")
            if not pid or not p.get("title"):
                continue
            blob = f"{p.get('title') or ''} {p.get('abstract') or ''}"
            matched = [label for label, pat in patterns if pat.search(blob)]
            if not matched:
                continue
            if pid not in candidates:
                ext = p.get("externalIds") or {}
                candidates[pid] = {
                    "arxiv_id": ext.get("ArXiv"),
                    "s2_paper_id": pid,
                    "title": p["title"],
                    "published": f"{p['year']}-01-01T00:00:00Z" if p.get("year") else None,
                    "summary": p.get("abstract"),
                    "authors": [a.get("name") for a in (p.get("authors") or [])],
                    "_venue": None,
                    "_venue_source": None,
                    "_raw_venue": p.get("venue") or (p.get("publicationVenue") or {}).get("name"),
                    "_topics": [],
                    "_sources": ["s2_author"],
                }
            for label in matched:
                if label not in candidates[pid]["_topics"]:
                    candidates[pid]["_topics"].append(label)
            kept += 1
        log(f"  {kept} on-topic (of {len(papers)})")
    return candidates
