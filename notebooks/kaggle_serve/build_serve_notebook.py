"""Builds the Kaggle notebook that serves Qwen3-4B with vLLM and exposes it through a tunnel.

python notebooks/kaggle_serve/build_serve_notebook.py [--hours 1.5]
The API key and the ntfy topic are read from .env (CHATBOT_LLM_API_KEY, CHATBOT_NTFY_TOPIC) and written
into the private notebook. The tunnel address is published to https://ntfy.sh/<topic>, so the laptop can
find it while the session is still running (Kaggle only hands out logs after a run ends).
"""
import json, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
HERE = pathlib.Path(__file__).parent
env = dict(l.split("=", 1) for l in (ROOT / ".env").read_text(encoding="utf-8").splitlines() if "=" in l and not l.startswith("#"))
KEY, TOPIC = env["CHATBOT_LLM_API_KEY"].strip(), env["CHATBOT_NTFY_TOPIC"].strip()
HOURS = float(sys.argv[sys.argv.index("--hours") + 1]) if "--hours" in sys.argv else 1.5
OUT = HERE / "kernel"
OUT.mkdir(exist_ok=True)

md = lambda s: {"cell_type": "markdown", "metadata": {}, "source": s}
code = lambda s: {"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [], "source": s}

cells = [
    md("# Player Graph: phục vụ Qwen3-4B bằng vLLM qua tunnel\n\nNotebook riêng tư. Cần GPU T4 x2 và Internet."),
    code("import subprocess, sys, os, glob, time, json, re, urllib.request, threading\n"
         "print(sys.version)\n"
         "print(subprocess.run(['nvidia-smi', '--query-gpu=name,memory.total', '--format=csv'], capture_output=True, text=True).stdout)\n"
         f"API_KEY = {KEY!r}\nTOPIC = {TOPIC!r}\nMAX_HOURS = {HOURS}\nSTART = time.time()\n"
         "def notify(msg):\n"
         "    try:\n"
         "        urllib.request.urlopen(urllib.request.Request(f'https://ntfy.sh/{TOPIC}', data=msg.encode()), timeout=20)\n"
         "    except Exception as e:\n"
         "        print('ntfy lỗi', e)\n"
         "notify('status: notebook started')"),
    code("# Cài vLLM (có Internet). Ảnh Kaggle mới dùng Python 3.13; nếu bản cố định lỗi thì thử bản mới nhất.\n"
         "def pip(*a):\n"
         "    r = subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', *a], capture_output=True, text=True)\n"
         "    print(r.stdout[-1500:], r.stderr[-3000:]); return r.returncode\n"
         "t = time.time()\n"
         "# Wheel dataset huudong2003uet/vllm-wheel holds cp312 wheels: usable only on a Python 3.12 image.\n"
         "WHEELS = sorted({os.path.dirname(p) for p in glob.glob('/kaggle/input/**/vllm-*.whl', recursive=True)})\n"
         "print('wheel dirs', WHEELS)\n"
         "if WHEELS and sys.version_info[:2] == (3, 12) and pip('--no-index', f'--find-links={WHEELS[0]}', 'vllm==0.28.0') == 0:\n"
         "    pass\n"
         "elif pip('vllm==0.28.0') != 0:\n"
         "    assert pip('vllm') == 0, 'Không cài được vLLM'\n"
         "import importlib; print('vllm', importlib.import_module('vllm').__version__, '|', round(time.time() - t), 's')\n"
         "notify('status: vllm installed')"),
    code("cfgs = [p for p in glob.glob('/kaggle/input/**/config.json', recursive=True)\n"
         "        if '\"hidden_size\": 2560' in open(p).read() and '\"num_hidden_layers\": 36' in open(p).read()]\n"
         "MODEL = os.path.dirname(sorted(cfgs)[0]); print('MODEL', MODEL)\n"
         "env = dict(os.environ, VLLM_USE_FLASHINFER_SAMPLER='0', HF_HUB_OFFLINE='1')\n"
         "cmd = ['vllm', 'serve', MODEL, '--host', '127.0.0.1', '--port', '8000', '--api-key', API_KEY,\n"
         "       '--served-model-name', 'qwen3-4b', '--tensor-parallel-size', '2', '--dtype', 'half',\n"
         "       '--max-model-len', '12288', '--gpu-memory-utilization', '0.85', '--max-num-seqs', '32', '--reasoning-parser', 'qwen3', '--seed', '0']\n"
         "vlog = open('/kaggle/working/vllm.log', 'w')\n"
         "server = subprocess.Popen(cmd, stdout=vlog, stderr=subprocess.STDOUT, env=env)\n"
         "for i in range(400):\n"
         "    time.sleep(3)\n"
         "    if server.poll() is not None:\n"
         "        print(open('/kaggle/working/vllm.log').read()[-6000:]); notify('status: vllm crashed'); raise SystemExit('vLLM dừng')\n"
         "    try:\n"
         "        urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3); break\n"
         "    except Exception:\n"
         "        if i % 20 == 0: print('đang nạp model...', i * 3, 's', flush=True)\n"
         "print('vLLM sẵn sàng'); notify('status: vllm ready')"),
    code("# cloudflared quick tunnel: không cần tài khoản; địa chỉ ngẫu nhiên được gửi lên ntfy.\n"
         "subprocess.run(['wget', '-q', 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64', '-O', '/kaggle/working/cloudflared'], check=True)\n"
         "os.chmod('/kaggle/working/cloudflared', 0o755)\n"
         "tun = subprocess.Popen(['/kaggle/working/cloudflared', 'tunnel', '--no-autoupdate', '--url', 'http://127.0.0.1:8000'],\n"
         "                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)\n"
         "URL = None\n"
         "for line in tun.stdout:\n"
         "    m = re.search(r'https://[a-z0-9-]+\\.trycloudflare\\.com', line)\n"
         "    if m: URL = m.group(0); break\n"
         "threading.Thread(target=lambda: [None for _ in tun.stdout], daemon=True).start()\n"
         "print('URL', URL); notify(f'url: {URL}')"),
    code("# Tự kiểm tra qua chính địa chỉ công khai\n"
         "time.sleep(10)\n"
         "req = urllib.request.Request(URL + '/v1/chat/completions', headers={'Authorization': f'Bearer {API_KEY}', 'Content-Type': 'application/json'},\n"
         "    data=json.dumps({'model': 'qwen3-4b', 'messages': [{'role': 'user', 'content': 'Xin chào, trả lời một câu ngắn.'}],\n"
         "                     'max_tokens': 64, 'chat_template_kwargs': {'enable_thinking': False}}).encode())\n"
         "try:\n"
         "    print(json.loads(urllib.request.urlopen(req, timeout=120).read())['choices'][0]['message']['content'])\n"
         "except Exception as e:\n"
         "    print('tự kiểm tra lỗi', e)"),
    code("# Giữ phiên đến hết MAX_HOURS, gửi nhịp tim mỗi 10 phút\n"
         "while time.time() - START < MAX_HOURS * 3600 - 300:\n"
         "    alive = server.poll() is None and tun.poll() is None\n"
         "    notify(f'heartbeat: {URL} alive={alive} minutes={round((time.time() - START) / 60)}')\n"
         "    if not alive: break\n"
         "    time.sleep(600)\n"
         "notify('status: stopping'); server.terminate(); tun.terminate()"),
]
nb = {"cells": cells, "metadata": {"kernelspec": {"name": "python3", "display_name": "Python 3", "language": "python"},
                                   "language_info": {"name": "python"}}, "nbformat": 4, "nbformat_minor": 5}
(OUT / "serve_qwen3_4b.ipynb").write_text(json.dumps(nb, ensure_ascii=False, indent=1), encoding="utf-8")
meta = {"id": "lequann/player-graph-qwen3-4b-serve", "title": "player-graph-qwen3-4b-serve",
        "code_file": "serve_qwen3_4b.ipynb", "language": "python", "kernel_type": "notebook",
        "is_private": True, "enable_gpu": True, "enable_internet": True,
        "dataset_sources": ["huudong2003uet/vllm-wheel"], "model_sources": ["qwen-lm/qwen-3/transformers/4b/1"], "competition_sources": [], "kernel_sources": []}
(OUT / "kernel-metadata.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
print("wrote", OUT, "| hours", HOURS)
