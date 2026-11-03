"""Read-only development audit. No API calls, graph import, or production rewrites.

Run from the project root with .venv/Scripts/python.exe scripts/audit_quality.py.
Manual judgments live beside the frozen samples; they are internal development judgments,
not an independent human gold set. Diagnostic samples must not be pooled with
the random sample to estimate population precision.
"""
from __future__ import annotations

import collections
import csv
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports/quality_audit_2026_10_30"


def read(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def save(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run():
    articles = {a["article_id"]: a for a in read(ROOT / "data/processed/articles.jsonl")}
    assertions = read(ROOT / "data/llm/main_v1/assertions.jsonl")
    entities = read(ROOT / "data/graph/pilot300_gemini3flash/entities.jsonl")
    pilot = read(ROOT / "data/graph/pilot300_gemini3flash/assertions.jsonl")
    pilot_articles = {a["article_id"]: a for a in read(ROOT / "data/graph/pilot300_gemini3flash/articles.jsonl")}
    pilot_snapshot_errors = sum(a["article_id"] not in pilot_articles or
                                pilot_articles[a["article_id"]]["clean_text"][a["evidence_start"]:a["evidence_end"]] != a["evidence_text"]
                                for a in pilot)
    structural = collections.Counter()
    for a in assertions:
        article = articles.get(a["article_id"])
        if not article:
            structural["missing_article"] += 1
        elif article["clean_text"][a["evidence_start"]:a["evidence_end"]] != a["evidence_text"]:
            structural["offset_mismatch"] += 1
    stale = collections.Counter()
    for a in pilot:
        article = articles.get(a["article_id"])
        if not article:
            stale["article_removed"] += 1
        elif article["clean_text"][a["evidence_start"]:a["evidence_end"]] != a["evidence_text"]:
            stale["offset_changed"] += 1

    # Inspect duplication introduced by a parent HTML element plus its children.
    content_flags = []
    for a in articles.values():
        paragraphs = a["clean_text"].splitlines()
        containers = [p for p in paragraphs if len(p) >= 1200]
        contained = [p for p in paragraphs if 80 <= len(p) < 1200 and any(p in big for big in containers)]
        if len(contained) >= 4:
            content_flags.append({"article_id": a["article_id"], "source": a["source"], "title": a["title"],
                                  "contained_paragraphs": len(contained), "url": a["canonical_url"]})
    save("content_duplication_flags.json", content_flags)

    # Replay the roster stage entirely offline. Per-name API fallback is NOT run.
    from player_graph.resolve import Wikidata, Roster, club_identity, expand_in_article
    from player_graph.common import normalize

    class CacheOnlyWikidata(Wikidata):
        def __init__(self):
            self.cache = ROOT / "data/cache/wikidata"
            self.misses = 0

        def get(self, params):
            key = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()
            path = self.cache / f"{key}.json"
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
            self.misses += 1
            return {}

    class CacheOnlyRoster(Roster):
        def _fetch(self, qid):
            path = self.cache / f"{qid}.json"
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
            self.failed.append(qid)
            return []

    wd = CacheOnlyWikidata()
    club_names = {a[k]["name"] for a in assertions for k in ("subject", "object") if a[k]["type"] == "Club"}
    clubs = {name: club_identity(name, wd) for name in sorted(club_names)}
    raw = read(ROOT / "data/llm/main_v1/raw_outputs.jsonl")
    expansion = expand_in_article(assertions, raw)
    people = collections.defaultdict(set)
    references = collections.defaultdict(list)
    for a in assertions:
        for end, other in (("subject", "object"), ("object", "subject")):
            if a[end]["type"] != "Person":
                continue
            surface = expansion.get((a["article_id"], normalize(a[end]["name"])), a[end]["name"])
            references[surface].append(a["assertion_id"])
            people[surface]
            if a[other]["type"] == "Club" and clubs[a[other]["name"]]["qid"]:
                people[surface].add(clubs[a[other]["name"]]["qid"])
    roster_qids = {p.stem for p in (ROOT / "data/cache/roster").glob("Q*.json")}
    roster = CacheOnlyRoster(roster_qids)
    matches = []
    for surface, context in people.items():
        match = roster.match(surface, context)
        if match:
            matches.append({"surface": surface, "qid": match[0], "canonical_name": roster.label[match[0]],
                            "method": match[1], "context_clubs": sorted(context), "assertion_ids": references[surface]})
    save("main_roster_replay.json", {"stage": "offline roster only; not final main_v1 graph", "cache_misses_club_search": wd.misses,
                                     "roster_clubs": len(roster_qids), "surface_names": len(people), "matches": matches})

    reviewed = read(OUT / "manual_reviews.jsonl")
    csv_rows = []
    for sample_file, kind in [("relation_sample.jsonl", "relation_random"), ("relation_diagnostics.jsonl", "relation_diagnostic"), ("identity_sample.jsonl", "identity_random")]:
        sample = read(OUT / sample_file)
        expected = {r.get("sample_id", r.get("diagnostic_id")) for r in sample}
        actual = [r["sample_id"] for r in reviewed if r["sample_kind"] == kind]
        assert set(actual) == expected and len(actual) == len(expected), (kind, "missing or duplicate reviews")
        for item in sample:
            index = item.get("sample_id", item.get("diagnostic_id"))
            label = next(r for r in reviewed if r["sample_kind"] == kind and r["sample_id"] == index)
            csv_rows.append({**label, "assertion_or_entity_id": item.get("assertion_id", item.get("entity_id")),
                             "subject_or_canonical_name": item.get("subject", {}).get("name", item.get("canonical_name", "")),
                             "relation": item.get("relation_type", ""), "object": item.get("object", {}).get("name", ""),
                             "aliases": " | ".join(item.get("aliases", [])), "evidence": item.get("evidence_text", ""),
                             "context": item.get("context", ""), "url": item.get("source_url", "")})
    summary = {"reviewer_type": "internal", "independent_human_gold": False,
               "main_articles": len(articles), "main_assertions": len(assertions), "structural_errors": dict(structural),
               "pilot_assertions_stale_against_current_corpus": dict(stale),
               "pilot_own_snapshot_structural_errors": pilot_snapshot_errors,
               "pilot_entities": len(entities), "pilot_unresolved_entities": sum(not e["qid"] for e in entities),
               "content_container_duplication_flags": len(content_flags),
               "review_counts": {kind: dict(collections.Counter(r["verdict"] for r in reviewed if r["sample_kind"] == kind)) for kind in sorted({r["sample_kind"] for r in reviewed})},
               "limitations": ["Relation random sample is assertion-weighted, not deduplicated-edge precision.",
                               "Supported means source-text entailment with article context; not verified real-world truth.",
                               "Identity judgments marked no_conflict are not independently verified QID accuracy.",
                               "Pilot graph is older than the cleaned corpus; main graph still running at audit start.",
                               "No recall estimate; no proof of >90% quality; diagnostics are deliberately non-random."]}
    save("summary.json", summary)
    with (OUT / "manual_reviews.csv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(csv_rows[0]))
        writer.writeheader()
        writer.writerows(csv_rows)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    run()
