"""Offline chatbot contract tests; no benchmark/config, credentials, or services.

The validator is an application helper for a small Cypher subset, not a
production sandbox. These tests do not establish query cost bounds or validate
all Cypher syntax. Known defects remain ordinary failing regression tests.
"""

import json
from copy import deepcopy
from unittest.mock import MagicMock, Mock

import pytest

from player_graph.chatbot import analytics, answer, backend, query


LIST_RETURN = " RETURN DISTINCT p.canonical_name AS name ORDER BY name LIMIT 10"
VALID = "MATCH (p:Person)-[:PLAYS_FOR]->(c:Club {canonical_name:$club})" + LIST_RETURN
PARAMS = {"club": "Arsenal"}


def raw(cypher):
    return json.dumps({"cypher": cypher, "plan": {"relations": ["PLAYS_FOR"]}})


def response(content, **overrides):
    return {"content": content, "seconds": 0.01, "usage": {},
            "finish_reason": "stop", **overrides}


def client_for(content, **overrides):
    return Mock(complete=Mock(return_value=response(content, **overrides)))


@pytest.fixture
def packets():
    return [
        {"row_id": "R1", "data": {"name": "Alice"}, "sources": [
            {"source_id": "E1_1", "url": "https://example.test/alice"},
            {"source_id": "E1_2", "url": "https://example.test/alice-2"}]},
        {"row_id": "R2", "data": {"name": "Bob"}, "sources": [
            {"source_id": "E2_1", "url": "https://example.test/bob"}]},
    ]


def selection():
    return {"items": [{"row_id": "R1", "source_ids": ["E1_1"]},
                      {"row_id": "R2", "source_ids": ["E2_1"]}]}


def test_validate_accepts_canonical_maps_and_json_fences():
    cypher, plan = query.validate("```json\n" + raw(VALID + ";") + "\n```", PARAMS)
    assert cypher == VALID
    assert plan == {"relations": ["PLAYS_FOR"]}


@pytest.mark.parametrize("suffix", [
    " DELETE p RETURN DISTINCT p.canonical_name AS name ORDER BY name LIMIT 10",
    " SET p.canonical_name='changed'" + LIST_RETURN,
    " MERGE (x:Person)" + LIST_RETURN,
    " CALL db.labels() YIELD label" + LIST_RETURN,
    " UNION MATCH (p:Person)" + LIST_RETURN,
    "; MATCH (p:Person)" + LIST_RETURN,
    " // comment\n" + LIST_RETURN,
    " /* comment */" + LIST_RETURN,
    " CALL { MATCH (n) RETURN n }" + LIST_RETURN,
])
def test_validate_rejects_writes_procedures_and_multiple_statements(suffix):
    with pytest.raises(ValueError):
        query.validate(raw("MATCH (p:Person)" + suffix), {})


@pytest.mark.parametrize("cypher", [
    VALID.replace("LIMIT 10", "LIMIT 11"),
    VALID.replace("LIMIT 10", "LIMIT $limit"),
    VALID.replace(" LIMIT 10", ""),
    VALID.replace("RETURN DISTINCT", "RETURN"),
    VALID.replace(" ORDER BY name", ""),
    VALID.replace(":PLAYS_FOR]", ":PLAYS_FOR*]"),
    VALID.replace(":PLAYS_FOR]", ":PLAYS_FOR*1..2]"),
    VALID.replace(":Person", ":Secret"),
    VALID.replace(":PLAYS_FOR", ":INVENTED"),
    VALID.replace("canonical_name:$club", "entity_id:$club"),
])
def test_validate_rejects_outside_documented_subset(cypher):
    with pytest.raises(ValueError):
        query.validate(raw(cypher), {**PARAMS, "limit": 10})


def test_validate_reports_all_unknown_parameters_without_values():
    cypher = "MATCH (p:Person) WHERE p.canonical_name=$z OR p.entity_id=$a" + LIST_RETURN
    with pytest.raises(ValueError, match="Unknown parameters: a,z"):
        query.validate(raw(cypher), PARAMS)


