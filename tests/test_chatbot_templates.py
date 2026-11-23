import json
from unittest.mock import Mock
from player_graph.chatbot import templates


def test_template_uses_frozen_query_not_model_cypher():
    client=Mock()
    client.complete.return_value={'content':json.dumps({'template_id':'example_02','cypher':'DELETE everything'}),'seconds':.1}
    graph=Mock()
    graph.run.return_value=[]
    r=templates.generate_template(client,graph,'HLV của Chelsea?',{'club':'Chelsea'})
    assert r['ok'] and 'MANAGES' in r['query'] and 'DELETE' not in r['query']
    assert graph.run.call_args.args[1]=={'club':'Chelsea'}


def test_forged_or_incompatible_template_id_does_not_execute():
    client=Mock()
    client.complete.return_value={'content':'{"template_id":"not_in_catalog"}','seconds':.1}
    graph=Mock()
    assert not templates.generate_template(client,graph,'question',{'club':'Chelsea'})['ok']
    graph.run.assert_not_called()


def test_unsupported_parameter_roles_do_not_call_model():
    client,graph=Mock(),Mock()
    result=templates.generate_template(client,graph,'question',{'nonexistent_role':'X'})
    assert not result['ok']
    client.complete.assert_not_called()
