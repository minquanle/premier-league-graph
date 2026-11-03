"""Network statistics and a fixed review sample for one built graph."""
from __future__ import annotations

import csv
import random
from collections import Counter

import networkx as nx

from .common import DATA, REPORTS, now, read_jsonl, write_json


def analyze(run_name, sample_size=120, per_type=8, seed=42):
    folder = DATA / "graph" / run_name
    entities = {e["entity_id"]: e for e in read_jsonl(folder / "entities.jsonl")}
    relations = read_jsonl(folder / "relations.jsonl")
    assertions = {a["assertion_id"]: a for a in read_jsonl(folder / "assertions.jsonl")}
    g = nx.MultiDiGraph()
    for eid, e in entities.items():
        g.add_node(eid, name=e["canonical_name"], type=e["entity_type"])
    for r in relations:
        g.add_edge(r["subject_id"], r["object_id"], key=r["relation_type"])
    simple = nx.Graph(g)
    components = sorted(nx.connected_components(simple), key=len, reverse=True)
    degree = sorted(simple.degree, key=lambda x: x[1], reverse=True)
    pagerank = nx.pagerank(simple) if simple.number_of_edges() else {}
    people = [e for e in entities.values() if e["entity_type"] == "Person"]
    stats = {
        "created_at": now(), "run_name": run_name,
        "nodes": g.number_of_nodes(), "edges": g.number_of_edges(),
        "nodes_by_type": dict(Counter(e["entity_type"] for e in entities.values())),
        "person_roles": dict(Counter("+".join(e["roles"]) or "unknown" for e in people)),
        "nodes_by_scope": dict(Counter(e["scope_role"] for e in entities.values())),
        "nodes_with_wikidata_qid": sum(1 for e in entities.values() if e["qid"]),
        "edges_by_type": dict(Counter(r["relation_type"] for r in relations).most_common()),
        "relation_types_observed": len({r["relation_type"] for r in relations}),
        "edges_with_multiple_evidence": sum(1 for r in relations if r["evidence_count"] > 1),
        "edges_by_evidence_language": dict(Counter("+".join(r["languages"]) for r in relations)),
        "density_undirected": round(nx.density(simple), 5),
        "average_degree": round(2 * simple.number_of_edges() / max(1, simple.number_of_nodes()), 2),
        "connected_components": len(components), "largest_component_nodes": len(components[0]) if components else 0,
        "top_degree": [(entities[n]["canonical_name"], d) for n, d in degree[:15]],
        "top_pagerank": [(entities[n]["canonical_name"], round(v, 4)) for n, v in sorted(pagerank.items(), key=lambda x: -x[1])[:15]],
    }
    write_json(REPORTS / f"graph_stats_{run_name}.json", stats)

    # Review sample: a few edges of every type (rare types are the risky ones), then random fill.
    rng = random.Random(seed)
    by_type = {}
    for r in relations:
        by_type.setdefault(r["relation_type"], []).append(r)
    sample = []
    for kind in sorted(by_type):
        sample += rng.sample(by_type[kind], min(per_type, len(by_type[kind])))
    stratified = len(sample)
    rest = [r for r in relations if r not in sample]
    sample += rng.sample(rest, max(0, min(len(rest), sample_size - len(sample))))
    path = REPORTS / f"review_sample_{run_name}.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["relation_id", "subject", "relation", "object", "language", "evidence", "source_url",
                                               "sampling", "dung_sai", "ghi_chu"])
        writer.writeheader()
        for i, r in enumerate(sample):
            a = assertions[r["assertion_ids"][0]]
            writer.writerow({"relation_id": r["relation_id"], "subject": entities[r["subject_id"]]["canonical_name"], "relation": r["relation_type"],
                             "object": entities[r["object_id"]]["canonical_name"], "language": a["language"], "evidence": a["evidence_text"],
                             "source_url": a["source_url"], "sampling": "per_type" if i < stratified else "random",
                             "dung_sai": "", "ghi_chu": ""})
    stats["review_sample"] = {"path": str(path.relative_to(REPORTS.parent)), "size": len(sample)}
    return stats
