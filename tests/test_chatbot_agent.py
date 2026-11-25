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


# ---- planner v2: structured decoding, dynamic few-shot, voting ----

def scripted(by_seed, thinking=None, repair=None):
    """Mock client whose reply depends on the sample seed (thread order does not matter)."""
    def complete(msgs, thinking_flag=False, **kw):
        if kw.get('thinking') or thinking_flag:
            return response(thinking)
        if repair is not None and 'Fix this problem' in msgs[-1]['content']:
            return response(repair)
        return response(by_seed[kw.get('seed', 42) - 42])
    client = Mock()
    client.complete = Mock(side_effect=lambda msgs, **kw: complete(msgs, **kw))
    return client


TEAM = json.dumps(plan(('person', ['teammates'])))
COACH = json.dumps(plan(('person', ['coaches_of_player'])))
BAD = json.dumps(plan(('person', ['players_of_club'])))


@pytest.fixture
def graph(monkeypatch):
    monkeypatch.setattr(agent, 'param_types', lambda graph, params: PERSON)
    agent._ROLES.clear()

    def run(query, params=None):
        if 'labels(n)' in query:
            return [{'name': n, 'labels': ['FootballEntity', 'Person', 'Player'], 'player': True, 'coach': False,
                     'agent': False} for n in params['names']]
        return [{'name': 'Bob'}]
    return Mock(run=Mock(side_effect=run))


def test_schema_closes_hop_and_parameter_names():
    schema = agent.plan_schema({'person': 'Person', 'club': 'Club'})
    chain = schema['properties']['chains']['items']['properties']
    assert chain['from']['enum'] == ['club', 'person']
    assert set(chain['hops']['items']['enum']) == set(agent.HOPS)
    assert chain['hops']['maxItems'] == agent.MAX_HOPS


def test_select_examples_prefers_matching_words_and_types():
    picked = agent.select_examples('Đồng đội của Bob là ai?', PERSON, k=5)
    assert len(picked) == 5
    assert any('teammates' in c['hops'] for e in picked for c in e['plan']['chains'])
    assert all(sorted(e['param_types'].values()) == ['Person'] for e in picked[:3])


def test_majority_vote_wins(graph):
    client = scripted([TEAM, COACH, TEAM])
    result = agent.generate_v2(client, graph, 'q', {'person': 'A'})
    assert result['ok'] and result['plan']['chains'][0]['hops'] == ['teammates']
    assert not result['thinking'] and client.complete.call_count == 3


def test_tie_is_broken_by_one_thinking_call(graph):
    client = scripted([TEAM, COACH, BAD], thinking=COACH)
    result = agent.generate_v2(client, graph, 'q', {'person': 'A'})
    assert result['ok'] and result['thinking'] and result['plan']['chains'][0]['hops'] == ['coaches_of_player']


def test_tie_without_thinking_falls_back_to_greedy(graph):
    client = scripted([TEAM, COACH, BAD])
    result = agent.generate_v2(client, graph, 'q', {'person': 'A'}, adaptive_thinking=False)
    assert result['ok'] and result['plan']['chains'][0]['hops'] == ['teammates']


def test_all_invalid_triggers_one_repair(graph):
    client = scripted([BAD, BAD, BAD], thinking=BAD, repair=TEAM)
    result = agent.generate_v2(client, graph, 'q', {'person': 'A'})
    assert result['ok'] and result['attempts'][-1].get('repair')


def test_unsupported_majority(graph):
    unsupported = '{"chains": [], "output": "unsupported"}'
    client = scripted([unsupported, unsupported, TEAM])
    result = agent.generate_v2(client, graph, 'q', {'person': 'A'})
    assert not result['ok'] and result['unsupported']


def test_structured_decoding_is_requested(graph):
    client = scripted([TEAM, TEAM, TEAM])
    agent.generate_v2(client, graph, 'q', {'person': 'A'})
    kwargs = client.complete.call_args_list[0][1]
    assert kwargs['schema']['required'] == ['chains', 'output']


def test_roles_are_shown_for_people_only():
    assert agent.describe({'person': 'A', 'club': 'X'}, {'person': 'Person', 'club': 'Club'},
                          {'A': ['Coach', 'Player'], 'X': []}) == {'person': 'Person (Coach, Player)', 'club': 'Club'}


def test_planner_prompt_carries_roles(graph):
    client = scripted([TEAM, TEAM, TEAM])
    agent.generate_v2(client, graph, 'q', {'person': 'A'})
    last = json.loads(client.complete.call_args_list[0][0][0][-1]['content'])
    assert last['params'] == {'person': 'Person (Player)'}


