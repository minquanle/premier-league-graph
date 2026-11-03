"""Turn a teacher run into (1) a human review sample and (2) a supervised fine-tuning dataset.

Neither needs entity resolution: correctness is judged against the evidence sentence, and the student
learns the same text-to-JSON task the teacher performed.
"""
from __future__ import annotations

import csv
import json
import random
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

from .common import DATA, REPORTS, normalize, now, read_jsonl, write_json, write_jsonl
from .llm_extract import PROMPT_VERSION, SYSTEM_PROMPT, chunks, user_message, validate

LABEL_GUIDE = """Hướng dẫn chấm (cột nhan): ghi D nếu đúng, S nếu sai.
Một dòng ĐÚNG khi: (1) chủ thể và đối tượng là đúng người/CLB được nói trong câu; (2) đúng loại quan hệ và đúng chiều
(CLB đích của chuyển nhượng, CLB sa thải HLV...); (3) đúng trạng thái (reported = bài khẳng định đã xảy ra/đang đúng,
rumor = tin đồn/quan tâm, negated = bị phủ nhận, hypothetical = giả định); (4) câu bằng chứng đủ để thấy điều đó.
Chỉ dựa vào câu bằng chứng và bài báo, không dựa vào hiểu biết ngoài. PLAYS_FOR/MANAGES đúng cả khi câu nói về quá khứ
("đội bóng cũ", "từng khoác áo"). Nếu sai, ghi loại lỗi vào cột loi: sai_nguoi, sai_clb, sai_chieu, sai_loai, sai_trang_thai,
bang_chung_khong_du, khac."""


def review_sample(run_name, per_type=10, size=150, seed=42):
    rows = read_jsonl(DATA / "llm" / run_name / "assertions.jsonl")
    rng = random.Random(seed)
    by_type = {}
    for r in rows:
        by_type.setdefault(r["relation_type"], []).append(r)
    picked = []
    for kind in sorted(by_type):
        picked += rng.sample(by_type[kind], min(per_type, len(by_type[kind])))
    stratified = len(picked)
    rest = [r for r in rows if r not in picked]
    picked += rng.sample(rest, max(0, min(len(rest), size - stratified)))
    path = REPORTS / f"review_teacher_{run_name}.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["stt", "assertion_id", "subject", "relation", "object", "status", "language",
                                               "evidence", "source_url", "sampling", "nhan", "loi", "ghi_chu"])
        writer.writeheader()
        for i, r in enumerate(picked, 1):
            writer.writerow({"stt": i, "assertion_id": r["assertion_id"], "subject": r["subject"]["name"], "relation": r["relation_type"],
                             "object": r["object"]["name"], "status": r["status"], "language": r["language"], "evidence": r["evidence_text"],
                             "source_url": r["source_url"], "sampling": "theo_loai" if i <= stratified else "ngau_nhien",
                             "nhan": "", "loi": "", "ghi_chu": ""})
    (REPORTS / "HUONG_DAN_CHAM.txt").write_text(LABEL_GUIDE + "\n", encoding="utf-8")
    return {"path": str(path.relative_to(REPORTS.parent)), "rows": len(picked), "stratified_rows": stratified,
            "random_rows": len(picked) - stratified, "by_type": dict(Counter(r["relation_type"] for r in picked))}


RARE_TYPES = {"RESIGNED_FROM", "CONFLICTS_WITH", "REPRESENTS", "NEGOTIATES_TO_SIGN"}
# Relations shared by many unrelated stories; a common triple of these does not make two articles the same event.
GENERIC_TYPES = {"PLAYS_FOR", "MANAGES", "CRITICIZES"}


def clean_target(article, part, payload):
    """Keep only relations the graph would keep: structurally valid and passing the semantic checks."""
    from .resolve import semantic_check
    types = {e.get("name"): e.get("type") for e in payload.get("entities", []) or [] if isinstance(e, dict)}
    kept, dropped = [], Counter()
    for relation in payload.get("relations", []) or []:
        ok, rejected = validate(article, part, {"entities": payload.get("entities", []), "relations": [relation]})
        reason = rejected[0].get("reason") if rejected else None
        if ok:
            reason = semantic_check({"relation_type": relation.get("relation"), "evidence_text": relation.get("evidence") or "",
                                     "object": {"name": relation.get("object"), "type": types.get(relation.get("object"))}})
        if ok and not reason:
            kept.append({k: relation.get(k) for k in ["subject", "relation", "object", "status", "evidence", "time"]})
        else:
            dropped[str(reason)] += 1
    names = {n for r in kept for n in (r["subject"], r["object"])}
    entities = [{k: e.get(k) for k in ["name", "type", "role"]} for e in payload.get("entities", []) or []
                if isinstance(e, dict) and e.get("name") in names]
    return {"entities": entities, "relations": kept}, dropped


