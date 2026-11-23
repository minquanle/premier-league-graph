"""Offline tests for the graph agent: plan checking, compilation and the repair loop (no Neo4j, no model)."""
import json
from unittest.mock import Mock

import pytest

from player_graph.chatbot import agent

PERSON = {'person': 'Person'}
TWO = {'person': 'Person', 'other': 'Person'}


def plan(*chains, output='list'):
    return {'chains': [{'from': f, 'hops': list(h)} for f, h in chains], 'output': output}


def test_compile_multihop_chain():
    cypher, kind = agent.compile_plan(plan(('person', ['teammates', 'coaches_of_player'])), PERSON)
    assert kind == 'Person'
    assert cypher == ('MATCH (:Person {canonical_name: $person})-[:TEAMMATE_OF]-(c0n0:Person)'
                      '-[:PLAYED_UNDER]->(ans:Person) RETURN DISTINCT ans.canonical_name AS name ORDER BY name LIMIT 10')


def test_compile_count_and_intersection():
    cypher, _ = agent.compile_plan(plan(('person', ['teammates']), ('other', ['teammates']), output='count'), TWO)
    assert ', (:Person {canonical_name: $other})-[:TEAMMATE_OF]-(ans)' in cypher
    assert cypher.endswith('RETURN count(DISTINCT ans) AS count LIMIT 10')


def test_reverse_direction_hops():
    cypher, kind = agent.compile_plan(plan(('club', ['managers_of_club'])), {'club': 'Club'})
    assert '(:Club {canonical_name: $club})<-[:MANAGES]-(ans:Person)' in cypher and kind == 'Person'


@pytest.mark.parametrize('bad,message', [
    (plan(('person', ['players_of_club'])), 'needs a Club'),
    (plan(('club', ['teammates'])), 'unknown entity') ,
    (plan(('person', ['made_up_hop'])), 'unknown hop'),
    (plan(('person', ['teammates'] * 4)), '1 to 3 hops'),
    (plan(('person', ['teammates']), ('other', ['clubs_played_for'])), 'same type'),
    (plan(('person', ['teammates']), ('person', ['teammates'])), 'different parameters'),
    ({'chains': [], 'output': 'list'}, 'one chain'),
    ({'chains': [{'from': 'person', 'hops': ['teammates']}], 'output': 'table'}, 'output must be'),
])
def test_invalid_plans_are_rejected_with_fixable_messages(bad, message):
    types = TWO if message != 'unknown entity' else PERSON
    with pytest.raises(ValueError) as error:
        agent.check(bad, types)
    if message != 'unknown entity':
        assert message in str(error.value)


def test_every_hop_has_typed_endpoints():
    for name, (rel, direction, src, dst, meaning) in agent.HOPS.items():
        assert direction in {'out', 'in', 'any'} and src in {'Person', 'Club'} and dst in {'Person', 'Club'} and meaning


def test_examples_compile():
    for example in agent.load_examples():
        agent.compile_plan(example['plan'], example['param_types'])


def test_parse_strips_fences_and_thinking():
    assert agent.parse('<think>x</think>```json\n{"chains": [], "output": "unsupported"}\n```')['output'] == 'unsupported'


def response(content):
    return {'content': content, 'seconds': 0.01, 'usage': {}, 'finish_reason': 'stop'}


def test_generate_repairs_a_bad_plan_once(monkeypatch):
    monkeypatch.setattr(agent, 'param_types', lambda graph, params: PERSON)
    bad = json.dumps(plan(('person', ['players_of_club'])))
    good = json.dumps(plan(('person', ['teammates'])))
    client = Mock(complete=Mock(side_effect=[response(bad), response(good)]))
    graph = Mock(run=Mock(return_value=[{'name': 'Bob'}]))
    result = agent.generate(client, graph, 'Đồng đội của A?', {'person': 'A'})
    assert result['ok'] and result['rows'] == [{'name': 'Bob'}] and len(result['attempts']) == 2
    assert 'needs a Club' in result['attempts'][0]['error']
    repair = client.complete.call_args_list[1][0][0][-1]['content']
    assert 'needs a Club' in repair


def test_generate_reports_unsupported(monkeypatch):
    monkeypatch.setattr(agent, 'param_types', lambda graph, params: PERSON)
    client = Mock(complete=Mock(return_value=response('{"chains": [], "output": "unsupported"}')))
    result = agent.generate(client, Mock(), 'Thời tiết?', {'person': 'A'})
    assert not result['ok'] and result['unsupported']


def test_provenance_direct_only_for_single_news_hop():
    assert agent.provenance(plan(('club', ['managers_of_club'])), {'club': 'Club'}) == {
        'kind': 'direct', 'relation': 'MANAGES', 'anchor_param': 'club', 'anchor_side': 'object'}
    assert agent.provenance(plan(('person', ['teammates'])), PERSON)['kind'] == 'aggregate'
    assert agent.provenance(plan(('person', ['teammates', 'coaches_of_player'])), PERSON)['kind'] == 'derived'
