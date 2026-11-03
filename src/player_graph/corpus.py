from __future__ import annotations

import gzip
import json
import random
import re
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from itertools import zip_longest
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
from langdetect import DetectorFactory, detect_langs
from rapidfuzz.fuzz import ratio
from trafilatura import bare_extraction

from .common import DATA, REPORTS, ROOT, canonical_url, normalize, now, read_jsonl, stable_id, write_json, write_jsonl
from .fetch import Fetcher

DetectorFactory.seed = 42
# Initialize the library's shared language profiles before any worker threads start.
detect_langs("This English sentence initializes the language detector for the local news collection pipeline.")
SOURCES = {
    "vnexpress_vi": {"host": "vnexpress.net", "language": "vi", "seeds": ["https://vnexpress.net/the-thao/ngoai-hang-anh"]},
    "bongdaplus": {"host": "bongdaplus.vn", "language": "vi", "seeds": ["https://bongdaplus.vn/ngoai-hang-anh", "https://bongdaplus.vn/tin-chuyen-nhuong-ngoai-hang-anh"]},
    "24h": {"host": "www.24h.com.vn", "language": "vi", "seeds": ["https://www.24h.com.vn/bong-da-ngoai-hang-anh-c149.html"]},
    "vnexpress_en": {"host": "e.vnexpress.net", "language": "en", "seeds": ["https://e.vnexpress.net/news/sports/football"]},
    "nhandan_en": {"host": "en.nhandan.vn", "language": "en", "seeds": ["https://en.nhandan.vn/sports/"]},
    "vietnamnews": {"host": "vietnamnews.vn", "language": "en", "seeds": ["https://vietnamnews.vn/sports"]},
}
CLUBS = json.loads((ROOT / "configs/clubs.json").read_text(encoding="utf-8"))
CORE_TERMS = list(CLUBS["core"])
URL_TOPIC = re.compile(r"premier-league|ngoai-hang-anh|manchester|man-utd|man-city|arsenal|chelsea|liverpool|tottenham|spurs|everton|newcastle|leicester|southampton|mourinho|guardiola|klopp|arteta|rashford|haaland|salah|pogba|fernandes|sacked|transfer|koscielny|pochettino", re.I)


def article_links(html, base, source):
    soup = BeautifulSoup(html, "html.parser")
    result = []
    for a in soup.select("a[href]"):
        url = canonical_url(urljoin(base, a["href"]))
        if urlsplit(url).netloc != SOURCES[source]["host"]:
            continue
        if source == "nhandan_en":
            valid = bool(re.search(r"-post\d+\.html$", url))
        elif source == "vietnamnews":
            valid = bool(re.search(r"/sports/\d+/[^/]+\.html$", url))
        elif source == "vnexpress_en":
            valid = "/news/sports/football/" in url and bool(re.search(r"-\d+\.html$", url))
        elif source == "24h":
            valid = bool(re.search(r"/bong-da/[^/]+-c48a\d+\.html$", url))
        elif source == "bongdaplus":
            valid = bool(re.search(r"/[^/]+/[^/]+-\d+\.html$", url)) and bool(URL_TOPIC.search(url))
        else:
            valid = bool(re.search(r"/[^/]+-\d{6,}\.html$", url)) and bool(URL_TOPIC.search(url))
        if valid:
            result.append(url)
    return list(dict.fromkeys(result))


def xml_locations(raw, *, article_only=False):
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    # Several publishers return their HTML homepage with HTTP 200 for stale sitemap URLs.
    if b"<html" in raw[:1000].lower() or b"<!doctype html" in raw[:1000].lower():
        raise ValueError("HTML returned instead of sitemap XML")
    root = ET.fromstring(raw)
    locations = []
    for parent in root:
        if article_only and parent.tag.rsplit("}", 1)[-1] != "url":
            continue
        for child in parent:
            if child.tag.rsplit("}", 1)[-1] == "loc" and child.text:
                locations.append(child.text.strip())
    return locations


