from collections import defaultdict

from player_graph.common import normalize
from player_graph.ner_role import FOOTBALLER, NameRecognizer, capitalised_runs, role_from_occupations
from player_graph.resolve import Roster


def roster_with(people):
    r = Roster.__new__(Roster)
    r.label, r.clubs, r.full, r.surname, r.names, r.failed = {}, defaultdict(set), defaultdict(set), defaultdict(set), defaultdict(set), []
    for qid, label in people:
        r.label[qid] = label
        r.full[normalize(label)].add(qid)
        r.surname[normalize(label).split()[-1]].add(qid)
    return r


PEOPLE = [("Q1", "Marcus Rashford"), ("Q2", "Tyrell Malacia"), ("Q3", "Erik ten Hag"), ("Q4", "Antony dos Santos"), ("Q5", "Wei Cho")]
OCCUPATIONS = {"Q1": [FOOTBALLER], "Q3": ["Q628099", FOOTBALLER]}


def recognizer(**options):
    no_tags = lambda sentence, language: []
    return NameRecognizer(roster_with(PEOPLE), OCCUPATIONS, options.pop("tagger", no_tags), **options)


def names(found, kind="Person"):
    return [m.get("name", m["surface"]) for m in found if m["type"] == kind]


def test_dictionary_finds_full_names_clubs_and_role():
    found = recognizer().recognize("Marcus Rashford rời Man Utd. HLV Erik ten Hag không giữ anh.")
    assert names(found) == ["Marcus Rashford", "Erik ten Hag"]
    assert names(found, "Club") == ["Man Utd"] and [m["club"] for m in found if m["type"] == "Club"] == ["Manchester United"]
    assert found[0]["roles"] == ["Player"] and found[0]["qid"] == "Q1"
    assert [m for m in found if m["surface"] == "Erik ten Hag"][0]["roles"] == ["Player", "Coach"]


def test_short_name_takes_the_full_name_in_the_same_text():
    found = recognizer().recognize("Tyrell Malacia gia nhập năm 2022. Sau đó Malacia chấn thương.")
    assert [m["name"] for m in found if m["type"] == "Person"] == ["Tyrell Malacia", "Tyrell Malacia"]


def test_vietnamese_word_is_not_a_surname_at_sentence_start_or_in_lower_case():
    found = recognizer().recognize("Cho đến nay chưa có tin. Man Utd cho mượn cầu thủ.")
    assert names(found) == []


def test_name_a_library_marks_but_no_dictionary_knows_is_flagged_not_guessed():
    tagger = lambda sentence, language: [(0, 6)] if sentence.startswith("Antony") else []
    found = recognizer(tagger=tagger).recognize("Antony sắp sang Real Betis.")
    person = [m for m in found if m["type"] == "Person"][0]
    assert person["surface"] == "Antony" and person["needs_lookup"] and person["qid"] is None


def test_capitalised_runs_keep_particles_inside_names_only():
    sentence = "HLV Erik ten Hag rời đi"
    assert [sentence[a:b] for a, b in capitalised_runs(sentence)] == ["HLV Erik ten Hag"]


def test_roles_from_occupations():
    assert role_from_occupations([FOOTBALLER, "Q628099"]) == ["Player", "Coach"] and role_from_occupations(["Q1"]) == []


def test_real_libraries_on_short_sentences():
    r = NameRecognizer(roster_with(PEOPLE), OCCUPATIONS)
    assert "Marcus Rashford" in names(r.recognize("Marcus Rashford bị gạch tên khỏi danh sách.", "vi"))
    assert "Marcus Rashford" in names(r.recognize("Marcus Rashford has been linked with Dortmund.", "en"))


def test_possessive_and_club_words_are_not_names():
    found = recognizer().recognize("Marcus Rashford's goal helped Man City. City won.")
    assert names(found) == ["Marcus Rashford"]
    assert names(found, "Club") == ["Man City"]


def test_article_names_are_cached_and_found_in_any_compatible_form(tmp_path):
    from player_graph.ner_role import article_names, name_found
    articles = {"a": {"clean_text": "Marcus Rashford rời Man Utd.", "language": "vi"}}
    cache = tmp_path / "names.json"
    first = article_names(articles, recognizer=recognizer(), cache_path=cache)
    assert "Marcus Rashford" in first["a"]
    # The second call must not touch the recognizer.
    assert article_names(articles, recognizer=object(), cache_path=cache) == first
    assert name_found("Rashford", first["a"]) and not name_found("Beth England", first["a"])
