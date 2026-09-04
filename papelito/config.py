"""Load and validate a papelito review configuration (a single YAML file)."""
from __future__ import annotations

import dataclasses
import os
from typing import List, Optional

import yaml


@dataclasses.dataclass
class Topic:
    """One search theme. `query` is used as-is against Semantic Scholar's
    plain-language search; `arxiv_query` overrides the auto-generated
    arXiv field-query (default: the whole `query` as one quoted phrase) for
    anyone who wants arXiv's boolean field syntax, e.g.
    'all:"graph neural network" AND all:"intrusion detection"'."""
    label: str
    query: str
    arxiv_query: Optional[str] = None

    def resolved_arxiv_query(self) -> str:
        if self.arxiv_query:
            return self.arxiv_query
        return f'all:"{self.query}"'


@dataclasses.dataclass
class Venue:
    """One allow-listed venue. `aliases` covers alternate names/abbreviations
    used across sources (e.g. name="NeurIPS", aliases=["NIPS", "Neural
    Information Processing Systems"])."""
    name: str
    aliases: List[str] = dataclasses.field(default_factory=list)

    def all_names(self) -> List[str]:
        return [self.name, *self.aliases]


@dataclasses.dataclass
class Options:
    arxiv_results_per_topic: int = 25
    s2_results_per_topic: int = 25
    snowball: bool = True
    snowball_limit: int = 500
    dblp_venue_confirm: bool = True


@dataclasses.dataclass
class Config:
    project_name: str
    topics: List[Topic]
    venues: List[Venue]
    seeds: List[str]
    options: Options
    output_dir: str
    known_titles_file: Optional[str] = None
    semantic_scholar_api_key_file: Optional[str] = None
    config_path: Optional[str] = None
    authors: List[str] = dataclasses.field(default_factory=list)


def _resolve(base_dir: str, path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    return path if os.path.isabs(path) else os.path.join(base_dir, path)


def load_config(path: str) -> Config:
    with open(path) as f:
        raw = yaml.safe_load(f) or {}

    base_dir = os.path.dirname(os.path.abspath(path))

    topics = []
    for t in raw.get("topics", []):
        if isinstance(t, str):
            topics.append(Topic(label=t, query=t))
        else:
            topics.append(Topic(**t))
    if not topics:
        raise ValueError("config must define at least one topic under `topics:`")

    venues = []
    for v in raw.get("venues", []) or []:
        if isinstance(v, str):
            venues.append(Venue(name=v))
        else:
            venues.append(Venue(name=v["name"], aliases=v.get("aliases", []) or []))

    options = Options(**(raw.get("options") or {}))

    return Config(
        project_name=raw.get("project_name", "Untitled review"),
        topics=topics,
        venues=venues,
        seeds=raw.get("seeds", []) or [],
        authors=raw.get("authors", []) or [],
        options=options,
        output_dir=_resolve(base_dir, raw.get("output_dir", "output")),
        known_titles_file=_resolve(base_dir, raw.get("known_titles_file")),
        semantic_scholar_api_key_file=_resolve(base_dir, raw.get("semantic_scholar_api_key_file")),
        config_path=os.path.abspath(path),
    )
