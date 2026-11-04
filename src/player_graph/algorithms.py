"""Network analysis on the derived networks: small-world measures, communities, central people.

sigma and omega use comparison graphs built directly: networkx's sigma/omega rewire the graph many times, which is not
practical at tens of thousands of edges. The lattice values for omega are the standard formula for a ring lattice,
so omega here is an approximation and is reported as one.
"""
from __future__ import annotations

import math
import random
import statistics
from collections import Counter

import networkx as nx

from .common import DATA, REPORTS, now, read_jsonl, write_json

RANDOM_GRAPHS = 10


def largest_component(graph):
    return graph.subgraph(max(nx.connected_components(graph), key=len)).copy()


def small_world(graph, seed=42):
    """L and C of the largest component against random graphs with the same nodes and edges."""
    giant = largest_component(graph)
    n, m = giant.number_of_nodes(), giant.number_of_edges()
    k = 2 * m / n
    C, L = nx.average_clustering(giant), nx.average_shortest_path_length(giant)
    Cr, Lr = [], []
    for i in range(RANDOM_GRAPHS):
        random_graph = largest_component(nx.gnm_random_graph(n, m, seed=seed + i))
        Cr.append(nx.average_clustering(random_graph))
        Lr.append(nx.average_shortest_path_length(random_graph))
    c_r, l_r = statistics.mean(Cr), statistics.mean(Lr)
    c_lattice = 3 * (k - 2) / (4 * (k - 1)) if k > 2 else 0
    return {"nodes": n, "edges": m, "average_degree": round(k, 2), "density": round(nx.density(giant), 4), "share_of_nodes_in_largest_component": round(n / graph.number_of_nodes(), 3),
            "L": round(L, 3), "C": round(C, 3), "L_random": round(l_r, 3), "L_random_std": round(statistics.pstdev(Lr), 4),
            "C_random": round(c_r, 4), "C_random_std": round(statistics.pstdev(Cr), 4), "random_graphs": RANDOM_GRAPHS,
            "sigma": round((C / c_r) / (L / l_r), 2) if c_r else None, "omega_approx": round(l_r / L - C / c_lattice, 3) if c_lattice else None,
            "is_small_world_by_sigma": bool(c_r and (C / c_r) / (L / l_r) > 1)}


def leiden(graph, seed=42):
    import igraph as ig
    import leidenalg
    nodes = list(graph)
    index = {node: i for i, node in enumerate(nodes)}
    g = ig.Graph(n=len(nodes), edges=[(index[a], index[b]) for a, b in graph.edges()])
    partition = leidenalg.find_partition(g, leidenalg.ModularityVertexPartition, seed=seed)
    return [{nodes[i] for i in part} for part in partition]


def communities(graph, truth=None, name=None, seed=42):
    giant = largest_component(graph)
    methods = {"louvain": nx.community.louvain_communities(giant, seed=seed), "leiden": leiden(giant, seed),
               "label_propagation": list(nx.community.asyn_lpa_communities(giant, seed=seed))}
    out = {}
    for method, groups in methods.items():
        row = {"communities": len(groups), "modularity": round(nx.community.modularity(giant, groups), 4), "largest": sorted((len(g) for g in groups), reverse=True)[:8]}
        if truth:
            row["nmi_with_main_club"] = round(_nmi(groups, truth), 3)
        out[method] = row
    best = methods["leiden"]
    described = []
    for group in sorted(best, key=len, reverse=True)[:6]:
        sub = giant.subgraph(group)
        top = sorted(sub.degree, key=lambda x: -x[1])[:6]
        clubs = Counter(truth[p] for p in group if truth and p in truth).most_common(3)
        described.append({"size": len(group), "density": round(nx.density(sub), 3), "people": [name(p) for p, _ in top] if name else [p for p, _ in top],
                          "main_clubs": [(name(c) if name else c, n) for c, n in clubs]})
    out["largest_leiden_communities"] = described
    return out


