"""Explicit allow-listed computation tools, not arbitrary generated Python execution."""
import networkx as nx


def tools(graph, name, args):
    if name == 'count_players':
        return graph.run('''MATCH (p:Person)-[:PLAYS_FOR]->(c:Club {canonical_name:$club})
            RETURN count(DISTINCT p) AS count''', {'club': args['club']})
    if name == 'top_clubs':
        return graph.run('''MATCH (p:Person)-[:PLAYS_FOR]->(c:Club)
            RETURN c.canonical_name AS name,count(DISTINCT p) AS count
            ORDER BY count DESC,name LIMIT 10''')
    if name == 'pagerank_teammates':
        rows = graph.run('''MATCH (p:Person)-[:TEAMMATE_OF]-(q:Person)
            RETURN p.entity_id AS p, q.entity_id AS q, p.canonical_name AS name''')
        network, names = nx.Graph(), {}
        for row in rows:
            network.add_edge(row['p'], row['q'])
            names[row['p']] = row['name']
        scores = nx.pagerank(network, alpha=0.85)
        return [{'name': names.get(node, node), 'score': score}
                for node, score in sorted(scores.items(), key=lambda r: (-r[1], names.get(r[0], r[0])))[:10]]
    raise ValueError('Unknown analytics tool')


def choose_tool(client, question, params):
    import json
    r = client.complete([{'role': 'system', 'content': '''Select one analytics tool and return JSON {"tool":"...","args":{...}}.
Available: count_players(club): count distinct people with PLAYS_FOR to club.
top_clubs(): top 10 clubs by count distinct PLAYS_FOR people.
pagerank_teammates(): top 10 PageRank nodes of the undirected full TEAMMATE_OF graph, damping 0.85.
Only use supplied canonical parameters. No code, no other tools. If unsupported return {"tool":"unsupported","args":{}}.'''
    }, {'role':'user','content':'{"question":"Có bao nhiêu cầu thủ ở Arsenal?","params":{"club":"Arsenal"}}'},
       {'role':'assistant','content':'{"tool":"count_players","args":{"club":"Arsenal"}}'},
       {'role':'user','content':'{"question":"CLB nào có nhiều cầu thủ nhất?","params":{}}'},
       {'role':'assistant','content':'{"tool":"top_clubs","args":{}}'},
       {'role':'user','content':'{"question":"Tính PageRank của mạng đồng đội","params":{}}'},
       {'role':'assistant','content':'{"tool":"pagerank_teammates","args":{}}'},
       {'role': 'user', 'content': json.dumps({'question': question, 'params': params}, ensure_ascii=False)}], json_output=True)
    plan = json.loads(r['content'])
    allowed = {'count_players': {'club'}, 'top_clubs': set(), 'pagerank_teammates': set()}
    name, args = plan.get('tool'), plan.get('args', {})
    if name not in allowed or set(args) != allowed[name]:
        raise ValueError('Unsupported tool or arguments')
    if name in {'top_clubs','pagerank_teammates'} and params:
        raise ValueError('This tool supports the whole graph only, not an entity-scoped analysis')
    if any(value not in params.values() for value in args.values()):
        raise ValueError('Tool argument not supplied by entity linker')
    return name, args, r
