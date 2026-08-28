"""DBLP publication-search API client. DBLP is a standard bibliographic
database, so using it as a venue-confirmation source is itself a
recognized, citable methodological choice, not an ad hoc shortcut. Note
DBLP's coverage is CS-centric: community venues outside computer science
won't be indexed there, so title lookups for those will legitimately come
back empty -- that's a coverage limitation to note in a report, not a bug."""
import re
import time

import requests

BASE = "https://dblp.org/search/publ/api"
MIN_INTERVAL = 2.0


def _norm(t):
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def _title_matches(query_title, hit_title, min_overlap=0.85):
    """Require the DBLP hit's title to actually be (near-)the same paper --
    DBLP's search is fuzzy/stemmed and can surface unrelated papers for
    short, generic-sounding titles."""
    a, b = _norm(query_title), _norm(hit_title or "")
    if not a or not b:
        return False
    if a == b:
        return True
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return False
    return len(ta & tb) / max(len(ta), len(tb)) >= min_overlap


class DblpClient:
    def __init__(self):
        self._last_call = 0.0

    def _throttle(self):
        now = time.monotonic()
        wait = self._last_call + MIN_INTERVAL - now
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def search_title(self, title, max_retries=6):
        """Query DBLP by title text. Returns a list of hit 'info' dicts, or
        None if every retry was exhausted (distinguishes "gave up" from
        "confirmed zero hits")."""
        delay = 4.0
        for attempt in range(max_retries):
            self._throttle()
            try:
                r = requests.get(BASE, params={"q": title, "format": "json", "h": 10}, timeout=30)
            except requests.exceptions.RequestException as e:
                print(f"    [dblp connection error: {e.__class__.__name__}] retrying in {delay:.1f}s")
                time.sleep(delay)
                delay = min(delay * 1.8, 45)
                continue
            if r.status_code != 200:
                print(f"    [dblp {r.status_code}] retrying in {delay:.1f}s")
                time.sleep(delay)
                delay = min(delay * 1.8, 45)
                continue
            try:
                d = r.json()
            except ValueError:
                print("    [dblp non-json response] retrying")
                time.sleep(delay)
                delay = min(delay * 1.8, 45)
                continue
            hits = d.get("result", {}).get("hits", {}).get("hit", [])
            return [h["info"] for h in hits]
        return None

    def best_venue_match(self, title, venue_matcher):
        """Returns a dict with the DBLP venue string and its canonical
        allow-list name, the string "LOOKUP_FAILED" if all retries were
        exhausted, or None if no hit both matches the title and (when a
        venue allow-list is configured) confirms an in-scope venue."""
        hits = self.search_title(title)
        if hits is None:
            return "LOOKUP_FAILED"
        for h in hits:
            if not _title_matches(title, h.get("title")):
                continue
            venue = h.get("venue")
            if venue_matcher.is_empty:
                if venue:
                    return {"venue": venue, "canonical_venue": venue, "year": h.get("year"), "key": h.get("key")}
                continue
            canonical = venue_matcher.match_structured(venue)
            if canonical:
                return {"venue": venue, "canonical_venue": canonical, "year": h.get("year"), "key": h.get("key")}
        return None
