"""Builds a larger held-out QA benchmark (configs/chatbot_benchmark_v2.json) from plan templates.

Each template is a hop plan plus several Vietnamese phrasings. Start entities are drawn from the live graph,
excluding every name that appears in the prompt examples or in the first benchmark, so the model has never
seen these entities in a demonstration. Reference rows come from the compiled plan on the fixed graph.
Phrasings are hand-written templates, so the set tests unseen entities and wording variety, not free-form
user language.
"""
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from player_graph.chatbot import agent
from player_graph.chatbot.query import Graph

T = []  # (category, chains, output, [phrasings]); {a} = first entity, {b} = second


def add(category, chains, output, *phrasings):
    T.append((category, chains, output, phrasings))


# 1 hop: lookups
add('lookup', [('club', ['players_of_club'])], 'list', 'Những ai từng thi đấu cho {a} theo đồ thị?', 'Liệt kê cầu thủ từng khoác áo {a}.')
add('lookup', [('club', ['managers_of_club'])], 'list', 'Ai từng làm HLV của {a}?', 'Đồ thị ghi những huấn luyện viên nào từng dẫn dắt {a}?')
add('lookup', [('person', ['clubs_played_for'])], 'list', '{a} từng chơi cho những CLB nào?', 'Cho biết các đội bóng mà {a} từng khoác áo.')
add('lookup', [('person', ['clubs_managed'])], 'list', '{a} từng dẫn dắt những đội nào?', 'Sự nghiệp huấn luyện của {a} gắn với những CLB nào?')
add('lookup', [('club', ['players_transferred_in'])], 'list', 'Những cầu thủ nào từng chuyển đến {a}?', '{a} từng mua về những ai theo dữ liệu?')
add('lookup', [('person', ['transfer_destinations'])], 'list', '{a} từng chuyển nhượng sang những CLB nào?')
add('lookup', [('club', ['players_loaned_in'])], 'list', '{a} từng mượn những cầu thủ nào?')
add('lookup', [('person', ['loan_destinations'])], 'list', '{a} từng được cho mượn tới đội nào?')
add('lookup', [('person', ['teammates'])], 'list', 'Kể tên vài đồng đội của {a}.', 'Ai từng là đồng đội của {a}?')
add('lookup', [('person', ['coaches_of_player'])], 'list', 'Những HLV nào từng huấn luyện {a}?', '{a} từng chơi dưới quyền ai?')
add('lookup', [('person', ['players_coached'])], 'list', '{a} từng huấn luyện những cầu thủ nào?')
add('lookup', [('person', ['agents_of'])], 'list', 'Ai là người đại diện của {a}?')
add('lookup', [('person', ['clients_of_agent'])], 'list', '{a} làm người đại diện cho những ai?')
add('lookup', [('person', ['criticized_targets'])], 'list', '{a} từng chỉ trích những ai?')
add('lookup', [('person', ['critics_of'])], 'list', 'Những ai từng lên tiếng chỉ trích {a}?')
add('lookup', [('club', ['managers_dismissed'])], 'list', '{a} từng sa thải những HLV nào?')
add('lookup', [('person', ['clubs_dismissed_by'])], 'list', '{a} từng bị CLB nào sa thải?')
add('lookup', [('club', ['managers_appointed'])], 'list', '{a} từng bổ nhiệm ai làm HLV?')
add('lookup', [('club', ['transfer_flow_to'])], 'list', 'Cầu thủ rời {a} thường chuyển sang những CLB nào?')
# 2 hops
add('multihop', [('person', ['teammates', 'coaches_of_player'])], 'list', 'Đồng đội của {a} từng được những HLV nào huấn luyện?')
add('multihop', [('person', ['teammates', 'clubs_played_for'])], 'list', 'Các đồng đội của {a} đã chơi cho những CLB nào?')
add('multihop', [('club', ['players_of_club', 'coaches_of_player'])], 'list', 'Cầu thủ từng khoác áo {a} đã chơi dưới quyền những HLV nào?')
add('multihop', [('club', ['managers_of_club', 'clubs_managed'])], 'list', 'Những HLV từng dẫn dắt {a} còn dẫn dắt các đội nào khác?')
add('multihop', [('person', ['coaches_of_player', 'clubs_managed'])], 'list', 'Các HLV từng huấn luyện {a} đã dẫn dắt những CLB nào?')
add('multihop', [('person', ['players_coached', 'clubs_played_for'])], 'list', 'Học trò của {a} từng thi đấu cho những đội nào?')
add('multihop', [('club', ['players_transferred_in', 'clubs_played_for'])], 'list', 'Những người chuyển đến {a} từng chơi cho các CLB nào?')
add('multihop', [('person', ['criticized_targets', 'clubs_played_for'])], 'list', 'Những người bị {a} chỉ trích từng thi đấu cho đội nào?')
add('multihop', [('club', ['players_of_club', 'agents_of'])], 'list', 'Cầu thủ của {a} được những người đại diện nào đại diện?')
# 3 hops
add('multihop3', [('person', ['teammates', 'coaches_of_player', 'clubs_managed'])], 'list', 'Đi từ {a} qua đồng đội, tới HLV của đồng đội đó: các HLV ấy từng dẫn dắt CLB nào?')
add('multihop3', [('club', ['managers_of_club', 'players_coached', 'clubs_played_for'])], 'list', 'Học trò của các HLV từng dẫn dắt {a} đã thi đấu cho những CLB nào?')
add('multihop3', [('club', ['players_of_club', 'teammates', 'coaches_of_player'])], 'list', 'Đồng đội của những người từng chơi cho {a} được những HLV nào huấn luyện?')
# counts
add('count', [('club', ['players_of_club'])], 'count', 'Có bao nhiêu người từng thi đấu cho {a}?', 'Đếm số cầu thủ khác nhau từng khoác áo {a}.')
add('count', [('person', ['teammates'])], 'count', '{a} có bao nhiêu đồng đội trong đồ thị?')
add('count', [('person', ['players_coached'])], 'count', '{a} đã huấn luyện tổng cộng bao nhiêu cầu thủ?')
add('count', [('person', ['clubs_played_for'])], 'count', '{a} từng chơi cho bao nhiêu CLB?')
add('count', [('club', ['managers_of_club'])], 'count', 'Số HLV từng dẫn dắt {a} là bao nhiêu?')
add('count', [('person', ['teammates', 'coaches_of_player'])], 'count', 'Đồng đội của {a} đã chơi dưới quyền tổng cộng bao nhiêu HLV?')
# intersections
add('intersection', [('club', ['players_of_club']), ('other', ['players_of_club'])], 'list', 'Ai từng chơi cho cả {a} và {b}?', 'Cầu thủ nào có tên trong lịch sử cả {a} lẫn {b}?')
add('intersection', [('club', ['managers_of_club']), ('other', ['managers_of_club'])], 'list', 'HLV nào từng dẫn dắt cả {a} và {b}?')
add('intersection', [('person', ['teammates']), ('other', ['teammates'])], 'list', 'Ai là đồng đội chung của {a} và {b}?')
add('intersection', [('person', ['coaches_of_player']), ('other', ['coaches_of_player'])], 'list', 'HLV nào từng huấn luyện cả {a} lẫn {b}?')
add('intersection', [('club', ['players_of_club']), ('person', ['players_coached'])], 'list', 'Trong số cầu thủ của {a}, ai từng được {b} huấn luyện?', 'Những học trò của {b} từng chơi cho {a} là ai?')
# empty-prone (people in roles they do not hold)
add('empty', [('person', ['clients_of_agent'])], 'list', 'Đồ thị ghi {a} đại diện cho những cầu thủ nào?')
add('empty', [('person', ['clubs_managed'])], 'list', 'Có CLB nào được {a} dẫn dắt trong dữ liệu không?')

