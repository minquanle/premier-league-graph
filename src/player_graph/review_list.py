"""A list of the graph's riskiest items for people to check, most useful first.

These groups are chosen because they look suspicious, so they must not be used to estimate the graph's precision;
that needs the simple random sample.
"""
from __future__ import annotations

import csv
import json
import re
from collections import Counter

from .common import DATA, REPORTS, now, read_jsonl
from .resolve import Wikidata, fold_name, same_token

SENTENCE_END = re.compile(r"[.!?…]\s+|\n+")
FIELDS = ["stt", "nhom", "uu_tien", "assertion_id", "ten_trong_bai_chu_the", "chu_the", "quan_he", "ten_trong_bai_doi_tuong", "doi_tuong", "trang_thai",
          "ly_do", "cau_truoc", "bang_chung", "cau_sau", "link", "nhan", "ghi_chu"]
RELATIONS_WITH_CLUB = {"PLAYS_FOR": ("subject_id", "object_id"), "TRANSFERRED_TO": ("subject_id", "object_id"), "LOANED_TO": ("subject_id", "object_id"),
                       "EXTENDED_CONTRACT_WITH": ("subject_id", "object_id"), "MANAGES": ("subject_id", "object_id"), "APPOINTED_MANAGER_OF": ("subject_id", "object_id"),
                       "RESIGNED_FROM": ("subject_id", "object_id"), "DISMISSED_MANAGER": ("object_id", "subject_id")}


def around(text, start, end):
    """The sentence before and after an evidence span: many evidence sentences name a person only as 'he'."""
    before = [p for p in SENTENCE_END.split(text[:start]) if p.strip()]
    after = [p for p in SENTENCE_END.split(text[end:]) if p.strip()]
    return (before[-1].strip() if before else ""), (after[0].strip() if after else "")


def given_name_only(surface, canonical):
    s, n = fold_name(surface), fold_name(canonical)
    return len(s) == 1 and len(n) > 1 and same_token(s[0], n[0]) and not same_token(s[0], n[-1])


def export(run_name):
    folder = DATA / "graph" / run_name
    summary = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
    entities = {e["entity_id"]: e for e in read_jsonl(folder / "entities.jsonl")}
    relations = read_jsonl(folder / "relations.jsonl")
    assertions = {a["assertion_id"]: a for a in read_jsonl(folder / "assertions.jsonl")}
    raw = {a["assertion_id"]: a for a in read_jsonl(DATA / "llm" / summary.get("llm_run", run_name) / "assertions.jsonl")}
    articles = {a["article_id"]: a for a in read_jsonl(folder / "articles.jsonl")}
    name = lambda entity_id: entities.get(entity_id, {}).get("canonical_name", entity_id)
    rows = []

    def add(group, priority, aid, reason):
        a, r = assertions[aid], raw[aid]
        text = articles.get(a["article_id"], {}).get("clean_text", "")
        before, after = around(text, a["evidence_start"], a["evidence_end"]) if a.get("evidence_start") is not None else ("", "")
        rows.append({"nhom": group, "uu_tien": priority, "assertion_id": aid, "ten_trong_bai_chu_the": r["subject"]["name"], "chu_the": name(a["subject_id"]),
                     "quan_he": a["relation_type"], "ten_trong_bai_doi_tuong": r["object"]["name"], "doi_tuong": name(a["object_id"]), "trang_thai": a["assertion_status"],
                     "ly_do": reason, "cau_truoc": before, "bang_chung": a["evidence_text"], "cau_sau": after, "link": a["source_url"], "nhan": "", "ghi_chu": ""})

    in_graph = {aid for r in relations for aid in r["assertion_ids"]}
    # 1. People identified from one given name.
    for aid in sorted(in_graph):
        a, r = assertions[aid], raw[aid]
        for end, key in (("subject", "subject_id"), ("object", "object_id")):
            if a[key] in entities and entities[a[key]]["entity_type"] == "Person" and given_name_only(r[end]["name"], name(a[key])):
                add("mot_chu_chi_khop_ten_rieng", 1, aid, f"'{r[end]['name']}' chỉ khớp tên riêng của {name(a[key])}")
                break
    # 2. Extractor names the recognizer did not find in the article.
    for aid in sorted(in_graph):
        if assertions[aid]["name_check"] == "not_found":
            add("ten_khong_thay_trong_bai", 2, aid, "Bộ nhận dạng không tìm thấy tên này trong bài")
    # 3. Edges whose person has no such club on Wikidata; one evidence each, those with least support first.
    teams = Wikidata(offline=True).claims(sorted(i for i, e in entities.items() if e["entity_type"] == "Person" and e.get("qid")))
    for r in sorted(relations, key=lambda r: r["evidence_count"]):
        ends = RELATIONS_WITH_CLUB.get(r["relation_type"])
        if ends and r[ends[0]] in teams and r[ends[1]] not in teams[r[ends[0]]].get("teams", set()):
            add("wikidata_khong_xac_nhan", 3, r["assertion_ids"][0], f"Wikidata không ghi {name(r[ends[0]])} từng ở {name(r[ends[1]])}; có {r['evidence_count']} bằng chứng")
    # 4. Rejections by the semantic rules, to see whether they are too strict.
    for aid, a in sorted(assertions.items()):
        if a.get("semantic_check"):
            add("luat_ngu_nghia_da_loai", 4, aid, a["semantic_check"])
    # 5. Candidates refused by the year check: no assertion to show, only the name and the refused person.
    for row in read_jsonl(folder / "year_rejections.jsonl"):
        rows.append({"nhom": "luat_nam_da_loai", "uu_tien": 5, "assertion_id": "", "ten_trong_bai_chu_the": row["surface"], "chu_the": row["candidate"], "quan_he": "",
                     "ten_trong_bai_doi_tuong": "", "doi_tuong": ",".join(row["clubs"]), "trang_thai": "", "ly_do": f"Không hợp với năm bài {row['year']}", "cau_truoc": "",
                     "bang_chung": "", "cau_sau": "", "link": "", "nhan": "", "ghi_chu": ""})
    path = REPORTS / f"review_priority_{run_name}.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for i, row in enumerate(rows, 1):
            writer.writerow({"stt": i, **row})
    counts = Counter(r["nhom"] for r in rows)
    return {"created_at": now(), "path": str(path.relative_to(REPORTS.parent)), "rows": len(rows), "by_group": dict(counts),
            "note": "Chọn theo mức đáng ngờ, không dùng để ước lượng độ chính xác của graph"}
