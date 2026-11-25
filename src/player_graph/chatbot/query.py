import json
import re
import unicodedata
from neo4j import GraphDatabase, Query, READ_ACCESS
from ..graph import settings
from .backend import ROOT

DIRECT = {'PLAYS_FOR', 'MANAGES', 'TRANSFERRED_TO', 'LOANED_TO',
          'EXTENDED_CONTRACT_WITH', 'NEGOTIATES_TO_SIGN', 'APPOINTED_MANAGER_OF',
          'DISMISSED_MANAGER', 'RESIGNED_FROM', 'REPRESENTS', 'CRITICIZES', 'CONFLICTS_WITH'}
DERIVED = {'TEAMMATE_OF', 'PLAYED_UNDER', 'TRANSFER_FLOW'}
# Direct relations stored from the club to the person (all others start at the person).
CLUB_TO_PERSON = {'DISMISSED_MANAGER', 'NEGOTIATES_TO_SIGN'}
SCHEMA = '''Translate Vietnamese graph questions to a single read-only Neo4j Cypher query.
Return JSON {"plan":{"relations":["RELATION"],"answer_type":"name|count"},"cypher":"MATCH ..."}.
Do not answer from memory. Use only the canonical values in supplied parameters, as $person, $club, $other etc.
Nodes: Person (also optional Player/Coach/Agent labels), Club. Properties: canonical_name, entity_id.
Person -> Club: PLAYS_FOR, MANAGES, TRANSFERRED_TO, LOANED_TO, EXTENDED_CONTRACT_WITH, RESIGNED_FROM, APPOINTED_MANAGER_OF (coach appointed by the club).
Club -> Person: DISMISSED_MANAGER (club sacked the coach), NEGOTIATES_TO_SIGN (club wants to sign the person).
Person -> Person: REPRESENTS (agent to client), CRITICIZES, CONFLICTS_WITH. CRITICIZES and CONFLICTS_WITH may also point Person -> Club.
Derived Person -- Person: TEAMMATE_OF, always undirected traversal.
Derived Person -> Person: PLAYED_UNDER, player to coach.
Derived Club -> Club: TRANSFER_FLOW.
All relations describe historical graph claims, not current employment.
Use only MATCH/WHERE/WITH/RETURN/ORDER BY/LIMIT, no procedures or writes.
Return canonical_name AS name for person or club lists; count(DISTINCT entity) AS count for counts.
Use DISTINCT for lists, ORDER BY name ascending and LIMIT 10. Counts also end LIMIT 10.
For ranking counts return name and count, ORDER BY count DESC, name LIMIT 10.
Count all matching entities BEFORE LIMIT, not only first ten.
Do not return nodes, paths, assertion IDs or evidence; the application retrieves provenance separately.
Every variable in RETURN must be bound in MATCH/WITH. Never invent labels, relations or parameters.
'''


def fold(text):
    return ''.join(c for c in unicodedata.normalize('NFD', text.lower())
                   if unicodedata.category(c) != 'Mn').replace('đ', 'd')


def category(question):
    q = fold(question)
    if any(w in q for w in ('bao nhieu', 'so luong', 'dem ', 'nhieu nhat', 'xep hang')):
        return 'count'
    if any(w in q for w in ('ca hai', 'ca ', 'cung tung', 'dong thoi', 'va ')) and ('clb' in q or 'cho ' in q):
        return 'intersection'
    if 'dong doi' in q or ('hlv' in q and 'cau thu' in q and 'tung' in q):
        return 'multihop'
    return 'lookup'


def choose_examples(question, n=4):
    examples = json.loads((ROOT / 'configs/chatbot_examples.json').read_text(encoding='utf-8'))
    words = set(fold(question).split())
    kind = category(question)
    def score(e):
        return (4 if e['category'] == kind else 0) + len(words & set(fold(e['question']).split())) / max(1, len(words))
    return sorted(examples, key=lambda e: (-score(e), e['id']))[:n]


def prompt(question, params, mode):
    messages = [{'role': 'system', 'content': SCHEMA}]
    selected = choose_examples(question) if mode != 'schema' else []
    for example in selected:
        messages.extend([
            {'role': 'user', 'content': json.dumps({'question': example['question'], 'params': example['params']}, ensure_ascii=False)},
            {'role': 'assistant', 'content': json.dumps({'plan': {'relations': re.findall(r'\[[^\]]*:\s*([A-Z_]+)', example['cypher']), 'answer_type': 'count' if example['category'] == 'count' else 'name'}, 'cypher': example['cypher']}, ensure_ascii=False)}])
    messages.append({'role': 'user', 'content': json.dumps({'question': question, 'params': params}, ensure_ascii=False)})
    return messages, [e['id'] for e in selected]


