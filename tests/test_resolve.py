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


def test_name_compatibility_rejects_shared_club_with_a_different_name():
    from player_graph.resolve import name_compatible as ok
    # Confirmed errors of main_v6: same club, different person.
    assert not ok("Antony", ["James Wilson"], ["James Antony Wilson"])
    assert not ok("Beth England", ["Mohamed Salah"])
    assert not ok("Simon Jordan", ["Anthony Martial"], ["Anthony Jordan Martial"])
    assert not ok("Diaz", ["Luis Suarez"], ["Luis Alberto Suarez Diaz"])
    assert not ok("James Hill", ["Jimmy Hill"])
    assert not ok("David Onana", ["Andre Onana"])
    # Correct forms must survive: surname, compound surname, accents, full alias, shortened or single-word names.
    assert ok("Antony", ["Antony dos Santos"])
    assert ok("De Gea", ["David de Gea"])
    assert ok("Alexander-Arnold", ["Trent Alexander-Arnold"])
    assert ok("Odegaard", ["Martin \u00d8degaard"])
    assert ok("Solskjaer", ["Ole Gunnar Solskj\u00e6r"])
    assert ok("Chicharito", ["Javier Hernandez"], ["Chicharito"])
    assert ok("Felipe Scolari", ["Luiz Felipe Scolari"])
    assert ok("Luis Nani", ["Nani"])
    assert ok("Alex Disasi", ["Axel Disasi"])
    assert ok("Marcos Alonso", ["Marcos Alonso Mendoza"])
    assert ok("Alexis MacAllister", ["Alexis Mac Allister"])
    assert ok("Ibrahim Didier Ndong", ["Didier Ibrahim Ndong"])
    assert ok("Sir Alex", ["Alex Ferguson"])
    assert ok("Rodrigo Moreno", ["Rodrigo"])
    assert ok("Beto", ["Norberto Bercique Gomes Betuncal"], ["Beto"])
    assert not ok("Luiz Diaz", ["Luis Suarez"], ["Luis Alberto Suarez Diaz"])
    assert not ok("Rodrigo", ["Rodri"])


def test_member_search_skips_incompatible_candidate_known_in_corpus():
    wd = FakeWikidata(search={"James Wilson": [{"id": "Q1", "label": "James Wilson", "description": "English footballer"}]},
                      members={("Antony", "MU"): ["Q1", "Q2"]}, teams={"Q1": {"MU"}, "Q2": {"MU"}},
                      labels={"Q1": "James Wilson", "Q2": "Antony dos Santos"})
    out = link_people({"James Wilson": {"MU"}, "Antony": {"MU"}}, wd)
    assert out["Antony"]["qid"] == "Q2"


def test_roster_ignores_alias_sharing_no_word_with_label():
    r = roster_with([("Q7", "Mykhailo Mudryk", {"CHE"})])
    r.full["djordje petrovic"].add("Q7")
    assert r.match("Djordje Petrovic", {"CHE"}) is None


def test_semantic_regressions_from_review():
    from player_graph.resolve import semantic_check

    def a(rel, obj, ev, obj_type="Club"):
        return {"relation_type": rel, "object": {"name": obj, "type": obj_type}, "evidence_text": ev}
    # The destination after "to/sang" is not the source; the source of another player in a swap does not count.
    assert semantic_check(a("TRANSFERRED_TO", "Arsenal", "Rice moved from Chelsea to Arsenal.")) is None
    assert semantic_check(a("TRANSFERRED_TO", "Arsenal", "Rice chuy\u1ec3n t\u1eeb Chelsea sang Arsenal.")) is None
    assert semantic_check(a("LOANED_TO", "Chelsea", "Real signed Courtois from Chelsea with Kovacic going the other way on loan.")) is None
    # A real source club is still rejected.
    assert semantic_check(a("TRANSFERRED_TO", "Chelsea", "Arsenal signed Havertz from Chelsea.")) == "destination_club_is_source_in_evidence"
    # Resignation wording and whose dismissal it is.
    assert semantic_check(a("RESIGNED_FROM", "Crystal Palace", "Allardyce was stepping down as Crystal Palace manager.")) is None
    assert semantic_check(a("RESIGNED_FROM", "Wigan", "Paul Cook resigned from Wigan after Watford sacked Nigel Pearson.")) is None
    assert semantic_check(a("RESIGNED_FROM", "Bournemouth", "Howe quy\u1ebft \u0111\u1ecbnh ra \u0111i tr\u01b0\u1edbc khi b\u1ecb sa th\u1ea3i.")) is None
    assert semantic_check(a("RESIGNED_FROM", "Chelsea", "Sarri resigned after he was pushed out by the board.")) == "resignation_not_stated_in_evidence"
    # Talks that are denied or only planned are not negotiations.
    for ev in ["Manchester United have not held talks with Pogba.", "Manchester United will begin talks with Pogba next week."]:
        assert semantic_check(a("NEGOTIATES_TO_SIGN", "Pogba", ev, "Person")) == "no_actual_negotiation_in_evidence"
    assert semantic_check(a("NEGOTIATES_TO_SIGN", "Pogba", "Man Utd in talks with Pogba over new contract.", "Person")) is None
    # "About to finish talks" means talks are under way.
    assert semantic_check(a("NEGOTIATES_TO_SIGN", "Enzo", "Chelsea sắp hoàn tất đàm phán để mua Enzo.", "Person")) is None


