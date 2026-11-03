"""Compare extractor outputs (base, student, another teacher) against the teacher on the held-out split.

Three views, because exact string agreement alone understates a model that names things differently:
- strict: (subject, relation, object, status) identical as strings, as in the Kaggle notebook;
- lenient: names normalized (club aliases, accents, person matched by surname) and status ignored;
- valid: share of predicted relations that pass the same validator the teacher passed (verbatim evidence etc.).
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict

from .common import DATA, REPORTS, normalize, now, read_jsonl, write_json
from .llm_extract import canonical_club, chunks, parse_json, validate


def club_or_person_key(name):
    canonical, scope = canonical_club(name or "")
    if scope != "unlisted":
        return "club:" + normalize(canonical)
    tokens = normalize(name or "").split()
    return "name:" + (tokens[-1] if tokens else "")


def triples(payload, mode):
    out = set()
    for r in (payload or {}).get("relations", []) or []:
        if not isinstance(r, dict):
            continue
        if mode == "strict":
            out.add((str(r.get("subject") or "").strip().lower(), r.get("relation"), str(r.get("object") or "").strip().lower(), r.get("status")))
        else:
            if r.get("status") != "reported":
                continue
            out.add((club_or_person_key(r.get("subject")), r.get("relation"), club_or_person_key(r.get("object"))))
    return out


def prf(tp, fp, fn):
    p, r = tp / max(1, tp + fp), tp / max(1, tp + fn)
    return {"precision": round(p, 3), "recall": round(r, 3), "f1": round(2 * p * r / max(1e-9, p + r), 3), "tp": tp, "fp": fp, "fn": fn}


def evaluate(dataset, predictions, split="test"):
    """predictions: {name: {example_id: raw_text_or_payload}} ; returns metrics per name and an error log."""
    test = {r["id"]: r for r in read_jsonl(DATA / "train" / dataset / f"{split}.jsonl")}
    articles = {a["article_id"]: a for a in read_jsonl(DATA / "processed/articles.jsonl")}
    report, errors = {}, {}
    for name, preds in predictions.items():
        counts = {m: Counter() for m in ["strict", "lenient"]}
        by_type, by_language = defaultdict(Counter), defaultdict(Counter)
        valid = Counter()
        log = []
        for ex_id, row in test.items():
            gold = json.loads(row["messages"][2]["content"])
            raw = preds.get(ex_id)
            try:
                payload = raw if isinstance(raw, dict) else parse_json(raw or "")
            except (ValueError, json.JSONDecodeError):
                payload = None
            valid["examples"] += 1
            valid["parsed"] += payload is not None
            article = articles[row["article_id"]]
            part = chunks(article["clean_text"])[int(ex_id.split("#")[1])]
            ok, bad = validate(article, part, payload or {})
            valid["predicted"] += len(ok) + len(bad)
            valid["passed"] += len(ok)
            for b in bad:
                valid["reject:" + b["reason"]] += 1
            for mode in counts:
                g, p = triples(gold, mode), triples(payload, mode)
                counts[mode]["tp"] += len(g & p)
                counts[mode]["fp"] += len(p - g)
                counts[mode]["fn"] += len(g - p)
                if mode == "lenient":
                    by_language[row["language"]].update(tp=len(g & p), fp=len(p - g), fn=len(g - p))
                    for t in g & p:
                        by_type[t[1]]["tp"] += 1
                    for t in p - g:
                        by_type[t[1]]["fp"] += 1
                        log.append({"id": ex_id, "kind": "extra", "triple": t})
                    for t in g - p:
                        by_type[t[1]]["fn"] += 1
                        log.append({"id": ex_id, "kind": "missed", "triple": t})
        report[name] = {"strict": prf(**counts["strict"]), "lenient": prf(**counts["lenient"]),
                        "json_parse_rate": round(valid["parsed"] / valid["examples"], 3),
                        "predicted_relations": valid["predicted"],
                        "passed_validator": round(valid["passed"] / max(1, valid["predicted"]), 3),
                        "validator_rejections": {k[7:]: v for k, v in valid.items() if k.startswith("reject:")},
                        "lenient_by_type": {k: prf(v["tp"], v["fp"], v["fn"]) for k, v in sorted(by_type.items())},
                        "lenient_by_language": {k: prf(v["tp"], v["fp"], v["fn"]) for k, v in sorted(by_language.items())}}
        errors[name] = log
    return report, errors


def llm_predictions(dataset, model, salt="", workers=4):
    """Run a teacher-style model on the held-out split through the same client and prompt."""
    from concurrent.futures import ThreadPoolExecutor
    from .llm_extract import Client
    client = Client(model, workers, salt)
    rows = read_jsonl(DATA / "train" / dataset / "test.jsonl")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        records = list(pool.map(lambda r: client.complete(r["messages"][1]["content"]), rows))
    return {r["id"]: rec["content"] for r, rec in zip(rows, records)}


def load_kaggle(path):
    return {r["id"]: r["output"] for r in read_jsonl(path)}


def run(dataset="sft_pilot300_gemini3flash", kaggle_dir="kaggle_run1", extra=None, split="test"):
    folder = DATA / "train" / kaggle_dir
    preds = {"qwen3_14b_base": load_kaggle(folder / "predictions_qwen3_14b_base.jsonl"),
             "qwen3_14b_student": load_kaggle(folder / "predictions_qwen3_14b_student.jsonl")}
    preds.update(extra or {})
    report, errors = evaluate(dataset, preds, split)
    write_json(REPORTS / f"student_eval_{kaggle_dir}.json", {"created_at": now(), "dataset": dataset, "split": split, "reference": "teacher ag/gemini-3-flash", "models": report})
    write_json(folder / "error_log.json", errors)
    return report