@pytest.mark.parametrize("edge", ["-[:TEAMMATE_OF]-", "-[t:TEAMMATE_OF]-"])
def test_teammate_undirected_is_valid(edge):
    cypher = f"MATCH (p:Person){edge}(q:Person)" + LIST_RETURN
    assert query.validate(raw(cypher), {})[0] == cypher


@pytest.mark.parametrize("edge", ["-[:TEAMMATE_OF]->", "<-[:TEAMMATE_OF]-"])
def test_teammate_directed_is_rejected(edge):
    with pytest.raises(ValueError, match="TEAMMATE_OF"):
        query.validate(raw(f"MATCH (p:Person){edge}(q:Person)" + LIST_RETURN), {})


@pytest.mark.parametrize("edge", [
    "-[:TEAMMATE_OF|PLAYED_UNDER]->",
    "-[:PLAYED_UNDER|TEAMMATE_OF]->",
    "-[:PLAYS_FOR|INVENTED]->",
])
def test_relation_alternatives_cannot_bypass_direction_or_allowlist(edge):
    # Unsupported alternatives may simply be rejected by this small helper.
    with pytest.raises(ValueError):
        query.validate(raw(f"MATCH (p:Person){edge}(q:Person)" + LIST_RETURN), {})


@pytest.mark.parametrize("relation,domain,target", [
    ("PLAYS_FOR", "Person", "Club"),
    ("MANAGES", "Coach", "Club"),
    ("APPOINTED_MANAGER_OF", "Club", "Coach"),
    ("DISMISSED_MANAGER", "Club", "Person"),
    ("REPRESENTS", "Agent", "Player"),
    ("PLAYED_UNDER", "Player", "Coach"),
    ("TRANSFER_FLOW", "Club", "Club"),
])
@pytest.mark.parametrize("reverse", [False, True])
def test_domain_checks_accept_valid_forward_and_reverse_traversals(relation, domain, target, reverse):
    pattern = (f"(p:{target})<-[:{relation}]-(q:{domain})" if reverse
               else f"(p:{domain})-[:{relation}]->(q:{target})")
    cypher = "MATCH " + pattern + LIST_RETURN
    assert query.validate(raw(cypher), {})[0] == cypher


@pytest.mark.parametrize("pattern", [
    "(c:Club)-[:PLAYS_FOR]->(p:Person)",
    "(p:Person)-[:APPOINTED_MANAGER_OF]->(c:Club)",
    "(p:Person)-[:TRANSFER_FLOW]->(c:Club)",
    "(c:Club)-[:PLAYED_UNDER]->(p:Person)",
    "(p:Person)<-[:PLAYS_FOR]-(c:Club)",
    "(p:Person)-[:PLAYS_FOR]->(c:Club)-[:PLAYED_UNDER]->(q:Person)",
])
def test_domain_checks_reject_wrong_types_including_later_chain_edges(pattern):
    with pytest.raises(ValueError, match="domain/range"):
        query.validate(raw("MATCH " + pattern + LIST_RETURN), {})


@pytest.mark.parametrize("pattern", [
    "( c:Club)-[:PLAYS_FOR]->(p:Person)",
    "(c:Club)-[:PLAYS_FOR]->(p:Person:Player)",
])
def test_domain_checks_cannot_be_skipped_by_spacing_or_multiple_labels(pattern):
    with pytest.raises(ValueError):
        query.validate(raw("MATCH " + pattern + LIST_RETURN), {})


def test_count_query_counts_before_limit():
    cypher = VALID.split(" RETURN")[0] + " RETURN count(DISTINCT p) AS count LIMIT 10"
    assert query.validate(raw(cypher), PARAMS)[0] == cypher


