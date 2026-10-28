"""Map source presentation only; never infer or generate knowledge content."""
import re
from .contracts import CanvasStyle, TitleFormat

STRUCTURES = {'org.xmind.ui.logic.right':'right','org.xmind.ui.logic.left':'left',
              'org.xmind.ui.map.unbalanced':'balanced','org.xmind.ui.org-chart.down':'down',
              'org.xmind.ui.org-chart.up':'up'}


def color(value):
    if isinstance(value,str) and re.fullmatch(r'#[0-9a-fA-F]{6}(FF)?',value):return value[:7]
    return None


def presentation(topic, theme, depth, kind, warn):
    data={}
    structure=topic.get('structureClass')
    if structure in STRUCTURES:data['structure']=STRUCTURES[structure]
    elif structure:warn('部分导图结构暂未支持，按原层级显示，原始结构保留在原件')
    key='floatingTopic' if kind=='detached' else 'summaryTopic' if kind=='summary' else 'centralTopic' if depth==0 else 'mainTopic' if depth==1 else 'subTopic'
    themed=theme.get(key,{}) if isinstance(theme,dict) else {}
    own=topic.get('style',{})
    def properties(value):
        props=value.get('properties',{}) if isinstance(value,dict) else {}
        return props if isinstance(props,dict) else {}
    props={**properties(themed),**properties(own)}
    style={}
    for source,target in [('fo:color','color'),('svg:fill','background'),('border-line-color','border')]:
        value=color(props.get(source))
        if value:style[target]=value
    value=props.get('fo:font-size')
    if isinstance(value,str) and re.fullmatch(r'\d+(?:\.\d+)?(?:pt|px)',value):
        size=round(float(value[:-2])*(4/3 if value.endswith('pt') else 1))
        style['font_size']=max(10,min(48,size))
        if not 10<=size<=48:warn('部分字号超出现有画布范围，已使用最接近字号，原值保留在原件')
    if props.get('fo:font-weight') not in (None,'inherited'):style['bold']=props['fo:font-weight'] in ('bold','600','700','800','900')
    if props.get('fo:font-style') not in (None,'inherited'):style['italic']=props['fo:font-style']=='italic'
    if isinstance(props.get('fo:text-decoration'),str) and props['fo:text-decoration']!='inherited':
        style['underline']='underline' in props['fo:text-decoration'];style['strike']='line-through' in props['fo:text-decoration']
    shapes={'org.xmind.topicShape.rect':'rectangle','org.xmind.topicShape.roundedRect':'rounded','org.xmind.topicShape.ellipse':'pill'}
    if props.get('shape-class') in shapes:style['shape']=shapes[props['shape-class']]
    if style:data['style']=CanvasStyle(**style).model_dump()
    width=topic.get('customWidth')
    if isinstance(width,(float,int)) and not isinstance(width,bool) and 80<=width<=800:data['width']=round(width)
    runs=[]
    attributed=topic.get('attributedTitle')
    if isinstance(attributed,list) and attributed and len(attributed)<=200:
        for run in attributed:
            if not isinstance(run,dict) or not isinstance(run.get('text'),str) or not 1<=len(run['text'])<=200:break
            item={'text':run['text']}
            if color(run.get('fo:color')):item['color']=color(run['fo:color'])
            if run.get('fo:font-weight'):item['bold']=run['fo:font-weight'] in ('bold','600','700','800','900')
            if run.get('fo:font-style'):item['italic']=run['fo:font-style']=='italic'
            runs.append(item)
        else:
            if ''.join(r['text'] for r in runs)==topic.get('title','').strip()[:200]:
                data['title_format']=TitleFormat(runs=runs,align=props.get('fo:text-align') if props.get('fo:text-align') in ('left','center','right') else 'left').model_dump()
    return data


def arrange(topics, heights):
    """Orient the deterministic tree layout without changing source parentage."""
    children={i:[] for i in range(-1,len(topics))}
    original=[(topic['x'],topic['y']) for topic in topics]
    for i,topic in enumerate(topics):children[topic['parent_index']].append(i)
    top=0
    def orient(index,direction,members):
        members.append(index)
        direction=topics[index].get('presentation',{}).get('structure',direction)
        for order,child in enumerate(children[index]):
            dx=original[child][0]-original[index][0];dy=original[child][1]-original[index][1]
            branch=('left' if order%2 else 'right') if direction=='balanced' else direction
            x,y=(-dx,dy) if branch=='left' else (dy,dx) if branch=='down' else (dy,-dx) if branch=='up' else (dx,dy)
            topics[child]['x']=topics[index]['x']+x;topics[child]['y']=topics[index]['y']+y
            orient(child,branch,members)
    for root in children[-1]:
        members=[];orient(root,'right',members)
        left=min(topics[i]['x'] for i in members);start=min(topics[i]['y'] for i in members)
        for i in members:topics[i]['x']-=left;topics[i]['y']+=top-start
        top=max(topics[i]['y']+heights[i] for i in members)+120
    return top