@pytest.mark.parametrize('hop,start,pattern', [
    # Directions as stored in the graph: coach->club for appointments, club->coach for dismissals,
    # club->person for negotiations.
    ('clubs_appointed_by', 'Person', '(:Person {canonical_name: $x})-[:APPOINTED_MANAGER_OF]->(ans:Club)'),
    ('managers_appointed', 'Club', '(:Club {canonical_name: $x})<-[:APPOINTED_MANAGER_OF]-(ans:Person)'),
    ('managers_dismissed', 'Club', '(:Club {canonical_name: $x})-[:DISMISSED_MANAGER]->(ans:Person)'),
    ('clubs_negotiating_for', 'Person', '(:Person {canonical_name: $x})<-[:NEGOTIATES_TO_SIGN]-(ans:Club)'),
    ('critics_of_club', 'Club', '(:Club {canonical_name: $x})<-[:CRITICIZES]-(ans:Person)'),
])
def test_hop_directions_match_stored_edges(hop, start, pattern):
    cypher, _ = agent.compile_plan(plan(('x', [hop])), {'x': start})
    assert pattern in cypher


# ---- planner v3: fast plan, deterministic checks, escalation only when a check fires ----

from player_graph.chatbot import verify  # noqa: E402


def problems(question, p, params=None, types=None, roles=None, rows=(1,)):
    params = params or {'person': 'A'}
    return verify.check_plan(p, question, params, types or {k: 'Person' for k in params},
                             roles or {'A': ['Player']}, agent.HOPS, [{'name': 'x', 'count': r} for r in rows])


def test_checks_flag_an_unused_entity():
    out = problems('Trong số cầu thủ của X, ai từng được A huấn luyện?', plan(('club', ['players_of_club', 'coaches_of_player'])),
                   params={'club': 'X', 'person': 'A'}, types={'club': 'Club', 'person': 'Person'})
    assert any(p.startswith('V1') for p in out)


def test_checks_flag_role_direction():
    assert any(p.startswith('V2') for p in problems('Đồng đội của A chơi dưới quyền bao nhiêu HLV?',
                                                     plan(('person', ['teammates', 'players_coached']), output='count')))
    assert not problems('Đồng đội của A chơi dưới quyền bao nhiêu HLV?',
                        plan(('person', ['teammates', 'coaches_of_player']), output='count'))


def test_checks_flag_a_missing_cue_hop_and_answer_type():
    out = problems('Đồng đội của những người từng chơi cho X được những HLV nào huấn luyện?',
                   plan(('club', ['players_of_club', 'coaches_of_player'])), params={'club': 'X'}, types={'club': 'Club'})
    assert any('đồng đội' in p for p in out)
    out = problems('A từng thi đấu cho những CLB nào?', plan(('person', ['teammates'])))
    assert any(p.startswith('V5') for p in out)


def test_empty_result_is_suspicious_unless_asking_existence():
    assert any(p.startswith('V4') for p in problems('A từng chơi cho những CLB nào?', plan(('person', ['clubs_played_for'])), rows=()))
    assert not problems('Có CLB nào A từng chơi cho không?', plan(('person', ['clubs_played_for'])), rows=())


def test_v3_keeps_a_clean_fast_plan_without_escalating(graph):
    client = scripted([TEAM])
    result = agent.generate_v3(client, graph, 'Đồng đội của A là ai?', {'person': 'A'})
    assert result['ok'] and not result['escalated'] and client.complete.call_count == 1


def test_v3_escalates_a_flagged_plan_with_feedback(graph):
    wrong = json.dumps(plan(('person', ['players_coached'])))
    client = scripted([wrong], thinking=COACH)
    result = agent.generate_v3(client, graph, 'Những HLV nào từng huấn luyện A?', {'person': 'A'})
    assert result['escalated'] and result['plan']['chains'][0]['hops'] == ['coaches_of_player']
    call = client.complete.call_args_list[1]
    assert call[1]['thinking'] and 'V2' in call[0][0][-1]['content']


def test_v3_escalates_an_invalid_plan(graph):
    client = scripted([BAD], thinking=TEAM)
    result = agent.generate_v3(client, graph, 'Đồng đội của A là ai?', {'person': 'A'})
    assert result['ok'] and result['escalated'] and result['fast']['problems'][0].startswith('V0')
