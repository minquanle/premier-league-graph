"""Builds configs/chatbot_agent_library.json: the planner's few-shot library (static 20 examples + 32 more).

Only entities already used in the first example set are used here; all of them are excluded from the
v2 benchmark, so no benchmark entity appears in a demonstration.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from player_graph.chatbot import agent

CLUBS = {'Arsenal', 'Liverpool', 'Chelsea', 'Tottenham Hotspur', 'Newcastle United', 'Everton', 'Manchester United'}
NEW = [
    ('lib_21', 'Arsène Wenger từng làm HLV ở những câu lạc bộ nào?', {'person': 'Arsène Wenger'}, [('person', ['clubs_managed'])], 'list'),
    ('lib_22', 'David Beckham đã chuyển sang những đội nào trong sự nghiệp?', {'person': 'David Beckham'}, [('person', ['transfer_destinations'])], 'list'),
    ('lib_23', 'Harry Kane có từng được đem cho mượn ở đâu không?', {'person': 'Harry Kane'}, [('person', ['loan_destinations'])], 'list'),
    ('lib_24', 'Ai đứng sau Cristiano Ronaldo với tư cách người đại diện?', {'person': 'Cristiano Ronaldo'}, [('person', ['agents_of'])], 'list'),
    ('lib_25', 'Có những ai từng phê phán Roy Keane?', {'person': 'Roy Keane'}, [('person', ['critics_of'])], 'list'),
    ('lib_26', 'José Mourinho từng mâu thuẫn với những người nào?', {'person': 'José Mourinho'}, [('person', ['conflicts_with'])], 'list'),
    ('lib_27', 'Chelsea đã cho thôi việc những HLV nào?', {'club': 'Chelsea'}, [('club', ['managers_dismissed'])], 'list'),
    ('lib_28', 'José Mourinho từng bị những đội nào sa thải?', {'person': 'José Mourinho'}, [('person', ['clubs_dismissed_by'])], 'list'),
    ('lib_29', 'Tottenham Hotspur đã bổ nhiệm những ai vào ghế HLV?', {'club': 'Tottenham Hotspur'}, [('club', ['managers_appointed'])], 'list'),
    ('lib_30', 'Frank Lampard được những CLB nào mời về làm HLV?', {'person': 'Frank Lampard'}, [('person', ['clubs_appointed_by'])], 'list'),
    ('lib_31', 'Những cầu thủ nào đã ký gia hạn với Liverpool?', {'club': 'Liverpool'}, [('club', ['players_extended_by'])], 'list'),
    ('lib_32', 'Wayne Rooney từng gia hạn hợp đồng với đội nào?', {'person': 'Wayne Rooney'}, [('person', ['contract_extended_with'])], 'list'),
    ('lib_33', 'Arsenal từng đàm phán để chiêu mộ những ai?', {'club': 'Arsenal'}, [('club', ['negotiation_targets'])], 'list'),
    ('lib_34', 'Những CLB nào từng muốn đàm phán mua Harry Kane?', {'person': 'Harry Kane'}, [('person', ['clubs_negotiating_for'])], 'list'),
    ('lib_35', 'Arsène Wenger từng xin từ chức ở CLB nào?', {'person': 'Arsène Wenger'}, [('person', ['clubs_resigned_from'])], 'list'),
    ('lib_36', 'Cầu thủ rời Everton hay sang những câu lạc bộ nào?', {'club': 'Everton'}, [('club', ['transfer_flow_to'])], 'list'),
    ('lib_37', 'Newcastle United thường nhận cầu thủ từ những CLB nào?', {'club': 'Newcastle United'}, [('club', ['transfer_flow_from'])], 'list'),
    ('lib_38', 'Những ai từng là học trò của Arsène Wenger?', {'person': 'Arsène Wenger'}, [('person', ['players_coached'])], 'list'),
    ('lib_39', 'Các HLV của Liverpool từng huấn luyện những cầu thủ nào?', {'club': 'Liverpool'}, [('club', ['managers_of_club', 'players_coached'])], 'list'),
    ('lib_40', 'Những người chuyển đến Chelsea trước đó chơi cho các CLB nào?', {'club': 'Chelsea'}, [('club', ['players_transferred_in', 'clubs_played_for'])], 'list'),
    ('lib_41', 'Người đại diện của các đồng đội Wayne Rooney là ai?', {'person': 'Wayne Rooney'}, [('person', ['teammates', 'agents_of'])], 'list'),
    ('lib_42', 'Các HLV từng dẫn dắt Arsenal còn huấn luyện những đội nào?', {'club': 'Arsenal'}, [('club', ['managers_of_club', 'clubs_managed'])], 'list'),
    ('lib_43', 'Học trò của Alex Ferguson sau đó chơi cho những đội nào?', {'person': 'Alex Ferguson'}, [('person', ['players_coached', 'clubs_played_for'])], 'list'),
    ('lib_44', 'Từ Harry Kane qua đồng đội, tới các CLB đồng đội đó khoác áo, rồi tới HLV của các CLB ấy: là những ai?', {'person': 'Harry Kane'}, [('person', ['teammates', 'clubs_played_for', 'managers_of_club'])], 'list'),
    ('lib_45', 'Đồng đội của học trò José Mourinho được những HLV nào huấn luyện?', {'person': 'José Mourinho'}, [('person', ['players_coached', 'teammates', 'coaches_of_player'])], 'list'),
    ('lib_46', 'Tổng số CLB mà Arsène Wenger từng dẫn dắt là mấy?', {'person': 'Arsène Wenger'}, [('person', ['clubs_managed'])], 'count'),
    ('lib_47', 'Đồng đội của Steven Gerrard đã thi đấu cho tổng cộng bao nhiêu CLB?', {'person': 'Steven Gerrard'}, [('person', ['teammates', 'clubs_played_for'])], 'count'),
    ('lib_48', 'Chelsea từng sa thải bao nhiêu HLV?', {'club': 'Chelsea'}, [('club', ['managers_dismissed'])], 'count'),
    ('lib_49', 'HLV nào từng huấn luyện cả Wayne Rooney lẫn Cristiano Ronaldo?', {'person': 'Wayne Rooney', 'other': 'Cristiano Ronaldo'}, [('person', ['coaches_of_player']), ('other', ['coaches_of_player'])], 'list'),
    ('lib_50', 'Trong số học trò của Alex Ferguson, những ai từng thi đấu cho Everton?', {'club': 'Everton', 'person': 'Alex Ferguson'}, [('club', ['players_of_club']), ('person', ['players_coached'])], 'list'),
    ('lib_51', 'CLB nào có cả Frank Lampard và David Beckham từng khoác áo?', {'person': 'Frank Lampard', 'other': 'David Beckham'}, [('person', ['clubs_played_for']), ('other', ['clubs_played_for'])], 'list'),
    ('lib_52', 'Cristiano Ronaldo cao bao nhiêu mét?', {'person': 'Cristiano Ronaldo'}, [], 'unsupported'),
]


def main():
    library = json.loads((ROOT / 'configs/chatbot_agent_examples.json').read_text(encoding='utf-8'))
    for ident, question, params, chains, output in NEW:
        types = {k: ('Club' if v in CLUBS else 'Person') for k, v in params.items()}
        plan = {'chains': [{'from': f, 'hops': h} for f, h in chains], 'output': output}
        if output != 'unsupported':
            agent.compile_plan(plan, types)
        library.append({'id': ident, 'question': question, 'params': params, 'param_types': types, 'plan': plan})
    (ROOT / 'configs/chatbot_agent_library.json').write_text(json.dumps(library, ensure_ascii=False, indent=1), encoding='utf-8')
    used = {h for x in library for c in x['plan']['chains'] for h in c['hops']}
    v2 = json.loads((ROOT / 'configs/chatbot_benchmark_v2.json').read_text(encoding='utf-8'))
    overlap = {v for c in v2 for v in c['params'].values()} & {v for x in library for v in x['params'].values()}
    print(len(library), 'examples | hops covered', len(used), '/', len(agent.HOPS), '| missing', sorted(set(agent.HOPS) - used))
    print('entities shared with v2 benchmark:', overlap or 'none')


if __name__ == '__main__':
    main()
