"""Teacher extraction through an OpenAI-compatible endpoint (9Router), with strict local validation.

The LLM proposes entities and relations; this module only keeps a relation when its evidence is a
verbatim span of the stored article, its relation type is in the schema and its endpoints have the
schema's entity types. Raw responses are cached so a rerun never pays for the same call twice.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import requests
from pathlib import Path
from rapidfuzz import fuzz

from .common import DATA, ROOT, normalize, now, read_jsonl, stable_id, write_json, write_jsonl

PROMPT_VERSION = "teacher-prompt-0.1"
SCHEMA = json.loads((ROOT / "configs/project_plan.json").read_text(encoding="utf-8"))
RELATIONS = {r["name"]: r for r in SCHEMA["relation_types"]}
VOCAB = json.loads((ROOT / "configs/clubs.json").read_text(encoding="utf-8"))
STATUSES = {"reported", "rumor", "negated", "hypothetical"}
ROLES = {"Player", "Coach", "Agent", "Other"}
MAX_CHUNK = 6000

CLUB_ALIASES = {}
for scope in ["core", "context"]:
    for canonical, aliases in VOCAB[scope].items():
        for alias in [canonical, *aliases]:
            CLUB_ALIASES[normalize(alias)] = (canonical, scope)


def load_env():
    """Read the project .env without printing secrets; process environment wins."""
    values = {}
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
    return {k: os.environ.get(k, values.get(k, "")) for k in ["LLM_ENABLED", "LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY"]}


def relation_guide():
    lines = []
    for r in SCHEMA["relation_types"]:
        lines.append(f"- {r['name']}: subject={r['subject']}, object={r['object']}. {r['meaning_vi']}")
    return "\n".join(lines)


SYSTEM_PROMPT = f"""You are an information extraction system that builds a knowledge graph of football (soccer)
players, coaches, agents and clubs from news articles written in Vietnamese or English.

Extract ONLY relations that the article text itself states. Do not use outside knowledge to add facts.

Allowed relation types (use the exact names):
{relation_guide()}

Rules:
1. Entities: type "Person" or "Club". For a Person give "role": Player, Coach, Agent or Other.
   Use the fullest form of the name that appears in the article (e.g. "Lampard" -> "Frank Lampard" if the
   article contains "Frank Lampard"). For a Club give its usual English name (e.g. "Quỷ đỏ", "MU",
   "Man Utd" -> "Manchester United"; "Pháo thủ" -> "Arsenal"). National teams are not clubs: skip them.
2. Every relation's subject and object must be listed in entities, with the direction given above.
3. "status": reported (the article states it happened / is true), rumor (speculation, interest, links,
   "sắp", "muốn", "linked with", "set to"), negated (denied or did not happen), hypothetical (if/could/would).
4. "evidence": copy ONE sentence (or a shorter span) from the article VERBATIM, character for character,
   in the article's original language. It must support the relation. Never translate or paraphrase it.
5. A player signing a new contract with his current club is EXTENDED_CONTRACT_WITH, not TRANSFERRED_TO.
   A loan is LOANED_TO, not TRANSFERRED_TO. For TRANSFERRED_TO/LOANED_TO the object is the DESTINATION club.
6. Quotes by the journalist are not criticism by a person. Match previews, scores and lineups alone are not relations,
   but phrases like "Chelsea midfielder X" or "HLV X của Liverpool" do support PLAYS_FOR / MANAGES.
7. If nothing qualifies, return empty lists.

