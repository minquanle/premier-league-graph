"""Builds the Kaggle notebook that runs the fine-tuned Qwen3-4B (and the base model) with vLLM.

python notebooks/kaggle_infer/build_infer_notebook.py
Stage 1: the 200 held-out test chunks with the fine-tuned model and with base Qwen3-4B (for the comparison).
Stage 2: all 6,843 chunks with the fine-tuned model. Output: predictions_*.jsonl as {"id", "output"} lines,
the format `cli import-student` already reads.
"""
import json, pathlib

HERE = pathlib.Path(__file__).parent
OUT = HERE / "kernel"
OUT.mkdir(exist_ok=True)
md = lambda s: {"cell_type": "markdown", "metadata": {}, "source": s}
code = lambda s: {"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [], "source": s}

cells = [
    md("# Player Graph: suy luận Qwen3-4B (đã fine-tune) bằng vLLM trên T4 x2\n\nGiai đoạn 1: 200 đoạn kiểm tra (fine-tune và gốc). Giai đoạn 2: toàn bộ 6.843 đoạn."),
    code("import subprocess, sys, os, glob, time, json, urllib.request, concurrent.futures as cf\n"
         "print(sys.version)\n"
         "def pip(*a):\n"
         "    r = subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', *a], capture_output=True, text=True)\n"
         "    print(r.stderr[-2000:]); return r.returncode\n"
         "WHEELS = sorted({os.path.dirname(p) for p in glob.glob('/kaggle/input/**/vllm-*.whl', recursive=True)})\n"
         "if not (WHEELS and sys.version_info[:2] == (3, 12) and pip('--no-index', f'--find-links={WHEELS[0]}', 'vllm==0.28.0') == 0):\n"
         "    assert pip('vllm==0.28.0') == 0 or pip('vllm') == 0\n"
         "DATA = os.path.dirname(sorted(glob.glob('/kaggle/input/**/chunks_all.jsonl', recursive=True))[0])\n"
         "TUNED = os.path.dirname(sorted(glob.glob('/kaggle/input/**/qwen3_4b_player_graph_merged/config.json', recursive=True))[0])\n"
         "BASE = os.path.dirname(sorted(p for p in glob.glob('/kaggle/input/**/config.json', recursive=True)\n"
         "                              if 'player_graph' not in p and '\"hidden_size\": 2560' in open(p).read() and '\"num_hidden_layers\": 36' in open(p).read())[0])\n"
         "OUT = '/kaggle/working'\n"
         "load = lambda p: [json.loads(l) for l in open(p, encoding='utf-8') if l.strip()]\n"
         "chunks, test = load(f'{DATA}/chunks_all.jsonl'), load(f'{DATA}/test_eval.jsonl')\n"
         "print('DATA', DATA, '\\nTUNED', TUNED, '\\nBASE', BASE, '\\nchunks', len(chunks), 'test', len(test))"),
    code("class Server:\n"
         "    def __init__(self, model, name):\n"
         "        self.name = name\n"
         "        cmd = ['vllm', 'serve', model, '--host', '127.0.0.1', '--port', '8000', '--served-model-name', name,\n"
         "               '--tensor-parallel-size', '2', '--dtype', 'half', '--max-model-len', '12288',\n"
         "               '--gpu-memory-utilization', '0.85', '--max-num-seqs', '48', '--seed', '0']\n"
         "        self.log = open(f'{OUT}/vllm_{name}.log', 'w')\n"
         "        self.p = subprocess.Popen(cmd, stdout=self.log, stderr=subprocess.STDOUT,\n"
         "                                  env=dict(os.environ, VLLM_USE_FLASHINFER_SAMPLER='0', HF_HUB_OFFLINE='1'))\n"
         "        for i in range(500):\n"
         "            time.sleep(3)\n"
         "            assert self.p.poll() is None, open(f'{OUT}/vllm_{name}.log').read()[-5000:]\n"
         "            try:\n"
         "                urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3); break\n"
         "            except Exception: pass\n"
         "        print(name, 'ready after', i * 3, 's', flush=True)\n"
         "    def stop(self):\n"
         "        self.p.terminate(); self.p.wait(120); time.sleep(10)\n"
         "\n"
         "def ask(name, messages):\n"
         "    body = {'model': name, 'messages': messages, 'temperature': 0, 'max_tokens': 4096, 'seed': 0,\n"
         "            'chat_template_kwargs': {'enable_thinking': False}}\n"
         "    for attempt in range(3):\n"
         "        try:\n"
         "            req = urllib.request.Request('http://127.0.0.1:8000/v1/chat/completions', data=json.dumps(body).encode(),\n"
         "                                         headers={'Content-Type': 'application/json'})\n"
         "            r = json.loads(urllib.request.urlopen(req, timeout=1800).read())\n"
         "            return r['choices'][0]['message']['content'] or '', r['choices'][0]['finish_reason'], r['usage']['completion_tokens']\n"
         "        except Exception as e:\n"
         "            err = str(e)[:200]; time.sleep(2 ** attempt)\n"
         "    return '', 'error: ' + err, 0\n"
         "\n"
         "def run(name, rows, path, workers=48):\n"
         "    done = {json.loads(l)['id'] for l in open(path)} if os.path.exists(path) else set()\n"
         "    todo, t, n, toks = [r for r in rows if r['id'] not in done], time.time(), 0, 0\n"
         "    with open(path, 'a', encoding='utf-8') as f, cf.ThreadPoolExecutor(workers) as ex:\n"
         "        futs = {ex.submit(ask, name, r['messages'][:2]): r for r in todo}\n"
         "        for fu in cf.as_completed(futs):\n"
         "            out, fin, k = fu.result(); r = futs[fu]; n += 1; toks += k\n"
         "            f.write(json.dumps({'id': r['id'], 'output': out, 'finish_reason': fin}, ensure_ascii=False) + '\\n'); f.flush()\n"
         "            if n % 200 == 0 or n == len(todo):\n"
         "                s = time.time() - t\n"
         "                print(f'{name}: {n}/{len(todo)} | {s/60:.0f} phút | {toks/s:.0f} tok/s | còn khoảng {s/n*(len(todo)-n)/60:.0f} phút', flush=True)\n"
         "    return time.time() - t"),
    code("timing = {}\n"
         "srv = Server(TUNED, 'tuned')\n"
         "timing['tuned_test'] = run('tuned', test, f'{OUT}/predictions_tuned_test.jsonl')\n"
         "timing['tuned_all'] = run('tuned', chunks, f'{OUT}/predictions_tuned_all.jsonl')\n"
         "srv.stop()\n"
         "srv = Server(BASE, 'base')\n"
         "timing['base_test'] = run('base', test, f'{OUT}/predictions_base_test.jsonl')\n"
         "srv.stop()\n"
         "json.dump(timing, open(f'{OUT}/timing.json', 'w'), indent=1); print(timing)"),
]
nb = {"cells": cells, "metadata": {"kernelspec": {"name": "python3", "display_name": "Python 3", "language": "python"},
                                   "language_info": {"name": "python"}}, "nbformat": 4, "nbformat_minor": 5}
(OUT / "infer_qwen3_4b_vllm.ipynb").write_text(json.dumps(nb, ensure_ascii=False, indent=1), encoding="utf-8")
meta = {"id": "lequann/player-graph-qwen3-4b-infer-vllm", "title": "player-graph-qwen3-4b-infer-vllm",
        "code_file": "infer_qwen3_4b_vllm.ipynb", "language": "python", "kernel_type": "notebook",
        "is_private": True, "enable_gpu": True, "enable_internet": True,
        "dataset_sources": ["lequann/player-graph-qwen3-4b-infer", "huudong2003uet/vllm-wheel"],
        "kernel_sources": ["lequann/player-graph-qwen3-4b-lora-t4"],
        "model_sources": ["qwen-lm/qwen-3/transformers/4b/1"], "competition_sources": []}
(OUT / "kernel-metadata.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
print("wrote", OUT)
