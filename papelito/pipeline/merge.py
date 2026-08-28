"""Stage 3: merge the arXiv, Semantic Scholar topic-search, and snowball
candidate pools into one deduplicated pool, keyed by arXiv id (preferred,
since it's stable across sources), then S2 paperId, then normalized
title."""
import re


def _norm_title(t):
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def _arxiv_key(aid):
    if not aid:
        return None
    return re.sub(r"v\d+$", "", aid)


def _unified_key(c):
    ak = _arxiv_key(c.get("arxiv_id"))
    if ak:
        return f"ARXIV:{ak}"
    if c.get("s2_paper_id"):
        return f"S2:{c['s2_paper_id']}"
    return f"TITLE:{_norm_title(c.get('title'))}"


def merge_pools(*pools):
    merged = {}
    for pool in pools:
        for c in pool.values():
            key = _unified_key(c)
            if key not in merged:
                merged[key] = {**c, "_topics": list(c.get("_topics", [])), "_sources": list(c.get("_sources", []))}
                continue
            existing = merged[key]
            for t in c.get("_topics", []):
                if t not in existing["_topics"]:
                    existing["_topics"].append(t)
            for s in c.get("_sources", []):
                if s not in existing["_sources"]:
                    existing["_sources"].append(s)
            if not existing.get("s2_paper_id") and c.get("s2_paper_id"):
                existing["s2_paper_id"] = c["s2_paper_id"]
            if not existing.get("arxiv_id") and c.get("arxiv_id"):
                existing["arxiv_id"] = c["arxiv_id"]
            if not existing.get("summary") and c.get("summary"):
                existing["summary"] = c["summary"]
            if not existing.get("authors") and c.get("authors"):
                existing["authors"] = c["authors"]
            if not existing.get("_venue") and c.get("_venue"):
                existing["_venue"] = c["_venue"]
                existing["_venue_source"] = c.get("_venue_source")
            if not existing.get("_raw_venue") and c.get("_raw_venue"):
                existing["_raw_venue"] = c["_raw_venue"]
    return merged
