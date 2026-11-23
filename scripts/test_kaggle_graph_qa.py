"""Exercise Kaggle Qwen -> read-only Cypher -> local Neo4j -> grounded answer.

Reads credentials from .env. Does not log credentials or the tunnel address.
This bounded smoke test accepts only a small MATCH/RETURN query subset.
"""
import argparse
import json
import pathlib
import re
import sys
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from neo4j import GraphDatabase, Query, READ_ACCESS
from player_graph.graph import settings


def config():
    return {k.strip(): v.strip().strip('"').strip("'")
            for line in (ROOT / ".env").read_text(encoding="utf-8-sig").splitlines()
            if "=" in line and not line.lstrip().startswith("#")
            for k, v in [line.split("=", 1)]}


def request(url, key=None, payload=None, timeout=90):
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(url, headers=headers,
                                 data=json.dumps(payload).encode() if payload is not None else None)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.load(response)


def discover(cfg, minutes, since):
    deadline = time.monotonic() + minutes * 60
    last_status = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                    f"https://ntfy.sh/{cfg['CHATBOT_NTFY_TOPIC']}/json?poll=1&since={since}",
                    timeout=20) as response:
                events = [json.loads(l) for l in response.read().decode().splitlines() if l]
            messages = [e.get("message", "") for e in events if e.get("event") == "message"]
            for message in reversed(messages):
                match = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", message)
                if match and not ("alive=False" in message):
                    url = match.group()
                    try:
                        models = request(url + "/v1/models", cfg["CHATBOT_LLM_API_KEY"], timeout=15)
                        if any(m["id"] == "qwen3-4b" for m in models.get("data", [])):
                            return url
                    except Exception:
                        pass
            status = next((m for m in reversed(messages) if m.startswith("status:")), "waiting for notebook")
            if status != last_status:
                print(status, flush=True)
                last_status = status
            if status == "status: vllm crashed":
                raise RuntimeError("Kaggle vLLM failed during startup")
        except RuntimeError:
            raise
        except Exception as error:
            print("Discovery retry:", type(error).__name__, flush=True)
        time.sleep(15)
    raise TimeoutError("No live Kaggle endpoint within wait window")


def llm(url, cfg, messages):
    start = time.monotonic()
    result = request(url + "/v1/chat/completions", cfg["CHATBOT_LLM_API_KEY"], {
        "model": "qwen3-4b", "messages": messages, "temperature": 0,
        "max_tokens": 1600, "chat_template_kwargs": {"enable_thinking": False}})
    choice = result["choices"][0]
    if choice.get("finish_reason") == "length":
        raise RuntimeError("Model output truncated")
    return choice["message"].get("content", ""), round(time.monotonic() - start, 3), result.get("usage")


def validate(text):
    query = re.sub(r"^```(?:cypher)?\s*|\s*```$", "", text.strip(), flags=re.I).strip().rstrip(";")
    # Deliberately limited to the queries in this smoke test, not a general Cypher firewall.
    if not re.match(r"^MATCH\b", query, re.I) or ";" in query or "//" in query or "/*" in query:
        raise ValueError("Only a single MATCH query is allowed")
    if re.search(r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|CALL|LOAD|FOREACH|UNION|INSERT|ALTER)\b", query, re.I):
        raise ValueError("Query outside read-only smoke-test subset")
    if not re.search(r"\bRETURN\b", query, re.I) or not re.search(r"\bLIMIT\s+(?:[1-9]|10)\s*$", query, re.I):
        raise ValueError("RETURN and a final LIMIT between 1 and 10 required")
    return query


SCHEMA = """Translate Vietnamese questions to Neo4j Cypher. Return ONLY the query.
Schema: (p:Person)-[r:PLAYS_FOR]->(c:Club); (p:Person)-[r:MANAGES]->(c:Club);
(p:Person)-[:TEAMMATE_OF]-(mate:Person); (p:Person)-[:PLAYED_UNDER]->(coach:Person).
Person and Club have canonical_name, entity_id. PLAYS_FOR/MANAGES edges have assertion_ids (list of strings).
TEAMMATE_OF is symmetric. PLAYED_UNDER goes from player to coach. Use historical meaning, never current employment.
Return name (canonical_name of answer person); include r.assertion_ids AS assertion_ids when using a direct newspaper edge.
Use RETURN DISTINCT, ORDER BY name, LIMIT 10. Do not return nodes or paths.
For a coach of a teammate, traverse TEAMMATE_OF then PLAYED_UNDER, and return coach.canonical_name AS name.
"""

