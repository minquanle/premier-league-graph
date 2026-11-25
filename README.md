# Premier League Graph

Mạng xã hội cầu thủ, huấn luyện viên, người đại diện và câu lạc bộ xoay quanh Ngoại hạng Anh, xây dựng bằng xử lý ngôn ngữ tự nhiên trên báo chí tiếng Việt và tiếng Anh, lưu trong Neo4j.

## Kết quả (bản `student_4b_v1`, graph chính)

Graph chính do Qwen3-4B đã fine-tune trích xuất, rồi lọc bằng Wikidata. Toàn bộ hệ thống, từ trích xuất đến chatbot, chỉ dùng mô hình ≤ 9B.

| Chỉ tiêu | Giá trị |
|---|---|
| Bài báo đóng góp | 6.219 (5.331 tiếng Việt, 888 tiếng Anh) |
| Nút | 2.046: 1.828 người và 218 Club; nhãn gồm 1.616 Player, 220 Coach, 32 Agent (55 người vừa là Player vừa là Coach) |
| Cạnh trích từ báo | 4.924, thuộc 12 loại quan hệ; 57,0% có từ 2 bằng chứng trở lên |
| Nút có mã Wikidata | 2.001/2.046 |
| Cạnh suy diễn, đếm riêng | 41.262 `TEAMMATE_OF`, 5.427 `PLAYED_UNDER`, 2.674 `TRANSFER_FLOW` |
| Mạng đồng đội | 1.433 người; thế giới nhỏ σ = 9,23 (L = 2,44 so với 2,05 ngẫu nhiên; C = 0,44 so với 0,04); lõi chính 41-core gồm 563 người |
| Độ chính xác (ước lượng) | ≈ 93%: 79% số cạnh trùng với graph 14B (97,1% đúng trên mẫu đã chấm), 21% còn lại chỉ có ở graph 4B (78,3% đúng trên mẫu 60 cạnh chấm sơ bộ, `reports/sample_student_4b_v1_only.csv`) |

**So sánh với graph do Qwen3-14B trích xuất** (`student_v4`): 2.147 nút, 5.111 cạnh, độ chính xác 94,0% (KTC 95%: 89,0–96,8%) trên mẫu 150 cạnh chấm nội bộ. F1 trích xuất trên 200 câu test của 14B là 0,695/0,775 (strict/lenient), của 4B là 0,629/0,681. Bộ lọc Wikidata kéo khoảng cách này xuống còn khoảng 1 điểm độ chính xác ở mức graph. Bản `main_v7` (nhãn của mô hình thầy) chỉ dùng để so sánh: 2.225 nút, 5.199 cạnh.

12 loại quan hệ: `PLAYS_FOR`, `MANAGES`, `TRANSFERRED_TO`, `LOANED_TO`, `EXTENDED_CONTRACT_WITH`, `NEGOTIATES_TO_SIGN`, `APPOINTED_MANAGER_OF`, `DISMISSED_MANAGER`, `RESIGNED_FROM`, `REPRESENTS`, `CRITICIZES`, `CONFLICTS_WITH`. Mỗi cạnh lưu câu bằng chứng nguyên văn, bài báo nguồn, ngôn ngữ và trạng thái (đã xảy ra, tin đồn, phủ định, giả định).

## Pipeline

