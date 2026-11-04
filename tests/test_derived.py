import networkx as nx

from player_graph.algorithms import _nmi, leiden, small_world
from player_graph.derived import overlap, periods, played_under, teammates, transfer_flow


def row(player=(), coach=(), born=1990):
    return {"born": born, "player": [list(s) for s in player], "coach": [list(s) for s in coach]}


def test_open_period_ends_at_next_club_or_stays_open_only_if_active():
    active = periods(row([("A", 2015.0, None), ("B", 2020.0, 2023.0)]), "player")
    assert active[0] == ("A", 2015.0, 2020.0)
    retired = periods(row([("A", 1990.0, None)], born=1965), "player")
    assert retired == [("A", 1990.0, 1993.0)]
    assert periods(row([("A", None, 2000.0)]), "player") == []  # no start date, no period


def test_teammates_need_overlapping_time_at_the_same_club():
    players = {"old": [("MU", 1992.0, 1997.0)], "young": [("MU", 2021.0, 2025.0)], "mate": [("MU", 2024.0, 2026.0)], "other": [("CHE", 2021.0, 2025.0)]}
    pairs = teammates(players)
    assert list(pairs) == [("mate", "young")]
    assert pairs[("mate", "young")]["MU"] == 1.0


def test_teammates_ignore_a_few_weeks_together():
    assert not teammates({"a": [("X", 2020.0, 2021.0)], "b": [("X", 2020.95, 2022.0)]})


def test_played_under_needs_same_club_and_period():
    pairs = played_under({"p": [("MU", 2021.0, 2025.0)]}, {"tenhag": [("MU", 2022.0, 2024.0)], "ferguson": [("MU", 1986.0, 2013.0)], "pep": [("MCI", 2016.0, 2026.0)]})
    assert list(pairs) == [("p", "tenhag")]


def test_transfer_flow_counts_consecutive_clubs_in_scope():
    players = {"a": [("X", 2010.0, 2012.0), ("Y", 2012.0, 2015.0)], "b": [("X", 2011.0, 2013.0), ("NT", 2012.0, 2020.0), ("Y", 2013.0, 2016.0)]}
    assert transfer_flow(players, {"X", "Y"}) == {("X", "Y"): 2}


def test_overlap_is_negative_without_common_time():
    assert overlap(("A", 2000, 2001), ("A", 2005, 2006)) < 0


def test_small_world_detects_clustered_graph_against_random():
    result = small_world(nx.watts_strogatz_graph(200, 8, 0.05, seed=1))
    assert result["C"] > result["C_random"] and result["sigma"] > 1 and result["is_small_world_by_sigma"]


def test_leiden_splits_two_cliques_and_nmi_scores_a_perfect_partition():
    graph = nx.disjoint_union(nx.complete_graph(8), nx.complete_graph(8))
    graph.add_edge(0, 8)
    groups = leiden(graph)
    assert sorted(len(g) for g in groups) == [8, 8]
    truth = {n: ("a" if n < 8 else "b") for n in graph}
    assert abs(_nmi(groups, truth) - 1) < 1e-9
