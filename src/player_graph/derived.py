"""Networks derived from the extracted graph and Wikidata career dates, kept apart from the edges read in the news.

TEAMMATE_OF: two players at the same club during overlapping periods ("cùng thầy cùng hát" needs the period: Cantona and
Garnacho both played for Man Utd, decades apart).
PLAYED_UNDER: a player at a club while a coach managed that club.
TRANSFER_FLOW: consecutive clubs in a player's career, both in the graph, weighted by the number of moves.
A period without a usable start date creates no edge.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import date
from itertools import combinations

from .common import DATA, now, read_jsonl, write_json, write_jsonl

SPELLS = DATA / "cache/person_spells.json"
MIN_OVERLAP_YEARS = 0.25  # about three months together
OPEN_SPELL_YEARS = 3  # an open-ended period of someone no longer playing cannot be assumed to last
ACTIVE_AFTER_BIRTH_YEAR = 1985
TODAY = date.today().year + (date.today().month - 1) / 12


def _year(snak):
    value = snak.get("datavalue", {}).get("value", {}) if snak else {}
    m = re.match(r"[+-](\d{4})-(\d{2})", value.get("time", "")) if isinstance(value, dict) else None
    return int(m.group(1)) + max(int(m.group(2)) - 1, 0) / 12 if m else None


def build_spells():
    """{qid: {born, player: [[club, start, end]], coach: [...]}} from the cached Wikidata responses; no network."""
    found = {}
    for path in (DATA / "cache/wikidata").glob("*.json"):
        if path.stat().st_size < 3000:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        for qid, entity in (data.get("entities") or {}).items() if isinstance(data, dict) else ():
            claims = entity.get("claims")
            if claims is None:
                continue
            row = {"born": None, "player": [], "coach": []}
            for statement in claims.get("P569", [])[:1]:
                row["born"] = _year(statement.get("mainsnak"))
            for prop, kind in (("P54", "player"), ("P6087", "coach")):
                for statement in claims.get(prop, []):
                    value = statement.get("mainsnak", {}).get("datavalue", {}).get("value", {})
                    if isinstance(value, dict) and value.get("id"):
                        q = statement.get("qualifiers", {})
                        row[kind].append([value["id"], _year((q.get("P580") or [None])[0]), _year((q.get("P582") or [None])[0])])
            found[qid] = row
    SPELLS.write_text(json.dumps(found), encoding="utf-8")
    return found


def load_spells():
    return json.loads(SPELLS.read_text(encoding="utf-8")) if SPELLS.exists() else build_spells()


def periods(row, kind):
    """[(club, start, end)]; a missing end runs to the person's next period, or stays open only for someone still active."""
    dated = sorted((s for s in row[kind] if s[1] is not None), key=lambda s: s[1])
    active = (row.get("born") or 0) >= ACTIVE_AFTER_BIRTH_YEAR
    out = []
    for i, (club, start, end) in enumerate(dated):
        if end is None:
            later = [s[1] for s in dated[i + 1:] if s[1] > start]
            end = later[0] if later else (TODAY if active else start + OPEN_SPELL_YEARS)
        if end > start:
            out.append((club, start, end))
    return out


def overlap(a, b):
    return min(a[2], b[2]) - max(a[1], b[1])


def teammates(players):
    """{(a, b): {club: years together}} for players {qid: periods}; pairs are sorted, so each appears once."""
    by_club = defaultdict(list)
    for person, rows in players.items():
        for row in rows:
            by_club[row[0]].append((person, row))
    pairs = defaultdict(dict)
    for club, rows in by_club.items():
        for (p, a), (q, b) in combinations(rows, 2):
            years = overlap(a, b)
            if p != q and years >= MIN_OVERLAP_YEARS:
                key = tuple(sorted((p, q)))
                pairs[key][club] = round(max(pairs[key].get(club, 0), years), 2)
    return pairs


