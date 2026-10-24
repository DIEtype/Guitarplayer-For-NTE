"""Local, reusable rhythm and string patterns, independent of a song's pitch."""
from dataclasses import replace
from fractions import Fraction as F
import json
from pathlib import Path
import re
import uuid

from .composition import capture_measures
from .guitar import UP
from .measures import valid_meter
from .rhythm_patterns import RhythmPattern
from .voicings import reference_voicings


def valid_pattern(pattern):
    if (not re.fullmatch(r'user_[a-f0-9]{32}',pattern.key) or not isinstance(pattern.name,str)
            or not pattern.name.strip() or len(pattern.name)>64 or any(ord(c)<32 for c in pattern.name)):
        raise ValueError('预设名称需为 1～64 个可见字符；请在“预设名称”框中填写名称，不使用换行或控制字符')
    if not valid_meter(pattern.meter) or not 1<=len(pattern.actions)<=512:raise ValueError('预设拍号或音位数无效')
    for action,duration in pattern.actions:
        if type(action) is int:ok=1<=action<=6
        elif isinstance(action,tuple):ok=bool(action) and len(set(action))==len(action) and all(type(s) is int and 1<=s<=6 for s in action)
        else:ok=action in ('bass','up','down','rest')
        if not ok or not F(1,960)<=duration<=128:raise ValueError('预设弦号、动作或时值无效')
    if sum((length for _,length in pattern.actions),F(0))!=F(pattern.meter[0]*4,pattern.meter[1]):
        raise ValueError('节奏预设必须恰好占满一个小节')
    return pattern


def capture_pattern(doc,index,name,adaptive_bass=True,key=None):
    packet=capture_measures(doc,index,index)[0]
    known=reference_voicings().get(packet.chord)
    used=[s for cell in packet.cells for s in cell.members]
    lowest=max((s for s,p in enumerate(known.pitches,1) if p is not None and p>=0),default=6) if known else max(used,default=6)
    cursor=F(0);actions=[]
    for cell in packet.cells:
        if cursor<cell.start:actions.append(('rest',cell.start-cursor))
        if cell.sweep:action='up' if cell.sweep==UP else 'down'
        elif not cell.members:action='rest'
        elif adaptive_bass and cell.members==(lowest,):action='bass'
        else:action=tuple(cell.members)
        actions.append((action,cell.beats));cursor=cell.end
    if cursor<packet.beats:actions.append(('rest',packet.beats-cursor))
    return valid_pattern(RhythmPattern(key or 'user_'+uuid.uuid4().hex,str(name).strip(),packet.meter,tuple(actions)))


def _read_patterns(path):
    if path is None or not Path(path).exists():return {}
    path=Path(path)
    if path.stat().st_size>2_000_000:raise ValueError('节奏预设库超过 2 MB')
    data=json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(data,dict) or data.get('version')!=1 or not isinstance(data.get('patterns'),list) or len(data['patterns'])>128:
        raise ValueError('节奏预设库格式无效')
    result={}
    for row in data['patterns']:
        try:
            actions=tuple((tuple(action) if isinstance(action,list) else action,F(length)) for action,length in row['actions'])
            pattern=valid_pattern(RhythmPattern(row['key'],row['name'],tuple(row['meter']),actions))
        except (KeyError,TypeError,ZeroDivisionError) as error:raise ValueError('节奏预设内容无效') from error
        if pattern.key in result or any(p.name==pattern.name for p in result.values()):raise ValueError('节奏预设名称或编号重复')
        result[pattern.key]=pattern
    return result


def read_patterns(path):
    try:return _read_patterns(path)
    except (ValueError,TypeError,KeyError,UnicodeError) as error:
        raise ValueError(f'节奏预设库内容无法读取（{path}）。原文件已保留。请先备份该文件，再从正常备份恢复；如需新建空库，可关闭程序后将此文件移出 settings 文件夹，再重新打开“节奏预设”。内置预设仍可使用。') from error
    except OSError as error:
        from .composer_guidance import file_guidance
        raise ValueError(file_guidance(error,'读取预设库')+f' 文件：{path}') from error


def write_patterns(path,patterns):
    if path is None:raise ValueError('未配置本地预设目录；请从正常启动入口打开程序，再保存预设')
    patterns=tuple(patterns.values())
    if len(patterns)>128:raise ValueError('最多保存 128 个自定义节奏预设；请在“我的预设”删除不用的项目，或用当前小节更新已有预设')
    for item in patterns:valid_pattern(item)
    if len({p.key for p in patterns})!=len(patterns) or len({p.name for p in patterns})!=len(patterns):raise ValueError('节奏预设名称不可重复；请更换“预设名称”。若要替换已有预设，请取消后在“我的预设”右键该项目，选择“用当前小节更新此预设…”')
    data={'version':1,'patterns':[{'key':p.key,'name':p.name,'meter':list(p.meter),
        'actions':[[action,str(length)] for action,length in p.actions]} for p in patterns]}
    text=json.dumps(data,ensure_ascii=False,indent=2)+'\n'
    if len(text.encode('utf-8'))>2_000_000:raise ValueError('节奏预设库超过 2 MB，请先删减不再使用的预设')
    path=Path(path)
    try:
        path.parent.mkdir(parents=True,exist_ok=True);temporary=path.with_suffix('.json.tmp')
        temporary.write_text(text,encoding='utf-8');temporary.replace(path)
    except OSError as error:
        from .composer_guidance import file_guidance
        raise ValueError(file_guidance(error,'保存预设')+f' 文件：{path}') from error


def save_pattern(path,pattern):
    patterns=read_patterns(path);patterns[pattern.key]=valid_pattern(pattern);write_patterns(path,patterns)
    return pattern


def rename_pattern(path,key,name):
    patterns=read_patterns(path)
    if key not in patterns:raise ValueError('请选择自定义节奏预设')
    item=valid_pattern(replace(patterns[key],name=str(name).strip()));patterns[key]=item;write_patterns(path,patterns)
    return item


def delete_pattern(path,key):
    patterns=read_patterns(path)
    if key not in patterns:raise ValueError('内置预设不能删除；请选择“我的预设”中的项目')
    del patterns[key];write_patterns(path,patterns)