Return ONLY a JSON object, no markdown:
{{"entities":[{{"name":"...","type":"Person|Club","role":"Player|Coach|Agent|Other|null"}}],
 "relations":[{{"subject":"...","relation":"...","object":"...","status":"reported|rumor|negated|hypothetical",
   "evidence":"...","time":"text time expression from the article or null"}}]}}"""


def chunks(text, limit=MAX_CHUNK):
    """Split long articles on paragraph boundaries so every evidence span stays inside one call."""
    parts, current = [], ""
    for paragraph in text.split("\n"):
        if current and len(current) + len(paragraph) + 1 > limit:
            parts.append(current)
            current = ""
        current = f"{current}\n{paragraph}" if current else paragraph
    if current:
        parts.append(current)
    return parts


class Client:
    def __init__(self, model=None, workers=4, salt=""):
        conf = load_env()
        if conf["LLM_ENABLED"].lower() != "true":
            raise RuntimeError("LLM_ENABLED is not true in .env")
        self.base = conf["LLM_BASE_URL"].rstrip("/")
        self.model = model or conf["LLM_MODEL"]
        self.key = conf["LLM_API_KEY"]
        self.cache = DATA / "cache/llm"
        self.cache.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.stats = Counter()
        self.slots = threading.Semaphore(workers)
        # A salt forces fresh calls (e.g. measuring how consistent the teacher is with itself).
        self.salt = salt

    def complete(self, user_text):
        prefix = f"{self.model}|{PROMPT_VERSION}|" + (f"salt={self.salt}|" if self.salt else "")
        key = hashlib.sha256(f"{prefix}{user_text}".encode("utf-8")).hexdigest()
        path = self.cache / f"{key}.json"
        if path.exists():
            with self.lock:
                self.stats["cache_hits"] += 1
            return json.loads(path.read_text(encoding="utf-8"))
        body = {"model": self.model, "stream": False, "temperature": 0,
                "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_text}]}
        for attempt in range(5):
            started = time.time()
            with self.slots:
                try:
                    response = requests.post(f"{self.base}/chat/completions", json=body, timeout=300,
                                             headers={"Authorization": f"Bearer {self.key}"})
                except requests.RequestException as error:
                    response, failure = None, str(error)
            with self.lock:
                self.stats["requests"] += 1
            if response is not None and response.ok:
                data = response.json()
                record = {"model": self.model, "prompt_version": PROMPT_VERSION, "created_at": now(),
                          "latency_s": round(time.time() - started, 2), "usage": data.get("usage", {}),
                          "content": data["choices"][0]["message"]["content"]}
                path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
                with self.lock:
                    for k, v in record["usage"].items():
                        if isinstance(v, int):
                            self.stats[k] += v
                return record
            failure = response.text[:300] if response is not None else failure
            with self.lock:
                self.stats["errors"] += 1
            # Rate limits and upstream hiccups: back off, then give up with the last error.
            time.sleep(min(60, 5 * 2 ** attempt) + random.random())
        raise RuntimeError(f"LLM call failed after retries: {failure}")


def parse_json(content):
    text = content.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no JSON object in response")
    return json.loads(text[start:end + 1])


def squash(text):
    """Compare spans independent of whitespace and quote style."""
    text = text.replace("“", '"').replace("”", '"').replace("’", "'").replace("‘", "'")
    return re.sub(r"\s+", " ", text).strip()


def locate_evidence(evidence, text):
    """Return (start, end, method) of the evidence in the stored text, or None when it is not there."""
    if not evidence or len(evidence) < 12:
        return None
    index = text.find(evidence)
    if index >= 0:
        return index, index + len(evidence), "exact"
    flat_text, flat_ev = squash(text), squash(evidence)
    if flat_ev in flat_text:
        mapped = _map_back(text, flat_ev, "normalized_whitespace_quotes")
        if mapped:
            return mapped
    # Small copy differences (e.g. a dropped character); accept only very close matches inside one sentence.
    best = None
    for match in re.finditer(r"[^.!?\n]+[.!?]?", text):
        score = fuzz.ratio(squash(match.group()), flat_ev)
        if score >= 95 and (best is None or score > best[0]):
            best = (score, match.start(), match.end())
    return (best[1], best[2], "fuzzy>=95") if best else None


def _map_back(text, flat_ev, method):
    words = flat_ev.split(" ")
    pattern = r"\s+".join(re.escape(w) for w in words)
    pattern = pattern.replace('"', '["“”]').replace("'", "['’‘]")
    m = re.search(pattern, text)
    return (m.start(), m.end(), method) if m else None


def canonical_club(name):
    hit = CLUB_ALIASES.get(normalize(name))
    return hit if hit else (name.strip(), "unlisted")


def type_ok(spec, entity):
    """spec like 'Person:Player|Coach' or 'Person|Club'."""
    options = spec.split("|")
    base = options[0].split(":")[0]
    if ":" not in spec:
        return entity["type"] in options
    if entity["type"] != base:
        return False
    roles = {o.split(":")[-1] for o in options}
    # The LLM's role label is a hint; an unknown role does not veto a typed relation.
    return entity.get("role") in roles or entity.get("role") in {None, "Other"}


def validate(article, chunk_text, payload):
    """Turn one LLM payload into accepted assertions and rejections, never trusting it blindly."""
    accepted, rejected = [], []
    entities = {}
    for e in payload.get("entities", []) or []:
        if not isinstance(e, dict) or not e.get("name") or e.get("type") not in {"Person", "Club"}:
            continue
        role = e.get("role") if e.get("role") in ROLES else None
        entities[normalize(e["name"])] = {"name": e["name"].strip(), "type": e["type"], "role": role if e["type"] == "Person" else None}
    folded_text = normalize(article["clean_text"])
    for r in payload.get("relations", []) or []:
        reason = None
        if not isinstance(r, dict):
            continue
        relation = r.get("relation")
        subject = entities.get(normalize(r.get("subject") or ""))
        obj = entities.get(normalize(r.get("object") or ""))
        status = r.get("status") if r.get("status") in STATUSES else None
        span = locate_evidence(r.get("evidence") or "", article["clean_text"])
        if relation not in RELATIONS:
            reason = "unknown_relation_type"
        elif not subject or not obj:
            reason = "endpoint_not_in_entities"
        elif normalize(subject["name"]) == normalize(obj["name"]):
            reason = "self_loop"
        elif not type_ok(RELATIONS[relation]["subject"], subject) or not type_ok(RELATIONS[relation]["object"], obj):
            reason = "endpoint_type_mismatch"
        elif status is None:
            reason = "invalid_status"
        elif span is None:
            reason = "evidence_not_verbatim_in_article"
        else:
            # A person's surname must occur in the article: catches names the model brought in from memory.
            for ent in (subject, obj):
                if ent["type"] == "Person" and normalize(ent["name"]).split()[-1] not in folded_text.split():
                    reason = "person_name_not_in_article"
        row = {"article_id": article["article_id"], "language": article["language"], "source_url": article["canonical_url"],
               "published_at": article["published_at"], "relation_type": relation, "status": status,
               "subject": r.get("subject"), "object": r.get("object"), "evidence_llm": r.get("evidence"), "time_text": r.get("time")}
        if reason:
            rejected.append({**row, "reason": reason})
            continue
        ends = []
        for ent in (subject, obj):
            if ent["type"] == "Club":
                name, scope = canonical_club(ent["name"])
                ends.append({"name": name, "type": "Club", "role": None, "club_scope": scope})
            else:
                ends.append({"name": ent["name"], "type": "Person", "role": ent["role"], "club_scope": None})
        start, end, method = span
        accepted.append({**row, "assertion_id": stable_id("llm_assertion", f"{article['article_id']}|{start}|{relation}|{ends[0]['name']}|{ends[1]['name']}"),
                         "subject": ends[0], "object": ends[1], "evidence_text": article["clean_text"][start:end],
                         "evidence_start": start, "evidence_end": end, "evidence_match": method,
                         "eligible_for_graph": status == "reported"})
    return accepted, rejected


def user_message(article, part):
    """The exact user turn sent to the teacher; training data reuses it so the student sees identical input."""
    return f"Title: {article['title']}\nLanguage: {article['language']}\nPublished: {article['published_at']}\n\nARTICLE:\n{part}"


def pick_articles(per_language, seed=7):
    articles = read_jsonl(DATA / "processed/articles.jsonl")
    picked = []
    for language in ["vi", "en"]:
        pool = [a for a in articles if a["language"] == language]
        picked += random.Random(seed).sample(pool, min(per_language, len(pool)))
    return picked


def run(run_name, model=None, per_language=10, workers=4, all_articles=False, articles_path=None):
    client = Client(model, workers)
    # all_articles: the whole corpus; already processed chunks come from the cache at no token cost.
    # articles_path: a frozen, screened snapshot (the live corpus file may be rewritten by a running collector).
    if articles_path:
        articles = read_jsonl(Path(articles_path))
    else:
        articles = read_jsonl(DATA / "processed/articles.jsonl") if all_articles else pick_articles(per_language)
    out = DATA / "llm" / run_name
    accepted, rejected, failures, outputs = [], [], [], []

    def work(article):
        results = []
        for i, part in enumerate(chunks(article["clean_text"])):
            record = client.complete(user_message(article, part))
            results.append((i, part, record))
        return article, results

    started = time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for future in [pool.submit(work, a) for a in articles]:
            try:
                article, results = future.result()
            except Exception as error:
                failures.append(str(error)[:300])
                continue
            for i, part, record in results:
                try:
                    payload = parse_json(record["content"])
                except (ValueError, json.JSONDecodeError) as error:
                    failures.append(f"{article['article_id']}#{i}: unparsable JSON ({error})")
                    continue
                outputs.append({"article_id": article["article_id"], "chunk": i, "payload": payload})
                ok, bad = validate(article, part, payload)
                accepted += ok
                rejected += bad
    # The model sometimes repeats a relation verbatim within one chunk; keep one copy per assertion id.
    accepted = list({a["assertion_id"]: a for a in accepted}.values())
    write_jsonl(out / "assertions.jsonl", accepted)
    write_jsonl(out / "rejected.jsonl", rejected)
    write_jsonl(out / "raw_outputs.jsonl", outputs)
    eligible = [a for a in accepted if a["eligible_for_graph"]]
    edges = {(a["subject"]["name"], a["relation_type"], a["object"]["name"]) for a in eligible}
    nodes = {n for s, _, o in edges for n in (s, o)}
    summary = {"created_at": now(), "run_name": run_name, "model": client.model, "prompt_version": PROMPT_VERSION,
               "articles": len(articles), "articles_by_language": dict(Counter(a["language"] for a in articles)),
               "elapsed_s": round(time.time() - started, 1), "llm_stats": dict(client.stats), "failures": failures,
               "assertions_accepted": len(accepted), "assertions_rejected": len(rejected),
               "rejection_reasons": dict(Counter(r["reason"] for r in rejected)),
               "status_counts": dict(Counter(a["status"] for a in accepted)),
               "eligible_assertions": len(eligible), "unique_edges": len(edges), "nodes_in_edges": len(nodes),
               "edges_per_article": round(len(edges) / max(1, len(articles)), 2),
               "edges_by_type": dict(Counter(t for _, t, _ in edges)),
               "eligible_by_language": dict(Counter(a["language"] for a in eligible)),
               "evidence_match": dict(Counter(a["evidence_match"] for a in accepted)),
               "review_status": "not_reviewed"}
    write_json(out / "summary.json", summary)
    return summary
