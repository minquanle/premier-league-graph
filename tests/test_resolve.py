from player_graph.resolve import expand_in_article, link_people


class FakeWikidata:
    """Offline stand-in: search results, club rosters and labels are fixed in the test."""

    def __init__(self, search=None, members=None, teams=None, labels=None):
        self._search, self._members = search or {}, members or {}
        self._teams, self._labels = teams or {}, labels or {}

    def search(self, name):
        return self._search.get(name, [])

    def member_search(self, term, club):
        return self._members.get((term, club), [])

    def claims(self, ids):
        return {q: {"label": self._labels.get(q), "teams": self._teams.get(q, set())} for q in ids}


def person(name):
    return {"name": name, "type": "Person", "role": "Player", "club_scope": None}


def test_short_name_expanded_only_when_unique_in_article():
    raw = [{"article_id": "a", "payload": {"entities": [{"name": "Michael Carrick", "type": "Person"}]}},
           {"article_id": "b", "payload": {"entities": [{"name": "Bruno Fernandes", "type": "Person"}, {"name": "Enzo Fernandes", "type": "Person"}]}}]
    rows = [{"article_id": "a", "subject": person("Carrick"), "object": person("X Y")},
            {"article_id": "b", "subject": person("Fernandes"), "object": person("X Y")}]
    mapping = expand_in_article(rows, raw)
    assert mapping[("a", "carrick")] == "Michael Carrick"
    assert ("b", "fernandes") not in mapping


def test_club_history_beats_namesake():
    wd = FakeWikidata(
        search={"Bruno Fernandes": [{"id": "Q1", "label": "Bruno Fernandes", "description": "Guinea-Bissau footballer"},
                                    {"id": "Q2", "label": "Bruno Fernandes", "description": "Portuguese association football player"}]},
        teams={"Q2": {"MU"}}, labels={"Q2": "Bruno Fernandes"})
    out = link_people({"Bruno Fernandes": {"MU"}}, wd)
    assert out["Bruno Fernandes"]["qid"] == "Q2"


def test_surname_resolved_through_club_roster_and_ambiguity_kept_local():
    wd = FakeWikidata(members={("Maguire", "MU"): ["Q9"], ("Smith", "MU"): ["Q5", "Q6"]}, labels={"Q9": "Harry Maguire"})
    out = link_people({"Maguire": {"MU"}, "Smith": {"MU"}, "Nobody": set()}, wd)
    assert out["Maguire"] == {"entity_id": "Q9", "qid": "Q9", "canonical_name": "Harry Maguire", "resolution_method": "wikidata_club_member_search"}
    assert out["Smith"]["qid"] is None and out["Nobody"]["qid"] is None


def test_ambiguous_surname_prefers_person_already_named_in_full():
    wd = FakeWikidata(search={"Michael Carrick": [{"id": "Q29", "label": "Michael Carrick", "description": "English footballer"}]},
                      members={("Carrick", "MU"): ["Q29", "Q80"]}, teams={"Q29": {"MU"}}, labels={"Q29": "Michael Carrick"})
    out = link_people({"Michael Carrick": {"MU"}, "Carrick": {"MU"}}, wd)
    assert out["Carrick"]["qid"] == out["Michael Carrick"]["qid"] == "Q29"


def roster_with(people):
    """Roster built from (qid, label, clubs) without network access."""
    from collections import defaultdict
    from player_graph.common import normalize
    from player_graph.resolve import Roster
    r = Roster.__new__(Roster)
    r.label, r.clubs, r.full, r.surname, r.failed = {}, defaultdict(set), defaultdict(set), defaultdict(set), []
    for qid, label, clubs in people:
        r.label[qid] = label
        r.clubs[qid] |= set(clubs)
        r.full[normalize(label)].add(qid)
        r.surname[normalize(label).split()[-1]].add(qid)
    return r


def test_roster_matches_surname_by_club_and_spelling_variant():
    r = roster_with([("Q1", "Harry Maguire", {"MU"}), ("Q2", "Jacob Maguire", {"XX"}), ("Q3", "Wilfried Zaha", {"CP"})])
    assert r.match("Maguire", {"MU"}) == ("Q1", "wikidata_roster_with_club")
    assert r.match("Maguire", set()) is None
    assert r.match("Wilfred Zaha", {"CP"}) == ("Q3", "wikidata_roster_with_club")
    assert r.match("Harry Maguire", set()) == ("Q1", "wikidata_roster_unique_full_name")


