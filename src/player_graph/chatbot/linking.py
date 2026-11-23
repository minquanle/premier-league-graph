import re
from .query import fold


def link(graph, question):
    """Resolve exact canonical/alias mentions, with longest matches and explicit ambiguity.

    Does not guess a surname when multiple people share it.
    """
    rows = graph.run('MATCH (n:FootballEntity) RETURN n.canonical_name AS name,n.aliases AS aliases,labels(n) AS labels')
    q = fold(question)
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
    accepted=[]
    for c in sorted(set(candidates),key=lambda c: (-(c[1]-c[0]),c[0],c[2])):
        same=[a for a in candidates if a[:2]==c[:2] and a[2]!=c[2]]
        if same:
            raise ValueError('Tên chưa rõ: ' + question[c[0]:c[1]])
        if not any(c[0]<a[1] and a[0]<c[1] for a in accepted):
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
