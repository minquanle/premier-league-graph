"""Entity resolution and graph assembly for LLM assertions.

Three tiers, cheapest and safest first:
1. Clubs: curated alias dictionary, then a Wikidata item restricted to football clubs.
2. Persons inside one article: a short name ("Carrick") is expanded to the unique longer name in the
   same article ("Michael Carrick").
3. Persons across articles: Wikidata search restricted to football people, preferring the candidate whose
   club history (P54 player, P6087 coach) contains a club the person is linked to in our assertions.
Anything still ambiguous keeps a local id: an extra node is safer than merging two different people.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import threading
import time
from collections import Counter, defaultdict

import requests
from rapidfuzz import fuzz

from .common import DATA, REPORTS, normalize, now, read_jsonl, stable_id, write_json, write_jsonl
from .llm_extract import VOCAB, canonical_club

API = "https://www.wikidata.org/w/api.php"
HEADERS = {"User-Agent": "PlayerGraphStudentProject/0.2 (university course project; football news graph)"}
FOOTBALL_PERSON = re.compile(r"football|soccer|footballer|futbol", re.I)
FOOTBALL_CLUB = re.compile(r"(association )?football club|soccer (club|team)|football team", re.I)
NOT_A_CLUB = re.compile(r"women|youth|reserve|academy|history of|season|under-\d|U\d\d|national", re.I)
ROLE_BY_RELATION = {"PLAYS_FOR": "Player", "TRANSFERRED_TO": "Player", "LOANED_TO": "Player", "MANAGES": "Coach",
                    "APPOINTED_MANAGER_OF": "Coach", "DISMISSED_MANAGER": "Coach", "RESIGNED_FROM": "Coach"}
CORE_CLUBS = set(VOCAB["core"])


NEGOTIATION = re.compile(r"dam phan|thuong luong|thuong thao|talks|negotiat|in discussions|agreement|agreed|dat thoa thuan|"
                         r"submitted (a|an) (bid|offer)|made (a|an) (bid|offer)|gui de nghi chinh thuc|hoi mua chinh thuc")
RESIGNATION = re.compile(r"tu chuc|xin nghi|resign|step(s|ped)? down|quit|walked away")
DISMISSAL_WORDS = re.compile(r"pushed out|sacked|fired|dismissed|axed|sa thai|bi loai bo|bi day")


WOMENS_FOOTBALL = re.compile(r"(?<!\w)(women|women s|womens|wsl|lionesses|doi nu|bong da nu|giai nu|nu (arsenal|chelsea|man city|manchester city|man utd|manchester united|liverpool|tottenham))(?!\w)")


def semantic_check(a):
    """Checks the validator cannot do structurally (quality audit R01-R03). Returns a rejection reason or None."""
    relation, text = a["relation_type"], normalize(a["evidence_text"])
    # The graph is the men's game: a sentence about a women's team or league is out of scope (final review 31/10).
    if WOMENS_FOOTBALL.search(text):
        return "womens_football_in_evidence"
    if relation in {"TRANSFERRED_TO", "LOANED_TO"} and a["object"]["type"] == "Club":
        canonical, _ = canonical_club(a["object"]["name"])
        aliases = {normalize(x) for x in [a["object"]["name"], canonical, *VOCAB["core"].get(canonical, []), *VOCAB["context"].get(canonical, [])]} - {""}
        # "bought him from Nottingham Forest": the club after "from/tu" is the source, not the destination.
        if any(re.search(r"(?<!\w)(from|tu)( \w+){0,2} " + re.escape(al) + r"(?!\w)", text) for al in aliases):
            return "destination_club_is_source_in_evidence"
    if relation == "NEGOTIATES_TO_SIGN" and not NEGOTIATION.search(text):
        return "no_actual_negotiation_in_evidence"
    if relation == "RESIGNED_FROM" and (not RESIGNATION.search(text) or DISMISSAL_WORDS.search(text)):
        return "resignation_not_stated_in_evidence"
    return None


class Wikidata:
    """Cached, throttled access; every distinct request is fetched once per project."""

    def __init__(self):
        self.cache = DATA / "cache/wikidata"
        self.cache.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.last = 0.0
        self.calls = 0
        self.failures = 0

    def get(self, params, cache=True):
        key = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()
        path = self.cache / f"{key}.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        # One request at a time, spaced out, honouring 429 Retry-After: Wikidata asks bots to be sequential.
        for attempt in range(6):
            with self.lock:
                wait = self.last + 0.5 - time.time()
                if wait > 0:
                    time.sleep(wait)
                self.calls += 1
                try:
                    response = requests.get(API, params={**params, "format": "json", "maxlag": 5}, headers=HEADERS, timeout=30)
                except requests.RequestException:
                    response = None
                self.last = time.time()
                if response is not None and response.status_code == 429:
                    time.sleep(int(response.headers.get("Retry-After", 30)) + 1)
                    continue
            try:
                data = response.json() if response is not None else {}
            except ValueError:
                data = {}
            if data and "error" not in data:
                if cache:
                    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                return data
            time.sleep(2 ** attempt)
        self.failures += 1
        return {}

    def search(self, name):
        data = self.get({"action": "wbsearchentities", "search": name, "language": "en", "uselang": "en", "type": "item", "limit": 7})
        return data.get("search", [])

    def member_search(self, term, club_qid):
        """Items matching `term` that list `club_qid` as a team (P54) or coached team (P6087)."""
        data = self.get({"action": "query", "list": "search", "srnamespace": 0, "srlimit": 5,
                         "srsearch": f"{term} haswbstatement:P54={club_qid}|P6087={club_qid}"})
        return [x["title"] for x in data.get("query", {}).get("search", [])]

    def claims(self, ids):
        # Cached per entity, not per batch: a batch key changes whenever the candidate list does.
        folder = DATA / "cache/wikidata_entity"
        folder.mkdir(parents=True, exist_ok=True)
        out = {}
        missing = []
        for qid in sorted(set(ids)):
            path = folder / f"{qid}.json"
            if path.exists():
                info = json.loads(path.read_text(encoding="utf-8"))
                out[qid] = {**info, "teams": set(info["teams"])}
            else:
                missing.append(qid)
        for i in range(0, len(missing), 50):
            data = self.get({"action": "wbgetentities", "ids": "|".join(missing[i:i+50]), "props": "claims|labels", "languages": "mul|en|vi"}, cache=False)
            for qid, entity in data.get("entities", {}).items():
                teams = set()
                for prop in ["P54", "P6087"]:
                    for statement in entity.get("claims", {}).get(prop, []):
                        value = statement.get("mainsnak", {}).get("datavalue", {}).get("value", {})
                        if isinstance(value, dict) and value.get("id"):
                            teams.add(value["id"])
                labels = entity.get("labels", {})
                # Wikidata now stores shared person names under "mul"; fall back to English, then Vietnamese.
                label = next((labels[l]["value"] for l in ["mul", "en", "vi"] if l in labels), None)
                years = {}
                for prop, name in [("P569", "born"), ("P570", "died")]:
                    for statement in entity.get("claims", {}).get(prop, []):
                        value = statement.get("mainsnak", {}).get("datavalue", {}).get("value", {})
                        m = re.match(r"[+-](\d{4})", value.get("time", "")) if isinstance(value, dict) else None
                        if m:
                            years[name] = int(m.group(1))
                            break
                out[qid] = {"label": label, "labels": [v["value"] for v in labels.values()], "teams": teams, **years}
                (folder / f"{qid}.json").write_text(json.dumps({**out[qid], "teams": sorted(teams)}, ensure_ascii=False), encoding="utf-8")
        return out


def display_name(info, surfaces, fallback):
    """Among the item's labels pick the one closest to how the news wrote the name.

    Guards against vandalised labels (seen: "Fernando calvo" on Fernando Torres, "Andrcu Onana").
    """
    labels = info.get("labels") or ([info["label"]] if info.get("label") else [])
    if not labels:
        return fallback
    return max(labels, key=lambda l: (max(fuzz.ratio(normalize(l), normalize(s)) for s in surfaces),
                                      max(fuzz.token_set_ratio(normalize(l), normalize(s)) for s in surfaces)))


def plausible_in_year(info, year, role):
    """Reject identities that cannot be the person a news story of `year` talks about (dead, or far too old to play)."""
    if info.get("died") and info["died"] < year - 1:
        return False
    if info.get("born"):
        age = year - info["born"]
        if age < 14 or (role == "Player" and age > 42) or age > 85:
            return False
    return True


def club_identity(name, wd):
    canonical, scope = canonical_club(name)
    queries = [canonical] if scope == "unlisted" or canonical.startswith(("FC ", "AFC ")) else [f"{canonical} F.C.", canonical]
    for query in queries:
        for candidate in wd.search(query):
            description = candidate.get("description", "")
            if FOOTBALL_CLUB.search(description) and not NOT_A_CLUB.search(description + " " + candidate.get("label", "")):
                label = canonical if scope != "unlisted" else candidate.get("label", canonical)
                return {"entity_id": candidate["id"], "qid": candidate["id"], "canonical_name": label, "scope": scope,
                        "resolution_method": "alias_dictionary+wikidata_club" if scope != "unlisted" else "wikidata_club_search"}
    return {"entity_id": stable_id("club", normalize(canonical)), "qid": None, "canonical_name": canonical, "scope": scope,
            "resolution_method": "alias_dictionary" if scope != "unlisted" else "unresolved_local"}


def expand_in_article(assertions, raw_outputs):
    """Map short person names to the unique longer name that the same article uses."""
    names = defaultdict(set)
    for row in raw_outputs:
        for e in row["payload"].get("entities", []) or []:
            if isinstance(e, dict) and e.get("type") == "Person" and e.get("name"):
                names[row["article_id"]].add(e["name"].strip())
    for a in assertions:
        for end in ("subject", "object"):
            names[a["article_id"]].add(a[end]["name"]) if a[end]["type"] == "Person" else None
    mapping = {}
    for article_id, people in names.items():
        for short in people:
            key = normalize(short)
            longer = {p for p in people if normalize(p) != key and (normalize(p).endswith(" " + key) or normalize(p).startswith(key + " "))}
            if len(longer) == 1:
                mapping[(article_id, key)] = longer.pop()
    return mapping


def name_of(key):
    """People are keyed by a surface string, or by (surface, clubs, year) when resolved in context."""
    return key if isinstance(key, str) else key[0]


def local_identity(key, clubs):
    surface = name_of(key)
    single_token = len(normalize(surface).split()) == 1
    # A bare surname stays apart per club context: "Henderson" at Chelsea is not assumed to be "Henderson" at Liverpool.
    suffix = "|" + ",".join(sorted(clubs)) if single_token else ""
    return {"entity_id": stable_id("person", normalize(surface) + suffix), "qid": None, "canonical_name": surface,
            "resolution_method": "unresolved_local_short_name" if single_token else "unresolved_local"}


def link_people(people, wd):
    """people: {key: set(club_qids)} -> {key: identity}; key is a surface or (surface, clubs, year)."""
    searches = {}
    for i, key in enumerate(people, 1):
        if i % 200 == 0 or i == len(people):
            print(f"[link] name search {i}/{len(people)} ({100 * i // len(people)}%), wikidata calls {getattr(wd, 'calls', 0)}", file=sys.stderr, flush=True)
        surface = name_of(key)
        if surface not in searches:
            searches[surface] = [c for c in wd.search(surface) if FOOTBALL_PERSON.search(c.get("description", ""))
                                 and "disambiguation" not in c.get("description", "")]
    details = wd.claims([c["id"] for cands in searches.values() for c in cands])
    out = {}
    for key, clubs in people.items():
        surface = name_of(key)
        scored = []
        for rank, c in enumerate(searches[surface]):
            info = details.get(c["id"], {"teams": set(), "label": c.get("label")})
            overlap = len(info["teams"] & clubs)
            exact = normalize(c.get("label", "")) == normalize(surface)
            scored.append((overlap, exact, -rank, c["id"], info["label"] or c.get("label")))
        scored.sort(reverse=True)
        single_token = len(normalize(surface).split()) == 1
        choice = None
        if scored:
            best = scored[0]
            if best[0] > 0:
                choice, method = best, "wikidata_with_club_history"
            elif not single_token and best[1] and sum(1 for s in scored if s[1]) == 1:
                choice, method = best, "wikidata_unique_exact_football_name"
        if choice:
            out[key] = {"entity_id": choice[3], "qid": choice[3], "canonical_name": choice[4], "resolution_method": method}
    # Second pass for surnames ("Maguire") and misspellings ("Wilfred Zaha"): search only among people whose
    # Wikidata record lists one of the clubs this person is linked to in the news.
    used = Counter(v["qid"] for v in out.values())
    members = {}
    for i, (key, clubs) in enumerate(people.items(), 1):
        if i % 500 == 0 or i == len(people):
            print(f"[link] club member search {i}/{len(people)} ({100 * i // len(people)}%)", file=sys.stderr, flush=True)
        if key in out or not clubs:
            continue
        surface = name_of(key)
        found = []
        for term in dict.fromkeys([surface, surface.split()[-1]]):
            for club in sorted(clubs):
                found += [q for q in wd.member_search(term, club) if q not in found]
            if found:
                break
        members[key] = found
    labels = wd.claims([q for found in members.values() for q in found])
    for key, found in members.items():
        if len(found) > 1:
            # Prefer a candidate the corpus already names in full (e.g. "Carrick" -> "Michael Carrick").
            known = [q for q in found if used[q]]
            found = known if len(known) == 1 else found
        if len(found) == 1:
            out[key] = {"entity_id": found[0], "qid": found[0], "canonical_name": labels.get(found[0], {}).get("label") or name_of(key),
                        "resolution_method": "wikidata_club_member_search"}
    for key, clubs in people.items():
        if key not in out:
            out[key] = local_identity(key, clubs)
    return out


class Roster:
    """Every player/coach Wikidata lists for the dictionary clubs, fetched once per club with SPARQL and cached.

    Matching then runs locally; only names the roster cannot settle go to the per-name API lookup.
    """

    QUERY = """SELECT ?p ?pLabel (GROUP_CONCAT(DISTINCT ?alt; separator="|") AS ?alts) WHERE {
      { ?p wdt:P54 wd:%(q)s } UNION { ?p wdt:P6087 wd:%(q)s } UNION { wd:%(q)s wdt:P286 ?p }
      ?p wdt:P31 wd:Q5 .
      OPTIONAL { ?p skos:altLabel ?alt . FILTER(LANG(?alt) IN ("en", "vi", "mul")) }
      SERVICE wikibase:label { bd:serviceParam wikibase:language "mul,en,vi". }
    } GROUP BY ?p ?pLabel"""

    def __init__(self, club_qids):
        self.cache = DATA / "cache/roster"
        self.cache.mkdir(parents=True, exist_ok=True)
        self.label, self.clubs = {}, defaultdict(set)
        self.full, self.surname = defaultdict(set), defaultdict(set)
        self.failed = []
        for qid in sorted(club_qids):
            for row in self._fetch(qid):
                person = row["p"]["value"].rsplit("/", 1)[-1]
                label = row["pLabel"]["value"]
                if re.fullmatch(r"Q\d+", label):
                    continue
                self.label[person] = label
                self.clubs[person].add(qid)
                names = [label, *[a for a in row.get("alts", {}).get("value", "").split("|") if a]]
                for name in names:
                    key = normalize(name)
                    if key:
                        self.full[key].add(person)
                tokens = normalize(label).split()
                if tokens:
                    self.surname[tokens[-1]].add(person)
                    if len(tokens) > 2:
                        self.full[f"{tokens[0]} {tokens[-1]}"].add(person)

    def _fetch(self, qid):
        path = self.cache / f"{qid}.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        for attempt in range(4):
            try:
                response = requests.get("https://query.wikidata.org/sparql", params={"query": self.QUERY % {"q": qid}},
                                        headers={**HEADERS, "Accept": "application/sparql-results+json"}, timeout=120)
                if response.status_code == 429:
                    time.sleep(int(response.headers.get("Retry-After", 30)) + 1)
                    continue
                if response.ok:
                    rows = response.json()["results"]["bindings"]
                    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
                    time.sleep(1)
                    return rows
            except (requests.RequestException, ValueError):
                pass
            time.sleep(5 * (attempt + 1))
        self.failed.append(qid)
        return []

    def match(self, surface, clubs):
        tokens = normalize(surface).split()
        if not tokens:
            return None
        key = " ".join(tokens)
        candidates = set(self.full.get(key, set()))
        exact = bool(candidates)
        if not candidates:
            same_surname = self.surname.get(tokens[-1], set())
            if len(tokens) == 1:
                candidates = set(same_surname)
            else:
                # Spelling variants only ("Wilfred"/"Wilfried", "Alex"/"Alexandre"); "Fernando" is not "Ferran".
                def same_first(p):
                    first = normalize(self.label[p]).split()[0]
                    return fuzz.ratio(first, tokens[0]) >= 85 or (min(len(first), len(tokens[0])) >= 4 and (first.startswith(tokens[0]) or tokens[0].startswith(first)))
                candidates = {p for p in same_surname if same_first(p)}
        if not candidates:
            return None
        overlap = [p for p in candidates if self.clubs[p] & clubs]
        if len(overlap) == 1:
            return overlap[0], "wikidata_roster_with_club"
        # Being the only such name in an incomplete roster is not proof of identity; accept only an exact full name.
        if len(tokens) > 1 and exact and len(candidates) == 1:
            return next(iter(candidates)), "wikidata_roster_unique_full_name"
        return None


def club_qids_for_roster(wd):
    names = [*VOCAB["core"], *VOCAB["context"]]
    return {club_identity(name, wd)["qid"] for name in names} - {None}


def link_with_roster(people, roster, wd, context=None):
    """context: {key: (year, role)}; bare surnames then also have to fit the story's year."""
    out, rest = {}, {}
    for key, clubs in people.items():
        hit = roster.match(name_of(key), clubs)
        if hit:
            out[key] = {"entity_id": hit[0], "qid": hit[0], "canonical_name": roster.label[hit[0]], "resolution_method": hit[1]}
        else:
            rest[key] = clubs
    # Per-name API lookups only for what the roster could not settle.
    out.update(link_people(rest, wd))
    if context:
        # A surname plus a club the person once played for can still point to someone from another era
        # (e.g. a 1920s "McConnell" for a 2026 Liverpool line-up): check birth/death years.
        weak = {k: v for k, v in out.items() if v["qid"] and len(normalize(name_of(k)).split()) == 1 and k in context}
        info = wd.claims([v["qid"] for v in weak.values()])
        for key, identity in weak.items():
            year, role = context[key]
            if not plausible_in_year(info.get(identity["qid"], {}), year, role):
                out[key] = {**local_identity(key, people[key]), "rejected_candidate": identity["qid"]}
    # A bare surname left unresolved ("Carrick" in a story that names no club) takes the identity of the only
    # resolved person in the corpus with that surname, if that person also fits the club context and the year.
    resolved = {v["qid"] for v in out.values() if v["qid"]}
    info = wd.claims(sorted(resolved))
    # Clubs the corpus itself links to each resolved person (Wikidata lags on recent transfers, e.g. Dorgu at MU).
    corpus_clubs = defaultdict(set)
    for key, identity in out.items():
        if identity["qid"]:
            corpus_clubs[identity["qid"]] |= people[key]
    by_surname = defaultdict(set)
    for qid in resolved:
        for label in info.get(qid, {}).get("labels") or [roster.label.get(qid, "")]:
            if normalize(label):
                by_surname[normalize(label).split()[-1]].add(qid)
    for key, identity in list(out.items()):
        tokens = normalize(name_of(key)).split()
        if identity["qid"] or len(tokens) != 1:
            continue
        clubs = people[key]
        options = [q for q in by_surname.get(tokens[0], set())
                   if (not clubs or (info.get(q, {}).get("teams", set()) | roster.clubs.get(q, set()) | corpus_clubs[q]) & clubs)
                   and (not context or key not in context or plausible_in_year(info.get(q, {}), *context[key]))]
        if len(options) == 1:
            q = options[0]
            out[key] = {"entity_id": q, "qid": q, "canonical_name": info.get(q, {}).get("label") or roster.label.get(q, name_of(key)),
                        "resolution_method": "corpus_unique_surname"}
    # Display names: the label closest to the surface forms used in the news (vandalised labels exist).
    surfaces = defaultdict(set)
    for key, identity in out.items():
        if identity["qid"]:
            surfaces[identity["qid"]].add(name_of(key))
    info = wd.claims(sorted(surfaces))
    for key, identity in out.items():
        if identity["qid"] in info:
            identity["canonical_name"] = display_name(info[identity["qid"]], surfaces[identity["qid"]], identity["canonical_name"])
    return out