def discover(fetcher, max_months=36):
    errors = []

    def source_urls(source):
        conf = SOURCES[source]
        found = []
        for seed in conf["seeds"]:
            try:
                found.extend(article_links(fetcher.get(seed), seed, source))
            except Exception as e:
                errors.append({"source": source, "url": seed, "error": str(e)})
        if source in {"nhandan_en", "vietnamnews"}:
            index = f"https://{conf['host']}/sitemap.xml"
            try:
                months = [u for u in xml_locations(fetcher.get(index)) if re.search(r"news-(201[6-9]|202[0-6])-\d+\.xml$", u)]
                # Favor transfer windows, include older coverage, and randomize deterministically.
                priority = [u for u in months if re.search(r"-(1|6|7|8|9)\.xml$", u)]
                random.Random(42).shuffle(priority)
                for u in priority[:max_months]:
                    try:
                        candidates = xml_locations(fetcher.get(u), article_only=True)
                        found.extend(canonical_url(x) for x in candidates if urlsplit(x).netloc == conf["host"] and URL_TOPIC.search(x) and x.endswith(".html") and (source != "vietnamnews" or "/sports/" in x))
                    except Exception as e:
                        errors.append({"source": source, "url": u, "error": str(e)})
            except Exception as e:
                errors.append({"source": source, "url": index, "error": str(e)})
        if source == "vnexpress_en":
            # Public load-more endpoint observed in the publisher's own category HTML.
            for page in [2, 3, 5, 8, 12, 18, 25, 35]:
                url = f"https://e.vnexpress.net/category/listcategory?category_id=1004315&page={page}"
                try:
                    fragment = json.loads(fetcher.get(url))["html"]
                    found.extend(u for u in article_links(fragment, conf["seeds"][0], source) if URL_TOPIC.search(u))
                except Exception as e:
                    errors.append({"source": source, "url": url, "error": str(e)})
        # Interleave years for archive sources; preserve deterministic order elsewhere.
        found = list(dict.fromkeys(found))
        if source in {"nhandan_en", "vietnamnews"}:
            random.Random(42).shuffle(found)
        return source, found

    with ThreadPoolExecutor(max_workers=len(SOURCES)) as pool:
        groups = dict(pool.map(source_urls, SOURCES))
    rows = []
    for batch in zip_longest(*(groups[s] for s in SOURCES)):
        for source, url in zip(SOURCES, batch):
            if url:
                rows.append({"url": url, "source": source, "language": SOURCES[source]["language"], "discovery": "category_or_sitemap"})
    write_jsonl(DATA / "interim/candidates.jsonl", rows)
    write_json(REPORTS / "discovery.json", {"created_at": now(), "candidates_by_source": {s: len(v) for s, v in groups.items()}, "errors": errors})
    print(json.dumps({"stage": "discovery", "candidates": len(rows), "by_source": {s: len(v) for s, v in groups.items()}}, ensure_ascii=False), flush=True)
    return rows


def parse_date(value):
    if not value:
        return None
    value = re.sub(r"([+-])(\d):", r"\g<1>0\2:", str(value).strip())
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return None


CORE_ALIAS_PATTERN = re.compile(r"(?<!\w)(" + "|".join(sorted({re.escape(normalize(a)) for al in CLUBS["core"].values() for a in al if len(a) > 3},
                                                               key=len, reverse=True)) + r")(?!\w)")


def strip_offtopic_briefs(paragraphs):
    """Wire pages (e.g. Nhan Dan) append '* ' briefs from every sport after the main story.

    A brief stays only if it names a Premier League club, so transfer round-ups are kept and F1/tennis/other leagues go.
    """
    return [p for p in paragraphs if not p.lstrip().startswith("*") or CORE_ALIAS_PATTERN.search(normalize(p))]