def test_audit_cases_are_not_merged_into_the_wrong_person():
    from player_graph.resolve import plausible_in_year
    r = roster_with([("Q44297797", "Ferran Torres", {"VAL"}), ("Q62657", "Neto", {"JUV"}),
                     ("Q6247530", "John McConnell", {"LIV"}), ("Q6846031", "Mike Bernard", {"EVE"})])
    # E01: Fernando is not a spelling variant of Ferran.
    assert r.match("Fernando Torres", {"LIV", "CHE"}) is None
    # E04: the only "Neto" in an incomplete roster, with no club in common, is not accepted.
    assert r.match("Neto", {"CHE"}) is None
    # E02/E03: a surname plus a shared club still has to fit the story's year.
    assert r.match("McConnell", {"LIV"}) == ("Q6247530", "wikidata_roster_with_club")
    assert not plausible_in_year({"born": 1890, "died": 1947}, 2026, "Player")
    assert not plausible_in_year({"born": 1948}, 2019, "Player")
    assert plausible_in_year({"born": 1993}, 2019, "Player")
    assert plausible_in_year({"born": 1941}, 2013, "Coach")


def test_bare_surname_without_identity_stays_apart_per_club():
    from player_graph.resolve import local_identity
    a = local_identity(("Henderson", frozenset({"CHE"}), 2024), {"CHE"})
    b = local_identity(("Henderson", frozenset({"LIV"}), 2024), {"LIV"})
    c = local_identity(("Jordan Henderson", frozenset({"LIV"}), 2024), {"LIV"})
    assert a["entity_id"] != b["entity_id"] and c["entity_id"] == local_identity("Jordan Henderson", set())["entity_id"]


def test_semantic_checks_from_quality_audit():
    from player_graph.resolve import semantic_check

    def a(rel, obj, ev, obj_type="Club"):
        return {"relation_type": rel, "object": {"name": obj, "type": obj_type}, "evidence_text": ev}
    # R01: source club taken as destination.
    assert semantic_check(a("TRANSFERRED_TO", "Nottingham Forest", "Man City paid 50m to sign Elliot Anderson from Nottingham Forest.")) == "destination_club_is_source_in_evidence"
    assert semantic_check(a("TRANSFERRED_TO", "Arsenal", "Arsenal signed Declan Rice from West Ham United.")) is None
    assert semantic_check(a("TRANSFERRED_TO", "Arsenal", "Rice chuyển từ West Ham sang Arsenal.")) is None
    # R02: pursuit is not negotiation.
    assert semantic_check(a("NEGOTIATES_TO_SIGN", "Elliot Anderson", "MU đã theo đuổi Elliot Anderson nhưng không thành.", "Person")) == "no_actual_negotiation_in_evidence"
    assert semantic_check(a("NEGOTIATES_TO_SIGN", "Pogba", "Man Utd in talks with Pogba over new contract.", "Person")) is None
    # R03: leaving or being pushed out is not resigning.
    assert semantic_check(a("RESIGNED_FROM", "Wigan", "Paul Cook has left his role at Wigan.")) == "resignation_not_stated_in_evidence"
    assert semantic_check(a("RESIGNED_FROM", "Chelsea", "Sarri resigned after he was pushed out by the board.")) == "resignation_not_stated_in_evidence"
    assert semantic_check(a("RESIGNED_FROM", "England", "Phil Neville stepped down as England manager.")) is None


def test_womens_football_evidence_is_out_of_scope():
    from player_graph.resolve import semantic_check
    a = lambda ev: {"relation_type": "PLAYS_FOR", "object": {"name": "Chelsea", "type": "Club"}, "evidence_text": ev}
    assert semantic_check(a("Beth England scored as Chelsea won in the FA Women's Super League.")) == "womens_football_in_evidence"
    assert semantic_check(a("Sam Kerr ghi bàn giúp nữ Chelsea thắng.")) == "womens_football_in_evidence"
    assert semantic_check(a("Cole Palmer scored twice for Chelsea.")) is None