def played_under(players, coaches):
    by_club = defaultdict(list)
    for coach, rows in coaches.items():
        for row in rows:
            by_club[row[0]].append((coach, row))
    pairs = defaultdict(dict)
    for player, rows in players.items():
        for row in rows:
            for coach, spell in by_club.get(row[0], []):
                years = overlap(row, spell)
                if player != coach and years >= MIN_OVERLAP_YEARS:
                    pairs[(player, coach)][row[0]] = round(max(pairs[(player, coach)].get(row[0], 0), years), 2)
    return pairs


def transfer_flow(players, clubs):
    """Next club in each career, among clubs of the graph. A club outside the graph between the two is not seen."""
    flow = Counter()
    for rows in players.values():
        career = [r for r in sorted(rows, key=lambda r: r[1]) if r[0] in clubs]
        for a, b in zip(career, career[1:]):
            if a[0] != b[0]:
                flow[(a[0], b[0])] += 1
    return flow


def build(run_name):
    folder = DATA / "graph" / run_name
    entities = {e["entity_id"]: e for e in read_jsonl(folder / "entities.jsonl")}
    people = {i for i, e in entities.items() if e["entity_type"] == "Person" and e.get("qid")}
    clubs = {i for i, e in entities.items() if e["entity_type"] == "Club" and e.get("qid")}
    spells = load_spells()
    players, coaches = {}, {}
    usable, all_spells = 0, 0
    for person in people:
        row = spells.get(person)
        if not row:
            continue
        # Only clubs in the graph: career rows also hold national and youth teams, which are not in the network's scope.
        in_scope = [p for p in periods(row, "player") if p[0] in clubs]
        all_spells += sum(1 for s in row["player"] if s[0] in clubs)
        usable += len(in_scope)
        if in_scope:
            players[person] = in_scope
        managed = [p for p in periods(row, "coach") if p[0] in clubs]
        if managed:
            coaches[person] = managed
    source = {"source_type": "derived", "source": "wikidata_p54_p6087_dates", "dataset": run_name}
    mates = [{"subject_id": a, "object_id": b, "relation_type": "TEAMMATE_OF", "clubs": sorted(v), "years_together": round(sum(v.values()), 2),
              "inference_rule": f"same club, overlap >= {MIN_OVERLAP_YEARS} years", **source} for (a, b), v in sorted(teammates(players).items())]
    under = [{"subject_id": a, "object_id": b, "relation_type": "PLAYED_UNDER", "clubs": sorted(v), "years_together": round(sum(v.values()), 2),
              "inference_rule": f"player period overlaps coach period at the club, >= {MIN_OVERLAP_YEARS} years", **source} for (a, b), v in sorted(played_under(players, coaches).items())]
    flow = [{"subject_id": a, "object_id": b, "relation_type": "TRANSFER_FLOW", "moves": n, "inference_rule": "consecutive graph clubs in a player's career", **source}
            for (a, b), n in sorted(transfer_flow(players, clubs).items())]
    out = folder / "derived"
    write_jsonl(out / "teammate_of.jsonl", mates)
    write_jsonl(out / "played_under.jsonl", under)
    write_jsonl(out / "transfer_flow.jsonl", flow)
    summary = {"created_at": now(), "run_name": run_name, "people_with_qid": len(people), "players_with_dated_periods": len(players), "coaches_with_dated_periods": len(coaches),
               "player_periods_at_graph_clubs": all_spells, "player_periods_usable": usable, "min_overlap_years": MIN_OVERLAP_YEARS,
               "teammate_of_edges": len(mates), "played_under_edges": len(under), "transfer_flow_edges": len(flow), "transfer_flow_moves": sum(f["moves"] for f in flow),
               "note": "Đây là cạnh suy diễn từ Wikidata, thống kê riêng, không cộng vào số cạnh trích từ báo"}
    write_json(out / "summary.json", summary)
    return summary