1. **Thu bài** (`collect_v2.py`, `corpus.py`): tìm bài theo chủ đề VnExpress và sitemap tiếng Anh theo tháng; phân loại tiêu đề; giới hạn số bài mỗi chủ đề; loại bài trùng và bài ngắn.
2. **Lọc chất lượng** (`screen.py`): loại bài về đội tuyển quốc gia, giải khác, bóng đá nữ/trẻ và bản tin tổng hợp nhiều môn; bỏ các đoạn tin vắn lạc đề.
3. **Trích xuất (teacher)** (`llm_extract.py`): mô hình ngôn ngữ lớn đọc từng đoạn (tối đa 6.000 ký tự) theo schema cố định và trả về JSON thực thể và quan hệ. Bộ kiểm tra cục bộ loại kết quả sai cấu trúc hoặc có bằng chứng không khớp nguyên văn bài. Kết quả được cache theo nội dung và phiên bản prompt.
3a. **Nhận dạng tên và vai trò** (`ner_role.py`): không dùng LLM. `underthesea` (từ loại) và spaCy (NER) đề xuất tên, từ điển roster Wikidata và bí danh câu lạc bộ xác nhận, P106 của Wikidata cho biết cầu thủ hay huấn luyện viên. Kết quả dùng để đối chiếu tên do mô hình trả về.
4. **Gom tên và liên kết thực thể** (`resolve.py`): chuẩn hóa bí danh câu lạc bộ; đối chiếu Wikidata (danh sách cầu thủ theo câu lạc bộ qua SPARQL, tìm kiếm theo tên, lịch sử câu lạc bộ, kiểm tra năm sinh hợp lý); mở rộng họ thành tên đầy đủ khi trong bài chỉ có một người mang họ đó. Tên trong bài phải tương thích với tên của người được chọn; một tên riêng phổ biến đứng một mình không đủ để xác định người.
5. **Kiểm tra ngữ nghĩa và phạm vi**: loại câu lạc bộ bán bị ghi nhầm thành câu lạc bộ mua, "theo đuổi" bị ghi nhầm thành "đàm phán", "rời đi" bị ghi nhầm thành "từ chức", câu về bóng đá nữ; chỉ giữ cạnh nối với câu lạc bộ Ngoại hạng Anh hoặc với người có quan hệ với các câu lạc bộ đó.
6. **Neo4j** (`graph.py`): nạp nút `Person`/`Club` (thêm nhãn `Player`, `Coach`, `Agent` theo vai trò), cạnh theo loại, bài báo (`SourceArticle`) và phát biểu trích xuất (`ExtractionAssertion`) để truy ngược bằng chứng.
7. **Thống kê** (`stats.py`): bậc nút, PageRank, thành phần liên thông, mẫu chấm độc lập.
8. **Mạng chiếu** (`derived.py`): đồng đội và cầu thủ–huấn luyện viên chỉ được nối khi cùng câu lạc bộ trong cùng giai đoạn; luồng chuyển nhượng giữa các câu lạc bộ.
9. **Phân tích mạng** (`algorithms.py`, `queries/main.cypher`): small-world (L, C, σ, ω xấp xỉ), Louvain, Leiden, Label Propagation, PageRank, betweenness; chạy được bằng networkx và Neo4j GDS.

### Mô hình student (tùy chọn)

Kết quả của teacher sau khi lọc được dùng để fine-tune **Qwen3-14B** bằng LoRa. Dữ liệu được chia train/val/test theo cụm sự kiện. Đánh giá trên 200 mẫu test, đo mức khớp với teacher:

| Mô hình | F1 khớp chặt | F1 khớp lỏng |
|---|---|---|
| Qwen3-14B gốc | 0,456 | 0,545 |
| Qwen3-14B sau fine-tune | 0,695 | 0,775 |

Mô hình đã train có thể trích quan hệ không cần gọi API: `export-chunks` tạo đầu vào, `import-student` nhập kết quả, rồi dựng graph như bình thường.

### Kết quả phân tích mạng đồng đội

| L | L ngẫu nhiên | C | C ngẫu nhiên | σ | Cụm Leiden | Modularity |
|---|---|---|---|---|---|---|
| 2,462 | 2,058 | 0,447 | 0,039 | 9,54 | 13 | 0,434 |


## Cài đặt và chạy

### Hỏi đáp đồ thị qua mô hình Kaggle

Bản thử hỏi đáp, few-shot/thinking, công cụ phân tích và cách chạy benchmark được mô tả trong [docs/CHATBOT_QA.md](docs/CHATBOT_QA.md). Chạy `scripts/chatbot_qa.py` khi Neo4j và endpoint Qwen3-4B đang hoạt động. Kết quả benchmark là mức khớp truy vấn trên graph, không thay thế đánh giá độ chính xác dữ liệu bóng đá.

