"""Waits for the Kaggle tunnel address on ntfy, then calls Qwen3-4B from this machine.

python scripts/test_remote_llm.py [--wait-minutes 20]
Reads CHATBOT_LLM_API_KEY and CHATBOT_NTFY_TOPIC from .env; never prints the key.
"""
import json, pathlib, sys, time, urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
env = dict(l.split("=", 1) for l in (ROOT / ".env").read_text(encoding="utf-8").splitlines() if "=" in l and not l.startswith("#"))
KEY, TOPIC = env["CHATBOT_LLM_API_KEY"].strip(), env["CHATBOT_NTFY_TOPIC"].strip()
WAIT = float(sys.argv[sys.argv.index("--wait-minutes") + 1]) if "--wait-minutes" in sys.argv else 20
since = int(time.time()) - 60


def messages():
    raw = urllib.request.urlopen(f"https://ntfy.sh/{TOPIC}/json?poll=1&since={since}", timeout=20).read().decode()
    return [json.loads(l)["message"] for l in raw.splitlines() if l.strip() and json.loads(l).get("event") == "message"]


url, deadline = None, time.time() + WAIT * 60
while time.time() < deadline:
    try:
        msgs = messages()
    except Exception as e:
        msgs = [f"(ntfy lỗi: {e})"]
    print(time.strftime("%H:%M:%S"), msgs[-1] if msgs else "(chưa có tin)", flush=True)
    if any("crashed" in m for m in msgs):
        sys.exit("vLLM trên Kaggle bị lỗi")
    hit = [m.split("url: ", 1)[1].strip() for m in msgs if m.startswith("url: ")]
    if hit:
        url = hit[-1]
        break
    time.sleep(30)
if not url:
    sys.exit("Hết thời gian chờ địa chỉ tunnel")


def call(path, payload=None):
    req = urllib.request.Request(url + path, headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
                                 data=json.dumps(payload).encode() if payload else None)
    t = time.time()
    return json.loads(urllib.request.urlopen(req, timeout=300).read()), time.time() - t


time.sleep(15)
models, _ = call("/v1/models")
print("models:", [m["id"] for m in models["data"]])
for q, think in [("Thủ đô của Anh là gì? Trả lời một câu.", False),
                 ("Viết một câu lệnh Cypher tìm các cầu thủ (Person) có cạnh PLAYS_FOR tới câu lạc bộ có canonical_name 'Chelsea'. Chỉ trả về câu lệnh.", False),
                 ("Nếu A là đồng đội của B, và B từng chơi dưới quyền HLV C, thì A và C cách nhau mấy bước trên đồ thị? Giải thích ngắn.", True)]:
    r, sec = call("/v1/chat/completions", {"model": "qwen3-4b", "messages": [{"role": "user", "content": q}],
                                           "max_tokens": 1024, "temperature": 0, "chat_template_kwargs": {"enable_thinking": think}})
    msg, use = r["choices"][0]["message"], r["usage"]
    print(f"\n[{'thinking' if think else 'no-thinking'}] {sec:.1f}s | tokens in {use['prompt_tokens']} out {use['completion_tokens']}")
    if think:
        print("reasoning:", (msg.get("reasoning_content") or msg.get("reasoning") or "")[:300].replace("\n", " "))
    print("answer:", (msg.get("content") or "")[:500])
print("\nKẾT NỐI TỪ MÁY: THÀNH CÔNG")
