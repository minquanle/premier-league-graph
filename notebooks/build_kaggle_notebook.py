"""Generate notebooks/kaggle_qwen3_14b_sft.ipynb (kept as code so the notebook is reviewable in git)."""
import json
from pathlib import Path

cells = []


def md(s):
    cells.append({"cell_type": "markdown", "metadata": {}, "source": s.strip("\n").splitlines(True)})


def code(s):
    cells.append({"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [], "source": s.strip("\n").splitlines(True)})


md("""
# Player Graph – fine-tune Qwen3-14B (student) từ dữ liệu teacher

Notebook chạy **không cần Internet**. Gắn qua *Add Input*:
1. Dataset riêng tư chứa `train.jsonl`, `val.jsonl`, `test.jsonl`, `test_eval.jsonl` (từ `data/train/sft_main_v6`).
2. Model Qwen3-14B (Kaggle Models hoặc dataset chứa thư mục Hugging Face có `config.json`).
3. (Tuỳ chọn) dataset chứa file wheel nếu môi trường thiếu thư viện.

Chạy bằng **Save Version → Save & Run All** để chạy nền. Kết quả nằm trong `/kaggle/working/`.
""")
md("## 1. Kiểm tra GPU và thư viện")
code(r'''
import subprocess, importlib, json, os, glob, sys, re, time, shutil
if shutil.which("nvidia-smi"):
    print(subprocess.run(["nvidia-smi"], capture_output=True, text=True).stdout)
else:
    print("nvidia-smi không có: phiên này nhiều khả năng chưa bật GPU (Settings -> Accelerator).")
import torch
print("python", sys.version.split()[0], "| torch", torch.__version__, "cuda", torch.version.cuda, "available", torch.cuda.is_available())
if not torch.cuda.is_available():
    raise SystemExit("Không thấy GPU. Bật Accelerator = GPU trong Settings, khởi động lại phiên rồi chạy lại ô này.")
if torch.cuda.is_available():
    print("device", torch.cuda.get_device_name(0), "capability", torch.cuda.get_device_capability(0),
          "memory GB", round(torch.cuda.get_device_properties(0).total_memory / 1e9, 1))
    x = torch.randn(2048, 2048, device="cuda", dtype=torch.bfloat16)
    print("matmul ok", float((x @ x).float().mean()))
for name in ["transformers", "peft", "accelerate", "trl", "bitsandbytes", "vllm"]:
    try:
        print(name, importlib.import_module(name).__version__)
    except Exception as e:
        print(name, "MISSING", type(e).__name__)
''')
md("## 2. Cài wheel offline nếu thiếu (bỏ qua nếu đã đủ)")
code(r'''
wheel_dirs = [d for d in glob.glob("/kaggle/input/*") if glob.glob(d + "/**/*.whl", recursive=True)]
print("wheel dirs:", wheel_dirs)
for d in wheel_dirs:
    subprocess.run([sys.executable, "-m", "pip", "install", "--no-index", "--find-links", d, "peft", "accelerate"], check=False)
''')
md("## 3. Tìm dữ liệu và model")
code(r'''
def find_one(pattern, label):
    hits = sorted(glob.glob(pattern, recursive=True))
    print(label, hits[:5])
    assert hits, f"Không tìm thấy {label}: kiểm tra Add Input"
    return hits[0]

DATA_DIR = os.path.dirname(find_one("/kaggle/input/**/train.jsonl", "train.jsonl"))
configs = []
for p in glob.glob("/kaggle/input/**/config.json", recursive=True):
    text = open(p).read().lower()
    if "qwen3" in text and '"hidden_size": 5120' in text:   # 5120 = Qwen3-14B
        configs.append(p)
print("model configs:", configs)
MODEL_DIR = os.environ.get("MODEL_DIR") or os.path.dirname(configs[0])
print("DATA_DIR =", DATA_DIR, "\nMODEL_DIR =", MODEL_DIR)

def load(split):
    with open(f"{DATA_DIR}/{split}.jsonl", encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]
train, val = load("train"), load("val")
# test_eval: a fixed 200-example subset of test, since generation dominates the run time.
test = load("test_eval") if os.path.exists(f"{DATA_DIR}/test_eval.jsonl") else load("test")
print(len(train), len(val), len(test))
''')
md("## 4. Cấu hình")
code(r'''
MAX_LEN = 8192          # system + bài (tối đa 6000 ký tự) + JSON đầu ra
EPOCHS = 2
LR = 1e-4
GRAD_ACCUM = 8
GEN_BATCH = 8           # số mẫu sinh cùng lúc khi đánh giá
LORA_R, LORA_ALPHA = 16, 32
RUN_BASELINE = True     # chạy Qwen3-14B gốc (chưa fine-tune) trên test để so sánh
MAX_NEW_TOKENS = 4096
OUT = "/kaggle/working"
''')
md("## 5. Tokenizer và model (bf16; GPU 96GB đủ nên không cần lượng tử hoá)")
code(r'''
from transformers import AutoTokenizer, AutoModelForCausalLM
tok = AutoTokenizer.from_pretrained(MODEL_DIR)
tok.padding_side = "left"
if tok.pad_token is None:
    tok.pad_token = tok.eos_token
# transformers 5 dùng `dtype` (tên cũ `torch_dtype` đã bị bỏ).
model = AutoModelForCausalLM.from_pretrained(MODEL_DIR, dtype=torch.bfloat16, device_map="cuda", attn_implementation="sdpa")
model.config.use_cache = False
print(round(model.num_parameters() / 1e9, 2), "B params")

def prompt_text(messages):
    # Non-thinking mode: the student answers with JSON directly, like the teacher.
    return tok.apply_chat_template(messages[:2], tokenize=False, add_generation_prompt=True, enable_thinking=False)
print(prompt_text(train[0]["messages"])[-300:])
''')
md("## 6. Hàm sinh và chấm (mức khớp với teacher)")
code(r'''
def parse(text):
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    text = m.group(1) if m else text
    s, e = text.find("{"), text.rfind("}")
    try:
        return json.loads(text[s:e + 1])
    except Exception:
        return None

def norm(s):
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()

def triples(payload):
    return {(norm(r.get("subject")), r.get("relation"), norm(r.get("object")), r.get("status"))
            for r in (payload or {}).get("relations", []) if isinstance(r, dict)}

@torch.no_grad()
def generate(m, rows, batch=GEN_BATCH):
    m.eval(); m.config.use_cache = True
    # Similar lengths in one batch waste less padding; outputs are put back in the original order.
    order = sorted(range(len(rows)), key=lambda i: len(prompt_text(rows[i]["messages"])))
    outs, t = {}, time.time()
    for i in range(0, len(order), batch):
        idx = order[i:i + batch]
        enc = tok([prompt_text(rows[j]["messages"]) for j in idx], return_tensors="pt", padding=True).to("cuda")
        gen = m.generate(**enc, max_new_tokens=MAX_NEW_TOKENS, do_sample=False, pad_token_id=tok.pad_token_id)
        for j, text in zip(idx, tok.batch_decode(gen[:, enc["input_ids"].shape[1]:], skip_special_tokens=True)):
            outs[j] = text
        done = i + len(idx)
        print(f"{done}/{len(rows)} | {round(time.time() - t)}s, còn khoảng {round((time.time() - t) / done * (len(rows) - done) / 60)} phút", flush=True)
    m.config.use_cache = False
    return [outs[i] for i in range(len(rows))]

def score(rows, outputs, name):
    tp = fp = fn = parsed = 0
    preds = []
    for r, o in zip(rows, outputs):
        p = parse(o)
        parsed += p is not None
        gold, pred = triples(json.loads(r["messages"][2]["content"])), triples(p)
        tp += len(gold & pred); fp += len(pred - gold); fn += len(gold - pred)
        preds.append({"id": r["id"], "language": r["language"], "output": o, "parsed": p is not None})
    prec, rec = tp / max(1, tp + fp), tp / max(1, tp + fn)
    res = {"model": name, "examples": len(rows), "json_parse_rate": round(parsed / len(rows), 3),
           "agreement_precision": round(prec, 3), "agreement_recall": round(rec, 3),
           "agreement_f1": round(2 * prec * rec / max(1e-9, prec + rec), 3)}
    print(res)
    with open(f"{OUT}/predictions_{name}.jsonl", "w", encoding="utf-8") as f:
        for x in preds:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")
    return res

results = []
''')
md("## 7. Baseline: Qwen3-14B gốc, chưa fine-tune")
code(r'''
if RUN_BASELINE:
    t = time.time()
    results.append({**score(test, generate(model, test), "qwen3_14b_base"), "seconds": round(time.time() - t)})
    with open(f"{OUT}/results.json", "w") as f:
        json.dump({"results": results}, f, indent=1)
''')
md("## 8. Fine-tune LoRA (chỉ tính loss trên phần JSON đầu ra)")
code(r'''
from peft import LoraConfig, get_peft_model
from transformers import Trainer, TrainingArguments, TrainerCallback
# torchao 0.10 có sẵn trên Kaggle quá cũ so với peft 0.20; notebook không dùng torchao nên tắt kiểm tra của peft.
import peft.import_utils as _piu, peft.tuners.lora.torchao as _plt
_piu.is_torchao_available = _plt.is_torchao_available = lambda: False

def encode(row):
    prompt = prompt_text(row["messages"])
    answer = row["messages"][2]["content"] + "<|im_end|>\n"
    p_ids = tok(prompt, add_special_tokens=False)["input_ids"]
    a_ids = tok(answer, add_special_tokens=False)["input_ids"]
    return {"input_ids": (p_ids + a_ids)[:MAX_LEN], "labels": ([-100] * len(p_ids) + a_ids)[:MAX_LEN]}

# Over-long examples are dropped, not truncated: a cut-off JSON answer would teach broken output.
train_enc = [x for x in map(encode, train) if len(x["input_ids"]) < MAX_LEN]
val_enc = [x for x in map(encode, val) if len(x["input_ids"]) < MAX_LEN]
lengths = [len(x["input_ids"]) for x in train_enc]
print("train", len(train_enc), "of", len(train), "| val", len(val_enc), "of", len(val),
      "| tokens max", max(lengths), "mean", sum(lengths) // len(lengths))

class Eta(TrainerCallback):
    """Prints elapsed time and an estimate of what is left, so a slow run is caught early."""
    def on_log(self, args, state, control, **kw):
        if state.global_step:
            spent = time.time() - train_start
            print(f"step {state.global_step}/{state.max_steps} | {round(spent / 60)} phút, "
                  f"còn khoảng {round(spent / state.global_step * (state.max_steps - state.global_step) / 60)} phút", flush=True)

def collate(batch):
    n = max(len(x["input_ids"]) for x in batch)
    pad = lambda seq, v: [v] * (n - len(seq)) + seq
    return {"input_ids": torch.tensor([pad(x["input_ids"], tok.pad_token_id) for x in batch]),
            "labels": torch.tensor([pad(x["labels"], -100) for x in batch]),
            "attention_mask": torch.tensor([pad([1] * len(x["input_ids"]), 0) for x in batch])}

model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
model.enable_input_require_grads()
peft_model = get_peft_model(model, LoraConfig(
    r=LORA_R, lora_alpha=LORA_ALPHA, lora_dropout=0.05, task_type="CAUSAL_LM",
    target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]))
peft_model.print_trainable_parameters()

half_epoch = max(1, len(train_enc) // GRAD_ACCUM // 2)
args = TrainingArguments(
    output_dir=f"{OUT}/checkpoints", num_train_epochs=EPOCHS, learning_rate=LR,
    per_device_train_batch_size=1, per_device_eval_batch_size=1, gradient_accumulation_steps=GRAD_ACCUM,
    lr_scheduler_type="cosine", warmup_steps=5, bf16=True, logging_steps=5,
    # Evaluate and save twice per epoch, so a stopped run still leaves a usable checkpoint.
    eval_strategy="steps", save_strategy="steps", eval_steps=half_epoch, save_steps=half_epoch,
    save_total_limit=2, load_best_model_at_end=True,
    metric_for_best_model="eval_loss", report_to="none", remove_unused_columns=False)
trainer = Trainer(model=peft_model, args=args, train_dataset=train_enc, eval_dataset=val_enc, data_collator=collate, callbacks=[Eta()])
train_start = time.time(); trainer.train(); train_seconds = round(time.time() - train_start)
peft_model.save_pretrained(f"{OUT}/qwen3_14b_player_graph_lora")
tok.save_pretrained(f"{OUT}/qwen3_14b_player_graph_lora")
with open(f"{OUT}/train_log.json", "w") as f:
    json.dump(trainer.state.log_history, f, indent=1)
print("train seconds", train_seconds)
''')
md("## 9. Đánh giá student trên tập test")
code(r'''
t = time.time()
results.append({**score(test, generate(peft_model, test), "qwen3_14b_student"), "seconds": round(time.time() - t)})
with open(f"{OUT}/results.json", "w") as f:
    json.dump({"results": results, "train_seconds": train_seconds,
               "config": {"epochs": EPOCHS, "lr": LR, "lora_r": LORA_R, "max_len": MAX_LEN, "train_examples": len(train_enc),
                          "eval_examples": len(test)}}, f, indent=1)
results
''')
md("""
## Ghi chú
- `agreement_*` đo mức **khớp với teacher**, không phải độ chính xác thật; độ chính xác thật đo bằng tập do người chấm.
- Tải về: thư mục adapter `qwen3_14b_player_graph_lora/`, `predictions_*.jsonl`, `results.json`, `train_log.json`.
""")

notebook = {"cells": cells, "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                                         "language_info": {"name": "python"}}, "nbformat": 4, "nbformat_minor": 5}
out = Path(__file__).with_name("kaggle_qwen3_14b_sft.ipynb")
out.write_text(json.dumps(notebook, ensure_ascii=False, indent=1), encoding="utf-8")
print("written", out, len(cells), "cells")
