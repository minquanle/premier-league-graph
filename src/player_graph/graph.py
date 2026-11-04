from __future__ import annotations

import json
import os
from collections import defaultdict

from neo4j import GraphDatabase

from .common import DATA, REPORTS, ROOT, now, read_jsonl, write_json

DATASET = "player_graph_pilot_2026_10_29"


def settings():
    config = {}
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                config[k.strip()] = v.strip()
    # Read only the named connection variables; never print credentials.
    return {key: os.environ.get(key, config.get(key, default)) for key, default in {
        "NEO4J_URI": "bolt://localhost:7687", "NEO4J_USERNAME": "neo4j", "NEO4J_PASSWORD": ""}.items()}


# Role labels for Person nodes, read from the edges they take part in. EXTENDED_CONTRACT_WITH, NEGOTIATES_TO_SIGN and
# CRITICIZES fit players and coaches alike, so they set no role; a person with none keeps only :Person.
ROLE_EDGES = {"Player": {("subject", "PLAYS_FOR"), ("subject", "TRANSFERRED_TO"), ("subject", "LOANED_TO")},
              "Coach": {("subject", "MANAGES"), ("subject", "RESIGNED_FROM"), ("object", "APPOINTED_MANAGER_OF"),
                        ("object", "DISMISSED_MANAGER")},
              "Agent": {("subject", "REPRESENTS")}}


def person_roles(edges, occupations=None, people=()):
    """{entity_id: set of role labels}; one person can be both Player and Coach (e.g. a former player now managing).

    occupations ({qid: P106 values}) fill in only people whose edges give no role, such as pundits who once played.
    """
    roles = defaultdict(set)
    for e in edges:
        for side in ("subject", "object"):
            for role, keys in ROLE_EDGES.items():
                if (side, e["relation_type"]) in keys:
                    roles[e[f"{side}_id"]].add(role)
    if occupations:
        from .ner_role import role_from_occupations
        for person in people:
            if person not in roles and role_from_occupations(occupations.get(person, [])):
                roles[person].update(role_from_occupations(occupations[person]))
    return roles


def import_derived(run_name):
    """Add the derived networks to a dataset already in the database; they stay marked and countable apart."""
    conf = settings()
    folder = DATA / "graph" / run_name / "derived"
    counts = {}
    with GraphDatabase.driver(conf["NEO4J_URI"], auth=(conf["NEO4J_USERNAME"], conf["NEO4J_PASSWORD"])) as driver:
        with driver.session(database="neo4j") as session:
            for kind, name in [("TEAMMATE_OF", "teammate_of"), ("PLAYED_UNDER", "played_under"), ("TRANSFER_FLOW", "transfer_flow")]:
                rows = read_jsonl(folder / f"{name}.jsonl")
                session.run(f"MATCH (:FootballEntity)-[r:{kind}]->(:FootballEntity) CALL (r) {{ DELETE r }} IN TRANSACTIONS OF 5000 ROWS").consume()
                for i in range(0, len(rows), 2000):
                    session.run(f"""UNWIND $rows AS row
                        MATCH (s:FootballEntity {{entity_id:row.subject_id}}), (o:FootballEntity {{entity_id:row.object_id}})
                        CREATE (s)-[r:{kind}]->(o) SET r = row, r.dataset_id=$dataset""", rows=rows[i:i + 2000], dataset=run_name).consume()
                found = session.run(f"MATCH (:FootballEntity)-[r:{kind} {{dataset_id:$dataset}}]->() RETURN count(r) AS n", dataset=run_name).single()["n"]
                if found != len(rows):
                    raise RuntimeError(f"{kind}: database has {found}, file has {len(rows)}")
                counts[kind] = found
    return {"created_at": now(), "dataset_id": run_name, "derived_edges": counts, "counts_verified_against_files": True}


