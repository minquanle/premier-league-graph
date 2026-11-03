# Hướng dẫn train student Qwen3-14B trên Kaggle (offline)

Dữ liệu: `sft_main_v6` (teacher Gemini 3 Flash, 31/10/2026). Notebook chạy **không cần Internet**.

## File cần tải lên

Cả hai nằm trong `C:\Player_graph\data\kaggle_upload\`:

| File | Dùng để |
|---|---|
| `player-graph-sft-v6.zip` (khoảng 8,7 MB) | Tạo Kaggle Dataset **Private** |
| `kaggle_qwen3_14b_sft.ipynb` | Import làm notebook |

Dataset chứa toàn văn đoạn bài báo: **bắt buộc để Private**, không chia sẻ công khai.

Trong zip:

| File | Số mẫu | Ghi chú |
|---|---|---|
| `train.jsonl` | 4.324 | Gồm 162 mẫu lặp lại của các đoạn có quan hệ hiếm |
| `val.jsonl` | 521 | Dùng để tính loss khi train |
| `test.jsonl` | 527 | Tập test đầy đủ, không dùng khi train |
| `test_eval.jsonl` | 200 | Mẫu cố định lấy từ test; notebook chỉ chấm trên 200 mẫu này cho nhanh |
| `summary.json` | | Thống kê |

## Bước 1. Tạo dataset

1. Vào kaggle.com, chọn **Create → New Dataset**.
2. Kéo thả file `player-graph-sft-v6.zip`. Kaggle tự giải nén.
3. Đặt tên là `player-graph-sft-v6`, chọn **Private**, bấm **Create**.
4. Mở dataset vừa tạo, kiểm tra có đủ 5 file như bảng trên.

## Bước 2. Tạo notebook

1. Chọn **Create → New Notebook**, sau đó **File → Import Notebook**, rồi chọn `kaggle_qwen3_14b_sft.ipynb`.
2. Ở cột phải, mục **Session options**:
   - **Accelerator**: chọn GPU RTX PRO 6000 (96 GB).
   - **Internet**: Off.
   - **Persistence**: chọn *Files only* nếu có.
3. Mục **Input**, bấm **Add Input**:
   - Dataset `player-graph-sft-v6` vừa tạo.
   - Model **Qwen3-14B**. Bạn đã thêm rồi; chỉ cần kiểm tra đúng bản 14B dạng Transformers, có `config.json` và các file `.safetensors`.

## Bước 3. Chạy thử 3 ô đầu (khoảng 2 phút, nên làm)

Bấm chạy lần lượt ô **1. Kiểm tra GPU**, **2. Cài wheel** và **3. Tìm dữ liệu và model**. Kết quả đúng là:

- Ô 1 in tên GPU, khoảng 95 GB, và dòng `matmul ok`. Các thư viện `transformers` và `peft` có phiên bản; `trl` và `vllm` báo MISSING là bình thường.
- Ô 3 in `DATA_DIR = /kaggle/input/...`, `MODEL_DIR = /kaggle/input/...` và dòng `4324 521 200`.

Nếu ô 3 báo **không tìm thấy model**: mở thư mục model ở cột Input, tìm thư mục chứa `config.json`, rồi thêm một dòng ở đầu ô 3:

```python
os.environ["MODEL_DIR"] = "/kaggle/input/<đường-dẫn-tới-thư-mục-có-config.json>"
```

Nếu `peft` báo MISSING: báo lại để mình chuẩn bị file wheel.

## Bước 4. Chạy toàn bộ ở chế độ nền

Bấm **Save Version**, chọn **Save & Run All (Commit)**, rồi bấm **Save**. Chạy kiểu này thì tắt trình duyệt vẫn chạy tiếp, tối đa 12 giờ.

Thời gian dự kiến (ước tính từ lần chạy thử trước, có thể lệch):

| Phần | Thời gian |
|---|---|
| Nạp model | khoảng 5 phút |
| Baseline: Qwen3-14B gốc trên 200 mẫu | khoảng 30–60 phút |
| Train 2 epoch (khoảng 1.080 bước) | khoảng 3–4 giờ |
| Đánh giá student trên 200 mẫu | khoảng 40–80 phút |
| **Tổng** | **khoảng 5–6,5 giờ** |

Theo dõi trong tab **Logs** của version đang chạy:

- Khi sinh: `40/200 | 600s, còn khoảng 45 phút`.
- Khi train: `step 50/1080 | 10 phút, còn khoảng 200 phút`.

Sau khoảng 50 bước, nếu dòng "còn khoảng" cho thấy tổng thời gian vượt 10 giờ, hãy **Cancel**, mở lại notebook, sửa ô 4 thành `RUN_BASELINE = False` (hoặc `EPOCHS = 1`) rồi chạy lại.

## Bước 5. Lấy kết quả về

Khi version chạy xong (Complete), mở tab **Output** và tải về:

- `results.json`: điểm F1 so với teacher của model gốc và student.
- `predictions_qwen3_14b_base.jsonl` và `predictions_qwen3_14b_student.jsonl`.
- `train_log.json`.
- Thư mục `qwen3_14b_player_graph_lora/` (adapter LoRA, vài trăm MB). Chỉ cần nếu muốn dùng lại model.

Chép các file này vào `C:\Player_graph\data\train\kaggle_run2\`, rồi báo mình chấm thêm theo mức khớp lỏng và theo từng loại quan hệ.

## Lỗi hay gặp

| Lỗi | Cách xử lý |
|---|---|
| `Không thấy GPU` | Settings → Accelerator → chọn GPU, khởi động lại phiên |
| `CUDA out of memory` khi train | Ô 4: đặt `MAX_LEN = 6144` |
| `CUDA out of memory` khi sinh | Ô 4: đặt `GEN_BATCH = 4` |
| Lỗi `torchao` khi import `peft` | Ô 8 đã có đoạn vá; nếu vẫn lỗi, gửi mình nguyên thông báo |
| Hết 12 giờ trước khi xong | Checkpoint lưu sau mỗi nửa epoch trong `checkpoints/`; báo mình để thêm bước chạy tiếp từ checkpoint |
