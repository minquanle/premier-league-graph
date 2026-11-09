// Truy vấn mẫu cho graph nộp bài. Đổi "student_v4" nếu nạp bộ khác.

// --- Tổng quát ---
// Lược đồ: các loại nút và cạnh
CALL db.schema.visualization();
// Số nút theo nhãn
MATCH (n) UNWIND labels(n) AS nhan RETURN nhan, count(*) AS so_nut ORDER BY so_nut DESC;
// Số cạnh theo 12 loại quan hệ trích từ báo (tổng 5.111)
MATCH (:FootballEntity)-[r]->(:FootballEntity) WHERE NOT type(r) IN ["TEAMMATE_OF", "PLAYED_UNDER", "TRANSFER_FLOW"]
RETURN type(r) AS quan_he, count(*) AS so_canh ORDER BY so_canh DESC;

// --- Xem mạng ---
// Quan hệ trích từ báo, chỉ 200 cạnh để trình duyệt không chậm
MATCH (a:FootballEntity)-[r]->(b:FootballEntity) WHERE r.dataset_id = "student_v4" AND r.source_type IS NULL RETURN a, r, b LIMIT 200;

// Quanh một câu lạc bộ
MATCH (c:Club {canonical_name: "Arsenal"})-[r]-(p) WHERE r.source_type IS NULL RETURN c, r, p LIMIT 150;

// Các HLV bị sa thải
MATCH (c:Club)-[r:DISMISSED_MANAGER]->(h:Coach) RETURN c.canonical_name AS clb, h.canonical_name AS hlv, r.evidence_count AS bang_chung ORDER BY bang_chung DESC LIMIT 25;

// Truy ngược một cạnh về câu bằng chứng và bài báo
MATCH (x:ExtractionAssertion)-[:SUBJECT]->(:Person {canonical_name: "Antony"}), (x)-[:EVIDENCED_BY]->(a:SourceArticle)
RETURN x.relation_type AS quan_he, x.assertion_status AS trang_thai, x.evidence_text AS bang_chung, a.canonical_url AS link LIMIT 20;

// --- Cạnh suy diễn ("cùng thầy cùng hát") ---
// Đồng đội của một cầu thủ, xếp theo thời gian chơi chung
MATCH (p:Player {canonical_name: "Alejandro Garnacho"})-[r:TEAMMATE_OF]-(q) RETURN q.canonical_name AS dong_doi, r.years_together AS so_nam ORDER BY so_nam DESC LIMIT 25;

// Cầu thủ từng chơi dưới một HLV
MATCH (p:Player)-[r:PLAYED_UNDER]->(h:Coach {canonical_name: "Erik ten Hag"}) RETURN p.canonical_name AS cau_thu, r.years_together AS so_nam ORDER BY so_nam DESC LIMIT 25;

// Hai cầu thủ nối với nhau qua bao nhiêu đồng đội
MATCH path = shortestPath((a:Player {canonical_name: "Kobbie Mainoo"})-[:TEAMMATE_OF*..6]-(b:Player {canonical_name: "Harry Kane"})) RETURN path;

// Tuyến chuyển nhượng nhiều nhất
MATCH (a:Club)-[r:TRANSFER_FLOW]->(b:Club) RETURN a.canonical_name AS tu, b.canonical_name AS den, r.moves AS so_vu ORDER BY so_vu DESC LIMIT 20;

// --- Thuật toán bằng Neo4j GDS, chạy từng khối theo thứ tự ---
CALL gds.graph.project("teammates", "Player", {TEAMMATE_OF: {orientation: "UNDIRECTED"}});

CALL gds.louvain.stream("teammates") YIELD nodeId, communityId
RETURN communityId, count(*) AS so_nguoi, collect(gds.util.asNode(nodeId).canonical_name)[..6] AS vi_du ORDER BY so_nguoi DESC LIMIT 10;

CALL gds.leiden.stats("teammates") YIELD communityCount, modularity RETURN communityCount, modularity;

CALL gds.pageRank.stream("teammates") YIELD nodeId, score RETURN gds.util.asNode(nodeId).canonical_name AS ten, score ORDER BY score DESC LIMIT 15;

CALL gds.betweenness.stream("teammates", {samplingSize: 300, samplingSeed: 42}) YIELD nodeId, score RETURN gds.util.asNode(nodeId).canonical_name AS ten, score ORDER BY score DESC LIMIT 15;

CALL gds.localClusteringCoefficient.stats("teammates") YIELD averageClusteringCoefficient RETURN averageClusteringCoefficient;

CALL gds.graph.drop("teammates");
