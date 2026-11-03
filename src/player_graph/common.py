from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
REPORTS = ROOT / "reports"


def stable_id(prefix: str, value: str) -> str:
    return prefix + "_" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).replace("đ", "d").replace("Đ", "D")
    return re.sub(r"[^a-z0-9]+", " ", "".join(c for c in text if not unicodedata.combining(c)).lower()).strip()


def canonical_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(("https", parts.netloc.lower(), parts.path.rstrip("/"), "", ""))


def now() -> str:
    return datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).isoformat()


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