Yêu cầu: Python 3.11, Docker Desktop.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
.\.venv\Scripts\python.exe scripts/init_env.py
docker compose up -d
```

Sao chép `.env.example` thành `.env`, điền endpoint tương thích OpenAI cho mô hình teacher và mật khẩu Neo4j.

```powershell
# Thu bài và lọc
.\.venv\Scripts\python.exe -m player_graph.cli discover-v3
.\.venv\Scripts\python.exe -m player_graph.cli collect-v4
.\.venv\Scripts\python.exe -m player_graph.cli screen --snapshot data/processed/snapshot_v6.jsonl
# Trích xuất, dựng graph, thống kê, nạp Neo4j
.\.venv\Scripts\python.exe -m player_graph.cli llm-extract --run-name main_v6 --all --articles data/processed/snapshot_v6.jsonl --workers 6
.\.venv\Scripts\python.exe -m player_graph.cli build-graph --run-name main_v7 --llm-run main_v6
.\.venv\Scripts\python.exe -m player_graph.cli graph-stats --run-name main_v7
.\.venv\Scripts\python.exe -m player_graph.cli import-neo4j --run-name main_v7
# Bộ nhận dạng tên, danh sách cần soát
.\.venv\Scripts\python.exe -m player_graph.cli ner-check --llm-run main_v6 --articles data/processed/snapshot_v6.jsonl
.\.venv\Scripts\python.exe -m player_graph.cli review-list --run-name main_v7
# Mạng chiếu, phân tích mạng
.\.venv\Scripts\python.exe -m player_graph.cli derive-networks --run-name main_v7
.\.venv\Scripts\python.exe -m player_graph.cli network-analysis --run-name main_v7
.\.venv\Scripts\python.exe -m player_graph.cli import-derived --run-name main_v7
# Graph chính từ Qwen: nhập kết quả Kaggle rồi dựng có lọc Wikidata
.\.venv\Scripts\python.exe -m player_graph.cli import-student --run-name student_v4 --articles data/processed/snapshot_v8.jsonl --predictions <các file dự đoán>
.\.venv\Scripts\python.exe -m player_graph.cli build-graph --run-name student_v4 --llm-run student_v4 --confirm-clubs
# Dữ liệu huấn luyện student
.\.venv\Scripts\python.exe -m player_graph.cli export-sft --run-name main_v6 --articles data/processed/snapshot_v6.jsonl
# Kiểm thử
.\.venv\Scripts\python.exe -m pytest -q
```

## Xem graph trên Neo4j

1. Mở Docker Desktop, chạy `docker compose up -d` trong thư mục dự án; Neo4j không tự bật cùng Docker.
2. Mở http://localhost:7474/browser/, đăng nhập `bolt://localhost:7687` bằng tài khoản trong `.env`.
3. Chạy các truy vấn trong `queries/main.cypher`, bắt đầu bằng `CALL db.schema.visualization()`. Luôn giữ `LIMIT`: cơ sở dữ liệu có 51.962 nút, phần lớn là câu bằng chứng và bài báo.

## Ứng dụng: chân dung node và hỏi đáp đa bước

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[app]"
.\.venv\Scripts\python.exe -m streamlit run app/streamlit_app.py
```

Ứng dụng gồm ba trang:

- **Tổng quan mạng:** thế giới nhỏ (σ, ω), cộng đồng Leiden/Louvain, lõi k-core và hạt nhân từng cộng đồng, nhân vật trung tâm.
- **Chân dung một nút:**
  - bậc, PageRank, chỉ số lõi và cộng đồng của người đó, kèm thứ hạng trong mạng;
  - mạng quan hệ xung quanh;
  - các quan hệ trích từ báo, kèm câu bằng chứng và link bài.
- **Hỏi đáp đa bước:** câu hỏi đi qua các bước sau:
  1. Liên kết thực thể (tên đầy đủ, họ, biệt danh).
  2. Định tuyến sang công cụ phân tích mạng (PageRank, lõi, hạt nhân cộng đồng, đường nối hai người) hoặc sang bộ lập kế hoạch.
  3. Qwen3-4B lập kế hoạch các bước nhảy trên đồ thị bằng **một lần gọi nhanh**.
  4. **Năm kiểm tra tự động** (`src/player_graph/chatbot/verify.py`): dùng đủ thực thể, vai trò, từ khoá quan hệ, loại đáp án, kết quả rỗng.
  5. Chỉ khi có cảnh báo mới lập lại kế hoạch bằng chế độ suy nghĩ.
  6. Code biên dịch kế hoạch sang Cypher, truy vấn, rồi trả lời kèm bằng chứng. Đồ thị con giải thích câu trả lời được vẽ lại.

Cần Neo4j đang chạy và endpoint Qwen3-4B phục vụ bằng vLLM trên Kaggle (`notebooks/kaggle_serve`). Không có endpoint thì hai trang đầu vẫn dùng được.

## Dữ liệu

Thư mục `data/` (toàn văn bài báo, cache, kết quả trích xuất) không được đưa lên repository vì liên quan bản quyền nội dung báo chí. Repository chỉ chứa mã nguồn, cấu hình và số liệu tổng hợp trong `reports/` (thống kê graph, kết quả nạp Neo4j, đánh giá student, hướng dẫn chấm).

## Hạn chế đã biết

- Độ chính xác của graph chưa được người chấm độc lập kiểm chứng; mẫu chấm được tạo bằng lệnh `graph-stats`.
- Luật tên giảm được lỗi gán nhầm danh tính nhưng chưa loại hết, nhất là với tên một chữ trùng nhiều người; các số đo bộ nhận dạng tên hiện so với kết quả mô hình, chưa so với người chấm.
- Luật lọc bóng đá nữ chỉ dựa vào câu bằng chứng.
- Cạnh suy diễn phụ thuộc ngày thi đấu trên Wikidata; khoảng 13% giai đoạn thi đấu không dùng được. ω là giá trị xấp xỉ.
- Bộ lọc `--confirm-clubs` loại cả một số cạnh đúng khi Wikidata chưa đầy đủ; luật kiểm tra chiều chưa bao phủ quan hệ cho mượn.
- `PLAYS_FOR` chiếm khoảng 54% số cạnh, phản ánh tần suất trên báo chí.
