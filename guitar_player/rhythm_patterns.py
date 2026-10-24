"""Editable accompaniment patterns; lengths use quarter-note beats internally."""
from dataclasses import dataclass
from fractions import Fraction as F

from .guitar import UP,DOWN


@dataclass(frozen=True)
class RhythmPattern:
    key:str
    name:str
    meter:tuple
    actions:tuple  # (string/bass/up/down/rest, quarter-note beats)


def _pattern(key,name,meter,actions):
    return RhythmPattern(key,name,meter,tuple((action,F(length)) for action,length in actions))


PATTERNS=(
    _pattern('arp8','分解 · 低音 3 2 1 2 3 2 3',(4,4),[(s,'1/2') for s in ('bass',3,2,1,2,3,2,3)]),
    _pattern('arp_up_down','分解 · 低音 4 3 2 1 2 3 4',(4,4),[(s,'1/2') for s in ('bass',4,3,2,1,2,3,4)]),
    _pattern('arp_alternating','分解 · 交替低音',(4,4),[(s,'1/2') for s in ('bass',3,2,3,4,3,1,3)]),
    _pattern('strum_quarters','扫弦 · 四分下扫',(4,4),[('down',1)]*4),
    _pattern('strum_eighths','扫弦 · 八分交替下上',(4,4),[(s,'1/2') for s in ('down','up')*4]),
    _pattern('strum_sixteenths','扫弦 · 十六分交替下上',(4,4),[(s,'1/4') for s in ('down','up')*8]),
    _pattern('strum_folk','扫弦 · 下 下上 空上 下上',(4,4),[('down',1),('down','1/2'),('up','1/2'),('rest','1/2'),('up','1/2'),('down','1/2'),('up','1/2')]),
    _pattern('strum_syncopated','扫弦 · 下 空上 空上 下上',(4,4),[('down',1),('rest','1/2'),('up','1/2'),('rest','1/2'),('up','1/2'),('down','1/2'),('up','1/2')]),
    _pattern('pick_strum','混合 · 低音 下扫 3弦 2弦 上扫',(4,4),[('bass',1),('down',1),(3,'1/2'),(2,'1/2'),('up',1)]),
    _pattern('waltz_pick','分解 · 三拍圆舞曲',(3,4),[(s,'1/2') for s in ('bass',3,2,1,2,3)]),
    _pattern('waltz_strum','扫弦 · 三拍低音 下 上',(3,4),[('bass',1),('down',1),('up',1)]),
    _pattern('six_eight_pick','分解 · 六八拍流动',(6,8),[(s,'1/2') for s in ('bass',3,2,1,2,3)]),
    _pattern('six_eight_strum','扫弦 · 六八拍 下下上下下上',(6,8),[(s,'1/2') for s in ('down','down','up','down','down','up')]),
)
PATTERN_MAP={pattern.key:pattern for pattern in PATTERNS}


def pattern_cells(doc,index,key,bass='auto',chord=None):
    from .fingerstyle_document import FingerCell,HIT_X,chord_name
    from .voicings import reference_voicings
    pattern=key if isinstance(key,RhythmPattern) else PATTERN_MAP[key];bar=doc.bar(index)
    if bar.meter!=pattern.meter:raise ValueError(f'第 {index+1} 小节拍号为 {bar.meter[0]}/{bar.meter[1]}，此预设用于 {pattern.meter[0]}/{pattern.meter[1]}。请选匹配拍号的预设，或先关闭此窗口，在“小节与反复 → 小节设置”调整目标拍号。')
    if chord is None and index in doc.measure_repeats:
        from .composition import measure_packets
        chord=measure_packets(doc)[index].chord
    chord=chord or doc.chord_at(bar.start)
    if not chord:raise ValueError(f'第 {index+1} 小节尚未标记和弦。请在此窗口的“和弦”框选择和弦，或回到“音符与奏法 → 标记此处”添加和弦后，再沿用各小节。')
    chord=chord_name(chord)
    voicing=reference_voicings().get(chord)
    available={s for s in range(1,7) if voicing and voicing.pitches[s-1] is not None and voicing.pitches[s-1]>=0}
    if bass=='auto':bass=max(available) if available else 6
    else:bass=int(bass)
    if bass not in range(1,7):raise ValueError('低音弦应为自动或 1～6 弦')
    cells=[];at=bar.start
    for action,length in pattern.actions:
        values=[None]*6;sweep=0
        if action in ('up','down'):values=[HIT_X]*6;sweep=UP if action=='up' else DOWN
        elif action!='rest':
            strings=(bass,) if action=='bass' else action if isinstance(action,tuple) else (action,)
            for string in strings:
                if voicing and string not in available:
                    guide='请将此窗口的“低音弦”改为“自动”或可发声弦。' if action=='bass' else '此弦是预设的固定弦位，调整“低音弦”不会改变它。请更换和弦／预设，或回谱面改写弦位并重新保存预设。'
                    raise ValueError(f'第 {index+1} 小节 {chord} 的 {string} 弦为静音；'+guide)
                values[string-1]=HIT_X
        cells.append(FingerCell(at,length,tuple(values),sweep));at+=length
    if at!=bar.end:raise ValueError('节奏组长度与小节不一致')
    return tuple(cells),chord


def apply_pattern(doc,first,last,key,bass='auto',chord=None):
    from .composition import _boundary
    if not 0<=first<=last<doc.bars:raise ValueError('请选择有效的小节范围')
    _boundary(doc,first,last);state=doc.snapshot()
    try:
        cells=[];labels=[]
        for index in range(first,last+1):
            group,name=pattern_cells(doc,index,key,bass,chord)
            cells.extend(group);labels.append((doc.bar(index).start,name))
        start=doc.bar(first).start;end=doc.bar(last).end;following=doc.chord_at(end)
        doc.cells=[c for c in doc.cells if not start<=c.start<end]+cells
        doc.chord_changes={at:name for at,name in doc.chord_changes.items() if not start<=at<end}
        doc.chord_changes.update(labels)
        if end<doc.layout[-1].end:doc.chord_changes.setdefault(end,following or 'N.C.')
        doc.measure_repeats.difference_update(range(first,last+1));doc.ensure_bars()
    except Exception:doc.restore(state);raise
