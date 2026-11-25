"""One question, end to end: link entities, route, plan or pick an analytics tool, answer with evidence.

Every step is returned so the interface can show the reasoning: linked entities, the chosen route,
the plan with the number of entities reached after each hop, the automatic checks, the Cypher query,
the answer rows bound to their news evidence, and the edges to draw.
"""
from . import agent, portrait
from .analytics import choose_tool, tools
from .answer import evidence_packets, render
from .linking import link
from .query import fold

ANALYTICS_CUES = ('pagerank', 'xep hang', 'trung tam', 'anh huong', 'quan trong nhat', 'hat nhan', 'loi cua mang',
                  'loi mang', 'cot loi', 'cong dong', 'thuoc nhom', 'nhom nao', 'noi voi nhau', 'ket noi voi',
                  'duong di', 'qua nhung ai', 'nhieu cau thu nhat', 'k-core', 'k core')
PRESENT_CUES = ('hien tai', 'dang thi dau', 'dang dan dat', 'bay gio')


def route(question, params):
    q = fold(question)
    if not params or any(cue in q for cue in ANALYTICS_CUES):
        return 'analytics'
    return 'planner'


def _answer(graph, rows, provenance, params):
    packets = evidence_packets(graph, rows, provenance, params)
    # Rows come from the query and names are rendered by code; each row cites its first own source.
    selection = {'items': [{'row_id': p['row_id'], 'source_ids': [s['source_id'] for s in p['sources'][:1]]}
                           for p in packets]}
    return packets, render(selection, packets)


def _tool_answer(name, args, rows):
    """Readable text for each analytics tool; numbers come straight from the computed rows."""
    if not rows:
        return 'Không tìm thấy kết quả trong đồ thị (có thể hai người không nối với nhau trong 6 bước đồng đội).'
    names = [r.get('name') for r in rows]
    if name == 'path_between':
        return (f'Chuỗi đồng đội ngắn nhất ({len(rows) - 1} bước): ' + ' → '.join(f'**{n}**' for n in names)
                + '\n\nMỗi mũi tên là một cặp từng thi đấu cùng CLB trong cùng giai đoạn (suy ra từ Wikidata).')
    if name == 'network_core':
        return (f'Lõi trong cùng của mạng đồng đội là **{rows[0]["count"]}-core**: mỗi người trong đó có ít nhất '
                f'{rows[0]["count"]} đồng đội cũng thuộc lõi. Tiêu biểu (theo số đồng đội): ' + ', '.join(names) + '.')
    if name == 'community_nucleus':
        return (f'{args.get("person")} thuộc một cộng đồng Leiden; hạt nhân (lõi k trong cộng đồng) gồm: '
                + ', '.join(f'{r["name"]} ({r["count"]} đồng đội trong nhóm)' for r in rows) + '.')
    if name == 'pagerank_teammates':
        return 'Xếp hạng PageRank trên mạng đồng đội (α = 0,85):\n' + '\n'.join(
            f'{i + 1}. {r["name"]}: {r["score"]:.5f}' for i, r in enumerate(rows))
    if name == 'top_clubs':
        return 'CLB có nhiều người từng thi đấu nhất trong đồ thị:\n' + '\n'.join(
            f'{i + 1}. {r["name"]}: {r["count"]} người' for i, r in enumerate(rows))
    if name == 'count_players':
        return f'{args.get("club")} có **{rows[0]["count"]}** người từng thi đấu được ghi nhận trong đồ thị.'
    return '\n'.join(str(r) for r in rows)


def _tool_edges(graph, name, rows):
    names = [r['name'] for r in rows if r.get('name')]
    if name == 'path_between':
        return [(a, 'TEAMMATE_OF', b) for a, b in zip(names, names[1:])]
    if name in ('network_core', 'community_nucleus', 'pagerank_teammates'):
        m = portrait.network_measures(graph)
        ids = [m['ids'][n] for n in names if n in m['ids']]
        sub = m['network'].subgraph(ids)
        return [(m['names'][a], 'TEAMMATE_OF', m['names'][b]) for a, b in sub.edges()]
    return []


def ask(client, graph, question, params=None):
    out = {'question': question, 'notes': []}
    if any(cue in fold(question) for cue in PRESENT_CUES):
        out['notes'].append('Đồ thị lưu quan hệ đã được báo chí ghi nhận theo thời gian, không phải trạng thái hiện tại.')
    try:
        params = params if params is not None else link(graph, question)
    except ValueError as error:
        return {**out, 'ok': False, 'stage': 'linking', 'message': str(error)}
    out['params'] = params
    out['route'] = route(question, params)

    if out['route'] == 'analytics':
        try:
            name, args, _ = choose_tool(client, question, params)
            rows = tools(graph, name, args)
            return {**out, 'ok': True, 'tool': name, 'args': args, 'rows': rows,
                    'answer': _tool_answer(name, args, rows), 'edges': _tool_edges(graph, name, rows)}
        except Exception as error:
            if not params:
                return {**out, 'ok': False, 'stage': 'analytics',
                        'message': 'Câu hỏi về toàn mạng nhưng chưa khớp công cụ phân tích nào (' + str(error)[:80] + ').'}
            out['route'] = 'planner'
            out['notes'].append('Không khớp công cụ phân tích, chuyển sang lập kế hoạch truy vấn.')

    result = agent.generate_v3(client, graph, question, params)
    out.update({k: result.get(k) for k in ('plan', 'query', 'checks', 'escalated', 'attempts', 'fast')})
    if not result['ok']:
        message = ('Câu hỏi nằm ngoài những gì đồ thị lưu trữ.' if result.get('unsupported')
                   else 'Chưa lập được kế hoạch hợp lệ cho câu hỏi này; hãy diễn đạt rõ hơn.')
        return {**out, 'ok': False, 'stage': 'planner', 'message': message}
    types = agent.param_types(graph, params)
    rows = result['rows']
    packets, text = _answer(graph, rows, result['provenance'], params)
    answers = [r['name'] for r in rows if r.get('name')]
    trace = agent.trace(graph, result['plan'], params, types)
    total = trace[-1]['count'] if len(result['plan']['chains']) == 1 and result['plan']['output'] == 'list' else None
    if result['provenance'].get('kind') != 'direct':
        text = text.replace(' [kết quả truy vấn graph; chưa gắn bằng chứng riêng]', '') + \
            '\n\n_Kết quả suy ra qua nhiều bước trên đồ thị; đường suy luận được vẽ ở đồ thị con bên dưới._'
    return {**out, 'ok': True, 'rows': rows, 'packets': packets, 'answer': text, 'total': total, 'trace': trace,
            'edges': portrait.plan_edges(graph, result['plan'], params, types, answers) if answers else []}
