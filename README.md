# Premier League Graph

Mạng xã hội cầu thủ, huấn luyện viên, người đại diện và câu lạc bộ xoay quanh Ngoại hạng Anh, xây dựng bằng xử lý ngôn ngữ tự nhiên trên báo chí tiếng Việt và tiếng Anh, lưu trong Neo4j.

## Kết quả (bản `main_v6`)

| Chỉ tiêu | Giá trị |
|---|---|
| Bài báo sau lọc | 5.023; 4.862 bài có đóng góp cạnh (VnExpress 3.901, Nhân Dân EN 473, Việt Nam News 436, Bóng Đá+ 48, khác 4) |
| Nút | 2.229: 1.669 Player, 228 Coach, 29 Agent, 302 Club (42 người vừa là Player vừa là Coach) |
| Cạnh | 5.206 |
| Loại quan hệ | 12 |
| Nút người có mã Wikidata | 1.902/1.927 (người không xác định được danh tính bị loại, trừ người đại diện có tên đầy đủ) |

12 loại quan hệ: `PLAYS_FOR`, `MANAGES`, `TRANSFERRED_TO`, `LOANED_TO`, `EXTENDED_CONTRACT_WITH`, `NEGOTIATES_TO_SIGN`, `APPOINTED_MANAGER_OF`, `DISMISSED_MANAGER`, `RESIGNED_FROM`, `REPRESENTS`, `CRITICIZES`, `CONFLICTS_WITH`. Mỗi cạnh lưu câu bằng chứng nguyên văn, bài báo nguồn, ngôn ngữ và trạng thái (đã xảy ra, tin đồn, phủ định, giả định).

## Pipeline

1. **Thu bài** (`collect_v2.py`, `corpus.py`): tìm bài theo chủ đề VnExpress và sitemap tiếng Anh theo tháng; phân loại tiêu đề; giới hạn số bài mỗi chủ đề; loại bài trùng và bài ngắn.
2. **Lọc chất lượng** (`screen.py`): loại bài về đội tuyển quốc gia, giải khác, bóng đá nữ/trẻ và bản tin tổng hợp nhiều môn; bỏ các đoạn tin vắn lạc đề.
3. **Trích xuất (teacher)** (`llm_extract.py`): mô hình ngôn ngữ lớn đọc từng đoạn (tối đa 6.000 ký tự) theo schema cố định và trả về JSON thực thể và quan hệ. Bộ kiểm tra cục bộ loại kết quả sai cấu trúc hoặc có bằng chứng không khớp nguyên văn bài. Kết quả được cache theo nội dung và phiên bản prompt.
4. **Gom tên và liên kết thực thể** (`resolve.py`): chuẩn hóa bí danh câu lạc bộ; đối chiếu Wikidata (danh sách cầu thủ theo câu lạc bộ qua SPARQL, tìm kiếm theo tên, lịch sử câu lạc bộ, kiểm tra năm sinh hợp lý); mở rộng họ thành tên đầy đủ khi trong bài chỉ có một người mang họ đó.
5. **Kiểm tra ngữ nghĩa và phạm vi**: loại câu lạc bộ bán bị ghi nhầm thành câu lạc bộ mua, "theo đuổi" bị ghi nhầm thành "đàm phán", "rời đi" bị ghi nhầm thành "từ chức", câu về bóng đá nữ; chỉ giữ cạnh nối với câu lạc bộ Ngoại hạng Anh hoặc với người có quan hệ với các câu lạc bộ đó.
6. **Neo4j** (`graph.py`): nạp nút `Person`/`Club` (thêm nhãn `Player`, `Coach`, `Agent` theo vai trò), cạnh theo loại, bài báo (`SourceArticle`) và phát biểu trích xuất (`ExtractionAssertion`) để truy ngược bằng chứng.
7. **Thống kê** (`stats.py`): bậc nút, PageRank, thành phần liên thông, mẫu chấm độc lập.

### Mô hình student (tùy chọn)

Kết quả của teacher sau khi lọc được dùng để fine-tune **Qwen3-14B** bằng LoRA, chạy offline trên Kaggle (`notebooks/kaggle_qwen3_14b_sft.ipynb`, hướng dẫn trong `docs/HUONG_DAN_KAGGLE.md`). Dữ liệu được chia train/val/test theo cụm sự kiện. Đánh giá trên 200 mẫu test, đo mức khớp với teacher:

| Mô hình | F1 khớp chặt | F1 khớp lỏng |
|---|---|---|
| Qwen3-14B gốc | 0,456 | 0,545 |
| Qwen3-14B sau fine-tune | 0,695 | 0,775 |

Graph nộp bài được dựng từ kết quả của teacher. Student là phương án chạy cục bộ không cần API.

## Cài đặt và chạy

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
.\.venv\Scripts\python.exe -m player_graph.cli build-graph --run-name main_v6
.\.venv\Scripts\python.exe -m player_graph.cli graph-stats --run-name main_v6
.\.venv\Scripts\python.exe -m player_graph.cli import-neo4j --run-name main_v6
# Dữ liệu huấn luyện student
.\.venv\Scripts\python.exe -m player_graph.cli export-sft --run-name main_v6 --articles data/processed/snapshot_v6.jsonl
# Kiểm thử
.\.venv\Scripts\python.exe -m pytest -q
```

## Dữ liệu

Thư mục `data/` (toàn văn bài báo, cache, kết quả trích xuất) không được đưa lên repository vì liên quan bản quyền nội dung báo chí. Repository chỉ chứa mã nguồn, cấu hình và số liệu tổng hợp trong `reports/` (thống kê graph, kết quả nạp Neo4j, đánh giá student, hướng dẫn chấm).

## Hạn chế đã biết

- Độ chính xác của graph chưa được người chấm độc lập kiểm chứng; mẫu chấm được tạo bằng lệnh `graph-stats`.
- Còn một số trường hợp gán nhầm danh tính khi bước dự phòng chấp nhận ứng viên Wikidata chưa khớp tên.
- Luật lọc bóng đá nữ chỉ dựa vào câu bằng chứng.
- `PLAYS_FOR` chiếm khoảng 54% số cạnh, phản ánh tần suất trên báo chí.