def import_neo4j(source=None, dataset=DATASET):
    """Load one extractor output folder; the database then holds exactly this dataset."""
    conf = settings()
    source = source or DATA / "processed"
    if not conf["NEO4J_PASSWORD"]:
        raise RuntimeError("NEO4J_PASSWORD not configured")
    articles = read_jsonl(source / "articles.jsonl")
    entities = read_jsonl(source / "entities.jsonl")
    assertions = read_jsonl(source / "assertions.jsonl")
    edges = read_jsonl(source / "relations.jsonl")
    if not articles or not entities:
        raise RuntimeError("Collect and extract first")
    allowed = {r["name"] for r in json.loads((ROOT / "configs/project_plan.json").read_text(encoding="utf-8"))["relation_types"]}
    with GraphDatabase.driver(conf["NEO4J_URI"], auth=(conf["NEO4J_USERNAME"], conf["NEO4J_PASSWORD"])) as driver:
        driver.verify_connectivity()
        with driver.session(database="neo4j") as session:
            for label, key in [("FootballEntity", "entity_id"), ("SourceArticle", "article_id"), ("ExtractionAssertion", "assertion_id")]:
                session.run(f"CREATE CONSTRAINT {label.lower()}_id IF NOT EXISTS FOR (n:{label}) REQUIRE n.{key} IS UNIQUE").consume()
            # Entity ids of different extractors can coincide, so the database holds one dataset at a time.
            # This touches only nodes created by this application (they all carry dataset_id); files stay the source of truth.
            # In batches: one transaction for the whole old graph runs out of Neo4j's transaction memory.
            session.run("MATCH (n) WHERE n.dataset_id IS NOT NULL CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 5000 ROWS").consume()
            for rows in [articles[i:i+100] for i in range(0, len(articles), 100)]:
                props = [{k: a[k] for k in ["article_id", "canonical_url", "source", "language", "title", "published_at", "content_hash", "duplicate_cluster_id"]} for a in rows]
                session.run("UNWIND $rows AS row MERGE (a:SourceArticle {article_id: row.article_id}) SET a += row, a.dataset_id=$dataset", rows=props, dataset=dataset).consume()
            for kind in ["Person", "Club"]:
                fields = ["entity_id", "qid", "canonical_name", "entity_type", "scope_role", "aliases", "roles", "resolution_method", "identity_review_status"]
                rows = [{k: e[k] for k in fields if k in e} for e in entities if e["entity_type"] == kind]
                session.run(f"UNWIND $rows AS row MERGE (n:FootballEntity:{kind} {{entity_id:row.entity_id}}) SET n += row, n.dataset_id=$dataset", rows=rows, dataset=dataset).consume()
            for i in range(0, len(assertions), 100):
                session.run("""UNWIND $rows AS row
                    MATCH (a:SourceArticle {article_id:row.article_id})
                    MERGE (x:ExtractionAssertion {assertion_id:row.assertion_id})
                    SET x += row, x.dataset_id=$dataset
                    MERGE (x)-[:EVIDENCED_BY]->(a)
                    WITH x,row MATCH (s:FootballEntity {entity_id:row.subject_id}), (o:FootballEntity {entity_id:row.object_id})
                    MERGE (x)-[:SUBJECT]->(s) MERGE (x)-[:OBJECT]->(o)""", rows=assertions[i:i+100], dataset=dataset).consume()
            for kind in sorted(allowed):
                rows = [e for e in edges if e["relation_type"] == kind]
                session.run(f"""UNWIND $rows AS row
                    MATCH (s:FootballEntity {{entity_id:row.subject_id}}), (o:FootballEntity {{entity_id:row.object_id}})
                    MERGE (s)-[r:{kind} {{relation_id:row.relation_id}}]->(o)
                    SET r += row, r.dataset_id=$dataset""", rows=rows, dataset=dataset).consume()
            from .ner_role import load_occupations
            roles = person_roles(edges, load_occupations(), [e["entity_id"] for e in entities if e["entity_type"] == "Person"])
            for role in ROLE_EDGES:
                ids = [i for i, r in roles.items() if role in r]
                session.run(f"UNWIND $ids AS id MATCH (n:Person {{entity_id:id}}) SET n:{role}", ids=ids).consume()
            labels = session.run("""MATCH (n:FootballEntity {dataset_id:$dataset})
                RETURN count(CASE WHEN n:Player THEN 1 END) AS players, count(CASE WHEN n:Coach THEN 1 END) AS coaches,
                       count(CASE WHEN n:Agent THEN 1 END) AS agents, count(CASE WHEN n:Club THEN 1 END) AS clubs,
                       count(CASE WHEN n:Player AND n:Coach THEN 1 END) AS player_and_coach,
                       count(CASE WHEN n:Person AND NOT (n:Player OR n:Coach OR n:Agent) THEN 1 END) AS person_without_role""",
                dataset=dataset).single().data()
            counts = session.run("""MATCH (n {dataset_id:$dataset})
                RETURN count(CASE WHEN n:SourceArticle THEN 1 END) AS articles,
                       count(CASE WHEN n:FootballEntity THEN 1 END) AS entity_candidates,
                       count(CASE WHEN n:ExtractionAssertion THEN 1 END) AS assertions""", dataset=dataset).single().data()
            rels = session.run("""MATCH (s:FootballEntity)-[r]->(o:FootballEntity)
                WHERE r.dataset_id=$dataset RETURN count(r) AS candidate_relations, count(DISTINCT type(r)) AS observed_candidate_types""", dataset=dataset).single().data()
            connected = session.run("""MATCH (n:FootballEntity {dataset_id:$dataset}) WHERE EXISTS {
                MATCH (n)-[r]-(other:FootballEntity) WHERE r.dataset_id=$dataset }
                RETURN count(n) AS entities_in_candidate_relations""", dataset=dataset).single().data()
            if counts["articles"] != len(articles) or counts["entity_candidates"] != len(entities) or rels["candidate_relations"] != len(edges):
                raise RuntimeError("Database counts disagree with exported files")
            summary = {"created_at": now(), "dataset_id": dataset, "database": "Neo4j Community 5.26.32",
                       "uri": conf["NEO4J_URI"], **counts, **rels, **connected, "node_labels": labels, "counts_verified_against_files": True,
                       "human_verified_relations": 0, "all_relation_review_status": "pending"}
    write_json(REPORTS / ("neo4j_import.json" if dataset == DATASET else f"neo4j_import_{dataset}.json"), summary)
    return summary