def build(run_name):
    folder = DATA / "llm" / run_name
    # Older runs may contain verbatim repeats of one relation; they must not inflate evidence counts.
    # Articles excluded after the teacher ran (e.g. 24h pages mixed with related-article text) give no evidence.
    excluded = {e["article_id"] for e in read_jsonl(DATA / "interim/excluded_articles_v2.jsonl")}
    assertions = [a for a in {a["assertion_id"]: a for a in read_jsonl(folder / "assertions.jsonl")}.values() if a["article_id"] not in excluded]
    raw = read_jsonl(folder / "raw_outputs.jsonl")
    articles = {a["article_id"]: a for a in read_jsonl(DATA / "processed/articles.jsonl")}
    wd = Wikidata()

    # Clubs first: their QIDs are the context for disambiguating people.
    club_names = {a[e]["name"] for a in assertions for e in ("subject", "object") if a[e]["type"] == "Club"}
    clubs = {name: club_identity(name, wd) for name in sorted(club_names)}

    expansion = expand_in_article(assertions, raw)

    def surface(a, end):
        return expansion.get((a["article_id"], normalize(a[end]["name"])), a[end]["name"])

    # Resolve each name in its own context (the clubs it is linked to in that article, and the article's year),
    # instead of one corpus-wide decision per surface string.
    article_clubs = defaultdict(set)
    article_roles = defaultdict(set)
    article_year = {}
    for a in assertions:
        article_year[a["article_id"]] = int(a["published_at"][:4])
        for end, other in (("subject", "object"), ("object", "subject")):
            if a[end]["type"] != "Person":
                continue
            mention = (a["article_id"], surface(a, end))
            article_clubs[mention]
            if a[other]["type"] == "Club" and clubs[a[other]["name"]]["qid"]:
                article_clubs[mention].add(clubs[a[other]["name"]]["qid"])
            if a["relation_type"] in ROLE_BY_RELATION:
                article_roles[mention].add(ROLE_BY_RELATION[a["relation_type"]])

    def person_key(a, end):
        mention = (a["article_id"], surface(a, end))
        return (mention[1], frozenset(article_clubs[mention]), article_year[a["article_id"]])

    people, context = {}, {}
    for (article_id, name), club_set in article_clubs.items():
        key = (name, frozenset(club_set), article_year[article_id])
        people[key] = set(club_set)
        roles = context.get(key, (None, set()))[1] | article_roles[(article_id, name)]
        context[key] = (key[2], roles)
    context = {k: (year, "Player" if "Player" in roles else ("Coach" if roles else "Unknown")) for k, (year, roles) in context.items()}
    club_qids = club_qids_for_roster(wd) | {c["qid"] for c in clubs.values() if c["qid"] and c["scope"] != "unlisted"}
    roster = Roster(club_qids)
    persons = link_with_roster(people, roster, wd, context)

    entities, out_assertions = {}, []
    roles = defaultdict(Counter)
    for a in assertions:
        ends = []
        for end in ("subject", "object"):
            if a[end]["type"] == "Club":
                ident = clubs[a[end]["name"]]
                entity_type = "Club"
            else:
                ident = persons[person_key(a, end)]
                entity_type = "Person"
            e = entities.setdefault(ident["entity_id"], {"entity_id": ident["entity_id"], "qid": ident["qid"], "canonical_name": ident["canonical_name"],
                                                         "entity_type": entity_type, "aliases": set(), "resolution_method": ident["resolution_method"],
                                                         "club_scope": ident.get("scope")})
            e["aliases"].add(a[end]["name"])
            ends.append(e)
        if a["eligible_for_graph"] and not semantic_check(a):
            for e in ends:
                if e["entity_type"] == "Person" and a["relation_type"] in ROLE_BY_RELATION:
                    roles[e["entity_id"]][ROLE_BY_RELATION[a["relation_type"]]] += 1
        out_assertions.append({"assertion_id": a["assertion_id"], "subject_id": ends[0]["entity_id"], "object_id": ends[1]["entity_id"],
                               "relation_type": a["relation_type"], "assertion_status": a["status"], "article_id": a["article_id"],
                               "language": a["language"], "source_url": a["source_url"], "published_at": a["published_at"],
                               "evidence_text": a["evidence_text"], "evidence_start": a["evidence_start"], "evidence_end": a["evidence_end"],
                               "evidence_match": a["evidence_match"], "time_text": a.get("time_text"),
                               "semantic_check": semantic_check(a), "eligible_for_graph": a["eligible_for_graph"] and not semantic_check(a),
                               "extractor": f"llm:{run_name}", "review_status": "pending"})

    # Scope: keep an edge when it touches a Premier League club, or a person who has such an edge.
    core_ids = {i for i, e in entities.items() if e["entity_type"] == "Club" and e["club_scope"] == "core"}
    eligible = [a for a in out_assertions if a["eligible_for_graph"]]
    # Identity: a person with no Wikidata match is left out (it may be split or merged wrongly), except an agent
    # named in full, since agents are rarely on Wikidata and a full name seldom collides.
    agents = {a["subject_id"] for a in eligible if a["relation_type"] == "REPRESENTS"}
    unverified = {i for i, e in entities.items() if e["entity_type"] == "Person" and e["resolution_method"].startswith("unresolved")
                  and not (i in agents and len(e["canonical_name"].split()) > 1)}
    dropped_unverified = len({(a["subject_id"], a["relation_type"], a["object_id"]) for a in eligible
                              if a["subject_id"] in unverified or a["object_id"] in unverified})
    eligible = [a for a in eligible if a["subject_id"] not in unverified and a["object_id"] not in unverified]
    linked = {a[k] for a in eligible for k in ("subject_id", "object_id") if a["subject_id"] in core_ids or a["object_id"] in core_ids}
    grouped = defaultdict(list)
    for a in eligible:
        if a["subject_id"] in linked or a["object_id"] in linked:
            grouped[(a["subject_id"], a["relation_type"], a["object_id"])].append(a)
    relations = [{"relation_id": stable_id("relation", f"{s}|{t}|{o}"), "subject_id": s, "relation_type": t, "object_id": o,
                  "assertion_ids": sorted(x["assertion_id"] for x in rows), "article_ids": sorted({x["article_id"] for x in rows}),
                  "evidence_count": len(rows), "languages": sorted({x["language"] for x in rows}),
                  "first_publication": min(x["published_at"] for x in rows), "last_publication": max(x["published_at"] for x in rows),
                  "counting_mode": "unique_pair_and_type", "review_status": "pending"}
                 for (s, t, o), rows in sorted(grouped.items())]
    in_graph = {r[k] for r in relations for k in ("subject_id", "object_id")}
    for eid, e in entities.items():
        e["aliases"] = sorted(e["aliases"])
        e["roles"] = sorted(roles[eid])
        e["scope_role"] = "core" if eid in core_ids or (eid in linked and e["entity_type"] == "Person") else "context"
    out_dir = DATA / "graph" / run_name
    write_jsonl(out_dir / "entities.jsonl", sorted((e for e in entities.values() if e["entity_id"] in in_graph), key=lambda e: e["entity_id"]))
    write_jsonl(out_dir / "assertions.jsonl", out_assertions)
    write_jsonl(out_dir / "relations.jsonl", relations)
    write_jsonl(out_dir / "articles.jsonl", [articles[i] for i in sorted({a["article_id"] for a in out_assertions}) if i in articles])
    graph_entities = [e for e in entities.values() if e["entity_id"] in in_graph]
    summary = {"created_at": now(), "run_name": run_name, "wikidata_requests": wd.calls, "wikidata_failed_lookups": wd.failures, "roster_clubs": len(club_qids), "roster_people": len(roster.label), "roster_failed_clubs": roster.failed, "excluded_articles_skipped": len(excluded), "rejected_by_year_check": sum(1 for v in persons.values() if v.get("rejected_candidate")), "person_contexts": len(persons),
               "assertions": len(out_assertions), "eligible_assertions": len(eligible),
               "surface_person_names": len(people), "short_names_expanded_in_article": len(expansion),
               "entities_before_scope": len(entities), "nodes": len(graph_entities),
               "nodes_by_type": dict(Counter(e["entity_type"] for e in graph_entities)),
               "nodes_with_qid": sum(1 for e in graph_entities if e["qid"]),
               "resolution_methods": dict(Counter(e["resolution_method"] for e in graph_entities)),
               "merged_surface_forms": sum(len(e["aliases"]) - 1 for e in graph_entities),
               "edges": len(relations), "edges_by_type": dict(Counter(r["relation_type"] for r in relations)),
               "edges_dropped_out_of_scope": len({(a["subject_id"], a["relation_type"], a["object_id"]) for a in eligible}) - len(relations),
               "semantic_rejections": dict(Counter(a["semantic_check"] for a in out_assertions if a["semantic_check"])),
               "edges_dropped_unverified_person": dropped_unverified, "unverified_people": len(unverified),
               "edges_with_multiple_evidence": sum(1 for r in relations if r["evidence_count"] > 1),
               "edges_supported_in_both_languages": sum(1 for r in relations if len(r["languages"]) == 2)}
    write_json(out_dir / "summary.json", summary)
    return summary
