"""Run one graph QA question using the measured policy and Kaggle endpoint."""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from player_graph.chatbot.backend import KaggleClient
from player_graph.chatbot.query import Graph,generate,category,DIRECT,fold
from player_graph.chatbot.linking import link
from player_graph.chatbot.answer import evidence_packets,synthesize
from player_graph.chatbot.analytics import choose_tool,tools
from player_graph.chatbot.templates import generate_template


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('question')
    parser.add_argument('--mode',choices=['auto','schema','fewshot','thinking','template'],default='auto')
    parser.add_argument('--analytics',action='store_true')
    parser.add_argument('--params',help='Optional canonical parameters JSON; otherwise use alias linker')
    args=parser.parse_args()
    if any(w in fold(args.question) for w in ('hien tai','dang thi dau','dang dan dat')):
        raise SystemExit('Graph lưu quan hệ lịch sử; chưa đủ dữ liệu xác nhận trạng thái hiện tại.')
    graph=Graph()
    try:
        params=json.loads(args.params) if args.params else link(graph,args.question)
        print('Thực thể:',json.dumps(params,ensure_ascii=False),flush=True)
        client=KaggleClient.discover()
        q=fold(args.question)
        auto_analytics = (not params and ('pagerank' in q or ('xep hang' in q and ('clb' in q or 'cau lac bo' in q)))) or (
            set(params)=={'club'} and 'bao nhieu' in q and ('cau thu' in q or 'thi dau' in q))
        if args.analytics or auto_analytics:
            name,values,call=choose_tool(client,args.question,params)
            rows=tools(graph,name,values)
            packets=[{'row_id':f'R{i+1}','data':r,'sources':[],'provenance_kind':'aggregate'} for i,r in enumerate(rows)]
            answer=synthesize(client,packets,args.question)
            print(json.dumps({'tool':name,'args':values,'rows':rows,'answer':answer},ensure_ascii=False,indent=2))
            return
        if not params and args.params is None:
            raise SystemExit('Chưa xác định được thực thể. Hãy dùng tên đầy đủ hoặc --params; với phân tích toàn mạng dùng --analytics.')
        mode=args.mode
        if mode=='auto':
            policy=json.loads((ROOT/'configs/chatbot_policy.json').read_text(encoding='utf-8'))
            mode=policy['by_category'].get(category(args.question),policy['default_mode'])
        result=generate_template(client,graph,args.question,params) if mode=='template' else generate(client,graph,args.question,params,mode)
        if not result['ok']:
            raise SystemExit('Chưa sinh được truy vấn hợp lệ; mẫu không hỗ trợ hoặc cần diễn đạt rõ hơn.')
        query=result['query']
        relations=set(re.findall(r'\[[^\]]*:\s*([A-Z_]+)',query))
        provenance={'kind':'aggregate'}
        if 'TEAMMATE_OF' in relations and 'PLAYED_UNDER' in relations:
            provenance={'kind':'derived'}
        elif len(relations)==1 and next(iter(relations)) in DIRECT:
            relation=next(iter(relations))
            if len(params)==1:
                anchor=next(iter(params))
                reverse=relation in {'APPOINTED_MANAGER_OF','DISMISSED_MANAGER'}
                side=('subject' if reverse else 'object') if anchor=='club' else 'subject'
                provenance={'kind':'direct','relation':relation,'anchor_param':anchor,'anchor_side':side}
        packets=evidence_packets(graph,result['rows'],provenance,params)
        answer=synthesize(client,packets,args.question)
        print(json.dumps({'mode':mode,'cypher':query,'attempts':len(result['attempts']),
                          'rows':result['rows'],'packets':packets,'answer':answer},ensure_ascii=False,indent=2))
    finally:
        graph.close()


if __name__=='__main__':main()
