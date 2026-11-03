"""Targeted collection (strategy v2, docs/CHIEN_LUOC_THU_BAI.md).

Candidates come with a title (from topic listings) or a slug (from monthly sitemaps), so they are classified
before any article is downloaded: junk and off-topic pages are dropped, confirmed events are preferred over
rumours, and each fetch batch follows type quotas so the graph is not dominated by PLAYS_FOR.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
from rapidfuzz.fuzz import token_set_ratio

from .common import DATA, REPORTS, canonical_url, normalize, now, read_jsonl, write_json, write_jsonl
from .corpus import CLUBS, SOURCES, article_links, extract_article, xml_locations
from .fetch import Fetcher

TODAY = date.today().isoformat()
QUOTAS = {"event": 0.55, "analysis": 0.25, "match": 0.20}
RUMOR_SHARE = 0.05

CLUB_WORDS = sorted({normalize(a) for scope in ["core"] for c, al in CLUBS[scope].items() for a in [c, *al] if len(a) > 3}, key=len, reverse=True)
VN_NICKNAMES = ["quy do", "phao thu", "the blues", "lu doan do", "the kop", "man xanh", "ga trong", "bay cao", "chich choe", "bua tau", "dai bang", "bay ong", "the citizens"]
MANAGERS = ["ferguson", "wenger", "mourinho", "guardiola", "klopp", "arteta", "pochettino", "conte", "tuchel", "lampard", "potter",
            "postecoglou", "ten hag", "amorim", "solskjaer", "rangnick", "carrick", "slot", "maresca", "howe", "emery", "dyche", "moyes",
            "ancelotti", "benitez", "rodgers", "ranieri", "nuno", "iraola", "frank", "glasner", "silva", "hodgson", "allardyce", "bielsa",
            "hasenhuttl", "xabi alonso", "nagelsmann"]
PL_PATTERN = re.compile(r"(?<!\w)(" + "|".join(re.escape(w) for w in [*CLUB_WORDS, *VN_NICKNAMES, *MANAGERS, "premier league", "ngoai hang anh", "epl"]) + r")(?!\w)")

JUNK = re.compile(r"(?<!\w)(livescore|truc tuyen|truc tiep|lich thi dau|bang xep hang|bxh|nhan dinh|soi keo|du doan|ty le|keo|"
                  # Accent-stripped: "ảnh" (photo) collides with "Anh" (England), so photo pages are caught by "photo"/"chum anh" only.
                  r"video|clip|highlight|highlights|link xem|xem truc|tin moi nhat bong da|tin bong da|chum anh|photo|photos|gallery|"
                  r"live|preview|odds|betting|how to watch|where to watch|in brief|sports news|roundup|round up|briefs|fixtures|standings|"
                  r"quiz|podcast)(?!\w)")
OFFSCOPE = re.compile(r"(?<!\w)(viet nam|vietnam|vietnamese|v league|sea games|aff|asean|u\d\d|women|nu|futsal|tennis|golf|f1|formula|"
                       r"boxing|snooker|cricket|rugby|nba|basketball|esports|chess|cycling|marathon|badminton|volleyball)(?!\w)")
EVENT = re.compile(r"(?<!\w)(chinh thuc|hoan tat|ra mat|gia nhap|cap ben|ky hop dong|ky ket|gia han|cho muon|mua dut|ban dut|"
                   r"sa thai|bo nhiem|tu chuc|chia tay|ra di|roi doi|thay the|tan hlv|ghe nong|nguoi dai dien|dam phan|thoa thuan|"
                   r"phi chuyen nhuong|bom tan|chi trich|mau thuan|bat hoa|xung dot|cong kich|phan phao|ky luat|"
                   r"signs|signed|signing|joins|joined|completes|completed|seals|loan|loans|loaned|extends|extension|new deal|"
                   r"contract|sacked|sacks|fired|axed|appointed|appoints|named|resigns|resigned|quits|steps down|leaves|departs|"
                   r"agent|talks|agree|agreed|fee|record deal|deadline day|window|criticises|criticizes|slams|blasts|row|clash|"
                   r"feud|hits back|exit|replaces|successor)(?!\w)")
RUMOR = re.compile(r"(?<!\w)(nham|muon|tranh mua|theo duoi|de mat|lien he|hoi mua|san don|co the|sap|tinh mua|chen chan|tham do|tam ngam|"
                   r"cuoc dua|ung vien|tin don|linked|link|target|targets|eye|eyes|eyeing|consider|considering|could|set to|want|"
                   r"wants|interest|race|monitor|monitoring|plot|chase|keen|eyed)(?!\w)")
MATCH = re.compile(r"(?<!\w)(ket qua|thang|thua|hoa|danh bai|ha guc|nguoc dong|vs|beat|beats|draw|draws|win|wins|thrash|"
                   r"stun|stuns|held|edge|edges|rout|defeat|defeats|victory|knocked out|equaliser|hat trick)(?!\w)|\d+ ?- ?\d+")


def classify(text):
    """text: title, or slug words. Returns (category, score); category 'drop:*' means do not fetch."""
    t = normalize(text)
    if JUNK.search(t):
        return "drop:junk", 0
    pl = bool(PL_PATTERN.search(t))
    if OFFSCOPE.search(t) and not pl:
        return "drop:offscope", 0
    event, rumor = bool(EVENT.search(t)), bool(RUMOR.search(t))
    if event and not rumor:
        category = "event"
    elif rumor and not event:
        category = "rumor"
    elif MATCH.search(t):
        category = "match"
    elif event:
        category = "event"
    else:
        category = "analysis"
    score = 2 * pl + 2 * (category == "event") + (category == "analysis") - 2 * (category == "rumor")
    return category, score


def slug_words(url):
    slug = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
    slug = re.sub(r"(-post\d+|-\d{5,}|\.html?)$", "", slug)
    return slug.replace("-", " ")


# ---------- discovery ----------

def english_sitemaps(fetcher, first_year=2017):
    rows = []
    months = [(y, m) for y in range(first_year, date.today().year + 1) for m in range(1, 13) if (y, m) <= (date.today().year, date.today().month)]

    def one(job):
        source, host, (y, m) = job
        try:
            locs = xml_locations(fetcher.get(f"https://{host}/sitemaps/news-{y}-{m}.xml"), article_only=True)
        except Exception:
            return []
        out = []
        for url in locs:
            url = canonical_url(url)
            if source == "vietnamnews" and "/sports/" not in url:
                continue
            words = slug_words(url)
            if PL_PATTERN.search(normalize(words)):
                out.append({"url": url, "source": source, "language": "en", "title_hint": words, "discovery": f"sitemap:{y}-{m:02d}"})
        return out

    jobs = [(s, SOURCES[s]["host"], ym) for s in ["nhandan_en", "vietnamnews"] for ym in months]
    with ThreadPoolExecutor(max_workers=2) as pool:
        for part in pool.map(one, jobs):
            rows += part
    return rows


TOPIC_STOP = re.compile(r"thanh pho|tinh |quan |viet nam|doi tuyen|world cup|euro|sea games|v league|champions league|europa|"
                        r"bong da|the thao|chuyen nhuong")


def vnexpress_topics(fetcher, seed_pages, club_pages=20, person_pages=6, max_topics=150, initial_topics=()):
    """Snowball over VnExpress topic pages; listings give titles so classification happens before fetching."""
    topics, rows, seen = {}, [], set()
    queue = list(seed_pages)

    def harvest(raw, base):
        soup = BeautifulSoup(raw, "html.parser")
        found_topics = []
        for a in soup.select('a[href*="/chu-de/"]'):
            url = canonical_url(urljoin(base, a["href"]))
            m = re.match(r"https://vnexpress\.net/chu-de/([a-z0-9-]+)-(\d+)$", url)
            if m and not TOPIC_STOP.search(m.group(1).replace("-", " ") + " "):
                found_topics.append(url)
        items = []
        for a in soup.select("h2 a[href], h3 a[href], h4 a[href]"):
            url = canonical_url(urljoin(base, a["href"]))
            if re.match(r"https://vnexpress\.net/[a-z0-9-]+-\d{7}\.html$", url):
                items.append((url, " ".join(a.get_text(" ", strip=True).split())))
        return found_topics, items

    for url in queue:
        try:
            _, items = harvest(fetcher.get(url), url)
        except Exception:
            continue
        for item_url, title in items:
            if item_url not in seen:
                seen.add(item_url)
                rows.append({"url": item_url, "source": "vnexpress_vi", "language": "vi", "title_hint": title, "discovery": "category"})
    # Topic ids come from article pages (tags); fetch a sample of listed football articles to read their tags.
    def topics_from_article(url):
        try:
            found, _ = harvest(fetcher.get(url), url)
            return found
        except Exception:
            return []

    # Club topic ids found by scanning /chu-de/x-<id> redirects get crawled first.
    for t in initial_topics:
        topics[t] = 1000
    frontier = [r["url"] for r in rows if classify(r["title_hint"])[0] in QUOTAS][:40]
    for found in map(topics_from_article, frontier):
        for t in found:
            topics.setdefault(t, 0)
            topics[t] += 1
    processed = set()
    while topics and len(processed) < max_topics:
        topic = max((t for t in topics if t not in processed), key=lambda t: topics[t], default=None)
        if topic is None:
            break
        processed.add(topic)
        name = normalize(topic.rsplit("/", 1)[-1])
        is_club = topic in initial_topics or any(re.search(r"(?<!\w)" + re.escape(w) + r"(?!\w)", name) for w in CLUB_WORDS)
        pages = club_pages if is_club else person_pages
        new_items = 0
        for page in range(1, pages + 1):
            url = topic if page == 1 else f"{topic}-p{page}"
            try:
                raw = fetcher.get(url)
            except Exception:
                break
            found, items = harvest(raw, url)
            if not items:
                break
            for t in found:
                topics[t] = topics.get(t, 0) + 1
            for item_url, title in items:
                if item_url not in seen:
                    seen.add(item_url)
                    new_items += 1
                    rows.append({"url": item_url, "source": "vnexpress_vi", "language": "vi", "title_hint": title, "discovery": f"topic:{topic.rsplit('/', 1)[-1]}"})
    return rows, sorted(processed)


def category_pages(fetcher):
    """Current listings of the other publishers (no usable archive) and VnExpress International's load-more API."""
    rows = []
    for source in ["bongdaplus", "24h"]:
        for seed in SOURCES[source]["seeds"] + (["https://bongdaplus.vn/tin-chuyen-nhuong"] if source == "bongdaplus" else []):
            try:
                raw = fetcher.get(seed)
            except Exception:
                continue
            soup = BeautifulSoup(raw, "html.parser")
            for a in soup.select("a[href]"):
                url = canonical_url(urljoin(seed, a["href"]))
                title = " ".join(a.get_text(" ", strip=True).split())
                if url in article_links(f'<a href="{url}"></a>', seed, source) and len(title) > 20:
                    rows.append({"url": url, "source": source, "language": "vi", "title_hint": title, "discovery": "category"})
    for page in range(1, 80):
        try:
            raw = fetcher.get(f"https://e.vnexpress.net/category/listcategory?category_id=1004315&page={page}")
        except Exception:
            break
        try:
            html = json.loads(raw).get("html", "")
        except ValueError:
            break
        soup = BeautifulSoup(html, "html.parser")
        items = [(canonical_url(urljoin("https://e.vnexpress.net/", a["href"])), " ".join(a.get_text(" ", strip=True).split()))
                 for a in soup.select("h2 a[href], h3 a[href], h4 a[href]")]
        if not items:
            break
        rows += [{"url": u, "source": "vnexpress_en", "language": "en", "title_hint": t, "discovery": f"listcategory:{page}"}
                 for u, t in items if "/news/sports/football/" in u]
    return rows