def event_clusters(raw, articles):
    """Union articles that are the same story: same duplicate cluster, or the same specific event (subject, relation,
    object outside GENERIC_TYPES) published within 14 days. Splitting by cluster keeps one event out of both train and test."""
    parent = {i: i for i in articles}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    first = {}
    for aid, a in articles.items():
        if a.get("duplicate_cluster_id"):
            parent[find(aid)] = find(first.setdefault(a["duplicate_cluster_id"], aid))
    seen = defaultdict(set)
    for row in raw:
        for r in row["payload"].get("relations", []) or []:
            if isinstance(r, dict) and r.get("relation") not in GENERIC_TYPES:
                seen[(normalize(str(r.get("subject"))), r.get("relation"), normalize(str(r.get("object"))))].add(row["article_id"])
    day = lambda aid: date.fromisoformat(articles[aid]["published_at"][:10])
    for ids in seen.values():
        ids = sorted(ids, key=day)
        for a, b in zip(ids, ids[1:]):
            if (day(b) - day(a)).days <= 14:
                parent[find(a)] = find(b)
    return {aid: find(aid) for aid in articles}


def export_training(run_name, dataset_name=None, articles_path=None, seed=42, val=0.1, test=0.1, empty_share=0.15, eval_size=200):
    """Chat-format SFT data from a frozen snapshot, filtered like the graph and split by event cluster."""
    dataset_name = dataset_name or f"sft_{run_name}"
    articles_path = Path(articles_path) if articles_path else DATA / "processed/articles.jsonl"
    excluded = {e["article_id"] for e in read_jsonl(DATA / "interim/excluded_articles_v2.jsonl")}
    articles = {a["article_id"]: a for a in read_jsonl(articles_path) if a["article_id"] not in excluded}
    raw_all = read_jsonl(DATA / "llm" / run_name / "raw_outputs.jsonl")
    raw = [r for r in raw_all if r["article_id"] in articles]
    rng = random.Random(seed)
    cluster = event_clusters(raw, articles)
    rows, dropped = [], Counter()
    for row in raw:
        article = articles[row["article_id"]]
        part = chunks(article["clean_text"])[row["chunk"]]
        target, why = clean_target(article, part, row["payload"])
        dropped += why
        rows.append({"id": f"{row['article_id']}#{row['chunk']}", "article_id": row["article_id"], "cluster": cluster[row["article_id"]],
                     "language": article["language"], "types": sorted({r["relation"] for r in target["relations"]}),
                     "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                                  {"role": "user", "content": user_message(article, part)},
                                  {"role": "assistant", "content": json.dumps(target, ensure_ascii=False)}]})
    # Whole clusters go to test, then val, until each holds its share of the chunks.
    sizes = Counter(r["cluster"] for r in rows)
    order = sorted(sizes)
    rng.shuffle(order)
    split_of, filled = {}, Counter()
    for c in order:
        split = next((s for s, share in [("test", test), ("val", val)] if filled[s] < share * len(rows)), "train")
        split_of[c] = split
        filled[split] += sizes[c]
    out = {"train": [], "val": [], "test": []}
    for r in rows:
        out[split_of[r["cluster"]]].append(r)
    # Train only: keep some empty answers (so the student learns to return nothing) and repeat rare relation types once.
    empty = [r for r in out["train"] if not r["types"]]
    full = [r for r in out["train"] if r["types"]]
    keep_empty = rng.sample(empty, min(len(empty), round(empty_share / (1 - empty_share) * len(full))))
    rare = [r for r in full if RARE_TYPES & set(r["types"])]
    out["train"] = full + keep_empty + rare
    rng.shuffle(out["train"])
    # A fixed evaluation subset: generation is slow, so the notebook scores these instead of the whole test split.
    out["test_eval"] = sorted(rng.sample(out["test"], min(eval_size, len(out["test"]))), key=lambda r: r["id"])
    folder = DATA / "train" / dataset_name
    for split, items in out.items():
        write_jsonl(folder / f"{split}.jsonl", [{k: v for k, v in r.items() if k != "cluster"} for r in items])
    relations = lambda items: Counter(x["relation"] for r in items for x in json.loads(r["messages"][2]["content"])["relations"])
    summary = {"created_at": now(), "dataset": dataset_name, "teacher_run": run_name, "prompt_version": PROMPT_VERSION,
               "articles_source": str(articles_path), "split_unit": "event_cluster", "seed": seed,
               "raw_chunks": len(raw_all), "chunks_from_excluded_or_missing_articles": len(raw_all) - len(raw),
               "relations_dropped_from_targets": dict(dropped),
               "clusters": len(sizes), "largest_cluster_chunks": max(sizes.values()),
               "examples": {k: len(v) for k, v in out.items()},
               "train_repeated_rare_examples": len(rare), "train_empty_kept": len(keep_empty), "train_empty_available": len(empty),
               "articles": {k: len({r["article_id"] for r in v}) for k, v in out.items()},
               "languages": {k: dict(Counter(r["language"] for r in v)) for k, v in out.items()},
               "relations_by_type": {k: dict(relations(v)) for k, v in out.items()},
               "empty_targets": {k: sum(1 for r in v if not r["types"]) for k, v in out.items()},
               "max_chars": max(sum(len(m["content"]) for m in r["messages"]) for v in out.values() for r in v)}
    write_json(folder / "summary.json", summary)
    return summary
