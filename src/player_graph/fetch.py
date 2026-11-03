from __future__ import annotations

import gzip
import json
import threading
import time
from urllib.parse import urlsplit

import requests
from protego import Protego

from .common import DATA, now, stable_id, write_json


class Fetcher:
    """Cached public HTTP requests, one in-flight request per host, robots aware."""

    def __init__(self, interval=0.8, attempts=2):
        self.interval = interval
        self.attempts = attempts
        self.cache = DATA / "raw" / "http"
        self.cache.mkdir(parents=True, exist_ok=True)
        self._locks = {}
        self._last = {}
        self._robots = {}
        self._guard = threading.Lock()
        self._log_lock = threading.Lock()

    def _lock(self, host):
        with self._guard:
            return self._locks.setdefault(host, threading.RLock())

    def _record(self, row):
        with self._log_lock:
            with (self.cache.parent / "requests.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def get(self, url: str, *, robots=True) -> bytes:
        host = urlsplit(url).netloc
        if not url.startswith("https://"):
            raise ValueError("HTTPS sources required")
        key = stable_id("http", url)
        path = self.cache / (key + ".gz")
        with self._lock(host):
            if robots:
                if host not in self._robots:
                    raw = self.get(f"https://{host}/robots.txt", robots=False)
                    self._robots[host] = Protego.parse(raw.decode("utf-8", errors="replace"))
                rules = self._robots[host]
                if not all(rules.can_fetch(url, agent) for agent in ["ChatGPT-User", "PlayerGraphResearch"]):
                    self._record({"url": url, "time": now(), "outcome": "robots_disallowed"})
                    raise PermissionError(f"robots disallows {url}")
            if path.exists():
                return gzip.decompress(path.read_bytes())
            for attempt in range(self.attempts):
                wait = self.interval - (time.monotonic() - self._last.get(host, 0))
                if wait > 0:
                    time.sleep(wait)
                started = time.monotonic()
                try:
                    response = requests.get(
                        url,
                        headers={"User-Agent": "PlayerGraphResearch/0.1 (academic pilot; ChatGPT-User)"},
                        timeout=(8, 20),
                    )
                    self._last[host] = time.monotonic()
                    row = {"url": url, "final_url": response.url, "time": now(), "status": response.status_code,
                           "bytes": len(response.content), "attempt": attempt + 1,
                           "seconds": round(time.monotonic() - started, 3)}
                    self._record(row)
                    if response.status_code in {401, 403, 404, 410}:
                        response.raise_for_status()
                    if response.status_code in {429, 500, 502, 503, 504} and attempt + 1 < self.attempts:
                        time.sleep(min(10, 2 ** (attempt + 1)))
                        continue
                    response.raise_for_status()
                    # Do not follow a redirect to a different source without checking its rules.
                    if robots and urlsplit(response.url).netloc != host:
                        raise PermissionError("Cross-host redirect requires source review")
                    path.write_bytes(gzip.compress(response.content, mtime=0))
                    write_json(path.with_suffix(".json"), row)
                    return response.content
                except requests.RequestException as error:
                    self._last[host] = time.monotonic()
                    self._record({"url": url, "time": now(), "error": str(error), "attempt": attempt + 1})
                    if getattr(error, "response", None) is not None and error.response.status_code in {401, 403, 404, 410}:
                        raise
                    if attempt + 1 >= self.attempts:
                        raise
                    time.sleep(2)
        raise RuntimeError("Request failed")
