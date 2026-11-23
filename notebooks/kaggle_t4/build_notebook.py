"""Builds the Kaggle notebook that fine-tunes Qwen3-4B on two T4 GPUs.

python notebooks/kaggle_t4/build_notebook.py [--smoke]
Writes notebooks/kaggle_t4/kernel/ (notebook + kernel-metadata.json), ready for `kaggle kernels push`.
"""
import json, pathlib, sys

HERE = pathlib.Path(__file__).parent
SMOKE = "--smoke" in sys.argv
OUT = HERE / "kernel"
OUT.mkdir(exist_ok=True)
train_py = (HERE / "train_qwen3_4b_lora_t4.py").read_text(encoding="utf-8")

md = lambda s: {"cell_type": "markdown", "metadata": {}, "source": s}
code = lambda s: {"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [], "source": s}

cells = [
    md("# Player Graph: fine-tune Qwen3-4B bằng LoRA trên 2 GPU T4\n\n"
       "Chạy offline. Input: dataset `player-graph-sft-v6` (train/val/test) và model Qwen3-4B (Kaggle Models).\n"
       "T4 không có bf16 nên dùng fp16; trọng số LoRA giữ fp32. Hai GPU chạy song song dữ liệu (DDP) qua `torchrun`."),
    code("import subprocess, sys, os, glob, json, shutil, torch\n"
         "print(subprocess.run(['nvidia-smi'], capture_output=True, text=True).stdout)\n"
         "print('torch', torch.__version__, '| GPU', torch.cuda.device_count())\n"
         "assert torch.cuda.device_count() >= 2, 'Cần GPU T4 x2'\n"
         "import transformers, peft, accelerate\n"
         "print('transformers', transformers.__version__, '| peft', peft.__version__, '| accelerate', accelerate.__version__)"),
    code(f"SMOKE = {SMOKE}          # True: chạy thử 20 bước để kiểm tra bộ nhớ và tốc độ\n"
         "EPOCHS = 2\n"
         "MAX_HOURS = 10.5      # tự lưu checkpoint và dừng trước giới hạn 12 giờ\n"
         "OUT = '/kaggle/working'\n"
         "DATA_DIR = os.path.dirname(sorted(glob.glob('/kaggle/input/**/train.jsonl', recursive=True))[0])\n"
         "# Qwen3-4B: hidden_size 2560, 36 tầng\n"
         "cfgs = [p for p in glob.glob('/kaggle/input/**/config.json', recursive=True)\n"
         "        if '\"hidden_size\": 2560' in open(p).read() and '\"num_hidden_layers\": 36' in open(p).read()]\n"
         "MODEL_DIR = os.path.dirname(sorted(cfgs)[0])\n"
         "# Checkpoint của lần chạy trước (nếu gắn output của version trước làm input) để chạy tiếp\n"
         "prev = sorted(glob.glob('/kaggle/input/**/checkpoints/checkpoint-*', recursive=True), key=lambda d: int(d.rsplit('-', 1)[-1]))\n"
         "RESUME = prev[-1] if prev else ''\n"
         "print('DATA', DATA_DIR, '\\nMODEL', MODEL_DIR, '\\nRESUME', RESUME or '(không)')"),
    code("%%writefile /kaggle/working/train_qwen3_4b_lora_t4.py\n" + train_py),
    code("cmd = ['torchrun', '--nproc_per_node', '2', '/kaggle/working/train_qwen3_4b_lora_t4.py',\n"
         "       '--data', DATA_DIR, '--model', MODEL_DIR, '--out', OUT, '--epochs', str(EPOCHS), '--max-hours', str(MAX_HOURS)]\n"
         "if SMOKE: cmd += ['--max-steps', '20']\n"
         "if RESUME: cmd += ['--resume', RESUME]\n"
         "env = dict(os.environ, PYTORCH_CUDA_ALLOC_CONF='expandable_segments:True', TOKENIZERS_PARALLELISM='false')\n"
         "p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)\n"
         "for line in p.stdout:\n"
         "    print(line, end='', flush=True)\n"
         "assert p.wait() == 0, 'Huấn luyện lỗi, xem log phía trên'\n"
         "print(subprocess.run(['nvidia-smi', '--query-gpu=memory.used,memory.total', '--format=csv'], capture_output=True, text=True).stdout)"),
    md("## Gộp LoRA vào mô hình gốc (fp16) để chạy bằng vLLM"),
    code("import peft.import_utils as _piu, peft.tuners.lora.torchao as _plt\n"
         "_piu.is_torchao_available = _plt.is_torchao_available = lambda: False\n"
         "log = json.load(open(f'{OUT}/train_log.json'))\n"
         "print('hoàn tất' if log['finished'] else 'CHƯA xong: chạy version mới, gắn output này làm input để tiếp tục')\n"
         "if log['finished'] and not SMOKE:\n"
         "    from transformers import AutoModelForCausalLM, AutoTokenizer\n"
         "    from peft import PeftModel\n"
         "    base = AutoModelForCausalLM.from_pretrained(MODEL_DIR, dtype=torch.float16, device_map='cpu')\n"
         "    merged = PeftModel.from_pretrained(base, f'{OUT}/qwen3_4b_player_graph_lora').merge_and_unload()\n"
         "    merged.save_pretrained(f'{OUT}/qwen3_4b_player_graph_merged', safe_serialization=True)\n"
         "    AutoTokenizer.from_pretrained(MODEL_DIR).save_pretrained(f'{OUT}/qwen3_4b_player_graph_merged')\n"
         "    shutil.rmtree(f'{OUT}/checkpoints', ignore_errors=True)   # đủ rồi, giữ output gọn\n"
         "print(subprocess.run(['du', '-sh', OUT], capture_output=True, text=True).stdout)"),
]
nb = {"cells": cells, "metadata": {"kernelspec": {"name": "python3", "display_name": "Python 3", "language": "python"},
                                   "language_info": {"name": "python"}}, "nbformat": 4, "nbformat_minor": 5}
(OUT / "train_qwen3_4b_t4.ipynb").write_text(json.dumps(nb, ensure_ascii=False, indent=1), encoding="utf-8")
meta = {"id": "lequann/player-graph-qwen3-4b-lora-t4", "title": "player-graph-qwen3-4b-lora-t4",
        "code_file": "train_qwen3_4b_t4.ipynb", "language": "python", "kernel_type": "notebook",
        "is_private": True, "enable_gpu": True, "enable_internet": False,
        "dataset_sources": ["lequann/player-graph-sft-v6"],
        "model_sources": ["qwen-lm/qwen-3/transformers/4b/1"], "competition_sources": [], "kernel_sources": []}
(OUT / "kernel-metadata.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
print("wrote", OUT, "| smoke" if SMOKE else "| full")
