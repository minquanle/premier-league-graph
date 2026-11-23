"""Bind every citation to a result before the model formats the answer."""
import json
from .query import DIRECT


def evidence_packets(graph, rows, provenance, params):
    packets = []
    for i, row in enumerate(rows):
        packet = {'row_id': f'R{i+1}', 'data': row, 'sources': []}
        kind = provenance.get('kind', 'aggregate')
        if kind == 'direct' and row.get('name'):
            relation = provenance['relation']
            if relation not in DIRECT:
                raise ValueError('Unsupported provenance relation')
            side = provenance.get('anchor_side', 'object')
            anchor = params[provenance['anchor_param']]
            sname, oname = (anchor, row['name']) if side == 'subject' else (row['name'], anchor)
            matches = graph.run(f'''MATCH (s:FootballEntity)-[r:{relation}]->(o:FootballEntity)
                WHERE s.canonical_name=$sname AND o.canonical_name=$oname
                WITH r UNWIND r.assertion_ids AS aid
                MATCH (x:ExtractionAssertion {{assertion_id:aid}})-[:EVIDENCED_BY]->(a:SourceArticle)
                RETURN x.assertion_id AS assertion_id, x.evidence_text AS quote, a.canonical_url AS url,
                       x.assertion_status AS status ORDER BY assertion_id LIMIT 2''', {'sname': sname, 'oname': oname})
            packet['sources'] = [{'source_id': f'E{i+1}_{j+1}', **m} for j, m in enumerate(matches)]
        elif kind == 'derived' and row.get('name') and params.get('person'):
            # Generic two-hop witness retrieval; absent witness stays absent, never fabricated.
            witnesses = graph.run('''MATCH (p:Person {canonical_name:$person})-[t:TEAMMATE_OF]-(m:Person)-[u:PLAYED_UNDER]->(c:Person)
                WHERE c.canonical_name=$answer
                RETURN m.canonical_name AS teammate, t.clubs AS teammate_clubs, u.clubs AS coach_clubs LIMIT 2''',
                {'person': params['person'], 'answer': row['name']})
            if witnesses:
                packet['sources'] = [{'source_id': f'D{i+1}', 'kind': 'derived', 'witnesses': witnesses,
                    'rule': 'Đồng đội: cùng CLB trong giai đoạn chồng lấn. Cầu thủ–HLV: giai đoạn chồng lấn riêng, có thể ở CLB và thời gian khác. Không suy ra HLV dẫn dắt người được hỏi.'}]
        packet['provenance_kind'] = kind
        packets.append(packet)
    return packets


def check_selection(selection, packets):
    wanted = {p['row_id']: {s['source_id'] for s in p['sources']} for p in packets}
    items = selection.get('items', [])
    if len(items) != len(wanted) or {i.get('row_id') for i in items} != set(wanted):
        raise ValueError('Answer must cover each result row exactly once')
    if [i['row_id'] for i in items] != [p['row_id'] for p in packets]:
        raise ValueError('Answer must preserve result order')
    for item in items:
        allowed = wanted[item['row_id']]
        if 'source_ids' not in item:
            raise ValueError('source_ids must be supplied even for source-free rows')
        refs = item['source_ids']
        if not isinstance(refs, list) or not set(refs) <= allowed:
            raise ValueError('Citation belongs to a different result or does not exist')
        if allowed and not refs:
            raise ValueError('Missing available evidence citation')


def render(selection, packets):
    check_selection(selection, packets)
    if not packets:
        return 'Không tìm thấy dữ liệu phù hợp trong đồ thị; điều này không chứng minh sự việc chưa từng xảy ra.'
    by_id = {p['row_id']: p for p in packets}
    lines = ['Theo dữ liệu trong đồ thị (tối đa 10 kết quả):']
    for item in selection['items']:
        p = by_id[item['row_id']]
        data = p['data']
        text = str(data.get('name', 'Số lượng'))
        if 'count' in data:
            text += ': ' + str(data['count'])
        if 'score' in data:
            text += ': ' + format(data['score'], '.8f')
        sources = {s['source_id']: s for s in p['sources']}
        labels = []
        for ref in item['source_ids']:
            source = sources[ref]
            if source.get('url'):
                labels.append(f"[{ref}]({source['url']})")
            elif source.get('kind') == 'derived':
                mates = ', '.join(w['teammate'] for w in source['witnesses'])
                labels.append(f'[{ref}: suy diễn Wikidata qua {mates}]')
        if not labels:
            labels = ['[kết quả truy vấn graph; chưa gắn bằng chứng riêng]']
        lines.append('- ' + text + ' ' + ' '.join(labels))
    return '\n'.join(lines)


def synthesize(client, packets, question):
    system = '''Format graph answers as JSON only: {"items":[{"row_id":"R1","source_ids":["E1_1"]}]}.
Include EVERY supplied row exactly once, in its existing order. Choose at least one available source ID from that SAME row.
If a row has no sources use []. If there are no rows use {"items":[]}.
Do not add prose, people, facts, quotes or source IDs. The application renders canonical names and actual links.
This prevents invented facts and cross-person citations.'''
    response = client.complete([{'role': 'system', 'content': system},
                               {'role': 'user', 'content': json.dumps({'question': question, 'rows': packets}, ensure_ascii=False)}], json_output=True)
    valid, error = True, None
    try:
        selection = json.loads(response['content'])
        check_selection(selection, packets)
    except Exception as exc:
        valid, error = False, str(exc)[:180]
        selection = {'items': [{'row_id': p['row_id'], 'source_ids': [s['source_id'] for s in p['sources'][:1]]} for p in packets]}
    return {'answer': render(selection, packets), 'selection': selection,
            'model_selection_valid': valid, 'fallback_used': not valid,
            'seconds': response['seconds'], 'usage': response['usage'], 'selection_error': error}
