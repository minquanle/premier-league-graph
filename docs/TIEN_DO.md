# Tiến độ Player Graph (cập nhật 31/10/2026)

Ghi chú để tiếp tục công việc nếu tắt máy hoặc mở phiên mới. Các số liệu chi tiết nằm trong `reports/` và `data/*/summary.json`.

## Trạng thái hiện tại

| Bước | Trạng thái | File chính |
|---|---|---|
| Thu bài | Xong: kho 5.201 bài (pilot 244, v2 1.907, v3 1.000, v4 2.050) | `data/processed/articles.jsonl` |
| Lọc chất lượng | Xong cho bản chụp cuối: 5.023 bài | `data/processed/snapshot_v6.jsonl`, loại: `data/interim/excluded_articles_v2.jsonl` |
| Teacher LLM (`ag/gemini-3-flash` qua 9Router) | Xong `main_v6` trên 5.023 bài; 33 đoạn lỗi JSON | `data/llm/main_v6/` |
| Gom tên + lọc phạm vi + kiểm tra ngữ nghĩa | Xong `main_v6`: 2.229 nút, 5.206 cạnh, 12 loại; đã lọc bóng đá nữ theo câu bằng chứng; đã loại người chưa có mã Wikidata (trừ người đại diện tên đầy đủ) và 27 bài 24h lẫn nội dung | `data/graph/main_v6/`, log `reports/pipeline_v6.log` |
| Neo4j | Xong: đã nạp `main_v6` (2.229 nút, 5.206 cạnh, khớp file); nhãn 1.669 Player, 228 Coach, 29 Agent, 302 Club, 42 người vừa Player vừa Coach, 43 người không vai trò | `reports/import_neo4j_main_v6.log` |
| Student Qwen3-14B (Kaggle) | Xong: train trên `sft_main_v6` (4.322 mẫu, 2 epoch); F1 so với teacher 0,695 (chặt), 0,775 (lỏng); model gốc 0,456 và 0,545 | `data/train/kaggle_run2/`,  `notebooks/kaggle_qwen3_14b_sft.ipynb` |

Các bản `main_v1`–`main_v5`, `pilot*` là bản trung gian, **không dùng để nộp**. Graph nộp bài = `main_v6` dựng bằng luật gom tên sau rà soát chất lượng 30/10.

## Mục tiêu đề bài
≥2.000 nút, ≥5.000 cạnh, ≥10 loại quan hệ, độ chính xác >90% (cần người chấm độc lập; các rà soát hiện có là rà soát nội bộ).

## Khi bật máy lại
1. Mở Docker Desktop (Neo4j), rồi `docker compose up -d` trong `C:\Player_graph`.
2. Chạy `9router` trong một terminal (chỉ cần nếu chạy lại teacher).
3. Kiểm tra file kho không bị cắt dở: mọi dòng của `data/processed/articles.jsonl` phải là JSON hợp lệ (có bản sao `snapshot_raw_v3b.jsonl`).
4. Chạy tiếp bước còn dở. Mọi bước đều có cache, chạy lại chỉ làm phần chưa xong:

```
.venv\Scripts\python.exe -m player_graph.cli build-graph --run-name main_v6
.venv\Scripts\python.exe -m player_graph.cli graph-stats --run-name main_v6
.venv\Scripts\python.exe -m player_graph.cli import-neo4j --run-name main_v6
.venv\Scripts\python.exe -m player_graph.cli export-sft --run-name main_v6
```

## Việc còn lại
- Lưu thêm câu trước/sau cho bằng chứng trong mẫu chấm (rà soát R04).
- 33 đoạn lỗi JSON của teacher: bỏ qua, ghi vào báo cáo (dưới 1%).
- Xuất mẫu chấm mới từ `main_v6` cho người chấm độc lập.
- Student Qwen3-14B train trên `sft_main_v6` (Kaggle, 2 epoch, 2,8 giờ train): F1 so với teacher trên 200 mẫu test tăng từ 0,456 lên 0,695 (khớp chặt), 0,545 lên 0,775 (khớp lỏng). Kết quả ở `data/train/kaggle_run2/` và `reports/student_eval_kaggle_run2.json`.
- Viết báo cáo, đẩy GitHub (chưa commit lần nào; `data/` đã nằm trong `.gitignore`).