def test_graph_execution_is_read_only_with_timeout_and_explain():
    graph = query.Graph.__new__(query.Graph)
    graph.driver = MagicMock()
    session = graph.driver.session.return_value.__enter__.return_value
    tx = session.begin_transaction.return_value.__enter__.return_value
    tx.run.return_value.data.return_value = [{"name": "Alice"}]
    assert graph.run(VALID, PARAMS, explain=True) == [{"name": "Alice"}]
    graph.driver.session.assert_called_once_with(default_access_mode=query.READ_ACCESS)
    session.begin_transaction.assert_called_once_with(timeout=12)
    # Works with either a Neo4j Query wrapper or a plain string.
    statement = tx.run.call_args.args[0]
    assert str(statement) == "EXPLAIN " + VALID
    assert tx.run.call_args.args[1] == PARAMS


def test_generate_repairs_invalid_query_before_any_execution():
    client = Mock()
    client.complete.side_effect = [response(raw(VALID.replace("$club", "$invented"))), response(raw(VALID))]
    graph = Mock()
    graph.run.side_effect = [[], [{"name": "Alice"}]]
    result = query.generate(client, graph, "Who played for Arsenal?", PARAMS, mode="schema")
    assert result["ok"] and result["rows"] == [{"name": "Alice"}]
    assert len(result["attempts"]) == 2
    assert "Unknown parameters" in result["attempts"][0]["error"]
    assert graph.run.call_count == 2
    assert graph.run.call_args_list[0].kwargs == {"explain": True}
    assert result["example_ids"] == []


def test_generate_stops_at_repair_budget_without_executing_invalid_queries():
    graph = Mock()
    client = client_for("not JSON")
    result = query.generate(client, graph, "question", {}, mode="schema", max_repairs=1)
    assert not result["ok"] and len(result["attempts"]) == 2
    graph.run.assert_not_called()


def test_generate_does_not_execute_truncated_json_even_when_parseable():
    graph = Mock()
    result = query.generate(client_for(raw(VALID), finish_reason="length"), graph,
                            "question", PARAMS, mode="schema", max_repairs=0)
    assert not result["ok"]
    graph.run.assert_not_called()


def test_generate_error_reports_do_not_expose_credentials():
    # Synthetic credential only. Error strings currently enter reports verbatim.
    secret = "UNIT_TEST_PASSWORD_DO_NOT_REPORT"
    graph = Mock()
    graph.run.side_effect = RuntimeError("Authentication failed: password=" + secret)
    result = query.generate(client_for(raw(VALID)), graph, "question", PARAMS,
                            mode="schema", max_repairs=0)
    assert secret not in json.dumps(result)


@pytest.mark.parametrize("items", [
    [{"row_id": "R1", "source_ids": ["FORGED"]}, {"row_id": "R2", "source_ids": ["E2_1"]}],
    [{"row_id": "R1", "source_ids": ["E2_1"]}, {"row_id": "R2", "source_ids": ["E1_1"]}],
    [{"row_id": "R1", "source_ids": ["E1_1"]}],
    [{"row_id": "R1", "source_ids": ["E1_1"]}] * 2,
    [{"row_id": "R1", "source_ids": []}, {"row_id": "R2", "source_ids": ["E2_1"]}],
    [{"row_id": "R1", "source_ids": "E1_1"}, {"row_id": "R2", "source_ids": ["E2_1"]}],
])
def test_evidence_guards_reject_forgery_cross_row_missing_duplicate_and_uncited_rows(packets, items):
    with pytest.raises(ValueError):
        answer.check_selection({"items": items}, packets)


@pytest.mark.parametrize("content", ["not JSON", "null", "[]", '{"items":[]}',
    '{"items":[{"row_id":"R1","source_ids":["E2_1"]},{"row_id":"R2","source_ids":["E1_1"]}]}'])
