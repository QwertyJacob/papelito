"""Stage 1: identify candidate papers via arXiv and Semantic Scholar, one
search per configured topic. Each candidate carries whatever venue signal
is available at individual-candidacy time; a later batch-confirmation pass
(see runner.py) authoritatively re-checks the whole merged pool."""


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
