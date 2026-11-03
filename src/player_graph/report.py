from __future__ import annotations

import csv
import json
from collections import Counter

import networkx as nx

from .common import DATA, REPORTS, ROOT, normalize, now, read_jsonl, write_json


def make_report():
    articles = read_jsonl(DATA / "processed/articles.jsonl")
    entities = read_jsonl(DATA / "processed/entities.jsonl")
    assertions = read_jsonl(DATA / "processed/assertions.jsonl")
    edges = read_jsonl(DATA / "processed/relations.jsonl")
    by_article = {a["article_id"]: a for a in articles}
    by_entity = {e["entity_id"]: e for e in entities}
    errors = []
    for a in assertions:
        article = by_article.get(a["article_id"])
        if not article or article["clean_text"][a["evidence_start"]:a["evidence_end"]] != a["evidence_text"]:
            errors.append({"assertion_id": a["assertion_id"], "error": "evidence_offset_mismatch"})
        if a["subject_id"] not in by_entity or a["object_id"] not in by_entity:
            errors.append({"assertion_id": a["assertion_id"], "error": "missing_endpoint"})
    if len({a["canonical_url"] for a in articles}) != len(articles):
        errors.append({"error": "duplicate_article_url"})
    if len({a["content_hash"] for a in articles}) != len(articles):
        errors.append({"error": "duplicate_article_content"})
    if len({e["relation_id"] for e in edges}) != len(edges):
        errors.append({"error": "duplicate_relation_id"})
    if len({e["entity_id"] for e in entities}) != len(entities):
        errors.append({"error": "duplicate_entity_id"})
    graph = nx.Graph()
    for e in edges:
        graph.add_edge(e["subject_id"], e["object_id"])
    components = sorted((len(c) for c in nx.connected_components(graph)), reverse=True)
    summary = {
        "created_at": now(), "api_calls": 0,
        "articles": len(articles), "articles_by_language": dict(Counter(a["language"] for a in articles)),
        "articles_by_source": dict(Counter(a["source"] for a in articles)),
        "articles_by_year": dict(sorted(Counter(a["published_at"][:4] for a in articles).items())),
        "oldest_publication": min((a["published_at"] for a in articles), default=None),
        "newest_publication": max((a["published_at"] for a in articles), default=None),
        "entity_candidates": len(entities), "entity_candidates_by_type": dict(Counter(e["entity_type"] for e in entities)),
        "entities_in_candidate_relations": graph.number_of_nodes(),
        "candidate_relations": len(edges), "candidate_relation_types": dict(Counter(e["relation_type"] for e in edges)),
        "assertions": len(assertions), "assertion_statuses": dict(Counter(a["assertion_status"] for a in assertions)),
        "human_verified_relations": 0, "precision": None, "recall": None,
        "structural_validation_errors": errors, "structural_validation_passed": not errors,
        "network_projection": {"definition": "Undirected simple graph of pending pair/type candidate relations, no isolated NER mentions", "connected_components": len(components), "largest_component_nodes": components[0] if components else 0,
                               "edges_after_projection": graph.number_of_edges(), "density": nx.density(graph) if graph else 0},
    }
    # Only explicit structural checks; no claim that evidence existence means semantic correctness.
    write_json(REPORTS / "pilot_summary.json", summary)
    # An earlier development audit is retained as a diagnostic, not a score for the final extractor.
    audit_path = REPORTS / "internal_audit_v0_2.json"
    audit_note = ""
    if audit_path.exists():
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        audit_note = """\n## Rà soát thăm dò và sửa lỗi\n\nNhóm đã đọc 40 cạnh lấy ngẫu nhiên từ bản trích xuất **v0.2**: 26 cạnh được câu hỗ trợ, 11 cạnh sai, 3 cạnh cần kiểm tra danh tính. Đây là rà soát phát triển nội bộ, không phải nhãn con người độc lập hoặc precision của bản cuối. Chi tiết và câu bằng chứng nằm trong `reports/internal_audit_v0_2.json`.\n\nBản **v0.3** sửa các nhóm lỗi đã phát hiện: nhầm CLB cũ khi chưa nhận diện được CLB đích, từ chức đội tuyển bị gán sang CLB cũ, cho mượn theo mùa bị coi là chuyển nhượng và tiêu đề bổ nhiệm giả định bị coi là sự kiện đã xảy ra. Vẫn cần giải quyết tên người bị NER cắt/hợp nhất sai, phân biệt cầu thủ với HLV và đánh giá trên tập mới chưa dùng để sửa luật.\n"""
    export = DATA / "exports"
    export.mkdir(parents=True, exist_ok=True)
    for filename, rows, fields in [
        ("articles.csv", articles, ["article_id", "canonical_url", "source", "language", "title", "published_at", "content_hash"]),
        ("entities.csv", entities, ["entity_id", "canonical_name", "entity_type", "scope_role", "identity_review_status"]),
        ("relations.csv", edges, ["relation_id", "subject_id", "relation_type", "object_id", "evidence_count", "review_status"]),
    ]:
        with (export / filename).open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
    source_text = "; ".join(f"{k}: {v}" for k, v in summary["articles_by_source"].items())
    type_lines = "\n".join(f"- `{k}`: {v} cạnh ứng viên." for k, v in summary["candidate_relation_types"].items())
    report = f"""# Kết quả pilot Player Graph

Cập nhật: {summary['created_at']}. Dữ liệu thực từ pipeline cục bộ; không dùng API mô hình.

## Dữ liệu

- {len(articles)} bài; tiếng Việt: {summary['articles_by_language'].get('vi', 0)}, tiếng Anh: {summary['articles_by_language'].get('en', 0)}.
- Nguồn: {source_text}.
- Ngày đăng: {summary['oldest_publication']} đến {summary['newest_publication']}.
- Phân bố năm: {json.dumps(summary['articles_by_year'], ensure_ascii=False)}.
- URL và hash nội dung duy nhất; lọc ngôn ngữ, ngày đăng và từ khóa CLB. Đây là kiểm tra tự động, chưa phải gán nhãn mức độ liên quan từng bài bởi con người.

## Trích xuất thử

- {len(entities)} thực thể ứng viên từ NER và từ điển CLB; {graph.number_of_nodes()} thực thể xuất hiện trong quan hệ ứng viên.
- {len(assertions)} phát biểu trích xuất; trạng thái: {json.dumps(summary['assertion_statuses'], ensure_ascii=False)}.
- {len(edges)} quan hệ ứng viên duy nhất theo cặp + loại; {len(summary['candidate_relation_types'])} loại quan sát được.
- Nút bài báo và bản ghi bằng chứng không tính vào số thực thể bóng đá.
- Vì chưa giải quyết thời gian sự kiện, nhiều phát biểu cùng cặp/loại được gộp bảo thủ. Không coi mỗi lần báo nhắc lại là cạnh mới.

{type_lines}

## Chất lượng và giới hạn

- Kiểm tra cấu trúc: {'đạt' if not errors else 'có lỗi'}, số lỗi: {len(errors)}. Bao gồm tính khớp của câu bằng chứng với offset và tính tồn tại của đầu cạnh.
- **Chưa có precision/recall trên tập gán nhãn độc lập; chưa chứng minh đạt trên 90%.** Toàn bộ cạnh có `review_status=pending`.
- Hai mô hình spaCy chạy trên máy: `en_core_web_sm` cho Anh và `xx_ent_wiki_sm` cho Việt. Mô hình đa ngôn ngữ chưa được huấn luyện riêng cho báo bóng đá Việt Nam.
- Luật ghép chủ thể/đối tượng, trạng thái phủ định/tin đồn và hợp nhất tên còn có thể sai. Từ điển CLB hỗ trợ chuẩn hóa, không phải bằng chứng về quan hệ.
- `reports/review_sample.csv` là mẫu cố định để rà soát; ô nhãn ban đầu để trống. Đánh giá nội bộ nếu có phải ghi đúng loại reviewer, không được gọi là nhãn con người độc lập.
- Thống kê thăm dò mạng chiếu vô hướng: {len(components)} thành phần, thành phần lớn nhất {components[0] if components else 0} nút. Đây là mạng ứng viên, chưa dùng để kết luận chuyên môn.
- Mẫu được chọn có chủ đích từ chuyên mục/kho lưu trữ và liên kết liên quan; không đại diện ngẫu nhiên toàn bộ báo chí hoặc toàn bộ mùa giải. Kiểm tra phân bố nguồn/năm trước khi ngoại suy quy mô.

## Tái lập

Xem README cho lệnh chạy. Nội dung tải về được cache theo URL tại `data/raw/http`; chạy lại dùng cache. Raw và toàn văn không được theo dõi bởi Git. Metadata bài, thực thể, phát biểu và cạnh nằm trong `data/processed`; CSV ở `data/exports`.

Kết quả kiểm tra import Neo4j nằm tại `reports/neo4j_import.json` nếu đã chạy thành công. Không suy ra import thành công chỉ từ file CSV.
"""
    (REPORTS / "PILOT_REPORT.md").write_text(report + audit_note, encoding="utf-8")
    if errors:
        raise RuntimeError(f"Structural validation failed: {len(errors)} errors")
    return summary
