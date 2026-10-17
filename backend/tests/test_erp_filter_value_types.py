import pytest
from services.agent.param_converter import params_to_filters
from services.kuaimai.erp_unified_filters import validate_filters

@pytest.mark.parametrize('value,expected', [(True,1),(False,0),('true',1),(' FALSE ',0),('1',1),(0,0)])
@pytest.mark.parametrize('field', ['is_scalping','is_refund','is_cancel'])
def test_flags(value,expected,field):
    result,error=validate_filters([{'field':field,'op':'ne','value':value}],'order')
    assert error is None
    assert type(result[0].value) is int
    assert result[0].value==expected

def test_entrypoints():
    semantic,_=params_to_filters({'exclude_filters':[{'field':'is_scalping','value':True}]})
    direct=[{'field':'is_scalping','op':'ne','value':'true'}]
    assert validate_filters(semantic,'order')==validate_filters(direct,'order')
    positive,_=params_to_filters({'is_scalping':True})
    assert validate_filters(positive,'order')[0][0].value==1

@pytest.mark.parametrize('op,value,expected',[('in',[True,'0'],[1,0]),('not_in',['false',1],[0,1]),('between',['0','1'],[0,1])])
def test_arrays(op,value,expected):
    result,error=validate_filters([{'field':'is_scalping','op':op,'value':value}],'order')
    assert error is None
    assert result[0].value==expected

@pytest.mark.parametrize('field,value',[('is_scalping','maybe'),('is_scalping',0.5),('amount',True),('amount','nan'),('amount',{}),('amount',None)])
def test_invalid(field,value):
    result,error=validate_filters([{'field':field,'op':'eq','value':value}],'order')
    assert not result and field in error

def test_null_and_codes():
    result,error=validate_filters([{'field':'is_scalping','op':'is_null','value':False},{'field':'amount','op':'between','value':['1.5','20']},{'field':'outer_id','op':'eq','value':'00123'}],'order')
    assert error is None
    assert result[0].value is False
    assert result[1].value==[1.5,20.0]
    assert result[2].value=='00123'
