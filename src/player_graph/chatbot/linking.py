import re
import unicodedata
from .query import fold


PROMINENCE = 3  # times more graph links than the runner-up


def link(graph, question):
    """Resolve exact canonical/alias mentions, with longest matches and explicit ambiguity.

    Does not guess a surname when multiple people share it.
    """
    rows = graph.run('MATCH (n:FootballEntity) RETURN n.canonical_name AS name,n.aliases AS aliases,labels(n) AS labels,COUNT { (n)--(:FootballEntity) } AS degree')
    degree = {row['name']: row.get('degree') or 0 for row in rows}
    original = unicodedata.normalize('NFC', question)
    q = fold(original)  # NFD then dropping marks keeps one character per NFC character, so offsets match
    candidates = []
    extra = {'mu': 'Manchester United', 'man utd': 'Manchester United', 'spurs': 'Tottenham Hotspur'}
    for row in rows:
        aliases = [row['name']] + (row.get('aliases') or [])
        aliases.extend(k for k,v in extra.items() if v==row['name'])
        for alias in aliases:
            if not isinstance(alias,str) or len(alias)<2:
                continue
            for match in re.finditer(r'(?<!\w)' + re.escape(fold(alias)) + r'(?!\w)',q):
                candidates.append((match.start(),match.end(),row['name'],'Club' if 'Club' in row['labels'] else 'Person'))
        # A bare surname ("Ronaldo", "Neville"), matched with its capital letter and accents so that
        # Vietnamese words folding to the same letters ("cần" -> "can") never count.
        surname = row['name'].split()[-1] if 'Club' not in row['labels'] and ' ' in row['name'] else ''
        if len(surname) >= 4 and surname[0].isupper():
            for match in re.finditer(r'(?<!\w)' + re.escape(surname) + r'(?!\w)', original):
                candidates.append((match.start(),match.end(),row['name'],'Person'))
    accepted=[]
    for c in sorted(set(candidates),key=lambda c: (-(c[1]-c[0]),c[0],c[2])):
        # A shorter mention inside an already accepted longer one ("Kane" in "Harry Kane") is not ambiguous.
        if any(c[0]<a[1] and a[0]<c[1] for a in accepted):
            continue
        same=sorted({a for a in candidates if a[:2]==c[:2]}, key=lambda a: -degree.get(a[2], 0))
        if len(same) > 1:
            # A bare surname: take the far better connected person ("Kane" -> Harry Kane), else ask.
            if degree.get(same[0][2], 0) < PROMINENCE * max(1, degree.get(same[1][2], 0)):
                raise ValueError('Tên chưa rõ: ' + question[c[0]:c[1]] + ' (có thể là: '
                                 + ', '.join(a[2] for a in same[:4]) + ')')
            c = same[0]
            if any(a[2] == c[2] for a in accepted):
                continue
        accepted.append(c)
    accepted.sort()
    params={}
    for _,_,name,kind in accepted:
        if name in params.values():
            continue
        primary='club' if kind=='Club' else 'person'
        key=primary if primary not in params else 'other'
        if key in params:
            raise ValueError('Bản hiện tại hỗ trợ tối đa hai thực thể cùng loại trong câu hỏi')
        params[key]=name
    return params
