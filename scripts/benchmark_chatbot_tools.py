"""Smoke-test tool selection and actual computation; not a PoT benchmark."""
import json
import sys
import time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from player_graph.chatbot.backend import KaggleClient
from player_graph.chatbot.query import Graph
from player_graph.chatbot.analytics import choose_tool,tools

client,graph=KaggleClient.discover(),Graph()
cases=[('Theo graph, Chelsea có bao nhiêu người từng thi đấu?',{'club':'Chelsea'},'count_players'),
       ('Xếp hạng 10 CLB theo số người từng thi đấu trong graph.',{},'top_clubs'),
       ('Trong toàn bộ mạng đồng đội, 10 người có PageRank cao nhất là ai?',{},'pagerank_teammates')]
records=[]
try:
    for question,params,expected in cases:
        start=time.monotonic()
        r={'question':question,'expected_tool':expected}
        try:
            name,args,response=choose_tool(client,question,params)
            rows=tools(graph,name,args)
            r.update(tool=name,args=args,selector_correct=name==expected,rows=rows,model_seconds=response['seconds'])
        except Exception as error:r['error']=type(error).__name__+': '+str(error)[:150]
        r['total_seconds']=round(time.monotonic()-start,3)
        records.append(r)
        print(json.dumps(r,ensure_ascii=False),flush=True)
finally:graph.close()
(ROOT/'reports/chatbot_tools_smoke.json').write_text(json.dumps({'scope':'3 tool-selection smoke tests with real computation, not generated Python/PoT','records':records},ensure_ascii=False,indent=2),encoding='utf-8')
