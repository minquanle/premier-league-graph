import json

import pytest

from player_graph.common import canonical_url, normalize
from player_graph.corpus import article_links, parse_date, xml_locations
from player_graph.nlp import Mention, assertion_status, club_mentions, match_sentence, sentences


def person(text, sentence):
    i = sentence.index(text)
    return Mention(text, i, i+len(text), "Person", text, "person_"+normalize(text))


def relations(sentence, people):
    return match_sentence(sentence, [*club_mentions(sentence), *(person(p, sentence) for p in people)])


def test_aliases_and_longest_match():
    found = club_mentions("MU faced Manchester United and Man City.")
    assert [m.canonical for m in found] == ["Manchester United", "Manchester United", "Manchester City"]
    assert found[0].entity_id == found[1].entity_id
    assert not club_mentions("We are reading a real story.")


@pytest.mark.parametrize("sentence,people,relation,subject,obj", [
    ("Arsenal signed Nicolas Pepe from Lille.", ["Nicolas Pepe"], "TRANSFERRED_TO", "Nicolas Pepe", "Arsenal"),
    ("Nicolas Pepe joined Arsenal.", ["Nicolas Pepe"], "TRANSFERRED_TO", "Nicolas Pepe", "Arsenal"),
    ("Chelsea sacked Graham Potter.", ["Graham Potter"], "DISMISSED_MANAGER", "Chelsea", "Graham Potter"),
    ("Graham Potter was sacked by Chelsea.", ["Graham Potter"], "DISMISSED_MANAGER", "Chelsea", "Graham Potter"),
    ("Arsenal appointed Unai Emery as manager.", ["Unai Emery"], "APPOINTED_MANAGER_OF", "Unai Emery", "Arsenal"),
    ("HLV Jurgen Klopp từ chức tại Liverpool.", ["Jurgen Klopp"], "RESIGNED_FROM", "Jurgen Klopp", "Liverpool"),
    ("Liverpool manager Jurgen Klopp spoke today.", ["Jurgen Klopp"], "MANAGES", "Jurgen Klopp", "Liverpool"),
    ("Mohamed Salah gia hạn hợp đồng với Liverpool.", ["Mohamed Salah"], "EXTENDED_CONTRACT_WITH", "Mohamed Salah", "Liverpool"),
])
def test_relation_direction(sentence, people, relation, subject, obj):
    found = relations(sentence, people)
    assert any(r == relation and s.canonical == subject and o.canonical == obj for r, s, o, *_ in found)


def test_new_contract_is_not_new_transfer():
    found = relations("Cole Palmer signed a new contract with Chelsea.", ["Cole Palmer"])
    assert any(row[0] == "EXTENDED_CONTRACT_WITH" for row in found)
    assert not any(row[0] == "TRANSFERRED_TO" for row in found)


def test_negation_and_hypothetical_are_not_completed():
    assert assertion_status("Chelsea have not sacked Graham Potter.") == "negated"
    assert assertion_status("Chelsea could sack Graham Potter.") == "hypothetical"
    assert assertion_status("Arsenal reportedly signed Nicolas Pepe.") == "rumor"
    assert assertion_status("Man Utd chưa sa thải Erik ten Hag.") == "negated"


def test_cooccurrence_does_not_create_relation():
    assert not relations("Mohamed Salah and Cole Palmer met in London with Chelsea and Liverpool fans.", ["Mohamed Salah", "Cole Palmer"])


def test_opponent_is_not_managers_club():
    found = relations("Manchester United beat Liverpool, manager Ole Gunnar Solskjaer said.", ["Ole Gunnar Solskjaer"])
    assert not any(row[0] == "MANAGES" for row in found)


def test_business_investment_is_not_player_transfer():
    found = relations("Crystal Palace said businessman John Textor has joined its board of directors.", ["John Textor"])
    assert not any(row[0] == "TRANSFERRED_TO" for row in found)