def validate(raw, params):
    text = re.sub(r'^```(?:json|cypher)?\s*|\s*```$', '', raw.strip(), flags=re.I)
    obj = json.loads(text)
    query = obj['cypher'].strip().rstrip(';')
    masked = re.sub(r"'[^']*'|\"[^\"]*\"", "''", query)
    if not re.match(r'^MATCH\b', query, re.I) or any(s in masked for s in (';', '//', '/*', '`', '{', '}')):
        # Property maps are legitimate but braces are also subqueries; allow only simple property maps below.
        maps_removed = re.sub(r'\{\s*canonical_name\s*:\s*\$\w+\s*\}', '', masked)
        if not re.match(r'^MATCH\b', query, re.I) or any(s in maps_removed for s in (';', '//', '/*', '`', '{', '}')):
            raise ValueError('Only a single bounded MATCH query with canonical parameter maps is accepted')
    if re.search(r'\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|CALL|LOAD|FOREACH|UNION|INSERT|ALTER|USE|SHOW|START)\b', masked, re.I):
        raise ValueError('Only read-only MATCH queries are accepted')
    if re.search(r'\[[^\]]*\*', masked):
        raise ValueError('Unbounded variable-length paths are not supported')
    if re.search(r'\[[^\]]*\|', masked):
        raise ValueError('Relationship alternatives are outside this query subset')
    if re.search(r'\([^)]*:\s*\w+\s*:', masked):
        raise ValueError('Use a single node label per pattern')
    if not re.search(r'\bRETURN\b', query, re.I) or not re.search(r'\bLIMIT\s+10\s*$', query, re.I):
        raise ValueError('RETURN and final LIMIT 10 are required')
    if not re.search(r'\b(count|sum|avg|min|max)\s*\(', masked, re.I) and not re.search(r'\bRETURN\s+DISTINCT\b', masked, re.I):
        raise ValueError('List queries require RETURN DISTINCT to avoid repeated entities')
    if not re.search(r'\b(count|sum|avg|min|max)\s*\(', masked, re.I) and not re.search(r'ORDER\s+BY\s+name\b', masked, re.I):
        raise ValueError('List queries require ORDER BY name for deterministic LIMIT')
    unknown = set(re.findall(r'\$(\w+)', query)) - set(params)
    if unknown:
        raise ValueError('Unknown parameters: ' + ','.join(sorted(unknown)))
    for label in re.findall(r':\s*([A-Za-z_]\w*)', masked):
        if label not in DIRECT | DERIVED | {'Person', 'Player', 'Coach', 'Agent', 'Club'}:
            raise ValueError('Unknown label or relation: ' + label)
    for rel in re.finditer(r'(<-|-)(\[[^\]]*:\s*TEAMMATE_OF\s*\])(->|-)', masked):
        if rel[1] != '-' or rel[3] != '-':
            raise ValueError('TEAMMATE_OF must be traversed without arrowheads')
    # Schema-level domain/range checks, independent of the question or benchmark answer.
    typed = {v: ('Club' if label == 'Club' else 'Person')
             for v,label in re.findall(r'\(\s*(\w+)\s*:\s*(Person|Player|Coach|Agent|Club)\b', masked)}
    node = r'\(\s*(\w*)\s*(?::\s*(Person|Player|Coach|Agent|Club))?\s*(?:\{[^}]*\})?\)'
    edge = node + r'\s*(<-|-)\s*\[[^\]]*:\s*([A-Z_]+)\s*\]\s*(->|-)\s*' + node
    for m in re.finditer(r'(?='+edge+r')',masked):
        left,llabel,arrow1,kind,arrow2,right,rlabel=m.groups()
        a=typed.get(left) or ('Club' if llabel=='Club' else 'Person' if llabel else None)
        b=typed.get(right) or ('Club' if rlabel=='Club' else 'Person' if rlabel else None)
        expected = ('Club','Person') if kind in CLUB_TO_PERSON else ('Person','Club') if kind in DIRECT-CLUB_TO_PERSON-{'REPRESENTS','CRITICIZES','CONFLICTS_WITH'} else ('Club','Club') if kind=='TRANSFER_FLOW' else ('Person','Person')
        if arrow1=='<-':
            a,b=b,a
        if kind in {'CRITICIZES','CONFLICTS_WITH'} and (a,b)==('Person','Club'):
            continue
        if arrow1=='-' and arrow2=='-' and (b,a)==expected:
            continue
        if (a and a!=expected[0]) or (b and b!=expected[1]):
            raise ValueError(f'{kind} domain/range is {expected[0]} -> {expected[1]}; check endpoint types and direction')
    return query, obj.get('plan', {})


def safe_error(error):
    if isinstance(error,ValueError) or str(getattr(error,'code','')).startswith('Neo.ClientError.Statement.'):
        return str(error)[:650]
    return type(error).__name__ + ': service operation failed (details withheld)'


class Graph:
    def __init__(self):
        cfg = settings()
        self.driver = GraphDatabase.driver(cfg['NEO4J_URI'], auth=(cfg['NEO4J_USERNAME'], cfg['NEO4J_PASSWORD']))
        self.driver.verify_connectivity()

    def close(self):
        self.driver.close()

    def run(self, cypher, params=None, explain=False):
        with self.driver.session(default_access_mode=READ_ACCESS) as session:
            q = ('EXPLAIN ' if explain else '') + cypher
            with session.begin_transaction(timeout=12) as tx:
                return tx.run(q, params or {}).data()


def generate(client, graph, question, params, mode='fewshot', max_repairs=2):
    messages, example_ids = prompt(question, params, mode)
    # Select thinking from question text, never from benchmark labels/answers.
    thinking = mode == 'thinking' and (category(question) in {'multihop', 'intersection', 'count'})
    attempts = []
    for i in range(max_repairs + 1):
        response = client.complete(messages, thinking=thinking, json_output=False)
        record = {**response}
        attempts.append(record)
        try:
            if response['finish_reason'] == 'length':
                raise ValueError('Generation truncated; reduce reasoning and return concise JSON')
            cypher, plan = validate(response['content'], params)
            graph.run(cypher, params, explain=True)
            rows = graph.run(cypher, params)
            return {'ok': True, 'query': cypher, 'plan': plan, 'rows': rows,
                    'attempts': attempts, 'example_ids': example_ids, 'thinking': thinking}
        except Exception as error:
            record['error'] = safe_error(error)
            if i < max_repairs:
                messages.extend([{'role': 'assistant', 'content': response['content']},
                                 {'role': 'user', 'content': 'Correct only this error and return JSON. Error: ' + safe_error(error)}])
    return {'ok': False, 'attempts': attempts, 'example_ids': example_ids, 'thinking': thinking}