def extend_vnexpress(fetcher, max_topics=150):
    """Second VnExpress pass seeded with the club topics, merged into the existing candidate file."""
    clubs = json.loads((DATA / "interim/vnexpress_club_topics.json").read_text(encoding="utf-8"))
    rows, topics = vnexpress_topics(fetcher, [], max_topics=max_topics, initial_topics=tuple(clubs))
    existing = {r["url"]: r for r in read_jsonl(DATA / "interim/candidates_v2.jsonl")}
    added = 0
    for r in rows:
        if r["url"] not in existing:
            r["category"], r["score"] = classify(r["title_hint"])
            existing[r["url"]] = r
            added += 1
    rows = list(existing.values())
    write_jsonl(DATA / "interim/candidates_v2.jsonl", rows)
    summary = {"created_at": now(), "added": added, "candidates": len(rows), "topics_crawled": len(topics),
               "by_source_category": {s: dict(Counter(r["category"] for r in rows if r["source"] == s)) for s in {r["source"] for r in rows}}}
    write_json(REPORTS / "discovery_v2_extend.json", summary)
    return summary


def discover_v2(fetcher):
    existing = read_jsonl(DATA / "processed/articles.jsonl")
    seeds = ["https://vnexpress.net/the-thao/ngoai-hang-anh", *[a["canonical_url"] for a in existing if a["source"] == "vnexpress_vi"]]
    with ThreadPoolExecutor(max_workers=3) as pool:
        en = pool.submit(english_sitemaps, fetcher)
        vi = pool.submit(vnexpress_topics, fetcher, seeds)
        other = pool.submit(category_pages, fetcher)
        vi_rows, topics = vi.result()
        rows = en.result() + vi_rows + other.result()
    unique = {}
    for r in rows:
        r["category"], r["score"] = classify(r["title_hint"])
        unique.setdefault(r["url"], r)
    rows = list(unique.values())
    write_jsonl(DATA / "interim/candidates_v2.jsonl", rows)
    summary = {"created_at": now(), "candidates": len(rows), "by_source": dict(Counter(r["source"] for r in rows)),
               "by_category": dict(Counter(r["category"] for r in rows)),
               "by_source_category": {s: dict(Counter(r["category"] for r in rows if r["source"] == s)) for s in {r["source"] for r in rows}},
               "vnexpress_topics_crawled": len(topics), "topics_sample": topics[:40]}
    write_json(REPORTS / "discovery_v2.json", summary)
    return summary


