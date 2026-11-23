"""Checks that the agent's hop catalogue can express every benchmark question.

For each benchmark case a hand-written oracle plan is compiled and run on the live graph and its rows are
compared with the reference query's rows. This tests the planner's action space, not the model: the
oracle plans are never shown to the model.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from player_graph.chatbot import agent
from player_graph.chatbot.query import Graph

ORACLE = {
    'dev_01': ([('club', ['managers_of_club'])], 'list'),
    'dev_02': ([('person', ['teammates', 'coaches_of_player'])], 'list'),
    'dev_03': ([('club', ['players_of_club'])], 'list'),
    'dev_04': ([('person', ['clubs_played_for'])], 'count'),
    'dev_05': ([('club', ['players_of_club']), ('other', ['players_of_club'])], 'list'),
    'dev_06': ([('person', ['teammates', 'coaches_of_player', 'clubs_managed'])], 'list'),
    'dev_07': ([('club', ['players_of_club']), ('person', ['players_coached'])], 'list'),
    'dev_08': ([('person', ['clients_of_agent'])], 'list'),
    'test_01': ([('club', ['managers_of_club'])], 'list'),
    'test_02': ([('person', ['clubs_played_for'])], 'list'),
    'test_03': ([('club', ['players_transferred_in'])], 'list'),
    'test_04': ([('club', ['players_loaned_in'])], 'list'),
    'test_05': ([('person', ['agents_of'])], 'list'),
    'test_06': ([('person', ['criticized_targets'])], 'list'),
    'test_07': ([('person', ['teammates', 'coaches_of_player'])], 'list'),
    'test_08': ([('club', ['players_of_club', 'teammates'])], 'list'),
    'test_09': ([('person', ['coaches_of_player', 'clubs_managed'])], 'list'),
    'test_10': ([('person', ['teammates', 'agents_of'])], 'list'),
    'test_11': ([('club', ['managers_of_club', 'players_coached', 'teammates'])], 'list'),
    'test_12': ([('person', ['teammates', 'coaches_of_player', 'clubs_managed'])], 'list'),
    'test_13': ([('club', ['players_of_club'])], 'count'),
    'test_14': ([('person', ['clubs_managed'])], 'count'),
    'test_15': ([('person', ['teammates'])], 'count'),
    'test_16': ([('person', ['players_coached'])], 'count'),
    'test_17': ([('club', ['players_loaned_in'])], 'count'),
    'test_18': ([('club', ['players_of_club']), ('other', ['players_of_club'])], 'list'),
    'test_19': ([('club', ['managers_of_club']), ('other', ['managers_of_club'])], 'list'),
    'test_20': ([('person', ['teammates']), ('other', ['teammates'])], 'list'),
    'test_21': ([('club', ['players_of_club']), ('person', ['players_coached'])], 'list'),
    'test_22': ([('person', ['loan_destinations'])], 'list'),
    'test_23': ([('person', ['clients_of_agent'])], 'list'),
    'test_24': ([('person', ['clubs_managed'])], 'list'),
}


def norm(rows):
    return sorted(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in rows)


def main():
    cases = json.loads((ROOT / 'configs/chatbot_benchmark.json').read_text(encoding='utf-8'))
    graph = Graph()
    ok = 0
    try:
        for case in cases:
            chains, output = ORACLE[case['id']]
            plan = {'chains': [{'from': f, 'hops': h} for f, h in chains], 'output': output}
            types = agent.param_types(graph, case['params'])
            cypher, _ = agent.compile_plan(plan, types)
            same = norm(graph.run(cypher, case['params'])) == norm(graph.run(case['cypher'], case['params']))
            ok += same
            print(case['id'], 'OK ' if same else 'DIFF', cypher if not same else '')
    finally:
        graph.close()
    print(f'{ok}/{len(cases)} benchmark cases expressible by the hop catalogue')


if __name__ == '__main__':
    main()
