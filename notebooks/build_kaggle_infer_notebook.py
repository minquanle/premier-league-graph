"""Generate notebooks/kaggle_qwen3_14b_infer.ipynb (kept as code so the notebook is reviewable in git)."""
import json
from pathlib import Path

cells = []


def md(s):
    cells.append({"cell_type": "markdown", "metadata": {}, "source": s.strip("\n").splitlines(True)})


def code(s):
    cells.append({"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [], "source": s.strip("\n").splitlines(True)})


md("""
# Player Graph – chạy Qwen3-14B đã train để trích quan hệ

Notebook chạy **không cần Internet** và không gọi API. Gắn qua *Add Input*:
1. Dataset riêng tư `player-graph-student-infer-v6` chứa `chunks.jsonl`.
2. Model Qwen3-14B (Transformers).
3. Dataset riêng tư chứa adapter đã train (`adapter_config.json`, `adapter_model.safetensors`).
4. (Khi chạy tiếp) Output của các lần chạy trước, chứa `student_predictions.jsonl`; đoạn đã làm sẽ được bỏ qua.

Chỉnh `MAX_CHUNKS` ở ô 3: 300 để đo tốc độ, `None` để chạy toàn bộ. Dùng **Save Version → Save & Run All**.
""")
md("## 1. Kiểm tra GPU")
code(r'''
import glob, json, os, re, shutil, subprocess, sys, time
if shutil.which("nvidia-smi"):
    print(subprocess.run(["nvidia-smi"], capture_output=True, text=True).stdout)
import torch
if not torch.cuda.is_available():
    raise SystemExit("Không thấy GPU. Bật Accelerator = GPU trong Settings, khởi động lại phiên rồi chạy lại ô này.")
print("device", torch.cuda.get_device_name(0), "memory GB", round(torch.cuda.get_device_properties(0).total_memory / 1e9, 1), "| torch", torch.__version__)
''')
md("## 2. Tìm dữ liệu, model, adapter")
code(r'''
def find_one(pattern, label, keep=lambda p: True):
    hits = sorted(p for p in glob.glob(pattern, recursive=True) if keep(p))
    print(label, hits[:5])
    assert hits, f"Không tìm thấy {label}: kiểm tra Add Input"
    return hits[0]

CHUNKS = find_one("/kaggle/input/**/chunks.jsonl", "chunks.jsonl")
configs = [p for p in glob.glob("/kaggle/input/**/config.json", recursive=True)
           if "qwen3" in open(p).read().lower() and '"hidden_size": 5120' in open(p).read()]  # 5120 = Qwen3-14B
assert configs, "Không tìm thấy model Qwen3-14B: kiểm tra Add Input"
MODEL_DIR = os.environ.get("MODEL_DIR") or os.path.dirname(configs[0])
# The adapter can be in any input (dataset or notebook output), but not an intermediate training checkpoint.
ADAPTER_DIR = os.environ.get("ADAPTER_DIR") or os.path.dirname(find_one("/kaggle/input/**/adapter_config.json", "adapter",
                                                                        keep=lambda p: "checkpoint" not in p.lower()))
assert os.path.exists(f"{ADAPTER_DIR}/adapter_model.safetensors"), f"Thiếu adapter_model.safetensors trong {ADAPTER_DIR}"
print("MODEL_DIR =", MODEL_DIR, "\nADAPTER_DIR =", ADAPTER_DIR)
''')
md("## 3. Cấu hình")
code(r'''
MAX_CHUNKS = 300        # 300 để đo tốc độ; None để chạy toàn bộ
GEN_BATCH = 16          # số đoạn sinh cùng lúc; tự giảm khi hết bộ nhớ GPU
BLOCK = 400             # sắp theo độ dài trong từng khối, để dừng giữa chừng vẫn là mẫu đại diện
MAX_NEW_TOKENS = 4096
MAX_HOURS = 11.0        # dừng sớm để Kaggle (giới hạn 12 giờ) còn kịp lưu kết quả
SKIP_FIRST = 300        # số đoạn đầu đã chạy xong ở lượt thử, không chạy lại
SHARDS = 1              # tổng số notebook chạy song song
SHARD = 0               # phần của notebook này: 0 đến SHARDS - 1
OUT = "/kaggle/working"
RESULT = f"{OUT}/student_predictions_part{SHARD}.jsonl" if SHARDS > 1 else f"{OUT}/student_predictions.jsonl"
assert 0 <= SHARD < SHARDS
''')
md("## 4. Nạp dữ liệu và bỏ qua đoạn đã làm")
code(r'''
with open(CHUNKS, encoding="utf-8") as f:
    rows = [json.loads(l) for l in f if l.strip()]
if MAX_CHUNKS:
    rows = rows[:MAX_CHUNKS]
rows = rows[SKIP_FIRST:][SHARD::SHARDS]  # each notebook takes its own chunks; together they cover the rest once
wanted = {r["id"] for r in rows}
done = {}
for path in glob.glob("/kaggle/input/**/student_predictions*.jsonl", recursive=True) + glob.glob(f"{OUT}/student_predictions*.jsonl"):
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                row = json.loads(line)
                if row["id"] in wanted:
                    done[row["id"]] = row["output"]
print("đã có sẵn", len(done))
# Earlier results are copied forward, so this version's output alone is the complete file.
with open(RESULT, "w", encoding="utf-8") as f:
    for key, output in done.items():
        f.write(json.dumps({"id": key, "output": output}, ensure_ascii=False) + "\n")
todo = [r for r in rows if r["id"] not in done]
print("tổng", len(rows), "| còn phải chạy", len(todo))
''')
md("## 5. Nạp model, gộp adapter")
code(r'''
from transformers import AutoTokenizer, AutoModelForCausalLM
from peft import PeftModel
# torchao 0.10 có sẵn trên Kaggle quá cũ so với peft 0.20; notebook không dùng torchao nên tắt kiểm tra của peft.
import peft.import_utils as _piu, peft.tuners.lora.torchao as _plt
_piu.is_torchao_available = _plt.is_torchao_available = lambda: False

t = time.time()
tok = AutoTokenizer.from_pretrained(MODEL_DIR)
tok.padding_side = "left"
if tok.pad_token is None:
    tok.pad_token = tok.eos_token
base = AutoModelForCausalLM.from_pretrained(MODEL_DIR, dtype=torch.bfloat16, device_map="cuda", attn_implementation="sdpa")
model = PeftModel.from_pretrained(base, ADAPTER_DIR).merge_and_unload()
model.eval()
model.config.use_cache = True
print("nạp xong sau", round(time.time() - t), "giây;", round(torch.cuda.memory_allocated() / 1e9, 1), "GB")

def prompt_text(messages):
    # Non-thinking mode, the same prompt format as during training.
    return tok.apply_chat_template(messages[:2], tokenize=False, add_generation_prompt=True, enable_thinking=False)
''')
md("## 6. Sinh kết quả (lưu sau mỗi lô)")
code(r'''
@torch.inference_mode()
def generate(texts):
    enc = tok(texts, return_tensors="pt", padding=True).to("cuda")
    gen = model.generate(**enc, max_new_tokens=MAX_NEW_TOKENS, do_sample=False, pad_token_id=tok.pad_token_id)
    return tok.batch_decode(gen[:, enc["input_ids"].shape[1]:], skip_special_tokens=True)

# Similar lengths in one batch waste less padding, but sorting the whole set would leave only long chunks for last:
# sort inside blocks of the (already shuffled) dataset, so a run stopped early is still a representative sample.
prompts = {r["id"]: prompt_text(r["messages"]) for r in todo}
order = [r for k in range(0, len(todo), BLOCK) for r in sorted(todo[k:k + BLOCK], key=lambda r: len(prompts[r["id"]]))]
started, finished, batch, i, ooms = time.time(), 0, GEN_BATCH, 0, 0
stopped_early = False
with open(RESULT, "a", encoding="utf-8") as f:
    while i < len(order):
        if i % BLOCK == 0:
            batch = GEN_BATCH  # short chunks at the start of a block can use the full batch again
        if (time.time() - started) / 3600 > MAX_HOURS:
            stopped_early = True
            print("Hết thời gian cho phép, dừng để lưu kết quả.")
            break
        part = order[i:i + batch]
        try:
            outputs = generate([prompts[r["id"]] for r in part])
        except torch.cuda.OutOfMemoryError:
            torch.cuda.empty_cache()
            ooms += 1
            if batch == 1:
                print("Bỏ qua đoạn quá dài:", part[0]["id"])
                i += 1
                continue
            batch = max(1, batch // 2)
            print("Hết bộ nhớ GPU, giảm batch còn", batch, flush=True)
            continue
        for r, output in zip(part, outputs):
            f.write(json.dumps({"id": r["id"], "output": output}, ensure_ascii=False) + "\n")
        f.flush()
        i += len(part)
        finished += len(part)
        spent = time.time() - started
        print(f"{finished}/{len(order)} | {round(spent / 60)} phút | {round(spent / finished, 1)} giây/đoạn | "
              f"còn khoảng {round(spent / finished * (len(order) - finished) / 60)} phút | batch {batch}", flush=True)
''')
md("## 7. Tổng kết")
code(r'''
def parses(text):
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    s, e = text.find("{"), text.rfind("}")
    try:
        json.loads(text[s:e + 1])
        return True
    except Exception:
        return False

with open(RESULT, encoding="utf-8") as f:
    results = {json.loads(l)["id"]: json.loads(l)["output"] for l in f if l.strip()}
spent = time.time() - started
total_chunks = len(rows)
speed = spent / max(1, finished)
summary = {"shard": SHARD, "shards": SHARDS, "chunks_in_this_part": total_chunks, "results_in_file": len(results), "generated_this_run": finished, "seconds": round(spent),
           "seconds_per_chunk": round(speed, 1), "estimated_hours_for_remaining": round(speed * (total_chunks - len(results)) / 3600, 1),
           "gen_batch_start": GEN_BATCH, "gen_batch_end": batch, "oom_events": ooms, "stopped_early": stopped_early,
           "json_parse_rate": round(sum(parses(v) for v in results.values()) / max(1, len(results)), 3)}
with open(f"{OUT}/infer_summary_part{SHARD}.json", "w") as f:
    json.dump(summary, f, indent=1)
print(json.dumps(summary, indent=1))
''')
md("""
## Ghi chú
- Tải về file `student_predictions*.jsonl` và `infer_summary*.json` từ tab Output.
- Chạy song song: tạo 2 notebook, đặt `SHARDS = 2`, một notebook `SHARD = 0`, notebook kia `SHARD = 1`.
- Chạy tiếp: thêm output của version vừa chạy vào Input, đặt `MAX_CHUNKS = None`, rồi Save & Run All lại.
""")

notebook = {"cells": cells, "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                                         "language_info": {"name": "python"}}, "nbformat": 4, "nbformat_minor": 5}
out = Path(__file__).with_name("kaggle_qwen3_14b_infer.ipynb")
out.write_text(json.dumps(notebook, ensure_ascii=False, indent=1), encoding="utf-8")
print("written", out, len(cells), "cells")