def test_synthesize_structured_fallback_preserves_every_row_and_own_citations(packets, content):
    original = deepcopy(packets)
    result = answer.synthesize(client_for(content), packets, "question")
    assert result["fallback_used"] and not result["model_selection_valid"]
    assert result["selection"] == selection()
    assert result["selection_error"]
    assert "Alice" in result["answer"] and "Bob" in result["answer"]
    assert "[E1_1](https://example.test/alice)" in result["answer"]
    assert "[E2_1](https://example.test/bob)" in result["answer"]
    assert packets == original


def test_synthesize_renders_only_canonical_data_and_real_urls(packets):
    payload = selection()
    payload.update(answer="Invented person", url="https://forged.test")
    result = answer.synthesize(client_for(json.dumps(payload)), packets, "question")
    assert result["model_selection_valid"] and not result["fallback_used"]
    assert "Invented person" not in result["answer"]
    assert "forged.test" not in result["answer"]


def test_selection_cannot_reorder_canonical_results(packets):
    payload = selection()
    payload["items"].reverse()
    result = answer.synthesize(client_for(json.dumps(payload)), packets, "question")
    assert [item["row_id"] for item in result["selection"]["items"]] == ["R1", "R2"]


def test_missing_source_ids_on_source_free_row_has_structured_fallback():
    packets = [{"row_id": "R1", "data": {"count": 0}, "sources": []}]
    result = answer.synthesize(client_for('{"items":[{"row_id":"R1"}]}'), packets, "question")
    assert result["fallback_used"]
    assert result["selection"] == {"items": [{"row_id": "R1", "source_ids": []}]}


def test_empty_results_and_source_free_counts_are_not_invented():
    empty = answer.synthesize(client_for('{"items":[]}'), [], "question")
    assert not empty["fallback_used"]
    assert "không chứng minh" in empty["answer"]
    packets = [{"row_id": "R1", "data": {"count": 0}, "sources": []}]
    result = answer.synthesize(client_for('{"items":[{"row_id":"R1","source_ids":[]}]}'), packets, "question")
    assert ": 0" in result["answer"] and "chưa gắn bằng chứng riêng" in result["answer"]


@pytest.mark.parametrize("side,expected", [
    ("object", {"sname": "Alice", "oname": "Arsenal"}),
    ("subject", {"sname": "Arsenal", "oname": "Alice"}),
])
def test_direct_evidence_binds_anchor_and_result_and_assigns_row_local_ids(side, expected):
    graph = Mock()
    graph.run.return_value = [{"assertion_id": "A1", "url": "https://example.test/article", "quote": "Supported claim", "status": "reported"}]
    packets = answer.evidence_packets(graph, [{"name": "Alice"}],
        {"kind": "direct", "relation": "PLAYS_FOR", "anchor_param": "club", "anchor_side": side}, PARAMS)
    assert graph.run.call_args.args[1] == expected
    assert "LIMIT 2" in graph.run.call_args.args[0]
    assert packets[0]["sources"][0]["source_id"] == "E1_1"


def test_unsupported_provenance_relation_is_rejected_before_querying():
    graph = Mock()
    with pytest.raises(ValueError, match="Unsupported provenance relation"):
        answer.evidence_packets(graph, [{"name": "Alice"}],
            {"kind": "direct", "relation": "PLAYS_FOR]->() DELETE r //", "anchor_param": "club"}, PARAMS)
    graph.run.assert_not_called()


@pytest.mark.parametrize("has_witness", [False, True])
def test_derived_evidence_uses_undirected_teammate_and_never_fabricates_witness(has_witness):
    graph = Mock()
    graph.run.return_value = ([{"teammate": "Bob", "teammate_clubs": ["X"], "coach_clubs": ["Y"]}]
                              if has_witness else [])
    packets = answer.evidence_packets(graph, [{"name": "Coach"}], {"kind": "derived"}, {"person": "Alice"})
    cypher, params = graph.run.call_args.args
    assert "-[t:TEAMMATE_OF]-" in cypher and "[t:TEAMMATE_OF]->" not in cypher
    assert params == {"person": "Alice", "answer": "Coach"}
    assert bool(packets[0]["sources"]) == has_witness
    if has_witness:
        assert "Không suy ra" in packets[0]["sources"][0]["rule"]