def extract_article(raw: bytes, candidate: dict):
    soup = BeautifulSoup(raw, "html.parser")
    # Viet Nam News renders its logo as the first <h1>; the headline is the next one.
    headings = [" ".join(h.get_text(" ", strip=True).split()) for h in soup.find_all("h1")]
    headings = [h for h in headings if h and h not in {"Việt Nam News", "Viet Nam News"}]
    if not headings:
        raise ValueError("missing_article_h1")
    title = headings[0]
    dates = []
    for meta in soup.select('meta[property="article:published_time"], meta[name="pubdate"], meta[itemprop="datePublished"], meta[name="date"]'):
        dates.append(meta.get("content"))
    for script in soup.select('script[type="application/ld+json"]'):
        dates.extend(re.findall(r'"datePublished"\s*:\s*"([^"\n]+)"', script.get_text()))
    published = next((d for v in dates if (d := parse_date(v))), None)
    if not published and candidate["source"] == "vietnamnews":
        visible_date = soup.select_one(".datetime")
        if visible_date:
            try:
                published = datetime.strptime(visible_date.get_text(" ", strip=True), "%B %d, %Y - %H:%M").date().isoformat()
            except ValueError:
                pass
    if not published:
        raise ValueError("missing_explicit_publication_date")
    if not "2010-01-01" <= published <= datetime.now().date().isoformat():
        raise ValueError("publication_date_out_of_window")
    selectors = {
        "bongdaplus": "#postContent",
        "vnexpress_vi": "article.fck_detail, .fck_detail",
        "vnexpress_en": "article.fck_detail, .fck_detail",
        "24h": ".cate-24h-foot-arti-deta-info, .text-conent, .text-content",
        "nhandan_en": ".article__body, .cms-body",
        "vietnamnews": ".ArticleContent, .article-content, .content-detail, .detail-content, .article__body",
    }
    body = soup.select_one(selectors[candidate["source"]])
    paragraphs = []
    if body:
        for node in body.select('script, style, figure, figcaption, table, .box-tin-lien-quan, .related-news, .related, .box_brief_info, .atclRdSbIn, [class*="popup"], [class*="minigame"], [class*="tin_lien_quan"], [class*="bailienquan"], [class*="related"], [id*="minigame"]'):
            node.decompose()
        paragraphs = [" ".join(p.get_text(" ", strip=True).split()) for p in body.select("p")]
    lead = soup.select_one(".description, .article__sapo, .sapo, h2.sapo")
    lead_text = " ".join(lead.get_text(" ", strip=True).split()) if lead else ""
    if not body:
        doc = bare_extraction(raw, url=candidate["url"], include_comments=False, include_tables=False, favor_precision=True)
        if doc and doc.text:
            paragraphs = doc.text.splitlines()
    paragraphs = list(dict.fromkeys(p for p in [lead_text, *paragraphs] if len(p) >= 35 and p != title
                    and not re.search(r"Action Images via Reuters|Soccer Football -|\(Photo:|Getty Images|Tham gia ngay.*THOÁT", p)))
    paragraphs = strip_offtopic_briefs(paragraphs)
    text = title + "\n" + "\n".join(paragraphs)
    if len(text) < 650 or len(paragraphs) < 3:
        raise ValueError("short_or_incomplete_article")
    detected = detect_langs(text[:7000])
    expected = candidate["language"]
    if not detected or detected[0].lang != expected or detected[0].prob < 0.80:
        raise ValueError("language_mismatch")
    folded = normalize(text)
    title_folded = normalize(title)
    # Core-club words must occur as complete words, not substrings in unrelated names.
    core = [club for club, aliases in CLUBS["core"].items()
            if any(re.search(r"(?<!\w)" + re.escape(normalize(a)) + r"(?!\w)", folded) for a in aliases if len(a) > 3)]
    if not core:
        raise ValueError("no_premier_league_core_club")
    if not re.search(r"premier league|ngoai hang anh|football|bong da|cau thu|hlv|coach|manager|goal|striker|midfielder|defender", folded):
        raise ValueError("not_football_context")
    if re.search(r"ban quyen|broadcasting rights|where to watch|how to watch|tv rights|betting tips|soi keo", title_folded):
        raise ValueError("non_network_article_topic")
    content_key = normalize("\n".join(paragraphs))
    return {
        "article_id": stable_id("article", candidate["url"]), "canonical_url": candidate["url"],
        "source": candidate["source"], "language": expected, "language_probability": round(detected[0].prob, 5),
        "title": title, "published_at": published, "retrieved_at": now(),
        "content_hash": stable_id("text", content_key), "duplicate_cluster_id": stable_id("cluster", content_key),
        "clean_text": text, "core_clubs_mentioned": core, "extraction_version": "html-adapters-0.1",
        "scope_status": "club_and_football_text_match_not_human_validated",
        "content_extraction_method": "source_body_selector" if body else "trafilatura_fallback_requires_review",
    }


