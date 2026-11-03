from player_graph.llm_extract import canonical_club, chunks, locate_evidence, parse_json, validate

TEXT = ("Chelsea đã sa thải HLV Frank Lampard sau 18 tháng dẫn dắt.\n"
        "Theo tin đồn, Quỷ đỏ muốn chiêu mộ Declan Rice.\n"
        "Arsenal signed Declan Rice from West Ham United on a five-year deal.")
ARTICLE = {"article_id": "a1", "language": "vi", "canonical_url": "https://x", "published_at": "2026-10-29", "clean_text": TEXT}


def payload(*relations, entities=None):
    entities = entities or [
        {"name": "Chelsea", "type": "Club"}, {"name": "Frank Lampard", "type": "Person", "role": "Coach"},
        {"name": "Manchester United", "type": "Club"}, {"name": "Declan Rice", "type": "Person", "role": "Player"},
        {"name": "Arsenal", "type": "Club"}]
    return {"entities": entities, "relations": list(relations)}


def rel(subject, relation, obj, evidence, status="reported"):
    return {"subject": subject, "relation": relation, "object": obj, "status": status, "evidence": evidence}


def test_parse_json_strips_markdown_fence():
    assert parse_json('```json\n{"ok": true}\n```') == {"ok": True}
    assert parse_json('Here: {"a": 1}') == {"a": 1}


def test_evidence_must_be_in_article():
    assert locate_evidence("Chelsea đã sa thải HLV Frank Lampard", TEXT)[2] == "exact"
    assert locate_evidence("Chelsea  đã sa thải HLV Frank Lampard", TEXT)[2] == "normalized_whitespace_quotes"
    assert locate_evidence("Chelsea fired coach Frank Lampard after 18 months", TEXT) is None


def test_valid_relation_accepted_and_club_canonicalized():
    ok, bad = validate(ARTICLE, TEXT, payload(rel("Chelsea", "DISMISSED_MANAGER", "Frank Lampard", "Chelsea đã sa thải HLV Frank Lampard sau 18 tháng dẫn dắt.")))
    assert not bad and ok[0]["subject"]["name"] == "Chelsea" and ok[0]["eligible_for_graph"]
    assert canonical_club("Man Utd") == ("Manchester United", "core")


def test_rumor_kept_but_not_eligible():
    ok, _ = validate(ARTICLE, TEXT, payload(rel("Manchester United", "NEGOTIATES_TO_SIGN", "Declan Rice", "Theo tin đồn, Quỷ đỏ muốn chiêu mộ Declan Rice.", "rumor")))
    assert ok and not ok[0]["eligible_for_graph"]


def test_rejections():
    cases = [
        (rel("Chelsea", "BOUGHT", "Frank Lampard", "Chelsea đã sa thải HLV Frank Lampard"), "unknown_relation_type"),
        (rel("Chelsea", "DISMISSED_MANAGER", "Graham Potter", "Chelsea đã sa thải HLV Frank Lampard"), "endpoint_not_in_entities"),
        (rel("Frank Lampard", "DISMISSED_MANAGER", "Chelsea", "Chelsea đã sa thải HLV Frank Lampard"), "endpoint_type_mismatch"),
        (rel("Chelsea", "DISMISSED_MANAGER", "Frank Lampard", "Chelsea fired Frank Lampard yesterday"), "evidence_not_verbatim_in_article"),
    ]
    for relation, reason in cases:
        ok, bad = validate(ARTICLE, TEXT, payload(relation))
        assert not ok and bad[0]["reason"] == reason


def test_person_from_model_memory_rejected():
    ents = [{"name": "Arsenal", "type": "Club"}, {"name": "Bukayo Saka", "type": "Person", "role": "Player"}]
    ok, bad = validate(ARTICLE, TEXT, payload(rel("Bukayo Saka", "PLAYS_FOR", "Arsenal", "Arsenal signed Declan Rice from West Ham United on a five-year deal."), entities=ents))
    assert not ok and bad[0]["reason"] == "person_name_not_in_article"


def test_chunks_split_on_paragraphs():
    parts = chunks("a" * 10 + "\n" + "b" * 10, limit=15)
    assert parts == ["a" * 10, "b" * 10]
