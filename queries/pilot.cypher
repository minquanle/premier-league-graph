// Domain entity counts exclude articles and extraction assertions.
MATCH (n:FootballEntity)
WHERE n.dataset_id = 'player_graph_pilot_2026_10_29'
RETURN n.entity_type AS type, count(*) AS entity_candidates;

MATCH (:FootballEntity)-[r]->(:FootballEntity)
WHERE r.dataset_id = 'player_graph_pilot_2026_10_29'
RETURN type(r) AS relation, count(*) AS candidates ORDER BY candidates DESC;

// Small graph for visual inspection; every relation is still a candidate.
MATCH p=(n:FootballEntity)-[r]->(m:FootballEntity)
WHERE r.dataset_id = 'player_graph_pilot_2026_10_29'
RETURN p LIMIT 80;

// Inspect extraction evidence and the original article URL.
MATCH (x:ExtractionAssertion)-[:EVIDENCED_BY]->(a:SourceArticle)
WHERE x.dataset_id = 'player_graph_pilot_2026_10_29'
RETURN x.relation_type, x.assertion_status, x.evidence_text, a.canonical_url LIMIT 30;

// Verify no accepted relation has been claimed without human review.
MATCH (:FootballEntity)-[r]->(:FootballEntity)
WHERE r.dataset_id = 'player_graph_pilot_2026_10_29' AND r.review_status = 'human_verified'
RETURN count(r) AS human_verified_relations;
