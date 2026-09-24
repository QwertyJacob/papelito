"""arXiv API client -- the primary identification source: far more
reliable rate-limit-wise than the unauthenticated Semantic Scholar API.
Venue membership can be inferred from the self-reported `comment` /
`journal-ref` fields (arXiv authors routinely state acceptance venue
there); DBLP title-lookup (see dblp_client.py) is the usual fallback for
papers that don't self-report. Works standalone, with no API key."""
import time
import xml.etree.ElementTree as ET

import requests

NS = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
BASE = "https://export.arxiv.org/api/query"
MIN_INTERVAL = 3.5  # arXiv API terms of use ask for >=3s between requests


def _parse_entry(e):
    def txt(tag, ns="a"):
        el = e.find(f"{ns}:{tag}", NS)
        return el.text.strip() if el is not None and el.text else None

    title = txt("title")
    title = " ".join(title.split()) if title else None
    summary = txt("summary")
    summary = " ".join(summary.split()) if summary else None
    authors = [a.find("a:name", NS).text for a in e.findall("a:author", NS)]
    id_txt = txt("id")
    primary_cat_el = e.find("arxiv:primary_category", NS)
    return {
        "arxiv_id": id_txt.split("/abs/")[-1] if id_txt else None,
        "url": id_txt,
        "title": title,
        "published": txt("published"),
        "updated": txt("updated"),
        "summary": summary,
        "authors": authors,
        "comment": txt("comment", ns="arxiv"),
        "journal_ref": txt("journal_ref", ns="arxiv"),
        "primary_category": primary_cat_el.attrib.get("term") if primary_cat_el is not None else None,
    }


class ArxivClient:
    def __init__(self):
        self._last_call = 0.0

    def _throttle(self):
        now = time.monotonic()
        wait = self._last_call + MIN_INTERVAL - now
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def search(self, search_query, start=0, max_results=30, sort_by="relevance", max_retries=5):
        params = {
            "search_query": search_query,
            "start": start,
            "max_results": max_results,
            "sortBy": sort_by,
            "sortOrder": "descending",
        }
        delay = 5.0
        for attempt in range(max_retries):
            self._throttle()
            r = requests.get(BASE, params=params, timeout=30, allow_redirects=True)
            if r.status_code >= 400:
                print(f"  [{r.status_code}] retrying in {delay:.1f}s")
                time.sleep(delay)
                delay = min(delay * 1.7, 60)
                continue
            try:
                root = ET.fromstring(r.text)
            except ET.ParseError:
                print(f"  [parse error] retrying in {delay:.1f}s")
                time.sleep(delay)
                delay = min(delay * 1.7, 60)
                continue
            return [_parse_entry(e) for e in root.findall("a:entry", NS)]
        raise RuntimeError(f"Gave up after {max_retries} retries on query={search_query!r}")
