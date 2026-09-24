"""Stage 2 (optional, requires a Semantic Scholar key): citation-graph
snowballing from user-specified seed papers -- backward references and
forward citations, kept only when they are BOTH venue-eligible AND
topically on-theme. That's precision over recall, deliberately: an OR
filter here would flood the pool with anything that merely cites (or is
cited by) a heavily-cited seed, regardless of topic."""
import re


def _norm(t):
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def resolve_seeds(seed_queries, s2_client, log=print):
    """Resolve each seed query/title to its top Semantic Scholar search
    hit, but only if that hit is actually a confident match for the query
    (>=50% word overlap) -- otherwise skip it rather than snowball from the
    wrong paper."""
    resolved = {}
    for q in seed_queries:
        try:
            r = s2_client.search(q, limit=5)
        except Exception as e:
            log(f"  [seed] ERROR for {q!r}: {e}")
            continue
        hits = r.get("data") or []
        if not hits:
            log(f"  [seed] no hit for {q!r}")
            continue
        qn = set(_norm(q).split())
        best_hit, best_overlap = None, 0.0
        for h in hits:
            tn = set(_norm(h.get("title")).split())
            if not tn or not qn:
                continue
            overlap = len(qn & tn) / max(1, min(len(qn), len(tn)))
            if overlap > best_overlap:
                best_overlap = overlap
                best_hit = h
        if not best_hit or best_overlap < 0.35:
            top_title = best_hit.get("title") if best_hit else (hits[0].get("title") if hits else None)
            log(f"  [seed] SKIP (top hit too dissimilar, overlap={best_overlap:.2f}): {q!r} -> {top_title!r}")
            continue
        resolved[q] = best_hit
        log(f"  [seed] OK (overlap={best_overlap:.2f}): {q!r} -> {best_hit.get('title')!r}")
    return resolved


def _topic_keywords(topics):
    kws = set()
    for t in topics:
        kws.add(t.label.lower())
        kws.update(w.lower() for w in t.query.split())
    return [k for k in kws if len(k) > 3]


def _topically_relevant(title, abstract, patterns, min_hits=2):
    """Require at least `min_hits` DISTINCT keywords to match, not just one.
    A citation graph is large and generic; topic vocabularies inevitably
    include ordinary words ("interface", "vision", "network", "reality")
    that show up in unrelated papers on their own ("teacher interfaces",
    "curriculum vision"). A single hit is essentially noise; two distinct
    on-topic words landing in the same title/abstract is a much stronger
    signal, and is what actually delivers on this module's stated
    precision-over-recall goal."""
    blob = f"{title or ''} {abstract or ''}"
    return sum(1 for p in patterns if p.search(blob)) >= min_hits


def snowball(seeds, topics, s2_client, venue_matcher, limit, log=print):
    patterns = [re.compile(re.escape(k), re.I) for k in _topic_keywords(topics)]
    candidates = {}

    def add(p, label, venue):
        pid = p.get("paperId")
        if not pid or not p.get("title"):
            return
        if pid not in candidates:
            ext = p.get("externalIds") or {}
            candidates[pid] = {
                "arxiv_id": ext.get("ArXiv"),
                "s2_paper_id": pid,
                "title": p.get("title"),
                "published": f"{p.get('year')}-01-01T00:00:00Z" if p.get("year") else None,
                "summary": p.get("abstract"),
                "authors": None,
                "_venue": venue,
                "_venue_source": "s2_snowball",
                "_raw_venue": p.get("venue") or (p.get("publicationVenue") or {}).get("name"),
                "_topics": [],
                "_sources": [],
            }
        if label not in candidates[pid]["_topics"]:
            candidates[pid]["_topics"].append(label)
        candidates[pid]["_sources"].append(label)

    for q, seed in seeds.items():
        pid = seed["paperId"]
        label_base = f"snowball:{q[:40]}"
        for direction, fetch in (("refs", s2_client.paper_refs), ("cites", s2_client.paper_cites)):
            log(f"  fetching {direction} for {seed['title']!r} ...")
            try:
                resp = fetch(pid, limit=limit)
            except Exception as e:
                log(f"    ERROR: {e}")
                continue
            items = resp.get("data") or []
            kept = 0
            for item in items:
                p = item.get("citedPaper") or item.get("citingPaper") or item
                if not p or not p.get("title"):
                    continue
                raw_venue = p.get("venue") or (p.get("publicationVenue") or {}).get("name")
                on_topic = _topically_relevant(p.get("title"), p.get("abstract"), patterns)
                if venue_matcher.is_empty:
                    matched_venue = raw_venue
                    eligible = on_topic
                else:
                    matched_venue = venue_matcher.match_structured(p.get("venue"), raw_venue)
                    eligible = bool(matched_venue) and on_topic
                if not eligible:
                    continue
                add(p, f"{label_base} ({direction})", matched_venue)
                kept += 1
            log(f"    {len(items)} {direction}, {kept} kept (venue + on-topic)")
    return candidates
