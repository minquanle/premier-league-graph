"""Benchmark the graph agent on the same held-out cases as the Text2Cypher prompts.

Same rules as scripts/benchmark_chatbot.py: canonical parameters are supplied, reference queries and
categories are never shown to the model, and correctness is a full row match on the fixed graph.
"""
import argparse
import concurrent.futures
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'scripts'))
from benchmark_chatbot import normalized, summarize
from player_graph.chatbot import agent
from player_graph.chatbot.backend import KaggleClient
from player_graph.chatbot.query import Graph


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--split', choices=['dev', 'test', 'all'], default='all')
    p.add_argument('--modes', nargs='+', default=['agent', 'agent_thinking'], choices=['agent', 'agent_thinking'])
    p.add_argument('--workers', type=int, default=4)
    p.add_argument('--wait-minutes', type=float, default=20)
    args = p.parse_args()
    cases = [c for c in json.loads((ROOT / 'configs/chatbot_benchmark.json').read_text(encoding='utf-8'))
             if args.split == 'all' or c['split'] == args.split]
    graph = Graph()
    client = KaggleClient.discover(args.wait_minutes)
    print('connected; cases', len(cases), flush=True)
    expected = {c['id']: graph.run(c['cypher'], c['params']) for c in cases}
    records = []

    def run(job):
        mode, c = job
        t = time.monotonic()
        r = {'mode': mode, 'id': c['id'], 'split': c['split'], 'question': c['question'], 'category': c['category'], 'hops': c['hops']}
        try:
            res = agent.generate(client, graph, c['question'], c['params'], thinking=mode == 'agent_thinking')
            r.update({k: v for k, v in res.items() if k != 'provenance'})
            r['correct'] = res['ok'] and normalized(res['rows']) == normalized(expected[c['id']])
            r['first_correct'] = r['correct'] and len(res['attempts']) == 1
        except Exception as e:
            r.update(ok=False, correct=False, first_correct=False, error=f'{type(e).__name__}: {str(e)[:200]}')
        r['seconds'] = round(time.monotonic() - t, 3)
        return r

    try:
        jobs = [(m, c) for c in cases for m in args.modes]
        with concurrent.futures.ThreadPoolExecutor(args.workers) as pool:
            for r in pool.map(run, jobs):
                records.append(r)
                print(r['mode'], r['id'], 'correct=', r['correct'], 'plan=', json.dumps(r.get('plan'), ensure_ascii=False), flush=True)
        out = {'model': client.model, 'method': 'hop-catalogue planner; code compiles Cypher',
               'canonical_parameters_supplied': True,
               'summary': {split: summarize([r for r in records if r['split'] == split])
                           for split in sorted({r['split'] for r in records})},
               'records': records}
        path = ROOT / 'reports' / f'chatbot_benchmark_agent_{args.split}.json'
        path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
        for split, s in out['summary'].items():
            for mode, v in s.items():
                print(split, mode, v['after_repair_correct'], '/', v['n'], 'first', v['first_correct'],
                      {k: f"{x['correct']}/{x['n']}" for k, x in v['by_category'].items()}, 'mean s', v['mean_query_seconds'])
    finally:
        graph.close()


if __name__ == '__main__':
    main()