def test_backend_uses_auth_header_and_never_reports_credentials_or_reasoning(monkeypatch, capsys):
    fake_key, fake_url = "UNIT_TEST_API_KEY", "https://unit-test.invalid"
    fetch = Mock(return_value={"choices": [{"message": {"content": "{}", "reasoning_content": "PRIVATE_THOUGHT"}, "finish_reason": "stop"}], "usage": {"total_tokens": 3}})
    monkeypatch.setattr(backend, "fetch", fetch)
    client = backend.KaggleClient(fake_url + "/", fake_key)
    result = client.complete([{"role": "user", "content": "question"}], json_output=True)
    url, key, body = fetch.call_args.args
    assert url == fake_url + "/v1/chat/completions" and key == fake_key
    assert body["response_format"] == {"type": "json_object"}
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["temperature"] == 0
    output = json.dumps(result) + str(capsys.readouterr())
    assert all(value not in output for value in (fake_key, fake_url, "PRIVATE_THOUGHT"))


def test_fetch_sends_key_in_header_not_url_or_json(monkeypatch):
    opened = MagicMock()
    opened.__enter__.return_value.read.return_value = b'{"ok":true}'
    urlopen = Mock(return_value=opened)
    monkeypatch.setattr(backend.urllib.request, "urlopen", urlopen)
    assert backend.fetch("https://unit-test.invalid/v1", "UNIT_TEST_KEY", {"x": 1}, timeout=7) == {"ok": True}
    request = urlopen.call_args.args[0]
    assert request.get_header("Authorization") == "Bearer UNIT_TEST_KEY"
    assert "UNIT_TEST_KEY" not in request.full_url
    assert json.loads(request.data) == {"x": 1}
    assert urlopen.call_args.kwargs == {"timeout": 7}


@pytest.mark.parametrize("plan", [
    {"tool": "eval", "args": {}},
    {"tool": "unsupported", "args": {}},
    {"tool": "count_players", "args": {}},
    {"tool": "count_players", "args": {"club": "Arsenal", "extra": 1}},
    {"tool": "count_players", "args": {"club": "Invented Club"}},
    {"tool": "top_clubs", "args": {"club": "Arsenal"}},
])
def test_analytics_rejects_unknown_tools_extra_args_and_unsupplied_entities(plan):
    with pytest.raises(ValueError):
        analytics.choose_tool(client_for(json.dumps(plan)), "question", PARAMS)


def test_analytics_selects_and_executes_only_supplied_canonical_club():
    name, args, _ = analytics.choose_tool(client_for(json.dumps({"tool": "count_players", "args": PARAMS})), "question", PARAMS)
    graph = Mock()
    graph.run.return_value = [{"count": 42}]
    assert analytics.tools(graph, name, args) == [{"count": 42}]
    assert graph.run.call_args.args[1] == PARAMS
    assert "count(DISTINCT p)" in graph.run.call_args.args[0]


def test_pagerank_uses_undirected_edges_and_deterministic_ties():
    graph = Mock()
    graph.run.return_value = [
        {"p": "a", "q": "b", "name": "Alice"},
        {"p": "b", "q": "a", "name": "Bob"},
        {"p": "b", "q": "c", "name": "Bob"},
        {"p": "c", "q": "b", "name": "Carol"},
    ]
    rows = analytics.tools(graph, "pagerank_teammates", {})
    assert [row["name"] for row in rows] == ["Bob", "Alice", "Carol"]
    assert sum(row["score"] for row in rows) == pytest.approx(1)
    assert "-[:TEAMMATE_OF]-" in graph.run.call_args.args[0]


def test_unknown_analytics_tool_never_executes_graph():
    graph = Mock()
    with pytest.raises(ValueError, match="Unknown analytics tool"):
        analytics.tools(graph, "__import__", {})
    graph.run.assert_not_called()
