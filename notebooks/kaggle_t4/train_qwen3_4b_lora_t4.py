"""LoRA fine-tune of Qwen3-4B on the Player Graph SFT data, fp16, DDP over two T4 GPUs.

Run with: torchrun --nproc_per_node 2 train_qwen3_4b_lora_t4.py --data DIR --model DIR --out DIR
T4 has no bf16, so weights stay fp16 and the LoRA weights are kept in fp32 for the gradient scaler.
Loss is computed only on the answer tokens, and logits are produced only for those positions:
full-vocabulary logits for an 8k-token prompt would take about 5 GB on their own.
"""
import argparse, glob, json, math, os, time

import torch
import torch.nn.functional as F
from peft import LoraConfig, get_peft_model
# Kaggle ships torchao 0.10, which newer peft rejects; this run does not use torchao, so turn the check off.
import peft.import_utils as _piu, peft.tuners.lora.torchao as _plt
_piu.is_torchao_available = _plt.is_torchao_available = lambda: False
from transformers import AutoModelForCausalLM, AutoTokenizer, Trainer, TrainerCallback, TrainingArguments

ap = argparse.ArgumentParser()
ap.add_argument("--data", required=True)
ap.add_argument("--model", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--max-len", type=int, default=8192)
ap.add_argument("--epochs", type=float, default=2)
ap.add_argument("--lr", type=float, default=1e-4)
ap.add_argument("--grad-accum", type=int, default=4)       # x2 GPUs = effective batch 8, as for the 14B run
ap.add_argument("--lora-r", type=int, default=16)
ap.add_argument("--lora-alpha", type=int, default=32)
ap.add_argument("--max-hours", type=float, default=10.5)   # stop and save before Kaggle's 12-hour limit
ap.add_argument("--max-steps", type=int, default=-1)       # smoke test: a few steps only
ap.add_argument("--resume", default="")                    # checkpoint folder from an earlier, stopped run
args = ap.parse_args()

rank = int(os.environ.get("LOCAL_RANK", 0))
torch.cuda.set_device(rank)
log = (lambda *a: print(*a, flush=True)) if rank == 0 else (lambda *a: None)
START = time.time()

tok = AutoTokenizer.from_pretrained(args.model)
if tok.pad_token is None:
    tok.pad_token = tok.eos_token


def prompt_text(messages):
    # Non-thinking mode: the model answers with JSON directly, like the training labels.
    return tok.apply_chat_template(messages[:2], tokenize=False, add_generation_prompt=True, enable_thinking=False)


def load(split):
    with open(os.path.join(args.data, f"{split}.jsonl"), encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def encode(row):
    p_ids = tok(prompt_text(row["messages"]), add_special_tokens=False)["input_ids"]
    a_ids = tok(row["messages"][2]["content"] + "<|im_end|>\n", add_special_tokens=False)["input_ids"]
    return {"input_ids": p_ids + a_ids, "prompt_len": len(p_ids)}


# Over-long examples are dropped, not truncated: a cut-off JSON answer would teach broken output.
train = [x for x in map(encode, load("train")) if len(x["input_ids"]) <= args.max_len]
val = [x for x in map(encode, load("val")) if len(x["input_ids"]) <= args.max_len]
lens = sorted(len(x["input_ids"]) for x in train)
log(f"train {len(train)} | val {len(val)} | tokens median {lens[len(lens) // 2]} p90 {lens[int(.9 * len(lens))]} max {lens[-1]}")

model = AutoModelForCausalLM.from_pretrained(args.model, dtype=torch.float16, device_map={"": rank}, attn_implementation="sdpa")
model.config.use_cache = False
model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
model.enable_input_require_grads()
model = get_peft_model(model, LoraConfig(
    r=args.lora_r, lora_alpha=args.lora_alpha, lora_dropout=0.05, task_type="CAUSAL_LM",
    target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]))
for p in model.parameters():
    if p.requires_grad:
        p.data = p.data.float()   # the fp16 gradient scaler refuses fp16 trainable weights
if rank == 0:
    model.print_trainable_parameters()


def collate(batch):
    # Batch size is 1 per GPU, so no padding is needed.
    x = batch[0]
    ids = torch.tensor([x["input_ids"]])
    return {"input_ids": ids, "attention_mask": torch.ones_like(ids), "prompt_len": torch.tensor([x["prompt_len"]])}


from torch.utils.checkpoint import checkpoint

# T4 has no flash attention; the "math" fallback builds the full L x L attention matrix (about 3.8 GB at 8k
# tokens), so only the memory-efficient kernel is allowed. With no padding, no mask is passed (pure causal).
torch.backends.cuda.enable_math_sdp(False)
torch.backends.cuda.enable_flash_sdp(False)
torch.backends.cuda.enable_mem_efficient_sdp(True)
# The memory-efficient kernel rejects grouped-query attention passed as enable_gqa=True ("Invalid backend").
# Make transformers expand the key/value heads itself instead; that costs little memory.
import transformers.integrations.sdpa_attention as _sdpa
_sdpa.use_gqa_in_sdpa = lambda *a, **k: False

CHUNK = 512       # answer positions per lm_head call: 512 x 152k vocab in fp32 is about 0.3 GB
inner = model.base_model.model                       # Qwen3ForCausalLM inside the PEFT wrapper
NEEDS_NORM = None                                    # whether hidden_states[-1] still lacks the final norm


def chunk_loss(h, target):
    logits = inner.lm_head(h.to(inner.lm_head.weight.dtype)).float()
    return F.cross_entropy(logits, target, reduction="sum")


class AnswerOnlyTrainer(Trainer):
    def compute_loss(self, model, inputs, return_outputs=False, num_items_in_batch=None):
        global NEEDS_NORM
        ids, p = inputs["input_ids"], int(inputs["prompt_len"][0])
        # The forward still goes through the DDP wrapper (so gradients are synchronised); only one logit
        # position is produced there, and the answer logits are computed below in small chunks.
        out = model(input_ids=ids, logits_to_keep=1, output_hidden_states=True)
        h = out.hidden_states[-1][0]
        if NEEDS_NORM is None:
            with torch.no_grad():
                plain = inner.lm_head(h[-1:].to(inner.lm_head.weight.dtype)).float()
                NEEDS_NORM = not torch.allclose(plain, out.logits[0, -1:].float(), atol=1e-2, rtol=1e-2)
            log("final norm applied to hidden states:", NEEDS_NORM)
        if NEEDS_NORM:
            h = inner.model.norm(h.to(inner.model.norm.weight.dtype))
        h, target = h[p - 1:-1], ids[0, p:]          # position t predicts token t+1
        total = sum(checkpoint(chunk_loss, h[i:i + CHUNK], target[i:i + CHUNK], use_reentrant=False)
                    for i in range(0, target.shape[0], CHUNK))
        loss = total / target.shape[0]
        return (loss, out) if return_outputs else loss

    def prediction_step(self, model, inputs, prediction_loss_only, ignore_keys=None):
        with torch.no_grad():
            return self.compute_loss(model, inputs).detach(), None, None


class Guard(TrainerCallback):
    """Prints progress with a time estimate, and stops cleanly before the session limit."""
    def on_log(self, a, state, control, logs=None, **kw):
        if state.global_step and rank == 0:
            spent = time.time() - START
            left = spent / state.global_step * (state.max_steps - state.global_step)
            print(f"step {state.global_step}/{state.max_steps} | {spent / 60:.0f} phút, còn khoảng {left / 60:.0f} phút | {logs}", flush=True)

    def on_step_end(self, a, state, control, **kw):
        if time.time() - START > args.max_hours * 3600:
            control.should_save = True
            control.should_training_stop = True
            if rank == 0:
                print("Sắp hết giờ: lưu checkpoint và dừng. Chạy lại với --resume để tiếp tục.", flush=True)


steps_per_epoch = math.ceil(len(train) / 2 / args.grad_accum)
half_epoch = max(1, steps_per_epoch // 2)
targs = TrainingArguments(
    output_dir=os.path.join(args.out, "checkpoints"), num_train_epochs=args.epochs, max_steps=args.max_steps,
    learning_rate=args.lr, lr_scheduler_type="cosine", warmup_steps=10,
    per_device_train_batch_size=1, per_device_eval_batch_size=1, gradient_accumulation_steps=args.grad_accum,
    fp16=True, max_grad_norm=1.0, logging_steps=5, report_to=[],
    eval_strategy="steps", eval_steps=half_epoch, save_strategy="steps", save_steps=half_epoch, save_total_limit=2,
    remove_unused_columns=False, dataloader_num_workers=1,
    ddp_find_unused_parameters=False, gradient_checkpointing=False, seed=42)
trainer = AnswerOnlyTrainer(model=model, args=targs, train_dataset=train, eval_dataset=val, data_collator=collate, callbacks=[Guard()])

resume = args.resume or None
if resume and not os.path.isdir(resume):
    hits = sorted(glob.glob(os.path.join(resume, "**", "checkpoint-*"), recursive=True), key=lambda d: int(d.rsplit("-", 1)[-1]))
    resume = hits[-1] if hits else None
log("resume from:", resume)
trainer.train(resume_from_checkpoint=resume)

if rank == 0:
    model.save_pretrained(os.path.join(args.out, "qwen3_4b_player_graph_lora"))
    tok.save_pretrained(os.path.join(args.out, "qwen3_4b_player_graph_lora"))
    with open(os.path.join(args.out, "train_log.json"), "w") as f:
        json.dump({"log_history": trainer.state.log_history, "seconds": round(time.time() - START),
                   "config": vars(args), "train_examples": len(train), "val_examples": len(val),
                   "finished": trainer.state.global_step >= trainer.state.max_steps}, f, indent=1)
    print("Xong:", trainer.state.global_step, "/", trainer.state.max_steps, "bước", flush=True)
