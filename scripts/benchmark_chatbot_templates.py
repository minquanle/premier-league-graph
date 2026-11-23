"""Evaluate frozen-example query compilation on dev, then heldout wording."""
import json,sys,time,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
sys.path.insert(0,str(ROOT/'scripts'))
from player_graph.chatbot.backend import KaggleClient
from player_graph.chatbot.query import Graph
from player_graph.chatbot.templates import generate_template
from player_graph.chatbot.answer import evidence_packets,synthesize
from benchmark_chatbot import normalized,summarize

client,graph=KaggleClient.discover(),Graph()
cases=json.loads((ROOT/'configs/chatbot_benchmark.json').read_text(encoding='utf-8'))
out=ROOT/'reports/chatbot_template_benchmark.json'
records=[]
try:
    for split in ['dev','test']:
        for c in [c for c in cases if c['split']==split]:
            start=time.monotonic()
            result=generate_template(client,graph,c['question'],c['params'])
            expected=graph.run(c['cypher'],c['params'])
            record={'mode':'template','id':c['id'],'split':split,'category':c['category'],'question':c['question'],**result}
            record.update(correct=result['ok'] and normalized(result['rows'])==normalized(expected),seconds=round(time.monotonic()-start,3))
            record['first_correct']=record['correct']
            record['expected']=expected
            records.append(record)
            out.write_text(json.dumps({'scope':'Exploratory additional candidate using only the pre-existing 20 examples as executable templates. Same benchmark reused for comparison. No generated code.',
                                       'examples_hash':hashlib.sha256((ROOT/'configs/chatbot_examples.json').read_bytes()).hexdigest(),
                                       'summary':{s:summarize([r for r in records if r['split']==s]) for s in ['dev','test'] if any(r['split']==s for r in records)},'records':records},ensure_ascii=False,indent=2),encoding='utf-8')
            print(split,c['id'],'correct=',record['correct'],'seconds=',record['seconds'],flush=True)
        if split=='dev':
            # Select from dev only, before generating any template test responses.
            dev=json.loads((ROOT/'reports/chatbot_benchmark_dev.json').read_text(encoding='utf-8'))
            old=dev['summary']['fewshot'];new=summarize(records)['template']
            if new['after_repair_correct']>old['after_repair_correct'] or (new['after_repair_correct']==old['after_repair_correct'] and new['mean_query_seconds']<old['mean_query_seconds']):
                policy=json.loads((ROOT/'configs/chatbot_policy.json').read_text(encoding='utf-8'))
                policy['default_mode']='template'
                policy['by_category']={k:'template' for k in policy['by_category']}
                policy['selection_source']='Template 8 dev cases compared to frozen prompt dev results; selected before template test generation'
                (ROOT/'configs/chatbot_policy.json').write_text(json.dumps(policy,ensure_ascii=False,indent=2),encoding='utf-8')
    # Full grounded answer for one direct and one multi-hop case, separate from query score.
    for kind in ['lookup','multihop']:
        r=next(r for r in records if r['split']=='test' and r['category']==kind)
        if r['ok']:
            c=next(c for c in cases if c['id']==r['id'])
            packets=evidence_packets(graph,r['rows'],r.get('provenance',{}),c['params'])
            r['packets']=packets;r['answer']=synthesize(client,packets,c['question'])
    data=json.loads(out.read_text(encoding='utf-8'));data['records']=records
    out.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
finally:graph.close()