CASES = [
    ("lookup", "Ai từng dẫn dắt Chelsea theo dữ liệu trong đồ thị? Tên chuẩn CLB: Chelsea.",
     "MATCH (p:Person)-[r:MANAGES]->(c:Club {canonical_name:'Chelsea'}) RETURN DISTINCT p.canonical_name AS name, r.assertion_ids AS assertion_ids ORDER BY name LIMIT 10"),
    ("multihop", "HLV nào từng dẫn dắt đồng đội của Wayne Rooney? Tên chuẩn: Wayne Rooney.",
     "MATCH (p:Person {canonical_name:'Wayne Rooney'})-[:TEAMMATE_OF]-(mate:Person)-[:PLAYED_UNDER]->(coach:Person) RETURN DISTINCT coach.canonical_name AS name ORDER BY name LIMIT 10")]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--wait-minutes", type=float, default=20)
    parser.add_argument("--since", type=int, default=int(time.time()) - 7200)
    args = parser.parse_args()
    cfg = config()
    url = discover(cfg, args.wait_minutes, args.since)
    print("Kaggle endpoint healthy; qwen3-4b available", flush=True)
    connection = settings()
    report = {"model": "qwen3-4b", "backend": "kaggle_cloudflare", "tested_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "cases": []}
    with GraphDatabase.driver(connection["NEO4J_URI"], auth=(connection["NEO4J_USERNAME"], connection["NEO4J_PASSWORD"])) as driver:
        driver.verify_connectivity()
        with driver.session(default_access_mode=READ_ACCESS) as session:
            for name, question, reference in CASES:
                item = {"test": name, "question": question}
                try:
                    messages = [{"role": "system", "content": SCHEMA}, {"role": "user", "content": question}]
                    attempts = []
                    item["attempts"] = attempts
                    for attempt in range(3):
                        raw, elapsed, usage = llm(url, cfg, messages)
                        record = {"cypher": raw, "seconds": elapsed, "usage": usage}
                        attempts.append(record)
                        try:
                            query = validate(raw)
                            session.run(Query("EXPLAIN " + query, timeout=15)).consume()
                            if name == "multihop":
                                if not re.search(r"-\[:TEAMMATE_OF\]-(?!>)", query):
                                    raise ValueError("TEAMMATE_OF must be traversed undirected: -[:TEAMMATE_OF]-. Include PLAYED_UNDER after it. Derived edges have no assertion_ids; omit that column.")
                                if "PLAYED_UNDER" not in query:
                                    raise ValueError("Missing PLAYED_UNDER after TEAMMATE_OF")
                            break
                        except Exception as error:
                            record["error"] = str(error)[:600]
                            if attempt == 2:
                                raise
                            messages.extend([{"role": "assistant", "content": raw}, {"role": "user", "content": "Fix this query. Return ONLY corrected Cypher. Error: " + str(error)[:600] + "\nDeclare each variable in MATCH before using it. Only direct newspaper edges have assertion_ids. TEAMMATE_OF and PLAYED_UNDER have no assertion_ids: omit this column for derived queries. Traverse TEAMMATE_OF undirected."}])
                    item.update(cypher_seconds=round(sum(a["seconds"] for a in attempts), 3))
                    start = time.monotonic()
                    rows = session.run(Query(query, timeout=15)).data()
                    item.update(cypher=query, rows=rows, neo4j_seconds=round(time.monotonic()-start, 3))
                    expected = session.run(Query(reference, timeout=15)).data()
                    item["matches_reference_names"] = sorted({r.get("name") for r in rows}) == sorted({r["name"] for r in expected})
                    item["reference_names"] = [r["name"] for r in expected]
                    ids = list(dict.fromkeys(a for r in rows for a in (r.get("assertion_ids") or [])[:1]))[:10]
                    if name == "lookup" and not ids:
                        # Retrieve provenance in application code even if the model omits IDs.
                        found = session.run(Query("MATCH (p:Person)-[r:MANAGES]->(:Club {canonical_name:'Chelsea'}) WHERE p.canonical_name IN $names RETURN r.assertion_ids[0] AS aid", timeout=15), names=[r["name"] for r in rows]).data()
                        ids = list(dict.fromkeys(r["aid"] for r in found if r["aid"]))[:10]
                    evidence = session.run(Query("MATCH (x:ExtractionAssertion)-[:EVIDENCED_BY]->(a:SourceArticle) WHERE x.assertion_id IN $ids RETURN x.evidence_text AS quote, a.canonical_url AS url LIMIT 20", timeout=15), ids=ids).data() if ids else []
                    item["evidence"] = evidence
                    witnesses = session.run(Query("MATCH (:Person {canonical_name:'Wayne Rooney'})-[:TEAMMATE_OF]-(mate:Person)-[:PLAYED_UNDER]->(coach:Person) WHERE coach.canonical_name IN $names RETURN coach.canonical_name AS coach, collect(DISTINCT mate.canonical_name)[0..2] AS teammates ORDER BY coach LIMIT 10", timeout=15), names=[r["name"] for r in rows]).data() if name == "multihop" else []
                    item["path_witnesses"] = witnesses
                    context = {"rows": [{k: v for k, v in r.items() if k != "assertion_ids"} for r in rows], "evidence": evidence, "limit": 10,
                               "path_witnesses": witnesses,
                               "provenance_note": "TEAMMATE_OF and PLAYED_UNDER are derived from Wikidata club periods. Rooney and mate overlapped at a club. Separately, mate and coach overlapped at a club; this need NOT overlap Rooney's own career. Do NOT say these coaches all coached during Rooney's time or coached Rooney himself."}
                    answer, elapsed, usage = llm(url, cfg, [
                        {"role": "system", "content": "Answer concisely in Vietnamese using ONLY supplied graph rows and evidence. Treat them as graph claims, not independently verified facts. Never claim current employment. At most 10 results, not exhaustive. Cite evidence URLs if present; if derived relations, explain their Wikidata overlap origin. If rows empty, say no data. Do not invent sources or people."},
                        {"role": "user", "content": question + "\nData: " + json.dumps(context, ensure_ascii=False)}])
                    item.update(answer=answer, answer_seconds=elapsed, answer_usage=usage, status="completed")
                except Exception as error:
                    item.update(status="failed", error=type(error).__name__ + ": " + str(error)[:180])
                report["cases"].append(item)
                print(json.dumps(item, ensure_ascii=False), flush=True)
    out = ROOT / "reports" / "kaggle_graph_qa_smoke.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Report:", out, flush=True)


if __name__ == "__main__":
    main()