def _nmi(groups, truth):
    labels = {node: i for i, group in enumerate(groups) for node in group}
    nodes = [n for n in labels if n in truth]
    total = len(nodes)
    if not total:
        return 0.0
    a, b, joint = Counter(labels[n] for n in nodes), Counter(truth[n] for n in nodes), Counter((labels[n], truth[n]) for n in nodes)
    mutual = sum(v / total * math.log((v / total) / (a[x] / total * b[y] / total)) for (x, y), v in joint.items())
    entropy = lambda counts: -sum(v / total * math.log(v / total) for v in counts.values())
    denom = (entropy(a) + entropy(b)) / 2
    return mutual / denom if denom else 0.0


def central(graph, name, top=10, seed=42):
    pagerank = nx.pagerank(graph)
    degree = dict(graph.degree())
    betweenness = nx.betweenness_centrality(graph, k=min(300, graph.number_of_nodes()), seed=seed)
    ranked = lambda scores, digits=4: [(name(n), round(v, digits)) for n, v in sorted(scores.items(), key=lambda x: (-x[1], str(x[0])))[:top]]
    return {"degree": ranked(degree, 0), "pagerank": ranked(pagerank), "betweenness_sampled_300_sources": ranked(betweenness)}


def run(run_name):
    folder = DATA / "graph" / run_name
    entities = {e["entity_id"]: e for e in read_jsonl(folder / "entities.jsonl")}
    name = lambda i: entities.get(i, {}).get("canonical_name", i)
    mates = read_jsonl(folder / "derived/teammate_of.jsonl")
    under = read_jsonl(folder / "derived/played_under.jsonl")
    flow = read_jsonl(folder / "derived/transfer_flow.jsonl")
    extracted = read_jsonl(folder / "relations.jsonl")

    teammate = nx.Graph()
    teammate.add_edges_from((e["subject_id"], e["object_id"]) for e in mates)
    # The club each player spent most shared time at, to ask whether communities follow clubs.
    club_years = {}
    for e in mates:
        for person in (e["subject_id"], e["object_id"]):
            for club in e["clubs"]:
                club_years.setdefault(person, Counter())[club] += 1
    main_club = {p: c.most_common(1)[0][0] for p, c in club_years.items()}
    people = nx.Graph(teammate)
    people.add_edges_from((e["subject_id"], e["object_id"]) for e in under)
    clubs = nx.DiGraph()
    for e in flow:
        clubs.add_edge(e["subject_id"], e["object_id"], weight=e["moves"])
    base = nx.Graph()
    base.add_edges_from((r["subject_id"], r["object_id"]) for r in extracted)

    report = {"created_at": now(), "run_name": run_name,
              "extracted_graph": {"nodes": base.number_of_nodes(), "edges_undirected": base.number_of_edges(), "average_clustering": round(nx.average_clustering(base), 4),
                                  "note": "Gần như hai phía (người nối CLB), nên không đo small-world trên mạng này"},
              "teammate": {"small_world": small_world(teammate), "communities": communities(teammate, main_club, name), "central": central(teammate, name)},
              "teammate_and_played_under": {"small_world": small_world(people), "communities": communities(people, main_club, name), "central": central(people, name)}}
    undirected_clubs = nx.Graph()
    for a, b, d in clubs.edges(data=True):
        weight = d["weight"] + (undirected_clubs[a][b]["weight"] if undirected_clubs.has_edge(a, b) else 0)
        undirected_clubs.add_edge(a, b, weight=weight)
    report["transfer_flow"] = {"small_world": small_world(undirected_clubs), "communities": communities(undirected_clubs, None, name),
                               "top_routes": [(name(a), name(b), d["weight"]) for a, b, d in sorted(clubs.edges(data=True), key=lambda x: -x[2]["weight"])[:10]],
                               "most_players_in": [(name(c), v) for c, v in sorted(clubs.in_degree(weight="weight"), key=lambda x: -x[1])[:10]],
                               "most_players_out": [(name(c), v) for c, v in sorted(clubs.out_degree(weight="weight"), key=lambda x: -x[1])[:10]],
                               "central": central(undirected_clubs, name)}
    write_json(REPORTS / f"sna_{run_name}.json", report)
    return report