def test_passive_criticism_does_not_reverse_speaker():
    found = relations("Jason Wilcox trở thành tâm điểm chỉ trích công khai từ Lisandro Martinez ở Man Utd.", ["Jason Wilcox", "Lisandro Martinez"])
    assert not any(row[0] == "CRITICIZES" for row in found)


def test_old_club_not_used_when_destination_is_unknown():
    found = relations("Former Arsenal defender Sokratis Papastathopoulos has joined Olympiakos Piraeus.", ["Sokratis Papastathopoulos"])
    assert not any(row[0] == "TRANSFERRED_TO" and row[2].canonical == "Arsenal" for row in found)


def test_resignation_from_national_team_not_old_club():
    found = relations("Former Chelsea winger Damien Duff has stepped down from his role as Ireland coach.", ["Damien Duff"])
    assert not any(row[0] == "RESIGNED_FROM" for row in found)


def test_season_long_loan_is_not_permanent_transfer():
    found = relations("Norwich City have signed Brandon Williams from Manchester United on a season-long loan.", ["Brandon Williams"])
    assert any(r == "LOANED_TO" and o.canonical == "Norwich City" for r, s, o, *_ in found)
    assert not any(row[0] == "TRANSFERRED_TO" for row in found)


def test_prospective_appointment_title_is_hypothetical():
    assert assertion_status("MU phải trả 3 triệu bảng để bổ nhiệm Eddie Howe") == "hypothetical"


def test_evidence_offsets_with_unicode():
    text = "  Salah gia hạn với Liverpool.\n\nHLV Klopp từ chức.  "
    for start, sentence in sentences(text):
        assert text[start:start+len(sentence)] == sentence


def test_xml_does_not_read_image_locations_as_article_urls():
    xml = b'<urlset xmlns="urn:test" xmlns:image="urn:image"><url><loc>https://x/a.html</loc><image:image><image:loc>https://x/a.jpg</image:loc></image:image></url></urlset>'
    assert xml_locations(xml, article_only=True) == ["https://x/a.html"]
    with pytest.raises(ValueError, match="HTML"):
        xml_locations(b"<!doctype html><html>not a sitemap</html>")


def test_url_and_date_handling():
    assert canonical_url("http://EXAMPLE.com/a.html?tracking=x#comment") == "https://example.com/a.html"
    assert parse_date("2026-10-29T14:45+7:00") == "2026-10-29"
    assert parse_date("not a date") is None


def test_24h_category_id_is_not_article_id():
    html = '<a href="/bong-da/a-football-story-c48a123456.html">Article</a><a href="/bong-da/match-c48mid123.html">Score</a>'
    assert article_links(html, "https://www.24h.com.vn/", "24h") == ["https://www.24h.com.vn/bong-da/a-football-story-c48a123456.html"]


def test_offtopic_briefs_removed_but_premier_league_briefs_kept():
    from player_graph.corpus import strip_offtopic_briefs
    kept = strip_offtopic_briefs(["Arsenal forward Bukayo Saka has signed a new deal.",
                                  "* Cristiano Ronaldo scored as Al-Nassr beat Al-Shabab.",
                                  "* Portuguese rider Joao Almeida won stage 16 of the Giro.",
                                  "* Tottenham Hotspur signed winger Gareth Bale on loan from Real Madrid."])
    assert kept == ["Arsenal forward Bukayo Saka has signed a new deal.",
                    "* Tottenham Hotspur signed winger Gareth Bale on loan from Real Madrid."]


def test_person_roles_from_edges():
    from player_graph.graph import person_roles
    edges = [{"subject_id": "lampard", "relation_type": "PLAYS_FOR", "object_id": "che"},
             {"subject_id": "che", "relation_type": "APPOINTED_MANAGER_OF", "object_id": "lampard"},
             {"subject_id": "raiola", "relation_type": "REPRESENTS", "object_id": "pogba"},
             {"subject_id": "neville", "relation_type": "CRITICIZES", "object_id": "mu"}]
    roles = person_roles(edges)
    assert roles["lampard"] == {"Player", "Coach"} and roles["raiola"] == {"Agent"}
    assert "neville" not in roles and "pogba" not in roles