def collect(fetcher, target_per_language=150, discover_first=True):
    candidates = discover(fetcher) if discover_first else read_jsonl(DATA / "interim/candidates.jsonl")
    existing = read_jsonl(DATA / "processed/articles.jsonl")
    articles = existing[:]
    accepted_urls = {a["canonical_url"] for a in articles}
    seen_urls = set(accepted_urls)
    hashes = {a["content_hash"] for a in articles}
    counts = Counter(a["language"] for a in articles)
    old_rejections = read_jsonl(DATA / "interim/rejected_articles.jsonl")
    # A repaired adapter can reprocess cached rejected pages without downloading them again.
    retry = [r for r in old_rejections if r["source"] == "vietnamnews" and "/sports/" in r["url"]]
    queues = {lang: deque(c for c in [*retry, *candidates] if c["language"] == lang and c["url"] not in seen_urls) for lang in ["vi", "en"]}
    enqueued = {c["url"] for q in queues.values() for c in q}
    rejected = []
    expansions = Counter()

    def task(c):
        try:
            raw = fetcher.get(c["url"])
            try:
                article = extract_article(raw, c)
                return c, article, article_links(raw, c["url"], c["source"]), None
            except Exception as error:
                return c, None, article_links(raw, c["url"], c["source"]), str(error)
        except Exception as error:
            return c, None, [], str(error)

    total_attempts = 0
    with ThreadPoolExecutor(max_workers=6) as pool:
        while any(counts[l] < target_per_language and queues[l] for l in queues):
            batch = []
            for lang in queues:
                remaining = target_per_language - counts[lang]
                while queues[lang] and len([c for c in batch if c["language"] == lang]) < min(6, remaining):
                    c = queues[lang].popleft()
                    if c["url"] not in seen_urls:
                        seen_urls.add(c["url"])
                        batch.append(c)
            if not batch:
                break
            for candidate, article, links, error in pool.map(task, batch):
                total_attempts += 1
                lang = candidate["language"]
                # Follow related public articles to overcome limited category pagination.
                if expansions[candidate["source"]] < 250 and counts[lang] < target_per_language:
                    for url in links:
                        if url not in seen_urls and url not in enqueued:
                            queues[lang].append({**candidate, "url": url, "discovery": "related_article"})
                            enqueued.add(url)
                    expansions[candidate["source"]] += 1
                if error:
                    rejected.append({**candidate, "reason": error})
                    continue
                duplicate = article["content_hash"] in hashes
                if not duplicate:
                    # Conservative near-duplicate test within each language.
                    for prev in articles:
                        if prev["language"] != lang:
                            continue
                        if ratio(normalize(prev["title"]), normalize(article["title"])) > 86 and ratio(prev["clean_text"], article["clean_text"]) > 92:
                            duplicate = True
                            break
                if duplicate:
                    rejected.append({**candidate, "reason": "duplicate_or_near_duplicate"})
                    continue
                if counts[lang] >= target_per_language:
                    continue
                articles.append(article)
                hashes.add(article["content_hash"])
                counts[lang] += 1
            write_jsonl(DATA / "processed/articles.jsonl", articles)
            write_jsonl(DATA / "interim/rejected_articles.jsonl", rejected)
            print(json.dumps({"stage": "collect", "accepted": dict(counts), "attempted_this_run": total_attempts,
                              "rejected_this_run": len(rejected), "queued": {l: len(q) for l, q in queues.items()}}, ensure_ascii=False), flush=True)
            if total_attempts >= 1600:
                break
    summary = {"created_at": now(), "target_per_language": target_per_language,
               "accepted_by_language": dict(counts), "accepted_by_source": dict(Counter(a["source"] for a in articles)),
               "publication_years": dict(sorted(Counter(a["published_at"][:4] for a in articles).items())),
               "attempted_this_run": total_attempts, "rejected_this_run": len(rejected),
               "rejection_reasons_this_run": dict(Counter(r["reason"] for r in rejected)),
               "api_calls": 0, "complete": all(counts[l] == target_per_language for l in queues)}
    write_json(REPORTS / "collection.json", summary)
    return summary


def refresh_articles(fetcher):
    """Re-extract the accepted corpus from cached originals after an adapter fix."""
    old = read_jsonl(DATA / "processed/articles.jsonl")
    kept, rejected = [], []
    hashes = set()
    changed = 0
    for a in old:
        candidate = {"url": a["canonical_url"], "source": a["source"], "language": a["language"]}
        try:
            new = extract_article(fetcher.get(candidate["url"]), candidate)
            if new["content_hash"] in hashes:
                raise ValueError("duplicate_after_refresh")
            if new["clean_text"] != a["clean_text"]:
                changed += 1
            # Preserve original download time; the re-extraction time is a separate field.
            new["retrieved_at"] = a["retrieved_at"]
            new["reextracted_at"] = now()
            kept.append(new)
            hashes.add(new["content_hash"])
        except Exception as e:
            rejected.append({**candidate, "reason": str(e)})
    write_jsonl(DATA / "processed/articles.jsonl", kept)
    write_jsonl(DATA / "interim/rejected_after_refresh.jsonl", rejected)
    summary = {"created_at": now(), "input": len(old), "kept": len(kept), "changed": changed, "removed": len(rejected), "rejections": rejected}
    write_json(REPORTS / "corpus_refresh.json", summary)
    return summary
