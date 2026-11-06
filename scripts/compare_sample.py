"""Blind sample for choosing between two graphs, and its scoring.

Edges both graphs share need few labels, so the sample concentrates on edges only one graph has. Reviewers never see
which graph an edge comes from. The key is kept in a separate file.

    python scripts/compare_sample.py export main_v7 student_v1
    python scripts/compare_sample.py score main_v7 student_v1
"""
import csv
import json
import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from player_graph.review_list import around  # noqa: E402

SIZES = {"both": 30, "only_a": 60, "only_b": 60}
FIELDS = ["stt", "chu_the", "quan_he", "doi_tuong", "cau_truoc", "bang_chung", "cau_sau", "link", "nhan", "loai_loi", "ghi_chu"]
GUIDE = """Ghi cột nhan: D (đúng), S (sai), K (không đủ căn cứ, kể cả khi mở bài gốc).
Đúng khi: đúng người/CLB, đúng loại và chiều quan hệ, và đoạn bài báo thực sự nêu quan hệ đó.
Nếu sai, ghi loai_loi: sai_nguoi, sai_clb, sai_chieu, sai_loai, khong_phai_su_that, khac.
"""


def read(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load(run):
    folder = ROOT / "data/graph" / run
    return ({e["entity_id"]: e["canonical_name"] for e in read(folder / "entities.jsonl")}, read(folder / "relations.jsonl"),
            {a["assertion_id"]: a for a in read(folder / "assertions.jsonl")})


def export(a, b, seed=2026):
    articles = {x["article_id"]: x for x in read(ROOT / "data/processed/snapshot_v6.jsonl")}
    data = {run: load(run) for run in (a, b)}
    key = lambda r: (r["subject_id"], r["relation_type"], r["object_id"])
    edges = {run: {key(r): r for r in data[run][1]} for run in (a, b)}
    groups = {"both": (sorted(set(edges[a]) & set(edges[b])), a), "only_a": (sorted(set(edges[a]) - set(edges[b])), a), "only_b": (sorted(set(edges[b]) - set(edges[a])), b)}
    rng = random.Random(seed)
    rows, answer = [], []
    for group, (keys, run) in groups.items():
        for k in rng.sample(keys, min(SIZES[group], len(keys))):
            names, _, assertions = data[run]
            x = assertions[rng.choice(edges[run][k]["assertion_ids"])]
            text = articles.get(x["article_id"], {}).get("clean_text", "")
            before, after = around(text, x["evidence_start"], x["evidence_end"]) if x.get("evidence_start") is not None else ("", "")
            rows.append({"chu_the": names[k[0]], "quan_he": k[1], "doi_tuong": names[k[2]], "cau_truoc": before, "bang_chung": x["evidence_text"], "cau_sau": after,
                         "link": x["source_url"], "nhan": "", "loai_loi": "", "ghi_chu": "", "_group": group})
    rng.shuffle(rows)
    reports = ROOT / "reports"
    with (reports / f"compare_sample_{a}_{b}.csv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for i, row in enumerate(rows, 1):
            answer.append({"stt": i, "group": row["_group"]})
            writer.writerow({"stt": i, **{k: row[k] for k in FIELDS if k != "stt"}})
    (reports / f"compare_sample_{a}_{b}_key.json").write_text(json.dumps({"a": a, "b": b, "groups": {g: len(v[0]) for g, v in groups.items()}, "rows": answer}, indent=1), encoding="utf-8")
    (reports / "HUONG_DAN_CHAM_SO_SANH.txt").write_text(GUIDE, encoding="utf-8")
    print({"rows": len(rows), "population": {g: len(v[0]) for g, v in groups.items()}})


def wilson(correct, n, z=1.96):
    if not n:
        return (0, 0)
    p = correct / n
    centre, margin = p + z * z / (2 * n), z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return round((centre - margin) / (1 + z * z / n), 3), round((centre + margin) / (1 + z * z / n), 3)


def score(a, b):
    reports = ROOT / "reports"
    key = json.loads((reports / f"compare_sample_{a}_{b}_key.json").read_text(encoding="utf-8"))
    groups = {r["stt"]: r["group"] for r in key["rows"]}
    with (reports / f"compare_sample_{a}_{b}.csv").open(encoding="utf-8-sig", newline="") as f:
        labelled = [r for r in csv.DictReader(f) if r["nhan"].strip().upper() in {"D", "S", "K"}]
    tally = {g: [0, 0] for g in SIZES}
    for row in labelled:
        group = groups[int(row["stt"])]
        tally[group][1] += 1
        tally[group][0] += row["nhan"].strip().upper() == "D"
    rate = {g: (c / n if n else 0) for g, (c, n) in tally.items()}
    # Each graph's precision: shared edges and its own edges, weighted by how many of each it has.
    def precision(own):
        both, only = key["groups"]["both"], key["groups"][own]
        return round((both * rate["both"] + only * rate[own]) / (both + only), 3)
    print(json.dumps({"labelled": len(labelled), "by_group": {g: {"correct": c, "n": n, "wilson_95": wilson(c, n)} for g, (c, n) in tally.items()},
                      f"precision_{a}": precision("only_a"), f"precision_{b}": precision("only_b"),
                      "note": "Mẫu nhỏ để so sánh hai graph, chưa đủ để chứng minh trên 90%"}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    {"export": export, "score": score}[sys.argv[1]](sys.argv[2], sys.argv[3])
