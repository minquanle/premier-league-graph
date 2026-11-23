"""Compare frozen prompt configurations against held-out Text2Cypher cases.

Canonical parameters are supplied: this isolates Text2Cypher, not entity linking.
Reference queries and category labels are never passed to generation or repairs.
"""
import argparse
import concurrent.futures
import hashlib
import json
import statistics
import sys
import time
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from player_graph.chatbot.backend import KaggleClient
from player_graph.chatbot.query import Graph, generate, SCHEMA
from player_graph.chatbot.answer import evidence_packets, synthesize


def normalized(rows):
    return sorted(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in rows)


def summarize(records):
    out = {}
    for mode in sorted({r['mode'] for r in records}):
        group = [r for r in records if r['mode'] == mode]
        answered = [r for r in group if 'answer' in r]
        out[mode] = {'n': len(group), 'first_correct': sum(r.get('first_correct', False) for r in group),
                     'after_repair_correct': sum(r.get('correct', False) for r in group),
                     'executed': sum(r.get('ok', False) for r in group),
                     'mean_query_seconds': round(statistics.mean(r['seconds'] for r in group), 3),
                     'answer_tests': len(answered),
                     'valid_model_answer_selections': sum(r['answer']['model_selection_valid'] for r in answered),
                     'correct_query_and_valid_answer_selection': sum(r.get('correct',False) and r['answer']['model_selection_valid'] for r in answered)}
        k,n=out[mode]['after_repair_correct'],len(group)
        z=1.96
        center=(k/n+z*z/(2*n))/(1+z*z/n)
        spread=z*math.sqrt((k/n)*(1-k/n)/n+z*z/(4*n*n))/(1+z*z/n)
        out[mode]['execution_accuracy_wilson95']=[round(center-spread,4),round(center+spread,4)]
        latencies=sorted(r['seconds'] for r in group)
        out[mode]['p95_query_seconds']=latencies[min(n-1,math.ceil(.95*n)-1)]
        out[mode]['thinking_actually_enabled']=sum(r.get('thinking',False) for r in group)
        out[mode]['by_category'] = {kind: {'n': len(g := [r for r in group if r['category'] == kind]),
                                                  'correct': sum(r.get('correct', False) for r in g)}
                                    for kind in sorted({r['category'] for r in group})}
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--split', choices=['dev', 'test'], required=True)
    p.add_argument('--modes', nargs='+', default=['schema', 'fewshot', 'thinking'], choices=['schema', 'fewshot', 'thinking'])
    p.add_argument('--workers', type=int, default=2)
    p.add_argument('--answer-limit', type=int, default=4)
    p.add_argument('--wait-minutes', type=float, default=2)
    args = p.parse_args()
    path = ROOT / 'reports' / f'chatbot_benchmark_{args.split}.json'
    cases = [c for c in json.loads((ROOT / 'configs/chatbot_benchmark.json').read_text(encoding='utf-8')) if c['split'] == args.split]
    fingerprint = hashlib.sha256((SCHEMA + (ROOT/'configs/chatbot_examples.json').read_text(encoding='utf-8') +
                                  (ROOT/'src/player_graph/chatbot/query.py').read_text(encoding='utf-8')).encode()).hexdigest()
    data_hash = hashlib.sha256((ROOT/'configs/chatbot_benchmark.json').read_bytes()).hexdigest()
    client, graph = KaggleClient.discover(args.wait_minutes), Graph()
    print('Kaggle connected; graph connected; split=', args.split, 'cases=', len(cases), flush=True)
    previous = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    if previous and (previous.get('prompt_code_hash') != fingerprint or previous.get('dataset_hash') != data_hash):
        raise RuntimeError('Report exists for different frozen config; archive it before rerunning')
    records = previous.get('records', [])
    done = {(r['mode'], r['id']) for r in records}
    expected = {c['id']: graph.run(c['cypher'], c['params']) for c in cases}
    snapshot = graph.run('MATCH (n:FootballEntity) RETURN n.dataset_id AS dataset,count(*) AS count')
    def save():
        data = {'model': client.model, 'backend': 'kaggle_cloudflare', 'split': args.split,
                'prompt_code_hash': fingerprint, 'dataset_hash': data_hash, 'graph': snapshot,
                'canonical_parameters_supplied': True, 'workers': args.workers,
                'note': 'Exploratory fixed-snapshot execution match. Not independent real-world factual accuracy. No entity-linking score. Test cases have heldout wording/entities, not all unseen query families.',
                'summary': summarize(records), 'records': records}
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        tmp.replace(path)
    def evaluate(job):
        mode, c = job
        t = time.monotonic()
        r = {'mode': mode, 'id': c['id'], 'question': c['question'], 'category': c['category'], 'hops': c['hops']}
        try:
            result = generate(client, graph, c['question'], c['params'], mode)
            r.update(result)
            r['correct'] = result['ok'] and normalized(result['rows']) == normalized(expected[c['id']])
            r['first_correct'] = r['correct'] and len(result['attempts']) == 1
            r['expected'] = expected[c['id']]
        except Exception as e:
            r.update(ok=False, correct=False, first_correct=False, error=type(e).__name__ + ': ' + str(e)[:150])
        r['seconds'] = round(time.monotonic()-t, 3)
        return r
    try:
        jobs = [(mode,c) for c in cases for mode in args.modes if (mode,c['id']) not in done]
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            for future in concurrent.futures.as_completed([pool.submit(evaluate, j) for j in jobs]):
                r = future.result()
                records.append(r)
                save()
                print(r['mode'], r['id'], 'correct=', r['correct'], 'attempts=', len(r.get('attempts',[])), 'seconds=', r['seconds'], flush=True)
        case_map = {c['id']: c for c in cases}
        for mode in args.modes:
            picked = []
            # Predefined representative families; do not select only successfully answered questions.
            for kind in ['lookup', 'multihop', 'count', 'empty']:
                candidate = next((r for r in records if r['mode']==mode and r['category']==kind), None)
                if candidate:
                    picked.append(candidate)
            for r in picked[:args.answer_limit]:
                if 'answer' in r or not r.get('ok'):
                    continue
                c = case_map[r['id']]
                try:
                    packets = evidence_packets(graph,r['rows'],c.get('provenance',{}),c['params'])
                    r['packets'] = packets
                    r['answer'] = synthesize(client, packets, c['question'])
                except Exception as e:
                    r['answer_error'] = type(e).__name__ + ': ' + str(e)[:150]
                save()
        print(json.dumps(summarize(records), ensure_ascii=False), flush=True)
    finally:
        graph.close()


if __name__ == '__main__':
    main()
