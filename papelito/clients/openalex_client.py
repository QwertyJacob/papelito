"""Thin wrapper around the OpenAlex REST API for papelito.
Provides academic search, citation graph (backward references & forward citations),
and Open Access PDF links without requiring an API key.
"""
import re
import time
import requests

BASE = "https://api.openalex.org"
MIN_INTERVAL = 0.15  # OpenAlex polite pool allows ~10 req/sec


def reconstruct_abstract(inverted_index):
    if not inverted_index:
        return None
    pos_word = []
    for word, positions in inverted_index.items():
        for pos in positions:
            pos_word.append((pos, word))
    pos_word.sort(key=lambda x: x[0])
    return " ".join(w for _, w in pos_word)


def _normalize_work(w):
    if not w or not isinstance(w, dict):
        return None
    wid = w.get("id", "").split("/")[-1]
    primary_loc = w.get("primary_location") or {}
    source = primary_loc.get("source") or {}
    raw_venue = source.get("display_name")
    
    # Try to extract arXiv ID
    arxiv_id = None
    ids = w.get("ids") or {}
    for k in ("arxiv", "doi"):
        val = ids.get(k)
        if val and "arxiv" in val.lower():
            arxiv_id = val.split("/")[-1].lower().replace("arxiv:", "")
    landing = (primary_loc.get("landing_page_url") or "").lower()
    if not arxiv_id and "arxiv.org/abs/" in landing:
        arxiv_id = landing.split("arxiv.org/abs/")[-1].strip("/")

    oa = w.get("open_access") or {}
    best_oa = w.get("best_oa_location") or {}
    pdf_url = oa.get("oa_url") or best_oa.get("pdf_url")
    if not pdf_url and arxiv_id:
        pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"

    authors = []
    for a in (w.get("authorships") or []):
        author_dict = a.get("author") or {}
        if author_dict.get("display_name"):
            authors.append({"name": author_dict.get("display_name")})

    return {
        "paperId": wid,
        "openalexId": w.get("id"),
        "title": w.get("title"),
        "year": w.get("publication_year"),
        "venue": raw_venue,
        "publicationVenue": {"name": raw_venue},
        "externalIds": {"ArXiv": arxiv_id} if arxiv_id else {},
        "abstract": reconstruct_abstract(w.get("abstract_inverted_index")),
        "authors": authors,
        "openAccessPdf": {"url": pdf_url} if pdf_url else None,
        "pdf_url": pdf_url,
        "cited_by_count": w.get("cited_by_count", 0),
        "referenced_works": w.get("referenced_works", []),
    }


class OpenAlexClient:
    def __init__(self, email="researcher@pidsmaker.org", api_key=None):
        self.email = email
        self.api_key = api_key
        self.session = requests.Session()
        ua = f"papelito/0.1 (mailto:{email}; https://github.com/QwertyJacob/papelito)"
        self.session.headers.update({"User-Agent": ua})
        if api_key:
            self.session.headers.update({"api_key": api_key})
        self._last_call = 0.0

    @property
    def authenticated(self) -> bool:
        return True  # Polite pool is always usable

    def _throttle(self):
        now = time.monotonic()
        wait = self._last_call + MIN_INTERVAL - now
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def _request(self, method, url, max_retries=5, **kwargs):
        delay = 2.0
        for attempt in range(max_retries):
            self._throttle()
            try:
                r = self.session.request(method, url, timeout=30, **kwargs)
            except requests.exceptions.RequestException as e:
                time.sleep(delay)
                delay = min(delay * 1.5, 30)
                continue
            if r.status_code == 429:
                time.sleep(delay)
                delay = min(delay * 2.0, 60)
                continue
            if r.status_code >= 500:
                time.sleep(delay)
                delay = min(delay * 1.5, 30)
                continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError(f"Gave up after {max_retries} retries on {url}")

    def search(self, query, limit=25):
        clean_query = re.sub(r'[:"\'()]+', " ", query).strip()
        data = self._request("GET", f"{BASE}/works", params={
            "search": clean_query,
            "per-page": min(limit, 50),
            "sort": "relevance_score:desc",
        })
        results = [_normalize_work(w) for w in data.get("results") or []]
        return {"data": [r for r in results if r]}

    def paper_details(self, paper_id):
        pid = paper_id.split("/")[-1]
        data = self._request("GET", f"{BASE}/works/{pid}")
        return _normalize_work(data)

    def paper_refs(self, paper_id, limit=50):
        pid = paper_id.split("/")[-1]
        work = self.paper_details(pid)
        if not work:
            return {"data": []}
        refs = work.get("referenced_works") or []
        if not refs:
            return {"data": []}
        
        target_ids = [r.split("/")[-1] for r in refs[:limit]]
        batch_size = 25
        results = []
        for i in range(0, len(target_ids), batch_size):
            chunk = target_ids[i:i + batch_size]
            filter_str = "|".join(chunk)
            try:
                data = self._request("GET", f"{BASE}/works", params={
                    "filter": f"openalex_id:{filter_str}",
                    "per-page": len(chunk),
                })
                for w in (data.get("results") or []):
                    nw = _normalize_work(w)
                    if nw:
                        results.append({"citedPaper": nw})
            except Exception:
                pass
        return {"data": results}

    def paper_cites(self, paper_id, limit=50):
        pid = paper_id.split("/")[-1]
        try:
            data = self._request("GET", f"{BASE}/works", params={
                "filter": f"cites:{pid}",
                "per-page": min(limit, 50),
                "sort": "cited_by_count:desc",
            })
            results = []
            for w in (data.get("results") or []):
                nw = _normalize_work(w)
                if nw:
                    results.append({"citingPaper": nw})
            return {"data": results}
        except Exception:
            return {"data": []}
