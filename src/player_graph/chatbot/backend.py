import json
import re
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def load_config():
    return {k.strip(): v.strip().strip('"').strip("'")
            for line in (ROOT / '.env').read_text(encoding='utf-8-sig').splitlines()
            if '=' in line and not line.lstrip().startswith('#')
            for k, v in [line.split('=', 1)]}


def fetch(url, key=None, body=None, timeout=90):
    headers = {'Content-Type': 'application/json'}
    if key:
        headers['Authorization'] = 'Bearer ' + key
    request = urllib.request.Request(url, headers=headers,
                                     data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


class KaggleClient:
    def __init__(self, base_url, key, model='qwen3-4b'):
        self.base_url, self.key, self.model = base_url.rstrip('/'), key, model

    @classmethod
    def discover(cls, wait_minutes=2):
        cfg = load_config()
        deadline = time.monotonic() + wait_minutes * 60
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(f"https://ntfy.sh/{cfg['CHATBOT_NTFY_TOPIC']}/json?poll=1&since=2h", timeout=20) as response:
                    events = [json.loads(l) for l in response.read().decode().splitlines() if l]
                for event in reversed(events):
                    msg = event.get('message', '')
                    match = re.search(r'https://[a-z0-9-]+\.trycloudflare\.com', msg)
                    if not match or 'alive=False' in msg:
                        continue
                    try:
                        base = match.group()
                        models = fetch(base + '/v1/models', cfg['CHATBOT_LLM_API_KEY'], timeout=10)
                        if any(m['id'] == 'qwen3-4b' for m in models.get('data', [])):
                            return cls(base, cfg['CHATBOT_LLM_API_KEY'])
                    except Exception:
                        pass
            except Exception:
                pass
            time.sleep(10)
        raise TimeoutError('No healthy Kaggle Qwen endpoint found')

    def complete(self, messages, thinking=False, json_output=False, schema=None, temperature=None, seed=42):
        start = time.monotonic()
        body = {'model': self.model, 'messages': messages,
                'max_tokens': 3072 if thinking else 1400,
                'temperature': temperature if temperature is not None else (0.6 if thinking else 0),
                'top_p': 0.95, 'seed': seed,
                'chat_template_kwargs': {'enable_thinking': thinking}}
        if schema is not None:
            # vLLM structured output: decoding can only produce JSON matching this schema.
            body['response_format'] = {'type': 'json_schema', 'json_schema': {'name': 'plan', 'schema': schema}}
        elif json_output:
            body['response_format'] = {'type': 'json_object'}
        # Do not return chain-of-thought text, credentials, or endpoint addresses in reports.
        data = fetch(self.base_url + '/v1/chat/completions', self.key, body, timeout=150)
        choice = data['choices'][0]
        return {'content': choice['message'].get('content') or '',
                'seconds': round(time.monotonic() - start, 3),
                'usage': data.get('usage', {}), 'finish_reason': choice.get('finish_reason')}
