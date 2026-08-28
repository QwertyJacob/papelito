"""Venue allow-list matching: structured-field matching (Semantic Scholar
and DBLP both return a clean `venue` string) plus free-text pattern
matching (arXiv `comment`/`journal_ref` fields, where authors routinely
self-report acceptance venue in prose, e.g. "Accepted to NeurIPS 2024")."""
import re


def _norm(s):
    return (s or "").strip().lower()


class VenueMatcher:
    def __init__(self, venues):
        self.venues = venues
        self._substrings = []  # [(lowercased alias, canonical name), ...]
        self._patterns = []    # [(compiled regex, canonical name), ...]
        for v in venues:
            for alias in v.all_names():
                if not alias:
                    continue
                self._substrings.append((_norm(alias), v.name))
                self._patterns.append((re.compile(r"\b" + re.escape(alias) + r"\b", re.I), v.name))

    @property
    def is_empty(self) -> bool:
        """True when the config carries no venue allow-list at all -- in
        that mode every candidate is kept regardless of venue, and venue
        fields on report entries are informational only, never filters."""
        return not self.venues

    def match_structured(self, *venue_strings):
        """Match against an API-supplied venue name (S2's `venue` /
        `publicationVenue.name`, or DBLP's `venue`). Returns the canonical
        allow-list name, or None."""
        for s in venue_strings:
            low = _norm(s)
            if not low:
                continue
            for alias, canonical in self._substrings:
                if alias in low:
                    return canonical
        return None

    def match_text(self, *texts):
        """Scan free text (arXiv comment/journal_ref) for a recognizable
        venue mention. Returns the canonical allow-list name, or None."""
        blob = " ".join(t for t in texts if t)
        if not blob:
            return None
        for pattern, canonical in self._patterns:
            if pattern.search(blob):
                return canonical
        return None
