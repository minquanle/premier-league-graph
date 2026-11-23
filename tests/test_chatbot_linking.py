import pytest
from player_graph.chatbot.linking import link


class FakeGraph:
    def __init__(self,rows):self.rows=rows
    def run(self,*args,**kwargs):return self.rows


ROWS=[{'name':'Manchester United','aliases':['Man Utd'],'labels':['FootballEntity','Club']},
      {'name':'Chelsea','aliases':[],'labels':['FootballEntity','Club']},
      {'name':'Wayne Rooney','aliases':['Rooney'],'labels':['FootballEntity','Person']},
      {'name':'José Mourinho','aliases':['Mourinho'],'labels':['FootballEntity','Person']}]


def test_aliases_are_canonical_parameters():
    assert link(FakeGraph(ROWS),'Ai chơi cho MU?')=={'club':'Manchester United'}
    assert link(FakeGraph(ROWS),'CLB của Rooney?')=={'person':'Wayne Rooney'}


def test_accent_insensitive_longest_match():
    assert link(FakeGraph(ROWS),'Jose Mourinho từng dẫn dắt Chelsea?')=={'person':'José Mourinho','club':'Chelsea'}


def test_token_boundaries_and_repeated_mentions():
    assert link(FakeGraph(ROWS),'Manchester United và MU')=={'club':'Manchester United'}
    assert link(FakeGraph(ROWS),'chumua')=={}


def test_second_club_and_ambiguous_surname():
    assert link(FakeGraph(ROWS),'MU và Chelsea')=={'club':'Manchester United','other':'Chelsea'}
    rows=ROWS+[{'name':'Alex Rooney','aliases':['Rooney'],'labels':['Person']}]
    with pytest.raises(ValueError,match='Tên chưa rõ'):link(FakeGraph(rows),'Rooney có đồng đội nào?')
