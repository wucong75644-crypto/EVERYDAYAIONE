"""Bounded arithmetic evidence, explicitly separate from document assertions."""
import ast
from decimal import Decimal,localcontext,InvalidOperation,DivisionByZero
import re

REQUEST=re.compile(r'核算|核对.*算|算术|计算|算式|合计')
EQUATION=re.compile(r'(?<![A-Za-z0-9_.-])([0-9][0-9.\s()+*/×÷−-]{1,100})\s*=\s*(-?[0-9]+(?:\.[0-9]+)?)')


def calculate(expression):
    expression=expression.strip().replace('×','*').replace('÷','/').replace('−','-')
    if len(expression)>100:raise ValueError('CALCULATION_CAPACITY')
    if not re.fullmatch(r'[0-9.() +*/\s-]+',expression):raise ValueError('UNSUPPORTED_CALCULATION')
    tree=ast.parse(expression,mode='eval')
    if len(list(ast.walk(tree)))>40:raise ValueError('CALCULATION_CAPACITY')
    def visit(node):
        if isinstance(node,ast.Expression):return visit(node.body)
        if isinstance(node,ast.Constant) and type(node.value) in (int,float):
            literal=ast.get_source_segment(expression,node)
            if not literal or not re.fullmatch(r'\d+(?:\.\d+)?',literal):raise ValueError('INVALID_NUMBER')
            return Decimal(literal)
        if isinstance(node,ast.UnaryOp) and isinstance(node.op,(ast.UAdd,ast.USub)):
            value=visit(node.operand);return -value if isinstance(node.op,ast.USub) else value
        if isinstance(node,ast.BinOp) and isinstance(node.op,(ast.Add,ast.Sub,ast.Mult,ast.Div)):
            a,b=visit(node.left),visit(node.right)
            if max(abs(a),abs(b))>Decimal('1e24'):raise ValueError('CALCULATION_CAPACITY')
            if isinstance(node.op,ast.Add):return a+b
            if isinstance(node.op,ast.Sub):return a-b
            if isinstance(node.op,ast.Mult):return a*b
            return a/b
        raise ValueError('UNSUPPORTED_CALCULATION')
    with localcontext() as context:
        context.prec=28;value=visit(tree)
    if not value.is_finite() or abs(value)>Decimal('1e24'):raise ValueError('CALCULATION_CAPACITY')
    text=format(value,'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def calculation_sources(sources,question):
    if not isinstance(question,str) or not REQUEST.search(question):return sources
    result=list(sources);keys={s['key'] for s in sources};count=0
    for source in sources:
        if source.get('kind') in ('metadata','calculation'):continue
        for match in EQUATION.finditer(source['text']):
            expression=match.group(1).strip()
            if not re.search(r'[+*/×÷−-]',expression):continue
            try:value=calculate(expression)
            except (ValueError,SyntaxError,InvalidOperation,DivisionByZero,OverflowError):continue
            key=source['key']+':calculation:'+str(match.start())
            if key in keys:continue
            text=f'程序算术核验（Decimal精度28位，仅核算数值，不代表业务规则）：{expression} = {value}。原文等号右侧写作 {match.group(2)}。'
            result.append({'key':key,'text':text,'kind':'calculation',**({'source':source['source']} if source.get('source') else {})})
            keys.add(key);count+=1
            if count>=20:return result
    return result
