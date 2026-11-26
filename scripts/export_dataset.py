"""Export the published dataset (dataset/) from a built graph.

Published: nodes, extracted edges with their source-article links, the three Wikidata-derived
networks, the article index (URL, title, date; no article text), network-analysis results,
the graded quality samples (without quoted sentences) and the 14B comparison graph.
Not published: article text and evidence sentences (copyright of the news outlets); they can be
re-fetched from the listed URLs.

    python scripts/export_dataset.py [--run student_4b_v1] [--compare student_v4]
"""
import argparse
import csv
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def jsonl(path):
    with open(path, encoding='utf-8') as f:
        return [json.loads(line) for line in f if line.strip()]


def write_csv(path, header, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='') as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    return len(rows)


def export_graph(run, out, with_articles=True):
    g = ROOT / 'data/graph' / run
    entities = jsonl(g / 'entities.jsonl')
    name = {e['entity_id']: e['canonical_name'] for e in entities}
    counts = {}
    counts['nodes'] = write_csv(out / 'nodes.csv', ['entity_id', 'name', 'type', 'roles', 'wikidata_qid', 'aliases'],
                                [[e['entity_id'], e['canonical_name'], e['entity_type'], '|'.join(e.get('roles') or []),
                                  e.get('qid') or '', '|'.join(e.get('aliases') or [])] for e in entities])
    relations = jsonl(g / 'relations.jsonl')
    counts['edges'] = write_csv(out / 'edges.csv',
                                ['relation_id', 'subject_id', 'subject', 'relation', 'object_id', 'object', 'evidence_count',
                                 'languages', 'first_seen', 'last_seen'],
                                [[r['relation_id'], r['subject_id'], name.get(r['subject_id'], ''), r['relation_type'],
                                  r['object_id'], name.get(r['object_id'], ''), r['evidence_count'], '|'.join(r['languages']),
                                  r.get('first_publication') or '', r.get('last_publication') or ''] for r in relations])
    if not with_articles:
        return counts
    articles = {a['article_id']: a for a in jsonl(g / 'articles.jsonl')}
    used = {aid for r in relations for aid in r['article_ids']}
    counts['edge_sources'] = write_csv(out / 'edge_sources.csv', ['relation_id', 'article_id', 'url'],
                                       [[r['relation_id'], aid, articles.get(aid, {}).get('canonical_url', '')]
                                        for r in relations for aid in r['article_ids']])
    counts['articles'] = write_csv(out / 'articles.csv', ['article_id', 'url', 'title', 'source', 'language', 'published'],
                                   [[a['article_id'], a['canonical_url'], a['title'], a['source'], a['language'],
                                     a.get('published_at') or ''] for aid, a in sorted(articles.items()) if aid in used])
    derived = {'teammate_of': ['years_together', 'clubs'], 'played_under': ['years_together', 'clubs'],
               'transfer_flow': ['moves']}
    for kind, extra in derived.items():
        rows = jsonl(g / 'derived' / f'{kind}.jsonl')
        counts[kind] = write_csv(out / 'derived' / f'{kind}.csv',
                                 ['subject_id', 'subject', 'object_id', 'object'] + extra,
                                 [[r['subject_id'], name.get(r['subject_id'], ''), r['object_id'], name.get(r['object_id'], '')]
                                  + ['|'.join(r[k]) if isinstance(r[k], list) else r[k] for k in extra] for r in rows])
    return counts


def strip_quotes(src, dst):
    """Graded samples without the quoted evidence sentence."""
    with open(src, encoding='utf-8-sig') as f:
        rows = list(csv.DictReader(f))
    keep = [k for k in rows[0] if k != 'bang_chung']
    write_csv(dst, keep, [[r[k] for k in keep] for r in rows])
    return len(rows)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--run', default='student_4b_v1')
    p.add_argument('--compare', default='student_v4')
    p.add_argument('--out', default='dataset')
    args = p.parse_args()
    out = ROOT / args.out
    readme = (out / 'README.md').read_text(encoding='utf-8') if (out / 'README.md').exists() else None
    if out.exists():
        shutil.rmtree(out)
    if readme is not None:  # hand-written, not generated
        out.mkdir(parents=True)
        (out / 'README.md').write_text(readme, encoding='utf-8')
    summary = {'main_graph': args.run, 'main': export_graph(args.run, out / 'graph'),
               'comparison_graph': args.compare, 'comparison': export_graph(args.compare, out / 'comparison_14b', with_articles=False)}
    reports = ROOT / 'reports'
    for name in [f'sna_{args.run}.json', f'cores_{args.run}.json', f'graph_stats_{args.run}.json',
                 f'sna_{args.compare}.json', f'cores_{args.compare}.json']:
        if (reports / name).exists():
            (out / 'analysis').mkdir(parents=True, exist_ok=True)
            shutil.copy(reports / name, out / 'analysis' / name)
    summary['graded_samples'] = {
        name: strip_quotes(reports / src, out / 'evaluation' / name)
        for src, name in [('sample_student_v4.csv', 'graded_sample_14b_150.csv'),
                          ('sample_student_4b_v1_only.csv', 'graded_sample_4b_only_60.csv')] if (reports / src).exists()}
    # LLM-as-judge: 400 random edges of the main graph (seed 2026), graded against the source articles.
    for name in ['cham_llm_judge_400.csv', 'cham_llm_judge_400_meta.json']:
        if (reports / name).exists():
            shutil.copy(reports / name, out / 'evaluation' / name.replace('cham_', ''))
    (out / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
