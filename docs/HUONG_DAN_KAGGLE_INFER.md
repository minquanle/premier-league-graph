# Hướng dẫn chạy Qwen3-14B đã train trên Kaggle (offline)

Mục tiêu: dùng mô hình đã fine-tune để trích quan hệ trên toàn bộ đoạn bài báo, không gọi API. Làm theo hai đợt: chạy thử 300 đoạn để đo tốc độ, rồi mới chạy toàn bộ.

## File cần dùng

Nằm trong `C:\Player_graph\data\kaggle_upload\`:

| File | Dùng để |
|---|---|
| `player-graph-student-infer-v6.zip` | Tạo Kaggle Dataset **Private** (có nội dung bài báo) |
| `kaggle_qwen3_14b_infer.ipynb` | Import làm notebook |

## Bước 1. Tạo dataset

1. Chọn **Create → New Dataset**, kéo thả file zip.
2. Đặt tên `player-graph-student-infer-v6`, chọn **Private**, bấm **Create**.
3. Kiểm tra có `chunks.jsonl` và `summary.json`.

## Bước 2. Tạo notebook

1. Chọn **Create → New Notebook → File → Import Notebook**, chọn file `.ipynb`.
2. **Session options:** Accelerator là GPU RTX PRO 6000; Internet **Off**.
3. **Add Input**, thêm đủ 3 mục:
   - Dataset `player-graph-student-infer-v6`.
   - Model **Qwen3-14B** (Transformers, `14b`).
   - Dataset **Private** chứa adapter đã train, gồm `adapter_config.json` và `adapter_model.safetensors` (tải từ output của notebook train lần trước).

## Bước 3. Kiểm tra nhanh (2 phút)

Chạy ô 1 và ô 2. Kết quả đúng:
- Ô 1 in tên GPU và dung lượng bộ nhớ.
- Ô 2 in đường dẫn `chunks.jsonl`, `MODEL_DIR` và `ADAPTER_DIR`, không báo lỗi.

Nếu ô 2 báo không tìm thấy adapter, mở cột Input, tìm thư mục chứa `adapter_config.json`, rồi thêm vào đầu ô 2:

```python
os.environ["ADAPTER_DIR"] = "/kaggle/input/<đường-dẫn-thư-mục-chứa-adapter_config.json>"
```

Ô 2 cũng kiểm tra có `adapter_model.safetensors` nằm cùng thư mục. Nếu thiếu file này, tải lại dataset adapter.

## Bước 4. Chạy thử 300 đoạn

1. Ô 3 giữ `MAX_CHUNKS = 300`.
2. Bấm **Save Version → Save & Run All (Commit)**.
3. Theo dõi tab **Logs**, dòng có dạng: `32/300 | 12 phút | 22.5 giây/đoạn | còn khoảng 100 phút | batch 16`.

Dự kiến mất khoảng 1–2 giờ, chưa đo với cấu hình này. Khi xong, tải `student_predictions.jsonl` và `infer_summary.json` trong tab **Output** rồi gửi mình. 200 đoạn đầu là tập test cũ, nên mình kiểm tra được kết quả có còn đạt F1 như trước không.

Xem `estimated_hours_for_remaining` trong `infer_summary.json`. Dưới 30 giờ và còn đủ hạn mức GPU thì sang bước 5; nếu không thì dừng, nộp bằng graph Gemini.

## Bước 5. Chạy toàn bộ

1. Mở lại notebook, **Add Input** thêm output của version chạy thử (để không làm lại 300 đoạn).
2. Import lại bản notebook mới nhất (xử lý theo từng khối để dừng giữa chừng vẫn là mẫu đại diện).
3. Sửa ô 3 thành `MAX_CHUNKS = None` và `GEN_BATCH = 32`.
4. **Save Version → Save & Run All (Commit)**.

Notebook tự dừng sau 11 giờ để kịp lưu. Nếu chưa xong:
1. **Add Input** thêm output của version vừa chạy.
2. **Save & Run All** lại. Đoạn đã làm được bỏ qua và copy sang file mới.
3. Lặp lại đến khi `estimated_hours_for_remaining` bằng 0.

Chỉ cần tải `student_predictions.jsonl` của **version cuối cùng**, vì file này đã gồm toàn bộ kết quả.

## Bước 6. Đưa kết quả về máy

Chép file vào `C:\Player_graph\data\train\kaggle_infer\`, rồi chạy:

```bash
.venv/Scripts/python.exe -m player_graph.cli import-student --run-name student_v1 --predictions data/train/kaggle_infer/student_predictions.jsonl --articles data/processed/snapshot_v6.jsonl
```

```bash
.venv/Scripts/python.exe -m player_graph.cli build-graph --run-name student_v1
```

## Lỗi hay gặp

| Lỗi | Cách xử lý |
|---|---|
| `Không thấy GPU` | Chọn lại Accelerator là GPU, khởi động lại phiên |
| Log báo `Hết bộ nhớ GPU, giảm batch` | Bình thường, notebook tự giảm batch rồi chạy tiếp |
| Tốc độ thấp hơn dự kiến nhiều | Ô 3: giảm `GEN_BATCH` xuống 8, hoặc gửi mình log |
| Lỗi `torchao` khi import `peft` | Ô 5 đã có đoạn vá; nếu vẫn lỗi, gửi mình nguyên thông báo |
| Hết hạn mức GPU trong tuần | Dừng, giữ file kết quả đã có; dựng graph từ phần đã xong để so sánh |
