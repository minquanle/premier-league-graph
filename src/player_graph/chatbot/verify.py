"""Deterministic checks on a hop plan, run before trusting the fast planner.

The fast planner (one greedy call) is right about 86% of the time. Its remaining mistakes are
systematic, so sampling more plans does not fix them; these checks find most of them without a
model call, and a flagged plan is re-planned once with thinking (see agent.generate_v3).

Each check returns a short message that is shown to the model as feedback and to the user as part
of the reasoning trace:
  V1  every entity the linker found must be used by the plan
  V2  soft role types: e.g. players_coached needs a coach as input, coaches_of_player a player
  V3  relation cue words in the question must be covered by a hop of that relation family
  V4  an empty result on a question that is not asking whether something exists
  V5  the answer type the question asks for ("CLB nào", "ai", "bao nhiêu") must match the plan
"""
import re

# hop -> (role its input should have, role of its output); None = no expectation.
ROLE_IO = {
    'teammates': ('Player', 'Player'),
    'coaches_of_player': ('Player', 'Coach'),
    'players_coached': ('Coach', 'Player'),
    'clubs_managed': ('Coach', None),
    'managers_of_club': (None, 'Coach'),
    'managers_appointed': (None, 'Coach'),
    'managers_dismissed': (None, 'Coach'),
    'players_of_club': (None, 'Player'),
    'clubs_played_for': ('Player', None),
    'clients_of_agent': ('Agent', 'Player'),
    'agents_of': ('Player', 'Agent'),
}

# Relation family of each hop, and the Vietnamese cue words that announce that family.
FAMILY = {
    'teammates': 'TEAM',
    'coaches_of_player': 'COACHED', 'players_coached': 'COACHED',
    'clubs_managed': 'MANAGES', 'managers_of_club': 'MANAGES',
    'players_of_club': 'PLAYS', 'clubs_played_for': 'PLAYS',
    'criticized_targets': 'CRIT', 'critics_of': 'CRIT', 'clubs_criticized': 'CRIT', 'critics_of_club': 'CRIT',
    'clients_of_agent': 'AGENT', 'agents_of': 'AGENT',
    'loan_destinations': 'LOAN', 'players_loaned_in': 'LOAN',
    'clubs_appointed_by': 'APPOINT', 'managers_appointed': 'APPOINT',
    'clubs_dismissed_by': 'DISMISS', 'managers_dismissed': 'DISMISS',
    'contract_extended_with': 'EXTEND', 'players_extended_by': 'EXTEND',
    'clubs_negotiating_for': 'NEGOTIATE', 'negotiation_targets': 'NEGOTIATE',
    'conflicts_with': 'CONFLICT',
}
CUES = {
    'TEAM': r'đồng đội|chơi cùng|sát cánh',
    'COACHED': r'học trò|dưới quyền|huấn luyện',
    'PLAYS': r'chơi cho|khoác áo|thi đấu cho|cầu thủ của',
    'CRIT': r'chỉ trích|chê bai',
    'AGENT': r'người đại diện|đại diện cho',
    'LOAN': r'cho mượn|theo dạng mượn',
    'APPOINT': r'bổ nhiệm',
    'DISMISS': r'sa thải|sa\-thải',
    'EXTEND': r'gia hạn',
    'NEGOTIATE': r'đàm phán|thương thảo',
    'CONFLICT': r'mâu thuẫn|xung đột|bất hòa',
}
EXISTENCE = r'\bcó\b.*\bkhông\b|bao nhiêu|số lượng|đếm'


def effective_roles(graph, names):
    """Role labels plus the roles the edges prove: labels alone are incomplete (Kolo Touré is labelled
    Player only, yet has players coached; Roberto Mancini is labelled Coach only, yet has teammates)."""
    if not names:
        return {}
    rows = graph.run('''MATCH (n:FootballEntity) WHERE n.canonical_name IN $names
        RETURN n.canonical_name AS name, [l IN labels(n) WHERE l IN ['Player','Coach','Agent']] AS labels,
               EXISTS { (n)-[:PLAYS_FOR]->() } OR EXISTS { (n)-[:TEAMMATE_OF]-() } OR EXISTS { (n)-[:PLAYED_UNDER]->() } AS player,
               EXISTS { (n)-[:MANAGES]->() } OR EXISTS { (n)<-[:PLAYED_UNDER]-() } AS coach,
               EXISTS { (n)-[:REPRESENTS]->() } AS agent''', {'names': list(names)})
    return {r['name']: sorted(set(r['labels']) | {role for role, flag in
                                                 (('Player', r['player']), ('Coach', r['coach']), ('Agent', r['agent'])) if flag})
            for r in rows}


def expected_answer(question):
    """'count', 'Club', 'Person' or None, read from the question word."""
    q = question.lower()
    if re.search(r'bao nhiêu|số lượng|\bđếm\b', q):
        return 'count'
    club = re.search(r'(clb|câu lạc bộ|đội bóng|đội) nào', q)
    person = re.search(r'\bai\b|(hlv|cầu thủ|người|học trò|thân chủ|người đại diện) nào', q)
    if club and not person:
        return 'Club'
    if person and not club:
        return 'Person'
    return None


def check_plan(plan, question, params, types, roles, hops, rows=None):
    """List of problems found in a type-valid plan (empty list = nothing suspicious)."""
    problems, q = [], question.lower()
    unused = sorted(set(params) - {c['from'] for c in plan['chains']})
    if unused:
        problems.append('V1: the question also names ' + ', '.join(f'{k}={params[k]!r}' for k in unused)
                        + ' but the plan never uses it (an intersection with a second chain may be needed)')
    for chain in plan['chains']:
        name = params[chain['from']]
        current = set(roles.get(name) or []) if types[chain['from']] == 'Person' else None
        who = name
        for hop in chain['hops']:
            need, give = ROLE_IO.get(hop, (None, None))
            if need and current and need not in current:
                problems.append(f'V2: hop {hop} expects a {need} as input, but {who} is '
                                + '/'.join(sorted(current)) + ' (direction may be reversed)')
            current, who = ({give}, f'the result of {hop}') if give else (None, f'the result of {hop}')
    families = {FAMILY.get(h) for c in plan['chains'] for h in c['hops']}
    for family, cue in CUES.items():
        found = re.search(cue, q)
        if not found or family in families:
            continue
        # "huấn luyện" also describes managing a club; only "học trò"/"dưới quyền" insist on the player-coach link.
        if family == 'COACHED' and 'MANAGES' in families and not re.search(r'học trò|dưới quyền', q):
            continue
        problems.append(f'V3: the question says "{found.group()}" but no hop of that relation is in the plan')
    want = expected_answer(question)
    end = hops[plan['chains'][0]['hops'][-1]][3]
    if want == 'count' and plan['output'] != 'count':
        problems.append('V5: the question asks how many, but the plan output is a list')
    elif want in ('Club', 'Person') and plan['output'] == 'list' and end != want:
        problems.append(f'V5: the question asks for a {want}, but the plan returns {end} entities')
    if rows is not None:
        empty = not rows or (plan['output'] == 'count' and rows[0].get('count') == 0)
        if empty and not re.search(EXISTENCE, q):
            problems.append('V4: the plan returns nothing; check relation directions and hops')
    return problems
