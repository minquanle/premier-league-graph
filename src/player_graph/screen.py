"""Corpus screening before the teacher runs (rules from the quality checks of 30/10/2026).

Each rule names a reason, and nothing is deleted: excluded articles go to interim/excluded_articles_v2.jsonl.
"""
from __future__ import annotations

import re
from collections import Counter

from .collect_v2 import PL_PATTERN, classify
from .common import DATA, normalize, now, read_jsonl, write_json, write_jsonl

# Women's/youth teams, Viet Nam or ASEAN tours, plus a few one-off off-topic stories found in review.
ALWAYS = re.compile(r"(?<!\w)(doi nu|nu |women|u\d\d|asean|all stars|viet nam|vietnam|trung quoc|indonesia|faiq|bolkiah|can tho|uruguay)(?!\w)")
NATIONAL = re.compile(r"(?<!\w)(tuyen anh|tuyen italy|tuyen ha lan|doi tuyen|national team|world cup|euro 20\d\d|nations league|portugal|south korea)(?!\w)")
OTHER_LEAGUE = re.compile(r"(?<!\w)(real madrid|barcelona|barca|juventus|inter milan|ac milan|napoli|psg|bayern|dortmund|atletico|serie a|"
                          r"la liga|bundesliga|ligue 1|saudi|al nassr|al hilal|inter miami)(?!\w)")
SPORTS = {
    "tennis": r"tennis|quan vot|wta|atp|grand slam|roland garros|us open|australian open",
    "f1": r"formula one|formula 1|\bf1\b|grand prix|motogp|pole position",
    "boxing": r"boxing|quyen anh|heavyweight|wbc|wba|ibf|wbo|ufc",
    "snooker": r"snooker",
    "golf": r"golf|pga|birdie",
    "cycling": r"cycling|tour de france|dauphine|giro",
    "basketball": r"nba|basketball|bong ro",
    "cricket_rugby": r"cricket|rugby|wicket",
}


def reason_for(article):
    title = normalize(article["title"]) + " "
    pl = bool(PL_PATTERN.search(title))
    kind, _ = classify(article["title"])
    if kind.startswith("drop:"):
        return "title_" + kind
    if ALWAYS.search(title) and "club world cup" not in title:
        return "women_youth_or_vietnam_asean_tour"
    if NATIONAL.search(title) and not pl and "club world cup" not in title:
        return "national_team_no_pl_subject"
    if OTHER_LEAGUE.search(title) and not pl and article.get("title_category") != "event":
        return "other_league_no_pl_subject"
    if article["source"] == "vnexpress_en" and not pl:
        return "vnexpress_en_no_pl_subject"
    body = normalize(article["clean_text"])
    if sum(len(re.findall(p, body)) >= 2 for p in SPORTS.values()) >= 2:
        return "multi_sport_digest"
    return None


def screen(source=None, snapshot=None):
    """Screen the corpus; with `snapshot`, write the kept articles there instead of rewriting the live corpus."""
    source = source or DATA / "processed/articles.jsonl"
    articles = read_jsonl(source)
    excluded = read_jsonl(DATA / "interim/excluded_articles_v2.jsonl")
    seen = {e["canonical_url"] for e in excluded}
    keep, drop = [], []
    for a in articles:
        reason = reason_for(a)
        (drop if reason else keep).append({**a, "exclusion_reason": reason} if reason else a)
    write_jsonl(snapshot or source, keep)
    write_jsonl(DATA / "interim/excluded_articles_v2.jsonl", excluded + [d for d in drop if d["canonical_url"] not in seen])
    summary = {"created_at": now(), "source": str(source), "written_to": str(snapshot or source), "kept": len(keep),
               "excluded_now": len(drop), "reasons": dict(Counter(d["exclusion_reason"] for d in drop)),
               "kept_by_collection": dict(Counter(a.get("collection", "pilot") for a in keep))}
    write_json(DATA / "interim/screen_last.json", summary)
    return summary
