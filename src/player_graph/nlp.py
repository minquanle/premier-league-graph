from __future__ import annotations

import csv
import json
import random
import re
from collections import Counter, defaultdict
from dataclasses import dataclass

import spacy

from .common import DATA, REPORTS, ROOT, normalize, now, read_jsonl, stable_id, write_json, write_jsonl

VERSION = "spacy-local-rules-0.3"
VOCAB = json.loads((ROOT / "configs/clubs.json").read_text(encoding="utf-8"))
SCHEMA = json.loads((ROOT / "configs/project_plan.json").read_text(encoding="utf-8"))
TYPES = {r["name"] for r in SCHEMA["relation_types"]}


@dataclass
class Mention:
    text: str
    start: int
    end: int
    kind: str
    canonical: str
    entity_id: str
    scope: str = "unverified"


CLUB_PATTERNS = []
for scope in ["core", "context"]:
    for canonical, aliases in VOCAB[scope].items():
        for alias in aliases:
            flags = 0 if len(alias) <= 4 or alias in {"Reading", "Real", "Como", "Roma"} else re.I
            CLUB_PATTERNS.append((re.compile(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", flags), canonical, scope))


def club_mentions(text):
    found = []
    for pattern, canonical, scope in CLUB_PATTERNS:
        for m in pattern.finditer(text):
            found.append(Mention(m.group(), m.start(), m.end(), "Club", canonical, stable_id("club", normalize(canonical)), scope))
    result = []
    for m in sorted(found, key=lambda v: (-(v.end-v.start), v.start)):
        if not any(m.start < x.end and x.start < m.end for x in result):
            result.append(m)
    return sorted(result, key=lambda v: v.start)


def sentences(text):
    start = 0
    for boundary in re.finditer(r"\n+|[.!?][\"”']*(?=\s+(?:[A-ZÀ-Ỹ]|$))", text):
        end = boundary.start() if boundary.group().startswith("\n") else boundary.end()
        raw = text[start:end]
        left = len(raw)-len(raw.lstrip())
        if raw.strip():
            yield start+left, raw.strip()
        start = boundary.end()
    raw = text[start:]
    if raw.strip():
        yield start+len(raw)-len(raw.lstrip()), raw.strip()


def valid_person(name):
    folded = normalize(name)
    if len(name) < 3 or len(name) > 55 or len(name.split()) > 5:
        return False
    if re.search(r"\d|[;:<>\n]", name):
        return False
    if not any(c.isupper() for c in name):
        return False
    banned = {"premier league", "champions league", "europa league", "fa cup", "ballon d or", "world cup", "anh", "hlv", "the", "england", "france", "vietnam", "viet nam", "afp", "reuters", "getty", "getty images", "ap", "bbc", "sky sports", "man utd", "united", "city", "league", "cup"}
    banned |= {"huyen", "danh", "dung", "goal", "nhung", "anh em", "doi bong", "quy do"}
    if folded in banned or re.search(r"^(hlv|clb|coach|manager|striker|midfielder|defender|captain|khi|nhung)\s", folded):
        return False
    particles = {"de", "da", "dos", "do", "del", "van", "von", "ten", "der", "di", "el", "al", "bin", "le"}
    if any(not word[0].isupper() and word.lower() not in particles for word in name.split() if word):
        return False
    return True


def document_mentions(article, pipeline):
    text = article["clean_text"]
    clubs = club_mentions(text)
    doc = pipeline(text)
    persons = []
    for span in doc.ents:
        if span.label_ not in {"PERSON", "PER"} or not valid_person(span.text):
            continue
        if any(span.start_char < c.end and c.start < span.end_char for c in clubs):
            continue
        name = re.sub(r"['’]s$", "", span.text.strip(" '’\"“”.,-"))
        if name:
            offset = text.find(name, span.start_char, span.end_char)
            persons.append(Mention(name, offset, offset+len(name), "Person", name, stable_id("person", normalize(name))))
    # Resolve shortened names only to a unique full name within this article.
    full = {normalize(m.text): m.text for m in persons if len(m.text.split()) >= 2}
    for m in persons:
        key = normalize(m.text)
        possibilities = [v for k, v in full.items() if k != key and (k.endswith(" "+key) or k.startswith(key+" "))]
        if len(set(possibilities)) == 1:
            m.canonical = possibilities[0]
            m.entity_id = stable_id("person", normalize(m.canonical))
    # Match repeats of names already recognized by the local statistical NER model.
    # This is not a registry of affiliations and does not manufacture relations.
    for name in sorted({m.canonical for m in persons}, key=len, reverse=True):
        for match in re.finditer(r"(?<!\w)"+re.escape(name)+r"(?!\w)", text):
            if not any(match.start() < x.end and x.start < match.end() for x in [*clubs, *persons]):
                persons.append(Mention(match.group(), match.start(), match.end(), "Person", name, stable_id("person", normalize(name))))
    return sorted([*clubs, *persons], key=lambda v: v.start)


def assertion_status(sentence, trigger_start=0, trigger_end=None):
    """Local polarity/modality guard. The sentence and its status are still reviewable."""
    # Consider the trigger's clause, not an unrelated sentence elsewhere in the article.
    clause = sentence[max(0, trigger_start-100):min(len(sentence), (trigger_end or len(sentence))+120)]
    folded = normalize(clause)
    if re.search(r"\b(chua|khong|phu nhan|not|never|denied|deny|no intention)\b", folded):
        return "negated"
    if re.search(r"\b(neu|if|would|could|co the|should|nen|can phai|hay|keu goi|phai tra|de bo nhiem)\b", folded):
        return "hypothetical"
    if re.search(r"\b(tin don|duoc don|rumour|rumor|reportedly|linked with|du kien|sap|se|will|set to|expected to|close to|muon|wants to|hopes to|considering|can nhac)\b", folded):
        return "rumor"
    return "reported"


@dataclass
class Rule:
    relation: str
    pattern: str
    mode: str
    person_role: str


# Explicit constructions only. The rules remain a baseline, not a claim of >90% accuracy.
RULES = [
    Rule("EXTENDED_CONTRACT_WITH", r"gia hạn(?: hợp đồng)?|\b(?:extends?|extended|renewed|renews)\b.{0,25}\b(?:contract|deal)\b|\bsign(?:ed|s)?\b.{0,30}\bnew (?:contract|deal)\b|\bsigned\b.{0,40}\bcontract extension\b", "person_to_club", "Unknown"),
    Rule("DISMISSED_MANAGER", r"\b(?:sacked|sacks|dismissed|dismisses|fired|fires)\b|sa thải", "club_to_person", "Coach"),
    Rule("RESIGNED_FROM", r"từ chức|\bresign(?:ed|s)?\b|\bstepped down\b", "person_to_club", "Coach"),
    Rule("APPOINTED_MANAGER_OF", r"bổ nhiệm|\bappoint(?:ed|s)?\b|\bnamed\b.{0,45}\b(?:manager|coach)\b", "appoint", "Coach"),
    Rule("LOANED_TO", r"\bjoin(?:ed|s)?\b(?=.{0,100}\bon\b.{0,35}\bloan\b)|\bloan move\b|gia nhập(?=.{0,70}cho mượn)|chuyển đến(?=.{0,70}cho mượn)", "person_to_club", "Player"),
    Rule("LOANED_TO", r"\bsigned\b(?=.{0,120}\bon\b.{0,35}\bloan\b)|\bloan(?:ed|s)\b|mượn|cho mượn", "loan", "Player"),
    Rule("TRANSFERRED_TO", r"gia nhập|chuyển (?:đến|tới|sang)|\bjoin(?:ed|s)?\b|\bmoved to\b|\bcomplet(?:ed|es)\b.{0,25}\bmove\b", "person_to_club", "Player"),
    Rule("TRANSFERRED_TO", r"chiêu mộ|\bsign(?:ed|s)\b|\brecruited\b|\bbought\b", "club_to_person", "Player"),
    Rule("NEGOTIATES_TO_SIGN", r"đàm phán|\bin talks\b|\bnegotiat(?:es|ed|ing|ions)\b", "club_to_person", "Player"),
    Rule("CRITICIZES", r"chỉ trích|\bcriticis(?:ed|es|ing)\b|\bcriticiz(?:ed|es|ing)\b|\bslammed\b|\bblasted\b", "person_to_entity", "Unknown"),
    Rule("CONFLICTS_WITH", r"xung đột với|mâu thuẫn với|\bclashed with\b|\bfeud with\b|\bconflict with\b", "person_to_entity", "Unknown"),
    Rule("MANAGES", r"\b(?:manager|coach)\b|\bHLV\b|huấn luyện viên", "role_coach", "Coach"),
    Rule("PLAYS_FOR", r"\bplays? for\b|thi đấu cho|khoác áo", "person_to_club", "Player"),
    Rule("REPRESENTS", r"\bagent of\b|\brepresents\b|đại diện cho", "person_to_person", "Agent"),
]


def attributed_club(person, clubs, sentence):
    """Only a local grammatical attribution, never the nearest opponent in the sentence."""
    descriptors = {"s", "interim", "head", "former", "assistant", "caretaker", "new", "brazil", "brazilian", "french", "england", "english", "spanish", "portuguese", "dutch", "italian", "german", "senegalese", "colombian", "egyptian", "scotland", "scottish", "wales", "welsh", "nigeria", "nigerian", "algeria", "algerian", "switzerland", "swiss", "striker", "forward", "defender", "winger", "midfielder", "goalkeeper", "manager", "coach", "boss", "captain"}
    candidates = []
    for club in clubs:
        if club.end <= person.start:
            gap = sentence[club.end:person.start]
            words = re.findall(r"[A-Za-z]+", gap.lower())
            if len(gap) <= 55 and not re.search(r"[^A-Za-z\s'’\-]", gap) and words and all(w in descriptors for w in words):
                candidates.append((len(gap), club))
        elif club.start >= person.end:
            gap = sentence[person.end:club.start]
            if re.fullmatch(r"\s+(?:của|tại|ở|of|at)\s+", gap, re.I):
                candidates.append((len(gap), club))
    return min(candidates, key=lambda v: v[0])[1] if candidates else None


def match_sentence(sentence: str, mentions: list[Mention]):
    """Return (relation, subject, object, status, role, rule) with sentence-local offsets."""
    found = []
    for rule in RULES:
        for trigger in re.finditer(rule.pattern, sentence, re.I):
            persons = [m for m in mentions if m.kind == "Person"]
            clubs = [m for m in mentions if m.kind == "Club"]
            before_p = sorted([m for m in persons if m.end <= trigger.start()], key=lambda m: trigger.start()-m.end)
            after_p = sorted([m for m in persons if m.start >= trigger.end()], key=lambda m: m.start-trigger.end())
            before_c = sorted([m for m in clubs if m.end <= trigger.start()], key=lambda m: trigger.start()-m.end)
            after_c = sorted([m for m in clubs if m.start >= trigger.end()], key=lambda m: m.start-trigger.end())
            person = club = subject = obj = None
            if rule.mode == "person_to_club":
                person = before_p[0] if before_p else None
                # A long rule may cover the destination club inside its trigger.
                destinations = [m for m in clubs if m.start >= trigger.start()]
                club = destinations[0] if destinations else None
                if club and rule.relation in {"TRANSFERRED_TO", "LOANED_TO"} and re.search(r"\bfrom\b|\btừ\b", sentence[trigger.start():club.start], re.I):
                    club = None
                if person and not club and rule.relation == "EXTENDED_CONTRACT_WITH":
                    club = attributed_club(person, clubs, sentence)
                subject, obj = person, club
            elif rule.mode in {"club_to_person", "appoint"}:
                club = before_c[0] if before_c else None
                person = after_p[0] if after_p else None
                if club and (trigger.start()-club.end > 55 or any(club.end <= p.start < trigger.start() for p in persons)):
                    club = None
                # 'X was sacked/appointed by Y' is passive; keep proper relation direction.
                if before_p and re.search(r"\b(?:was|been|is|bị|được)\b", sentence[before_p[0].end:trigger.start()], re.I):
                    person = before_p[0]
                    club = after_c[0] if after_c and re.search(r"\b(?:by|of|at)\b|bởi", sentence[trigger.end():after_c[0].start], re.I) else None
                subject, obj = (person, club) if rule.mode == "appoint" or rule.relation == "TRANSFERRED_TO" else (club, person)
            elif rule.mode == "loan":
                person = after_p[0] if after_p else (before_p[0] if before_p else None)
                if person:
                    if re.fullmatch(r"signed", trigger.group(), re.I):
                        club = before_c[0] if before_c else None
                    else:
                        destination = [m for m in clubs if m.start > person.end and re.search(r"\bto\b|sang|đến", sentence[person.end:m.start], re.I)]
                        club = destination[0] if destination else None
                subject, obj = person, club
            elif rule.mode == "person_to_entity":
                subject = before_p[0] if before_p else None
                if subject:
                    gap = sentence[subject.end:trigger.start()]
                    allowed = r"\s*(?:(?:đã|từng|công khai|tiếp tục|lên tiếng|gay gắt|cũng|has|have|had|publicly|openly|strongly|again|also|fiercely)\s+)*"
                    if not re.fullmatch(allowed, gap, re.I):
                        subject = None
                options = sorted([m for m in mentions if m.start >= trigger.end()], key=lambda m: m.start)
                obj = options[0] if options else None
            elif rule.mode == "role_coach":
                # 'Liverpool manager Jurgen Klopp' or 'HLV Jurgen Klopp của Liverpool'.
                person = after_p[0] if after_p and after_p[0].start-trigger.end() <= 8 else None
                if person:
                    club = attributed_club(person, clubs, sentence)
                subject, obj = person, club
            elif rule.mode == "person_to_person":
                subject = before_p[0] if before_p else None
                obj = after_p[0] if after_p else None
            if not subject or not obj or subject.entity_id == obj.entity_id:
                continue
            if max(subject.start, obj.start, trigger.start()) - min(subject.end, obj.end, trigger.end()) > 200:
                continue
            # A player signing a contract is not necessarily a new transfer.
            if rule.relation == "TRANSFERRED_TO" and re.search(r"gia hạn|\b(?:new|extend|renew)\b.{0,30}\b(?:contract|deal)\b|\bcontract extension\b|\bon\b.{0,35}\bloan\b|cho mượn|billionaire|businessman|investor|board of directors|giám đốc|chủ tịch", sentence, re.I):
                continue
            if rule.relation == "NEGOTIATES_TO_SIGN" and re.search(r"\b(?:exit|departure|renew|extension)\b|gia hạn|ra đi", sentence, re.I):
                continue
            if rule.relation == "TRANSFERRED_TO" and subject.kind == "Person" and re.search(r"\b(?:manager|coach|boss|HLV)\s*$", sentence[max(0,subject.start-20):subject.start], re.I):
                continue
            # MANAGES is a separate explicit state, not an automatic consequence of APPOINTED.
            status = assertion_status(sentence, trigger.start(), trigger.end())
            found.append((rule.relation, subject, obj, status, rule.person_role, rule.pattern))
    # Prefer a more specific event over a broad transfer rule in the same sentence.
    specific = {(s.entity_id, o.entity_id) for r, s, o, *_ in found if r in {"LOANED_TO", "EXTENDED_CONTRACT_WITH", "APPOINTED_MANAGER_OF"}}
    found = [x for x in found if not (x[0] == "TRANSFERRED_TO" and (x[1].entity_id, x[2].entity_id) in specific)]
    unique = {}
    for row in found:
        unique.setdefault((row[0], row[1].entity_id, row[2].entity_id), row)
    return list(unique.values())


def run_extraction():
    articles = read_jsonl(DATA / "processed/articles.jsonl")
    if not articles:
        raise RuntimeError("Collect articles first")
    models = {
        "en": spacy.load("en_core_web_sm", disable=["parser", "lemmatizer", "attribute_ruler"]),
        "vi": spacy.load("xx_ent_wiki_sm"),
    }
    entities = {}
    assertions = []
    mentions_out = []
    roles = defaultdict(set)
    evidence_by_entity = defaultdict(set)
    for index, article in enumerate(articles):
        text = article["clean_text"]
        mentions = document_mentions(article, models[article["language"]])
        for m in mentions:
            entities.setdefault(m.entity_id, {"entity_id": m.entity_id, "canonical_name": m.canonical, "entity_type": m.kind,
                "scope_role": m.scope if m.kind == "Club" else "unverified", "aliases": set(),
                "resolution_method": "curated_club_alias" if m.kind == "Club" else "NER_exact_name_and_unique_document_alias"})
            entities[m.entity_id]["aliases"].add(m.text)
            mentions_out.append({"article_id": article["article_id"], "entity_id": m.entity_id, "text": m.text,
                                 "start": m.start, "end": m.end, "ner_model": models[article["language"]].meta["name"]})
        for start, sentence in sentences(text):
            local = [Mention(m.text, m.start-start, m.end-start, m.kind, m.canonical, m.entity_id, m.scope)
                     for m in mentions if m.start >= start and m.end <= start+len(sentence)]
            if not any(m.kind == "Club" and m.scope == "core" for m in local):
                continue
            for relation, subject, obj, status, role, pattern in match_sentence(sentence, local):
                if not (subject.scope == "core" or obj.scope == "core") and not (subject.kind == obj.kind == "Person"):
                    continue
                sid, oid = subject.entity_id, obj.entity_id
                if relation == "CONFLICTS_WITH" and subject.kind == obj.kind == "Person" and sid > oid:
                    sid, oid = oid, sid
                supported = status in {"reported", "ongoing", "completed"}
                person = subject if subject.kind == "Person" else obj
                if person.kind == "Person" and role != "Unknown":
                    roles[person.entity_id].add(role)
                # Evidence linkage is not a proof of a real-world event date.
                assertion_id = stable_id("assertion", f"{article['article_id']}|{start}|{relation}|{sid}|{oid}")
                assertions.append({
                    "assertion_id": assertion_id, "subject_id": sid, "object_id": oid, "relation_type": relation,
                    "article_id": article["article_id"], "language": article["language"], "source_url": article["canonical_url"],
                    "published_at": article["published_at"], "event_date": None, "date_precision": "unknown",
                    "assertion_status": status, "evidence_text": sentence, "evidence_start": start, "evidence_end": start+len(sentence),
                    "extractor_version": VERSION, "model_id": models[article["language"]].meta["name"]+"-"+models[article["language"]].meta["version"],
                    "review_status": "pending", "structural_validation": "valid", "eligible_for_candidate_graph": supported,
                    "rule": pattern,
                })
                for entity in [subject, obj]:
                    if supported:
                        evidence_by_entity[entity.entity_id].add(assertion_id)
                        if subject.scope == "core" or obj.scope == "core":
                            if entities[entity.entity_id]["entity_type"] == "Person":
                                entities[entity.entity_id]["scope_role"] = "core_link_candidate"
        if (index+1) % 25 == 0:
            print(json.dumps({"stage": "extract", "articles": index+1, "entity_candidates": len(entities), "assertions": len(assertions)}), flush=True)
    # Conservative pair+type collapse while dates are unknown; separate events remain in assertions.
    grouped = defaultdict(list)
    for row in assertions:
        if row["eligible_for_candidate_graph"]:
            grouped[(row["subject_id"], row["relation_type"], row["object_id"])].append(row)
    edges = []
    for (sid, kind, oid), rows in sorted(grouped.items()):
        edges.append({"relation_id": stable_id("relation", f"{sid}|{kind}|{oid}"), "subject_id": sid,
                      "object_id": oid, "relation_type": kind, "assertion_ids": sorted({r["assertion_id"] for r in rows}),
                      "article_ids": sorted({r["article_id"] for r in rows}), "evidence_count": len(rows),
                      "review_status": "pending", "counting_mode": "unique_pair_and_type_unknown_event_dates",
                      "first_publication": min(r["published_at"] for r in rows), "last_publication": max(r["published_at"] for r in rows)})
    for eid, entity in entities.items():
        entity["aliases"] = sorted(entity["aliases"])
        entity["roles"] = sorted(roles[eid])
        entity["scope_evidence_ids"] = sorted(evidence_by_entity[eid])
        entity["identity_review_status"] = "pending"
    all_ner_entity_count = len(entities)
    write_jsonl(DATA / "interim/all_ner_entities.jsonl", sorted(entities.values(), key=lambda e: e["entity_id"]))
    connected_assertions = {a[k] for a in assertions for k in ["subject_id", "object_id"]}
    entities = {key: value for key, value in entities.items() if key in connected_assertions}
    mentions_out = [m for m in mentions_out if m["entity_id"] in entities]
    write_jsonl(DATA / "processed/entities.jsonl", sorted(entities.values(), key=lambda e: e["entity_id"]))
    write_jsonl(DATA / "processed/mentions.jsonl", mentions_out)
    write_jsonl(DATA / "processed/assertions.jsonl", assertions)
    write_jsonl(DATA / "processed/relations.jsonl", edges)
    by_type = Counter(e["relation_type"] for e in edges)
    connected = {e[k] for e in edges for k in ["subject_id", "object_id"]}
    summary = {"created_at": now(), "extractor_version": VERSION, "articles_processed": len(articles),
               "ner_models": {lang: {"name": m.meta["name"], "version": m.meta["version"]} for lang, m in models.items()},
               "api_calls": 0, "all_unfiltered_ner_entities": all_ner_entity_count, "entity_candidates": len(entities), "entities_in_candidate_relations": len(connected),
               "assertion_candidates": len(assertions), "assertion_statuses": dict(Counter(a["assertion_status"] for a in assertions)),
               "unique_candidate_relations": len(edges), "candidate_relations_by_type": dict(by_type),
               "candidate_relations_by_evidence_language": {lang: sum(any(a["language"] == lang and a["assertion_id"] in e["assertion_ids"] for a in assertions) for e in edges) for lang in ["vi", "en"]},
               "observed_candidate_types": len(by_type), "unobserved_types": sorted(TYPES-set(by_type)),
               "human_verified_relations": 0, "precision": None, "recall": None,
               "limitations": ["No human gold labels yet", "Candidate entities can contain NER errors", "Heuristic name resolution can miss aliases or merge namesakes", "Event dates unknown; pair/type collapse is conservative", "Rule-based argument selection and modality require review"]}
    write_json(REPORTS / "extraction.json", summary)
    # Fixed exploratory review sample; never called independent human ground truth.
    sample = random.Random(42).sample(assertions, min(80, len(assertions)))
    path = REPORTS / "review_sample.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        fields = ["assertion_id", "language", "subject", "relation_type", "object", "assertion_status", "evidence_text", "source_url", "reviewer_type", "label", "notes"]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in sample:
            writer.writerow({**{k: row[k] for k in ["assertion_id", "language", "relation_type", "assertion_status", "evidence_text", "source_url"]},
                             "subject": entities[row["subject_id"]]["canonical_name"], "object": entities[row["object_id"]]["canonical_name"],
                             "reviewer_type": "", "label": "", "notes": ""})
    return summary
