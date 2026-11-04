"""Find people and clubs in a text and say whether a person is a player or a coach, without any LLM.

Three layers, each usable alone so that their contribution can be measured:
1. Library: part-of-speech tags from underthesea (Vietnamese) and named entities from spaCy (English) propose name spans.
   underthesea's own NER is not used: it misses foreign names, which is most of this corpus.
2. Dictionary: Wikidata roster names and club aliases confirm the spans and say who they are.
3. Occupation: P106 on Wikidata says player or coach.

A capitalised name the dictionary cannot settle ("Antony") is returned with needs_lookup, not guessed.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

from .common import DATA, REPORTS, normalize, now, read_jsonl, write_json
from .llm_extract import VOCAB, canonical_club, chunks
from .resolve import Roster, name_compatible

SENTENCE = re.compile(r"(?<=[.!?…])\s+|\n+")
# The possessive stays out of the name ("Guardiola's"), an inner apostrophe stays in ("Eto'o").
WORD = re.compile(r"[^\W\d_](?:[\w-]|['’](?!s\b)(?=\w)|\.(?=\w))*")
NER_VERSION = "1"  # raise when the recognizer changes, so cached names are computed again
FOOTBALLER, COACH = "Q937857", "Q628099"
OCCUPATIONS = DATA / "cache/person_occupations.json"
# Capitalised words that are not people; lower-case particles allowed inside a name.
NOT_NAMES = {normalize(w) for w in """Theo Nhưng Trong Ngoài Nhiều Cựu Hậu Mùa Khó Khả Ưu Sau Trước Khi Với Tại Từ Cùng Đây Đó Các Những Một Hai Ba Anh Ông
    Tiền Thủ Trung HLV CLB USD EUR GBP BBC Sky Sport Sports The Times Guardian Mail Athletic Reuters AFP UEFA FIFA FA VAR La Liga Serie Premier League Bundesliga Ligue
    Champions Europa Conference Cup World Euro Sunday Monday Tuesday Wednesday Thursday Friday Saturday January February March April May June July August September
    October November December Brazil Bỉ Pháp Đức Italy Hà Lan Tây Ban Nha Bồ Đào Nha Argentina England Spain France Germany Portugal Old Trafford Stamford Bridge Anfield
    Etihad Emirates Việt Nam Quỷ Đỏ Pháo Thủ Nhà Vua Mr But And He She His Her They Their It In On At As By For From With After Before This That When While
    CĐV Chúng Họ Cậu Siêu Cầu Bóng Ở Tôi Mình Hạng Đội I A An We You My Our New German French English Spanish Italian Dutch Scottish Welsh Irish Park Royal Camp Club
    London Paris Madrid Bilbao Wembley Bernabeu Goodison Scotland Wales Ireland Na Uy Thụy Điển Mỹ Nhật Hàn Quốc Trung Tottenham Mo Jun Jan Feb Mar Apr Aug Sep Oct Nov Dec""".split()}
PARTICLES = {"van", "de", "der", "den", "ten", "di", "da", "dos", "del", "le", "la", "mac", "von"}


def build_occupations():
    """{qid: occupation qids} from the Wikidata responses already cached; no network."""
    found = {}
    for path in (DATA / "cache/wikidata").glob("*.json"):
        if path.stat().st_size < 3000:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        for qid, entity in (data.get("entities") or {}).items() if isinstance(data, dict) else ():
            if "claims" in entity:
                values = [s.get("mainsnak", {}).get("datavalue", {}).get("value", {}) for s in entity["claims"].get("P106", [])]
                found[qid] = sorted({v["id"] for v in values if isinstance(v, dict) and v.get("id")})
    OCCUPATIONS.write_text(json.dumps(found), encoding="utf-8")
    return found


def load_occupations():
    return json.loads(OCCUPATIONS.read_text(encoding="utf-8")) if OCCUPATIONS.exists() else build_occupations()


def role_from_occupations(occupations):
    """Player, Coach or both, in the same words as the graph labels."""
    roles = []
    if FOOTBALLER in occupations:
        roles.append("Player")
    if COACH in occupations:
        roles.append("Coach")
    return roles


class LibraryTagger:
    """Name spans proposed by the NLP libraries; models are loaded on first use."""

    def __init__(self):
        self.en = None

    def __call__(self, sentence, language):
        if language == "en":
            if self.en is None:
                import spacy
                self.en = spacy.load("en_core_web_sm", disable=["lemmatizer", "attribute_ruler"])
            return [(e.start_char, e.end_char) for e in self.en(sentence).ents if e.label_ in {"PERSON", "ORG", "GPE"}]
        from underthesea import pos_tag
        spans, cursor, current = [], 0, None
        for word, tag in pos_tag(sentence):
            start = sentence.find(word, cursor)
            if start < 0:
                current = None
                continue
            end = start + len(word)
            cursor = end
            if tag == "Np":
                current = (current[0], end) if current else (start, end)
            elif current:
                spans.append(current)
                current = None
        if current:
            spans.append(current)
        return spans


def capitalised_runs(sentence):
    """Spans of neighbouring capitalised words; a dictionary can confirm them even when a tagger misses them."""
    runs, current = [], None
    for m in WORD.finditer(sentence):
        word = m.group()
        if word[0].isupper() or (current and word.lower() in PARTICLES):
            current = [current[0], m.end()] if current else [m.start(), m.end()]
        else:
            if current:
                runs.append(tuple(current))
            current = None
    if current:
        runs.append(tuple(current))
    return runs


class NameRecognizer:
    def __init__(self, roster=None, occupations=None, tagger=None, use_library=True, use_dictionary=True):
        self.roster = roster if roster is not None else Roster({p.stem for p in (DATA / "cache/roster").glob("*.json")})
        self.occupations = load_occupations() if occupations is None else occupations
        self.tagger = (tagger or LibraryTagger()) if use_library else None
        self.use_dictionary = use_dictionary
        self.clubs = {}
        for canonical, aliases in {**VOCAB["core"], **VOCAB["context"]}.items():
            for alias in [canonical, *aliases]:
                if normalize(alias):
                    self.clubs[normalize(alias)] = canonical
        # "Man", "City", "United": a word of a club name is not a person on its own.
        self.club_words = {word for alias in self.clubs for word in alias.split()}

    def _scan(self, sentence, start, end, trusted):
        """Longest dictionary match first inside one span; what is left is a name to look up if a library proposed it."""
        words = list(WORD.finditer(sentence, start, end))
        found, i = [], 0
        while i < len(words):
            hit = None
            for n in range(min(5, len(words) - i), 0, -1):
                a, b = words[i].start(), words[i + n - 1].end()
                key = normalize(sentence[a:b])
                if key in NOT_NAMES and not (n > 1 and key in self.clubs):
                    continue
                if self.use_dictionary and key in self.clubs:
                    hit = (n, {"type": "Club", "surface": sentence[a:b], "start": a, "end": b, "club": self.clubs[key]})
                elif self.use_dictionary and n >= 2 and key in self.roster.full:
                    hit = (n, {"type": "Person", "surface": sentence[a:b], "start": a, "end": b})
                elif self.use_dictionary and n == 1 and key not in self.club_words and (key in self.roster.full or (key in self.roster.surname and (trusted or a > 0))):
                    hit = (n, {"type": "Person", "surface": sentence[a:b], "start": a, "end": b, "short": True})
                if hit:
                    break
            if hit:
                found.append(hit[1])
                i += hit[0]
                continue
            i += 1
        if trusted:
            # Words no dictionary claimed, from a span a library marked as a name.
            leftovers, current = [], None
            covered = [(f["start"], f["end"]) for f in found]
            for w in words:
                inside = any(a <= w.start() < b for a, b in covered)
                word = normalize(w.group())
                if inside or word in NOT_NAMES or word in self.club_words or not (w.group()[0].isupper() or (current and w.group().lower() in PARTICLES)):
                    if current:
                        leftovers.append(tuple(current))
                    current = None
                else:
                    current = [current[0], w.end()] if current else [w.start(), w.end()]
            if current:
                leftovers.append(tuple(current))
            found += [{"type": "Person", "surface": sentence[a:b], "start": a, "end": b, "needs_lookup": True} for a, b in leftovers]
        return found

    def recognize(self, text, language="vi"):
        mentions, offset = [], 0
        for sentence_id, sentence in enumerate(SENTENCE.split(text)):
            if not sentence.strip():
                continue
            offset = text.find(sentence, offset)
            trusted = self.tagger(sentence, language) if self.tagger else []
            spans = [(a, b, True) for a, b in trusted]
            if self.use_dictionary:
                spans += [(a, b, False) for a, b in capitalised_runs(sentence)]
            taken = []
            for a, b, is_trusted in sorted(spans, key=lambda s: (not s[2], s[0])):
                for m in self._scan(sentence, a, b, is_trusted):
                    if any(m["start"] < y and x < m["end"] for x, y in taken):
                        continue
                    taken.append((m["start"], m["end"]))
                    mentions.append({**m, "start": m["start"] + offset, "end": m["end"] + offset, "sentence": sentence_id})
            offset += len(sentence)
        return self._identify(sorted(mentions, key=lambda m: m["start"]))

    def _identify(self, mentions):
        """Who each person is, using the clubs of the same text; a short name takes the full name used nearby."""
        full_names = [m["surface"] for m in mentions if m["type"] == "Person" and len(normalize(m["surface"]).split()) > 1 and not m.get("needs_lookup")]
        out = []
        for m in mentions:
            if m["type"] == "Club":
                out.append(m)
                continue
            surface = m["surface"]
            key = normalize(surface)
            longer = {f for f in full_names if normalize(f).endswith(" " + key) or normalize(f).startswith(key + " ")}
            if len(longer) == 1:
                surface = longer.pop()
            candidates = self.roster.full.get(normalize(surface), set()) or (self.roster.surname.get(key, set()) if " " not in key else set())
            qid = next(iter(candidates)) if len(candidates) == 1 else None
            roles = role_from_occupations(self.occupations.get(qid, [])) if qid else []
            out.append({**m, "name": surface, "qid": qid, "candidates": len(candidates), "roles": roles,
                        "needs_lookup": m.get("needs_lookup", False) or qid is None})
        return out


# ---------- measuring it against the teacher, and checking the extractor against it ----------

def _matches(recognized, gemini_name):
    return name_compatible(recognized, [gemini_name]) or name_compatible(gemini_name, [recognized])


def name_found(name, names):
    """Was this person name, in any compatible form, among the names recognized in the article?"""
    return any(_matches(x, name) for x in names)


def article_names(articles, ids=None, recognizer=None, cache_path=None):
    """{article_id: names recognized}, cached by text and recognizer version so a rebuild stays fast."""
    cache_path = cache_path or DATA / "cache/ner_names.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    out, changed = {}, 0
    wanted = [i for i in (ids if ids is not None else articles) if i in articles]
    for count, article_id in enumerate(wanted, 1):
        article = articles[article_id]
        key = hashlib.sha256(f"{NER_VERSION}|{article['clean_text']}".encode()).hexdigest()
        entry = cache.get(article_id)
        if not entry or entry["key"] != key:
            recognizer = recognizer or NameRecognizer()
            found = recognizer.recognize(article["clean_text"], article["language"])
            names = sorted({m["surface"] for m in found if m["type"] == "Person"} | {m["name"] for m in found if m["type"] == "Person"})
            entry = cache[article_id] = {"key": key, "names": names}
            changed += 1
            if changed % 500 == 0:
                print(f"[ner] {count}/{len(wanted)} articles", file=sys.stderr, flush=True)
                cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        out[article_id] = entry["names"]
    if changed:
        cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    return out


def _prf(tp, fp, fn):
    p, r = tp / max(1, tp + fp), tp / max(1, tp + fn)
    return {"precision": round(p, 3), "recall": round(r, 3), "f1": round(2 * p * r / max(1e-9, p + r), 3), "tp": tp, "fp": fp, "fn": fn}


def score_chunks(recognizer, rows, articles):
    """Agreement with the entities the teacher listed for each chunk: a silver reference, not a human one."""
    people, club = Counter(), Counter()
    by_language = defaultdict(Counter)
    misses, extras = Counter(), Counter()
    for row in rows:
        article = articles[row["article_id"]]
        part = chunks(article["clean_text"])[row["chunk"]]
        found = recognizer.recognize(part, article["language"])
        gold_people = {e["name"] for e in row["payload"].get("entities", []) if isinstance(e, dict) and e.get("type") == "Person" and e.get("name")}
        gold_clubs = {canonical_club(e["name"])[0] for e in row["payload"].get("entities", []) if isinstance(e, dict) and e.get("type") == "Club" and e.get("name")}
        got_people = {m.get("name", m["surface"]) for m in found if m["type"] == "Person"}
        got_clubs = {m["club"] for m in found if m["type"] == "Club"}
        matched = {g for g in gold_people if any(_matches(x, g) for x in got_people)}
        extra = {x for x in got_people if not any(_matches(x, g) for g in gold_people)}
        people.update(tp=len(matched), fn=len(gold_people - matched), fp=len(extra))
        by_language[article["language"]].update(tp=len(matched), fn=len(gold_people - matched), fp=len(extra))
        misses.update(gold_people - matched)
        extras.update(extra)
        club.update(tp=len(got_clubs & gold_clubs), fp=len(got_clubs - gold_clubs), fn=len(gold_clubs - got_clubs))
    return {"people": _prf(**people), "clubs": _prf(**club), "people_by_language": {k: _prf(**v) for k, v in sorted(by_language.items())},
            "most_missed": misses.most_common(25), "most_extra": extras.most_common(25)}


def evaluate(llm_run, articles_path, roster=None):
    """Library alone, dictionary alone and both, on the fixed test subset; then a name check over the whole run."""
    articles = {a["article_id"]: a for a in read_jsonl(Path(articles_path))}
    raw = [r for r in read_jsonl(DATA / "llm" / llm_run / "raw_outputs.jsonl") if r["article_id"] in articles]
    test_ids = {r["id"] for r in read_jsonl(DATA / "train/sft_main_v6/test_eval.jsonl")}
    test_rows = [r for r in raw if f"{r['article_id']}#{r['chunk']}" in test_ids]
    # The stop list was tuned by reading errors on test_eval, so the val split is the fair measurement.
    val_ids = {r["id"] for r in read_jsonl(DATA / "train/sft_main_v6/val.jsonl")}
    val_rows = [r for r in raw if f"{r['article_id']}#{r['chunk']}" in val_ids]
    roster = roster or Roster({p.stem for p in (DATA / "cache/roster").glob("*.json")})
    occupations = load_occupations()
    report = {"created_at": now(), "llm_run": llm_run, "reference": "entities listed by the extractor; not a human reference", "test_chunks": len(test_rows), "val_chunks": len(val_rows)}
    tagger = LibraryTagger()
    for name, options in {"library_only": {"use_dictionary": False}, "dictionary_only": {"use_library": False}, "library_and_dictionary": {}}.items():
        started = time.time()
        recognizer = NameRecognizer(roster, occupations, tagger, **options)
        report[name] = {**score_chunks(recognizer, test_rows, articles), "seconds": round(time.time() - started, 1)}
        report[name]["val_unseen"] = {k: v for k, v in score_chunks(recognizer, val_rows, articles).items() if k not in {"most_missed", "most_extra"}}

    # Name check: which person names of the extractor does the recognizer not find in the same article?
    recognizer = NameRecognizer(roster, occupations, tagger)
    assertions = read_jsonl(DATA / "llm" / llm_run / "assertions.jsonl")
    by_article = defaultdict(list)
    for a in assertions:
        if a["eligible_for_graph"]:
            by_article[a["article_id"]].append(a)
    flagged, checked, started = [], 0, time.time()
    for article_id, rows in by_article.items():
        if article_id not in articles:
            continue
        article = articles[article_id]
        found = recognizer.recognize(article["clean_text"], article["language"])
        names = {m.get("name", m["surface"]) for m in found if m["type"] == "Person"} | {m["surface"] for m in found if m["type"] == "Person"}
        for a in rows:
            for end in ("subject", "object"):
                if a[end]["type"] != "Person":
                    continue
                checked += 1
                if not any(_matches(x, a[end]["name"]) for x in names):
                    flagged.append({"assertion_id": a["assertion_id"], "article_id": article_id, "name": a[end]["name"], "relation": a["relation_type"]})
    report["name_check"] = {"person_names_checked": checked, "flagged": len(flagged), "flagged_share": round(len(flagged) / max(1, checked), 4),
                            "seconds": round(time.time() - started, 1), "most_flagged": Counter(f["name"] for f in flagged).most_common(30)}
    write_json(REPORTS / f"ner_eval_{llm_run}.json", report)
    write_json(REPORTS / f"name_check_{llm_run}.json", flagged)
    return {k: (v if not isinstance(v, dict) else {x: y for x, y in v.items() if x not in {"most_missed", "most_extra", "most_flagged"}}) for k, v in report.items()}
