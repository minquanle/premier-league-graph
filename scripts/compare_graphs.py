"""Compare two built graphs: edges and people added, removed or moved to another identity. Read-only on the graphs."""
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(run):
    read = lambda name: [json.loads(line) for line in (ROOT / "data/graph" / run / name).read_text(encoding="utf-8").splitlines() if line.strip()]
    return {e["entity_id"]: e for e in read("entities.jsonl")}, read("relations.jsonl"), read("assertions.jsonl")


def run(old_name, new_name):
    old_e, old_r, old_a = load(old_name)
    new_e, new_r, new_a = load(new_name)
    key = lambda r: (r["subject_id"], r["relation_type"], r["object_id"])
    old_k, new_k = {key(r): r for r in old_r}, {key(r): r for r in new_r}
    name = lambda entities, i: entities.get(i, {}).get("canonical_name", i)
    show = lambda entities, k, rows: {"subject": name(entities, k[0]), "relation": k[1], "object": name(entities, k[2]), "evidence": rows[k]["evidence_count"]}
    removed = sorted(set(old_k) - set(new_k), key=lambda k: -old_k[k]["evidence_count"])
    added = sorted(set(new_k) - set(old_k), key=lambda k: -new_k[k]["evidence_count"])
    # The same assertion attached to another person shows identity changes directly.
    new_by_id = {a["assertion_id"]: a for a in new_a}
    moved = Counter()
    for a in old_a:
        b = new_by_id.get(a["assertion_id"])
        if not b:
            continue
        for end in ("subject_id", "object_id"):
            if a[end] != b[end]:
                moved[(name(old_e, a[end]), name(new_e, b[end]))] += 1
    summary = {"old": old_name, "new": new_name, "old_nodes": len(old_e), "new_nodes": len(new_e), "old_edges": len(old_r), "new_edges": len(new_r),
               "edges_removed": len(removed), "edges_added": len(added),
               "edges_by_type_old": dict(Counter(r["relation_type"] for r in old_r)), "edges_by_type_new": dict(Counter(r["relation_type"] for r in new_r)),
               "identity_changes": [{"from": a, "to": b, "assertions": n} for (a, b), n in moved.most_common()],
               "removed": [show(old_e, k, old_k) for k in removed], "added": [show(new_e, k, new_k) for k in added]}
    out = ROOT / f"reports/compare_{old_name}_{new_name}.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in summary if k not in {"removed", "added", "identity_changes"}}, ensure_ascii=False, indent=1))
    print("identity changes:", len(summary["identity_changes"]))
    return summary


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    run(sys.argv[1], sys.argv[2])
