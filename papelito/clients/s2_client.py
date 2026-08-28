"""Thin wrapper around the Semantic Scholar Graph API.

Semantic Scholar's rate limit is 1 request/sec, cumulative across all
endpoints; MIN_INTERVAL is kept slightly above that threshold on purpose,
to stay safely under the limit rather than skate it.

Attribution: if you publish results produced with this client, Semantic
Scholar's terms ask that you credit the Semantic Scholar Open Data
Platform -- see https://www.semanticscholar.org/product/api/tutorial.
"""
import os
import time

import requests

BASE = "https://api.semanticscholar.org/graph/v1"
MIN_INTERVAL = 1.05  # >1 req/sec cumulative limit -- stay safely under it

DEFAULT_SEARCH_FIELDS = "title,year,venue,publicationVenue,externalIds,abstract,paperId,authors,openAccessPdf"
DEFAULT_CITATION_FIELDS = "title,year,venue,publicationVenue,externalIds,paperId,abstract"
DEFAULT_BATCH_FIELDS = "title,year,venue,publicationVenue,externalIds,abstract,paperId"


class S2Client:
    def __init__(self, api_key=None):
        self.api_key = api_key
        self.session = requests.Session()
        headers = {"User-Agent": "papelito/0.1 (scoped literature search library; github.com/QwertyJacob/papelito)"}
        if api_key:
            headers["x-api-key"] = api_key
        self.session.headers.update(headers)
        self._last_call = 0.0

    @property
    def authenticated(self) -> bool:
        return bool(self.api_key)

    def _throttle(self):
        now = time.monotonic()
        wait = self._last_call + MIN_INTERVAL - now
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def _request(self, method, url, max_retries=8, **kwargs):
        delay = 5.0
        for attempt in range(max_retries):
            self._throttle()
            r = self.session.request(method, url, timeout=30, **kwargs)
            if r.status_code == 429:
                print(f"  [429 rate-limited] backing off {delay:.1f}s (attempt {attempt + 1}/{max_retries})")
                time.sleep(delay)
                delay = min(delay * 1.7, 90)
                continue
            if r.status_code >= 500:
                print(f"  [{r.status_code}] server error, retrying in {delay:.1f}s")
                time.sleep(delay)
                delay = min(delay * 1.7, 90)
                continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError(f"Gave up after {max_retries} retries on {url}")

    def search(self, query, limit=10, fields=DEFAULT_SEARCH_FIELDS):
        return self._request("GET", f"{BASE}/paper/search", params={"query": query, "limit": limit, "fields": fields})

    def paper_details(self, paper_id, fields):
        return self._request("GET", f"{BASE}/paper/{paper_id}", params={"fields": fields})

    def paper_refs(self, paper_id, limit=500, fields=DEFAULT_CITATION_FIELDS):
        return self._request("GET", f"{BASE}/paper/{paper_id}/references", params={"limit": limit, "fields": fields})

    def paper_cites(self, paper_id, limit=500, fields=DEFAULT_CITATION_FIELDS):
        return self._request("GET", f"{BASE}/paper/{paper_id}/citations", params={"limit": limit, "fields": fields})

    def paper_batch(self, ids, fields=DEFAULT_BATCH_FIELDS):
        """Fetch many papers in one request. ids can be S2 paperIds or
        prefixed external ids like 'ARXIV:2301.12345'. Chunked at 500 (the
        API max per call). Returns a list aligned with the input, with
        None for unmatched ids."""
        out = []
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            out.extend(self._request("POST", f"{BASE}/paper/batch", params={"fields": fields}, json={"ids": chunk}))
        return out


def resolve_api_key(config) -> "str | None":
    """Resolution order:
      1. `semantic_scholar_api_key_file` in the config, if set.
      2. the $SEMANTIC_SCHOLAR_API_KEY environment variable.
      3. ./unversioned/.semantic_scholar_api_key relative to the current
         working directory (see README.md for how to create it).
    Returns None (not an error) if no key is found anywhere -- callers fall
    back to an arXiv+DBLP-only run."""
    if config.semantic_scholar_api_key_file and os.path.isfile(config.semantic_scholar_api_key_file):
        with open(config.semantic_scholar_api_key_file) as f:
            key = f.read().strip()
            if key:
                return key

    env = os.environ.get("SEMANTIC_SCHOLAR_API_KEY")
    if env and env.strip():
        return env.strip()

    default_path = os.path.join(os.getcwd(), "unversioned", ".semantic_scholar_api_key")
    if os.path.isfile(default_path):
        with open(default_path) as f:
            key = f.read().strip()
            if key:
                return key

    return None
