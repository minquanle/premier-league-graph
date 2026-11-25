"""Whole-picture view of one node: its relations, its place in the teammate network, and the evidence.

Network measures are computed once per graph connection on the TEAMMATE_OF network (the network
analysed in the report): degree, PageRank, k-core number, Leiden community and that community's
nucleus (its own innermost k-core).
"""
import networkx as nx

from .analytics import _CACHE, _teammate_network

DERIVED = ['TEAMMATE_OF', 'PLAYED_UNDER', 'TRANSFER_FLOW']


def network_measures(graph):
    key = ('measures', id(graph))
    if key not in _CACHE:
        from ..algorithms import largest_component, leiden
        network, names = _teammate_network(graph)
        pagerank = nx.pagerank(network, alpha=0.85)
        core = nx.core_number(network)
        groups = sorted(leiden(largest_component(network), seed=42), key=len, reverse=True)
        community = {n: i for i, g in enumerate(groups) for n in g}
        by_rank = lambda scores: {n: i + 1 for i, (n, _) in enumerate(sorted(scores.items(), key=lambda kv: (-kv[1], kv[0])))}
        _CACHE[key] = {'network': network, 'names': names, 'ids': {v: k for k, v in names.items()},
                       'pagerank': pagerank, 'pagerank_rank': by_rank(pagerank),
                       'degree': dict(network.degree()), 'degree_rank': by_rank(dict(network.degree())),
                       'core': core, 'k_max': max(core.values()), 'groups': groups, 'community': community}
    return _CACHE[key]


def nucleus(m, group_index, top=8):
    group = m['groups'][group_index]
    sub = m['network'].subgraph(group)
    inner = nx.core_number(sub)
    k = max(inner.values())
    members = sorted((n for n, v in inner.items() if v == k), key=lambda n: (-sub.degree(n), m['names'].get(n, n)))
    return {'k': k, 'size': len(members), 'members': [m['names'].get(n, n) for n in members[:top]]}


def social_position(graph, name):
    """Network measures of a person in the teammate network, or None for clubs and isolated people."""
    m = network_measures(graph)
    node = m['ids'].get(name)
    if node is None:
        return None
    n = m['network'].number_of_nodes()
    group = m['community'].get(node)
    return {'people_in_network': n, 'degree': m['degree'][node], 'degree_rank': m['degree_rank'][node],
            'pagerank': m['pagerank'][node], 'pagerank_rank': m['pagerank_rank'][node],
            'core': m['core'][node], 'k_max': m['k_max'], 'in_main_core': m['core'][node] == m['k_max'],
            'community': None if group is None else {'index': group + 1, 'size': len(m['groups'][group]),
                                                     'nucleus': nucleus(m, group)}}


def entity(graph, name):
    rows = graph.run('''MATCH (n:FootballEntity {canonical_name:$name})
        RETURN n.canonical_name AS name, n.qid AS qid, n.entity_id AS entity_id, n.aliases AS aliases,
               [l IN labels(n) WHERE l IN ['Club','Player','Coach','Agent']] AS roles,
               'Club' IN labels(n) AS is_club''', {'name': name})
    return rows[0] if rows else None


def relations(graph, name, limit=200):
    """Extracted (news) relations touching the node, most evidenced first."""
    return graph.run('''MATCH (p:FootballEntity {canonical_name:$name})-[r]-(x:FootballEntity)
        WHERE NOT type(r) IN $derived
        RETURN type(r) AS relation, startNode(r) = p AS outgoing, x.canonical_name AS other,
               'Club' IN labels(x) AS other_is_club, toInteger(r.evidence_count) AS evidence,
               r.first_publication AS first_seen, r.last_publication AS last_seen
        ORDER BY evidence DESC, other LIMIT $limit''', {'name': name, 'derived': DERIVED, 'limit': limit})


def derived_counts(graph, name):
    rows = graph.run('''MATCH (p:FootballEntity {canonical_name:$name})
        RETURN COUNT { (p)-[:TEAMMATE_OF]-() } AS teammates,
               COUNT { (p)-[:PLAYED_UNDER]->() } AS coaches,
               COUNT { (p)<-[:PLAYED_UNDER]-() } AS players_coached,
               COUNT { (p)<-[:PLAYS_FOR]-() } AS players,
               COUNT { (p)-[:TRANSFER_FLOW]->() } AS flow_out,
               COUNT { (p)<-[:TRANSFER_FLOW]-() } AS flow_in''', {'name': name})
    return rows[0] if rows else {}


def evidence(graph, name, limit=8):
    """Latest news sentences behind the node's relations, with their source articles."""
    return graph.run('''MATCH (p:FootballEntity {canonical_name:$name})-[r]-(x:FootballEntity)
        WHERE r.assertion_ids IS NOT NULL
        UNWIND r.assertion_ids[..2] AS aid
        MATCH (e:ExtractionAssertion {assertion_id: aid})-[:EVIDENCED_BY]->(a:SourceArticle)
        RETURN DISTINCT type(r) AS relation, x.canonical_name AS other, e.evidence_text AS quote,
               a.title AS title, a.canonical_url AS url, a.published_at AS published
        ORDER BY published DESC LIMIT $limit''', {'name': name, 'limit': limit})


def ego_edges(graph, name, teammates=12):
    """Edges for drawing: every news relation plus the most central teammates (by PageRank)."""
    edges = [(name if r['outgoing'] else r['other'], r['relation'], r['other'] if r['outgoing'] else name)
             for r in relations(graph, name, limit=60)]
    m = network_measures(graph)
    node = m['ids'].get(name)
    if node is not None:
        mates = sorted(m['network'].neighbors(node), key=lambda n: -m['pagerank'][n])[:teammates]
        edges += [(name, 'TEAMMATE_OF', m['names'].get(n, n)) for n in mates]
    return edges


def node_kinds(graph, names):
    rows = graph.run('''MATCH (n:FootballEntity) WHERE n.canonical_name IN $names
        RETURN n.canonical_name AS name, [l IN labels(n) WHERE l IN ['Club','Coach','Agent','Player']] AS labels''',
                     {'names': list(names)})
    order = ['Club', 'Coach', 'Agent', 'Player']
    return {r['name']: next((l for l in order if l in r['labels']), 'Person') for r in rows}


def plan_edges(graph, plan, params, types, answers, limit=80):
    """Edges along the planned paths that end at the shown answers, for drawing the reasoning."""
    from .agent import HOPS
    edges = []
    for chain in plan['chains']:
        pattern, hop_rels = f"(s:{types[chain['from']]} {{canonical_name: ${chain['from']}}})", []
        for h, hop in enumerate(chain['hops']):
            rel, direction = HOPS[hop][:2]
            arrow = {'out': f'-[r{h}:{rel}]->', 'in': f'<-[r{h}:{rel}]-', 'any': f'-[r{h}:{rel}]-'}[direction]
            pattern += arrow + f'(n{h}:{HOPS[hop][3]})'
            hop_rels.append(h)
        last = len(chain['hops']) - 1
        rows = graph.run(f'''MATCH path = {pattern} WHERE n{last}.canonical_name IN $answers
            RETURN [n IN nodes(path) | n.canonical_name] AS names, [r IN relationships(path) | type(r)] AS rels,
                   [r IN relationships(path) | startNode(r).canonical_name] AS starts LIMIT $limit''',
                         {**params, 'answers': list(answers), 'limit': limit})
        for row in rows:
            for i, rel in enumerate(row['rels']):
                a, b = row['names'][i], row['names'][i + 1]
                edges.append((a, rel, b) if row['starts'][i] == a else (b, rel, a))
    return list(dict.fromkeys(edges))