def test_frequent_given_name_alone_does_not_pick_a_person():
    from player_graph.resolve import name_compatible as ok
    common = {"james", "gabriel", "rodrigo"}
    assert not ok("James", ["James Wilson"], ["James"], common)
    assert not ok("Rodrigo", ["Rodrigo Bentancur"], ["Rodrigo"], common)
    assert ok("James", ["Reece James"], (), common)  # the surname still identifies
    assert ok("Rodrigo", ["Rodrigo"], (), common)  # the person's only name
    assert ok("Alisson", ["Alisson Becker"], (), common)  # rare given name
    r = roster_with([("Q1", "James Wilson", {"MU"}), ("Q2", "Alisson Becker", {"LIV"})])
    r.names = {"Q1": ["James"], "Q2": ["Alisson"]}
    r.common_given = {"james"}
    r.full["james"].add("Q1"); r.full["alisson"].add("Q2")
    assert r.match("James", {"MU"}) is None
    assert r.match("Alisson", {"LIV"}) == ("Q2", "wikidata_roster_with_club")


def test_year_check_spares_a_name_that_is_the_persons_own_label():
    from player_graph.resolve import link_with_roster

    class Years(FakeWikidata):
        def claims(self, ids):
            return {q: {"label": None, "labels": [], "teams": set(), "born": 1890 if q == "Q2" else 1972} for q in ids}
    r = roster_with([("Q1", "Rivaldo", {"BAR"}), ("Q2", "John McConnell", {"LIV"})])
    r.names, r.common_given = {}, set()
    legend, old = ("Rivaldo", frozenset({"BAR"}), 2026), ("McConnell", frozenset({"LIV"}), 2026)
    out = link_with_roster({legend: {"BAR"}, old: {"LIV"}}, r, Years(), {legend: (2026, "Player"), old: (2026, "Player")})
    assert out[legend]["qid"] == "Q1"
    assert out[old]["qid"] is None


def test_edge_is_filtered_only_with_one_evidence_and_no_wikidata_club():
    from player_graph.resolve import unconfirmed_single_evidence as flagged
    teams = {"p": {"teams": {"MU"}}}
    edge = lambda rel, s, o, n=1: {"relation_type": rel, "subject_id": s, "object_id": o, "evidence_count": n}
    assert flagged(edge("PLAYS_FOR", "p", "BAR"), teams)
    assert not flagged(edge("PLAYS_FOR", "p", "MU"), teams)
    assert not flagged(edge("PLAYS_FOR", "p", "BAR", 2), teams)  # two sentences agree
    assert not flagged(edge("PLAYS_FOR", "unknown", "BAR"), teams)  # no Wikidata record to compare with
    assert flagged(edge("DISMISSED_MANAGER", "BAR", "p"), teams)  # the person is the object here
    assert not flagged(edge("CRITICIZES", "p", "BAR"), teams)  # not a membership claim


def test_match_members_uses_whole_words_then_close_spelling():
    from player_graph.resolve import match_members
    row = lambda q, label, alts="": {"p": {"value": f"http://www.wikidata.org/entity/{q}"}, "pLabel": {"value": label}, "alts": {"value": alts}}
    rows = [row("Q1", "Harry Maguire"), row("Q2", "Wilfried Zaha", "Zaha"), row("Q3", "Ryan Giggs")]
    assert match_members("Maguire", rows) == ["Q1"]
    assert match_members("Wilfred Zaha", rows) == ["Q2"]
    assert match_members("Wilfred Giggs", rows) == []
    assert match_members("", rows) == []


def test_failed_club_lookup_is_recorded_not_treated_as_empty(monkeypatch):
    from player_graph import resolve
    monkeypatch.setattr(resolve, "fetch_club_rows", lambda qid, offline=False: None)
    wd = resolve.Wikidata(offline=True)
    assert wd.member_search("Maguire", "Q18656") == []
    assert wd.failed_clubs == {"Q18656"}


def test_name_in_club_list_skips_the_search_per_name():
    from player_graph.resolve import link_people
    calls = []
    class Local(FakeWikidata):
        def search_cached(self, name):
            return False
        def search(self, name):
            calls.append(name)
            return []
    wd = Local(members={("Harry Maguire", "MU"): ["Q9"]}, labels={"Q9": "Harry Maguire"})
    out = link_people({"Harry Maguire": {"MU"}, "Nobody Known": {"MU"}}, wd)
    assert calls == ["Nobody Known"]
    assert out["Harry Maguire"]["qid"] == "Q9"


def test_calls_stop_after_repeated_failures(monkeypatch):
    from player_graph import resolve
    def boom(*a, **k):
        raise AssertionError("no request expected")
    monkeypatch.setattr(resolve.requests, "get", boom)
    wd = resolve.Wikidata()
    wd.failed_in_a_row = resolve.MAX_FAILURES_IN_A_ROW
    assert wd.get({"action": "test_never_cached"}) == {}
    assert wd.blocked and wd.failures == 1