def discover_v3(fetcher, max_topics=250, pages=8, first_year=2010):
    """Entity-diverse candidates: VnExpress topics read from tags of already collected articles (cached pages),
    skipping topics crawled before, plus the English monthly sitemaps before 2017."""
    existing = {r["url"]: r for r in read_jsonl(DATA / "interim/candidates_v2.jsonl")}
    crawled = {r["discovery"].split(":", 1)[1] for r in existing.values() if r["discovery"].startswith("topic")}
    counts = Counter()
    for a in read_jsonl(DATA / "processed/articles.jsonl"):
        if a["source"] != "vnexpress_vi":
            continue
        try:
            soup = BeautifulSoup(fetcher.get(a["canonical_url"]), "html.parser")
        except Exception:
            continue
        for link in soup.select('a[href*="/chu-de/"]'):
            url = canonical_url(urljoin(a["canonical_url"], link["href"]))
            m = re.match(r"https://vnexpress\.net/chu-de/([a-z0-9-]+-\d+)$", url)
            if m and m.group(1) not in crawled and not TOPIC_STOP.search(m.group(1).replace("-", " ") + " "):
                counts[url] += 1
    topics = [t for t, _ in counts.most_common(max_topics)]
    rows = []
    for topic in topics:
        slug = topic.rsplit("/", 1)[-1]
        for page in range(1, pages + 1):
            try:
                soup = BeautifulSoup(fetcher.get(topic if page == 1 else f"{topic}-p{page}"), "html.parser")
            except Exception:
                break
            items = [(canonical_url(urljoin(topic, x["href"])), " ".join(x.get_text(" ", strip=True).split()))
                     for x in soup.select("h2 a[href], h3 a[href], h4 a[href]")]
            items = [(u, t) for u, t in items if re.match(r"https://vnexpress\.net/[a-z0-9-]+-\d{7}\.html$", u)]
            if not items:
                break
            rows += [{"url": u, "source": "vnexpress_vi", "language": "vi", "title_hint": t, "discovery": f"topic3:{slug}"} for u, t in items]
    en = [r for r in english_sitemaps(fetcher, first_year) if r["discovery"] < "sitemap:2017"]
    for r in en:
        r["discovery"] = r["discovery"].replace("sitemap:", "sitemap3:")
    added = 0
    for r in rows + en:
        if r["url"] not in existing:
            r["category"], r["score"] = classify(r["title_hint"])
            existing[r["url"]] = r
            added += 1
    write_jsonl(DATA / "interim/candidates_v2.jsonl", list(existing.values()))
    new = [r for r in existing.values() if r["discovery"].startswith(("topic3:", "sitemap3:"))]
    summary = {"created_at": now(), "topics_crawled": len(topics), "topics_sample": topics[:30], "added_candidates": added,
               "new_by_source_category": {s: dict(Counter(r["category"] for r in new if r["source"] == s)) for s in {r["source"] for r in new}}}
    write_json(REPORTS / "discovery_v3.json", summary)
    return summary


