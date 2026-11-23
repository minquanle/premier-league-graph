"""Constrained alternative: classify intent, compile an existing checked query."""
import json
import re
from .backend import ROOT
from .query import validate


def generate_template(client,graph,question,params):
    examples=json.loads((ROOT/'configs/chatbot_examples.json').read_text(encoding='utf-8'))
    candidates=[e for e in examples if set(re.findall(r'\$(\w+)',e['cypher']))==set(params)]
    if not candidates:
        return {'ok':False,'attempts':[],'error':'No template supports these parameter roles','thinking':False}
    catalog=[{'id':e['id'],'example_question':e['question'],'example_params':e['params'],
              'relations':re.findall(r'\[[^\]]*:\s*([A-Z_]+)',e['cypher']),
              'output':'count' if ' AS count' in e['cypher'] else 'names'} for e in candidates]
    messages=[{'role':'system','content':'''Choose exactly one query template matching the user's meaning. Return JSON {"template_id":"example_01"} only. If none fits, return {"template_id":"unsupported"}.
Examples mention other people/clubs: replace names mentally with the supplied canonical parameters.
Distinguish listing from counting; coaches OF a player from PLAYERS of a coach; two-hop people from three-hop clubs.
Do not select a simpler pattern if the requested relation chain is absent from catalog. No Cypher or prose.'''
    },{'role':'user','content':json.dumps({'question':question,'params':params,'templates':catalog},ensure_ascii=False)}]
    response=client.complete(messages,json_output=True)
    attempts=[response]
    try:
        ident=json.loads(response['content'])['template_id']
        template=next((e for e in candidates if e['id']==ident),None)
        if not template:
            raise ValueError('No supported template selected')
        query,_=validate(json.dumps({'cypher':template['cypher']}),params)
        graph.run(query,params,explain=True)
        return {'ok':True,'query':query,'rows':graph.run(query,params),'attempts':attempts,
                'template_id':ident,'thinking':False,'provenance':template.get('provenance',{})}
    except Exception as error:
        attempts[0]['error']=type(error).__name__
        return {'ok':False,'attempts':attempts,'thinking':False}
