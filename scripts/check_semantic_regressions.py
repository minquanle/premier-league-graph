"""Offline diagnostic probes for the October 30 fixes; does not change live data."""
import hashlib
import json
from pathlib import Path

from player_graph.resolve import semantic_check, plausible_in_year

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports/semantic_regression_review_2026_10_30"


def read(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def run():
    OUT.mkdir(exist_ok=True)
    probes = []
    def probe(label, rel, obj, text, expected, assertion_id=None):
        row = {"relation_type": rel, "object": {"name": obj, "type": "Person" if rel == "NEGOTIATES_TO_SIGN" else "Club"}, "evidence_text": text}
        actual = semantic_check(row)
        probes.append({"name": label, "kind": "real_corpus" if assertion_id else "synthetic_regression",
                       "assertion_id": assertion_id, "input": row, "expected": expected, "actual": actual, "pass": actual == expected})

    original = read(ROOT / "reports/quality_audit_2026_10_30/relation_diagnostics.jsonl")
    for row in original:
        if row["diagnostic_id"] not in {9, 12, 17, 19, 23}:
            continue
        expected = {9: "no_actual_negotiation_in_evidence", 12: "no_actual_negotiation_in_evidence",
                    17: "resignation_not_stated_in_evidence", 19: "resignation_not_stated_in_evidence",
                    23: "destination_club_is_source_in_evidence"}[row["diagnostic_id"]]
        probe(f"original_diagnostic_{row['diagnostic_id']}", row["relation_type"], row["object"]["name"], row["evidence_text"], expected, row["assertion_id"])

    probe("correct_transfer_en", "TRANSFERRED_TO", "Arsenal", "Rice moved from Chelsea to Arsenal.", None)
    probe("correct_transfer_vi", "TRANSFERRED_TO", "Arsenal", "Rice chuyển từ Chelsea sang Arsenal.", None)
    probe("resignation_with_another_person_dismissed", "RESIGNED_FROM", "Wigan Athletic", "Paul Cook resigned from Wigan after Watford sacked Nigel Pearson.", None)
    probe("negotiation_negated", "NEGOTIATES_TO_SIGN", "Pogba", "Manchester United have not held talks with Pogba.", "no_actual_negotiation_in_evidence")
    probe("negotiation_future_only", "NEGOTIATES_TO_SIGN", "Pogba", "Manchester United will begin talks with Pogba next week.", "no_actual_negotiation_in_evidence")

    assertions = read(ROOT / "data/llm/main_v1/assertions.jsonl")
    actual_examples = {"llm_assertion_60ceb813d6e18b67030c", "llm_assertion_6347719876efb5f47421",
                       "llm_assertion_de0c3e19febe1b3cb088", "llm_assertion_7f68350a710721bc937a"}
    for row in assertions:
        if row["assertion_id"] in actual_examples:
            probe("valid_corpus_relation_rejected", row["relation_type"], row["object"]["name"], row["evidence_text"], None, row["assertion_id"])

    affected = [{"assertion_id": a["assertion_id"], "reason": semantic_check(a), "subject": a["subject"]["name"],
                 "relation": a["relation_type"], "object": a["object"]["name"], "evidence": a["evidence_text"]}
                for a in assertions if a["eligible_for_graph"] and semantic_check(a)]
    # Counts here are screening decisions, not a count of correctly removed errors.
    (OUT / "all_semantic_rejections.json").write_text(json.dumps(affected, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {"reviewer_type": "internal", "cases": probes,
               "historical_year_probe": {"context": "A 2026 article recalls a player born in 1940 playing in 1965.",
                                         "birth_year": 1940, "event_year": 1965, "publication_year": 2026,
                                         "accepted_using_publication_year": plausible_in_year({"born": 1940}, 2026, "Player"),
                                         "accepted_using_event_year": plausible_in_year({"born": 1940}, 1965, "Player")},
               "source_sha256": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                                 for p in [ROOT / "src/player_graph/resolve.py", ROOT / "src/player_graph/teacher_data.py"]}}
    (OUT / "reproduction.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"probes": len(probes), "passed": sum(p["pass"] for p in probes),
                      "regressions_or_remaining_failures": sum(not p["pass"] for p in probes),
                      "main_v1_semantic_rejections": len(affected)}, indent=2))


if __name__ == "__main__":
    run()