# ---------- selection and fetching ----------

def near_duplicate(title, kept_titles):
    return any(token_set_ratio(title, other) >= 90 for other in kept_titles[-400:])


def collect_v2(fetcher, target_vi=1100, target_en=900, batch=60, max_per_topic=None, prefer=None, collection="v2"):
    """max_per_topic spreads a batch over many entities; prefer moves candidates whose discovery starts with it first."""
    candidates = read_jsonl(DATA / "interim/candidates_v2.jsonl")
    articles = read_jsonl(DATA / "processed/articles.jsonl")
    have_urls = {a["canonical_url"] for a in articles}
    hashes = {a["content_hash"] for a in articles}
    titles = defaultdict(list)
    for a in articles:
        titles[a["language"]].append(normalize(a["title"]))
    rejected = read_jsonl(DATA / "interim/rejected_v2.jsonl")
    excluded = read_jsonl(DATA / "interim/excluded_articles_v2.jsonl")
    tried = have_urls | {r["url"] for r in rejected} | {a["canonical_url"] for a in excluded}
    targets = {"vi": target_vi, "en": target_en}
    added = Counter()
    added_by = Counter()
    pools = defaultdict(list)
    # Count articles already taken from each topic, so a resumed run respects the same cap.
    per_topic = Counter(a.get("discovery") for a in articles if a.get("discovery"))
    for c in sorted(candidates, key=lambda c: (not (prefer and c["discovery"].startswith(prefer)), -c["score"])):
        if c["url"] not in tried and not c["category"].startswith("drop:"):
            pools[(c["language"], c["category"])].append(c)
    new_rows = []

    def want(lang, picked):
        """Next category for this language: the one furthest below its quota, counting this batch's picks too."""
        pending = Counter(c["category"] for c in picked if c["language"] == lang)
        total = max(1, added[lang] + sum(pending.values()))
        have = lambda k: added_by[(lang, k)] + pending[k]
        options = [(have(k) / total - share, k) for k, share in QUOTAS.items() if pools[(lang, k)]]
        # Rumours only up to their small share, and never as the first pick.
        if added[lang] + sum(pending.values()) > 0 and have("rumor") / total < RUMOR_SHARE and pools[(lang, "rumor")]:
            options.append((-1, "rumor"))
        return min(options)[1] if options else None

    def task(c):
        try:
            return c, extract_article(fetcher.get(c["url"]), c), None
        except Exception as error:
            return c, None, str(error)[:120]

    with ThreadPoolExecutor(max_workers=6) as pool:
        while any(added[l] < targets[l] for l in targets):
            picked = []
            for lang in targets:
                quota = min(batch // 2, max(0, targets[lang] - added[lang]))
                # Skipped candidates must not use up a slot, or a batch can end empty while candidates remain.
                while sum(c["language"] == lang for c in picked) < quota:
                    kind = want(lang, picked)
                    if not kind:
                        break
                    c = pools[(lang, kind)].pop(0)
                    if max_per_topic and per_topic[c["discovery"]] + sum(p["discovery"] == c["discovery"] for p in picked) >= max_per_topic:
                        continue
                    if near_duplicate(normalize(c["title_hint"]), titles[lang]):
                        rejected.append({**c, "reason": "near_duplicate_title"})
                        continue
                    picked.append(c)
            if not picked:
                break
            for c, article, error in pool.map(task, picked):
                if article and article["content_hash"] in hashes:
                    error, article = "duplicate_content", None
                if article and not article["published_at"] <= TODAY:
                    error, article = "future_date", None
                if not article:
                    rejected.append({**c, "reason": error})
                    continue
                kind, _ = classify(article["title"])
                article.update({"collection": collection, "discovery": c["discovery"], "title_category": kind if not kind.startswith("drop:") else c["category"]})
                if kind.startswith("drop:"):
                    rejected.append({**c, "reason": "title_" + kind})
                    continue
                hashes.add(article["content_hash"])
                per_topic[c["discovery"]] += 1
                titles[c["language"]].append(normalize(article["title"]))
                new_rows.append(article)
                added[c["language"]] += 1
                added_by[(c["language"], article["title_category"])] += 1
            print({"added": dict(added), "by_kind": {f"{l}:{k}": v for (l, k), v in added_by.items()}, "rejected": len(rejected)}, flush=True)
            write_jsonl(DATA / "processed/articles.jsonl", articles + new_rows)
            write_jsonl(DATA / "interim/rejected_v2.jsonl", rejected)
    summary = {"created_at": now(), "added": dict(added), "by_language_kind": {f"{l}:{k}": v for (l, k), v in sorted(added_by.items())},
               "by_source": dict(Counter(a["source"] for a in new_rows)),
               "years": dict(sorted(Counter(a["published_at"][:4] for a in new_rows).items())),
               "rejections": dict(Counter(r["reason"].split(":")[0] for r in rejected)),
               "corpus_total": len(articles) + len(new_rows), "complete": all(added[l] >= targets[l] for l in targets)}
    write_json(REPORTS / "collection_v2.json", summary)
    return summary
