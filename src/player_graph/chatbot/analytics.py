"""Explicit allow-listed computation tools, not arbitrary generated Python execution."""
import networkx as nx

_CACHE = {}


def _teammate_network(graph):
    """Undirected TEAMMATE_OF network with display names, cached per graph connection."""
    key = id(graph)
    if key not in _CACHE:
        rows = graph.run('''MATCH (p:Person)-[:TEAMMATE_OF]-(q:Person)
            RETURN p.entity_id AS p, q.entity_id AS q, p.canonical_name AS name''')
        network, names = nx.Graph(), {}
        for row in rows:
            network.add_edge(row['p'], row['q'])
            names[row['p']] = row['name']
        network.remove_edges_from(nx.selfloop_edges(network))
        _CACHE[key] = (network, names)
    return _CACHE[key]


def _ranked(nodes, degree, names, top=10):
    return sorted(nodes, key=lambda n: (-degree(n), names.get(n, n)))[:top]


def tools(graph, name, args):
    if name == 'count_players':
        return graph.run('''MATCH (p:Person)-[:PLAYS_FOR]->(c:Club {canonical_name:$club})
            RETURN count(DISTINCT p) AS count''', {'club': args['club']})
    if name == 'top_clubs':
        return graph.run('''MATCH (p:Person)-[:PLAYS_FOR]->(c:Club)
            RETURN c.canonical_name AS name,count(DISTINCT p) AS count
            ORDER BY count DESC,name LIMIT 10''')
    if name == 'pagerank_teammates':
        network, names = _teammate_network(graph)
        scores = nx.pagerank(network, alpha=0.85)
        return [{'name': names.get(node, node), 'score': score}
                for node, score in sorted(scores.items(), key=lambda r: (-r[1], names.get(r[0], r[0])))[:10]]
    if name == 'network_core':
        # The network's nucleus: the k-core with the largest k, members ranked by degree.
        network, names = _teammate_network(graph)
        core = nx.core_number(network)
        k = max(core.values())
        members = [n for n, v in core.items() if v == k]
        return [{'name': names.get(n, n), 'count': k} for n in _ranked(members, network.degree, names)]
    if name == 'community_nucleus':
        # The person's Leiden community, and that community's own innermost k-core.
        from ..algorithms import largest_component, leiden
        network, names = _teammate_network(graph)
        node = next((n for n, v in names.items() if v == args['person']), None)
        if node is None:
            return []
        if ('leiden', id(graph)) not in _CACHE:
            _CACHE[('leiden', id(graph))] = leiden(largest_component(network), seed=42)
        group = next((g for g in _CACHE[('leiden', id(graph))] if node in g), None)
        if group is None:
            return []
        sub = network.subgraph(group)
        inner = nx.core_number(sub)
        k = max(inner.values())
        nucleus = [n for n, v in inner.items() if v == k]
        return [{'name': names.get(n, n), 'count': sub.degree(n)} for n in _ranked(nucleus, sub.degree, names)]
    if name == 'path_between':
        rows = graph.run('''MATCH (a:Person {canonical_name:$person}), (b:Person {canonical_name:$other})
            MATCH p = shortestPath((a)-[:TEAMMATE_OF*..6]-(b))
            RETURN [n IN nodes(p) | n.canonical_name] AS names''', {'person': args['person'], 'other': args['other']})
        return [{'name': n, 'count': i} for i, n in enumerate(rows[0]['names'])] if rows else []
    raise ValueError('Unknown analytics tool')


ALLOWED = {'count_players': {'club'}, 'top_clubs': set(), 'pagerank_teammates': set(), 'network_core': set(),
           'community_nucleus': {'person'}, 'path_between': {'person', 'other'}}
WHOLE_GRAPH = {'top_clubs', 'pagerank_teammates', 'network_core'}


def choose_tool(client, question, params):
    import json
    r = client.complete([{'role': 'system', 'content': '''Select one analytics tool and return JSON {"tool":"...","args":{...}}.
Available: count_players(club): count distinct people with PLAYS_FOR to club.
top_clubs(): top 10 clubs by count distinct PLAYS_FOR people.
pagerank_teammates(): top 10 PageRank nodes of the undirected full TEAMMATE_OF graph, damping 0.85.
network_core(): the nucleus (largest k-core) of the whole teammate network.
community_nucleus(person): the community this person belongs to and that community's nucleus (inner k-core).
path_between(person, other): shortest chain of teammates linking two people.
Only use supplied canonical parameters. No code, no other tools. If unsupported return {"tool":"unsupported","args":{}}.'''
    }, {'role':'user','content':'{"question":"Có bao nhiêu cầu thủ ở Arsenal?","params":{"club":"Arsenal"}}'},
       {'role':'assistant','content':'{"tool":"count_players","args":{"club":"Arsenal"}}'},
       {'role':'user','content':'{"question":"CLB nào có nhiều cầu thủ nhất?","params":{}}'},
       {'role':'assistant','content':'{"tool":"top_clubs","args":{}}'},
       {'role':'user','content':'{"question":"Tính PageRank của mạng đồng đội","params":{}}'},
       {'role':'assistant','content':'{"tool":"pagerank_teammates","args":{}}'},
       {'role':'user','content':'{"question":"Hạt nhân của mạng đồng đội gồm những ai?","params":{}}'},
       {'role':'assistant','content':'{"tool":"network_core","args":{}}'},
       {'role':'user','content':'{"question":"Wayne Rooney thuộc nhóm nào, ai là cốt lõi của nhóm đó?","params":{"person":"Wayne Rooney"}}'},
       {'role':'assistant','content':'{"tool":"community_nucleus","args":{"person":"Wayne Rooney"}}'},
       {'role':'user','content':'{"question":"Frank Lampard và Steven Gerrard nối với nhau qua những ai?","params":{"person":"Frank Lampard","other":"Steven Gerrard"}}'},
       {'role':'assistant','content':'{"tool":"path_between","args":{"person":"Frank Lampard","other":"Steven Gerrard"}}'},
       {'role': 'user', 'content': json.dumps({'question': question, 'params': params}, ensure_ascii=False)}], json_output=True)
    plan = json.loads(r['content'])
    name, args = plan.get('tool'), plan.get('args', {})
    if name not in ALLOWED or set(args) != ALLOWED[name]:
        raise ValueError('Unsupported tool or arguments')
    if name in WHOLE_GRAPH and params:
        raise ValueError('This tool supports the whole graph only, not an entity-scoped analysis')
    if any(value not in params.values() for value in args.values()):
        raise ValueError('Tool argument not supplied by entity linker')
    return name, args, r