BANNED = {'Arsenal', 'Liverpool', 'Chelsea', 'Tottenham Hotspur', 'Newcastle United', 'Everton', 'Manchester United',
          'Cristiano Ronaldo', 'Frank Lampard', 'Steven Gerrard', 'Wayne Rooney', 'José Mourinho', 'Alex Ferguson',
          'Arsène Wenger', 'Harry Kane', 'David Beckham', 'Roy Keane', 'Jorge Mendes'}
QUOTA = {'lookup': 30, 'multihop': 25, 'multihop3': 10, 'count': 15, 'intersection': 15, 'empty': 5}


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--seed', type=int, default=20261124)
    ap.add_argument('--out', default='configs/chatbot_benchmark_v2.json')
    ap.add_argument('--split', default='test_v2')
    ap.add_argument('--exclude', nargs='*', default=[], help='benchmark files whose entities must not be reused')
    args = ap.parse_args()
    for path in args.exclude:
        BANNED.update(v for c in json.loads((ROOT / path).read_text(encoding='utf-8')) for v in c['params'].values())
    rng = random.Random(args.seed)
    graph = Graph()
    try:
        people = [r['n'] for r in graph.run('''MATCH (p:Person)-[r]-() WHERE NOT p.canonical_name IN $b
            WITH p, count(r) AS d WHERE d >= 4 RETURN p.canonical_name AS n ORDER BY d DESC LIMIT 400''', {'b': list(BANNED)})]
        clubs = [r['n'] for r in graph.run('''MATCH (c:Club)-[r]-() WHERE NOT c.canonical_name IN $b
            WITH c, count(r) AS d WHERE d >= 8 RETURN c.canonical_name AS n ORDER BY d DESC''', {'b': list(BANNED)})]
        pool = {'Person': people, 'Club': clubs}
        used, cases = {}, []
        for category, quota in QUOTA.items():
            templates = [t for t in T if t[0] == category]
            made = lambda: sum(c['kind'] == category for c in cases)
            dead = set()
            k = 0
            while made() < quota and len(dead) < len(templates):
                ti = k % len(templates)
                k += 1
                if ti in dead:
                    continue
                _, chains, output, phrasings = templates[ti]
                types = {f: agent.HOPS[hops[0]][2] for f, hops in chains}
                plan = {'chains': [{'from': f, 'hops': h} for f, h in chains], 'output': output}
                cypher, _ = agent.compile_plan(plan, types)
                for attempt in range(150):
                    names = {f: rng.choice(pool[t]) for f, t in types.items()}
                    if len(set(names.values())) < len(names) or any(used.get(n, 0) >= 3 for n in names.values()):
                        continue
                    rows = graph.run(cypher, names)
                    empty = not rows or rows == [{'count': 0}]
                    if (category == 'empty') != empty:
                        continue
                    if category != 'empty' and output == 'list' and len(rows) < 2:
                        continue
                    keys = list(names)
                    text = rng.choice(phrasings).format(a=names[keys[0]], b=names[keys[1]] if len(keys) > 1 else '')
                    if any(c['question'] == text for c in cases):
                        continue
                    break
                else:
                    dead.add(ti)
                    continue
                for n in names.values():
                    used[n] = used.get(n, 0) + 1
                cases.append({'id': f"{args.split}_{len(cases) + 1:03d}", 'split': args.split, 'question': text, 'kind': category,
                              'category': 'multihop' if category == 'multihop3' else category,
                              'hops': max(len(h) for _, h in chains), 'params': names, 'param_types': types,
                              'oracle_plan': plan, 'cypher': cypher})
            print(category, made(), '/', quota, '| templates without a usable entity:', len(dead), flush=True)
        out = ROOT / args.out
        out.write_text(json.dumps(cases, ensure_ascii=False, indent=1), encoding='utf-8')
        from collections import Counter
        print(len(cases), 'cases', dict(Counter(c['category'] for c in cases)), dict(Counter(c['hops'] for c in cases)))
        print('distinct entities', len(used))
    finally:
        graph.close()


if __name__ == '__main__':
    main()
