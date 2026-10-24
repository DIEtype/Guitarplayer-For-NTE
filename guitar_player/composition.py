"""Measure blocks and repeat notation, independent of the editor and input."""
from dataclasses import dataclass,replace
from fractions import Fraction as F
from bisect import bisect_right

from .measures import MeasureChange,timbre_points


@dataclass(frozen=True)
class MeasurePacket:
    beats:F
    meter:tuple
    tone:str
    cells:tuple
    chord:str|None
    changes:tuple
    tempos:tuple
    bpm_mark:float|None=None


def validate_repeats(doc):
    layout=doc.layout
    for index in doc.measure_repeats:
        if type(index) is not int or not 1<=index<doc.bars:raise ValueError('% 重复小节必须位于第 2 小节及以后')
        if layout[index].meter!=layout[index-1].meter:raise ValueError('% 重复小节须与前一小节拍号一致')
    previous=-1;total=doc.bars
    for first,last,times in sorted(doc.repeat_sections):
        if any(type(n) is not int for n in (first,last,times)) or not 0<=first<=last<doc.bars or not 2<=times<=16:
            raise ValueError('反复段范围无效；总演奏次数为 2～16')
        if first<=previous:raise ValueError('反复段不能重叠或嵌套，请先清除相交的反复线')
        previous=last;total+=(last-first+1)*(times-1)
    if total>25000:raise ValueError('展开反复后超过 25000 小节，请先分段')


def playback_order(doc):
    validate_repeats(doc);sections={first:(last,times) for first,last,times in doc.repeat_sections}
    order=[];index=0
    while index<doc.bars:
        if index in sections:
            last,times=sections[index]
            for _ in range(times):order.extend(range(index,last+1))
            index=last+1
        else:order.append(index);index+=1
    return tuple(order)


def _crossings(doc,layout):
    cells=sorted(doc.cells,key=lambda cell:cell.start);position=0;furthest=F(0);crossed=set()
    for boundary in (*[bar.start for bar in layout],layout[-1].end):
        while position<len(cells) and cells[position].start<boundary:
            furthest=max(furthest,cells[position].end);position+=1
        if furthest>boundary:crossed.add(boundary)
    return crossed


def _boundary(doc,first,last,crossings=None,layout=None):
    layout=doc.layout if layout is None else layout
    start=layout[first].start;end=layout[last].end
    crossings=_crossings(doc,layout) if crossings is None else crossings
    if start in crossings or end in crossings:
        raise ValueError('范围边界穿过跨小节延音；请先断开边界处延音，或把完整延音包含在范围内')
    return start,end


def measure_packets(doc,resolve_percent=True):
    validate_repeats(doc)
    score=doc.score();tempos=score.tempo_map or ((F(0),60_000_000/score.bpm),)
    layout=doc.layout;starts=tuple(bar.start for bar in layout);crossings=_crossings(doc,layout)
    def index_at(at):return max(0,min(len(layout)-1,bisect_right(starts,at)-1))
    tempo_starts=[at for at,_ in tempos];marks=sorted(doc.chord_changes.items());mark_starts=[at for at,_ in marks]
    tones=timbre_points(score);tone_starts=[at for at,_ in tones]
    chord_groups=[[] for _ in range(doc.bars)];tempo_groups=[[] for _ in range(doc.bars)]
    for at,name in marks:
        if at<layout[-1].end:
            index=index_at(at);chord_groups[index].append((at-layout[index].start,name))
    for at,value in tempos:
        if at<layout[-1].end:
            index=index_at(at)
            if at>layout[index].start:tempo_groups[index].append((at-layout[index].start,value))
    cells=sorted(doc.cells,key=lambda c:c.start);packet_cells=[[] for _ in range(doc.bars)]
    for cell in cells:
        index=index_at(cell.start);packet_cells[index].append(replace(cell,start=cell.start-layout[index].start))
    result=[]
    for index,bar in enumerate(layout):
        inside=tuple(chord_groups[index]);pos=bisect_right(mark_starts,bar.start)-1
        active=marks[pos][1] if pos>=0 else None;active=None if active=='N.C.' else active
        tempo=tempos[max(0,bisect_right(tempo_starts,bar.start)-1)][1]
        points=((F(0),tempo),)+tuple(tempo_groups[index])
        change=doc.bar_changes.get(index)
        packet=MeasurePacket(bar.beats,bar.meter,tones[max(0,bisect_right(tone_starts,bar.start)-1)][1],tuple(packet_cells[index]),
                             active,inside,points,change.bpm if change else None)
        if resolve_percent and index in doc.measure_repeats:
            _boundary(doc,index-1,index-1,crossings,layout)
            previous=result[-1]
            if previous.beats!=packet.beats:raise ValueError('% 重复小节与来源小节长度不同')
            packet=replace(packet,cells=previous.cells,chord=previous.chord,changes=previous.changes)
        result.append(packet)
    return tuple(result)


def assemble(doc,packets):
    """Rebuild musical time from bars, restoring tempo/chord state at joins."""
    if not packets or len(packets)>25000:raise ValueError('小节数应为 1～25000')
    if sum(len(packet.cells) for packet in packets)>50000:raise ValueError('展开后超过 50000 个音位，请先分段')
    cells=[];marks={};changes={};tempos={};at=F(0);chord=None;meter=doc.meter;tone=doc.default_timbre
    for index,packet in enumerate(packets):
        if at+packet.beats>100000:raise ValueError('曲谱超过 100000 拍，请先分段')
        cells.extend(replace(cell,start=at+cell.start) for cell in packet.cells)
        if packet.chord!=chord:marks[at]=packet.chord or 'N.C.';chord=packet.chord
        for offset,name in packet.changes:
            marks[at+offset]=name;chord=None if name=='N.C.' else name
        if packet.meter!=meter or packet.tone!=tone or packet.bpm_mark is not None:
            changes[index]=MeasureChange(index,packet.bpm_mark,packet.meter if packet.meter!=meter else None,
                                         packet.tone if packet.tone!=tone else None)
        for offset,value in packet.tempos:tempos[at+offset]=value
        meter,tone=packet.meter,packet.tone;at+=packet.beats
    if len(cells)>50000:raise ValueError('展开后超过 50000 个音位，请先分段')
    # A tempo at each join restores the source state after a repeat or paste.
    compact=[]
    for pos,value in sorted(tempos.items()):
        if not compact or value!=compact[-1][1]:compact.append((pos,value))
    doc.cells=cells;doc.chord_changes=marks;doc.bar_changes=changes
    doc.tempo_map=tuple(compact);doc.source_bpm=doc.bpm;doc.bars=len(packets);doc.ensure_bars()


def capture_measures(doc,first,last):
    if not 0<=first<=last<doc.bars:raise ValueError('复制范围应位于现有小节内')
    _boundary(doc,first,last)
    return measure_packets(doc)[first:last+1]


def paste_measures(doc,block,target,insert=True,copies=1):
    if type(copies) is not int or not 1<=copies<=32:raise ValueError('粘贴次数应为 1～32')
    if not 0<=target<=doc.bars or not block:raise ValueError('粘贴位置或小节剪贴板无效')
    block=tuple(block)*copies;removed=0 if insert else min(len(block),doc.bars-target)
    if target<doc.bars:
        if removed:_boundary(doc,target,target+removed-1)
        elif any(c.start<doc.bar(target).start<c.end for c in doc.cells):
            raise ValueError('插入位置穿过延音，请先断开该处延音')
    end=target+removed;delta=len(block)-removed
    repeats=set();sections=[]
    for index in doc.measure_repeats:
        if target<=index<end:continue
        repeats.add(index+delta if index>=end else index)
    for first,last,times in doc.repeat_sections:
        if removed and first<end and last>=target:raise ValueError('粘贴范围与反复段相交，请先清除该段反复线')
        first+=delta if first>=end else 0;last+=delta if last>=end else 0
        sections.append((first,last,times))
    packets=measure_packets(doc);result=packets[:target]+block+packets[end:]
    state=doc.snapshot()
    try:
        assemble(doc,result);doc.measure_repeats=repeats;doc.repeat_sections=sections
        strip_percent_contents(doc);validate_repeats(doc)
    except Exception:doc.restore(state);raise


def copy_measures(doc,first,last,target,insert=True,copies=1):
    paste_measures(doc,capture_measures(doc,first,last),target,insert,copies)


def set_measure_repeat(doc,index):
    if not 1<=index<doc.bars:raise ValueError('第 1 小节不能重复前一小节')
    _boundary(doc,index-1,index-1);start,end=_boundary(doc,index,index)
    if doc.bar(index).meter!=doc.bar(index-1).meter:raise ValueError('% 重复小节须与前一小节拍号一致')
    following=doc.chord_at(end)
    doc.cells=[c for c in doc.cells if not start<=c.start<end]
    doc.chord_changes={at:name for at,name in doc.chord_changes.items() if not start<=at<end}
    doc.measure_repeats.add(index)
    if end<doc.layout[-1].end:doc.chord_changes.setdefault(end,following or 'N.C.')


def set_repeat_section(doc,first,last,times=2):
    if any(type(n) is not int for n in (first,last,times)) or not 0<=first<=last<doc.bars:
        raise ValueError('请选择有效的反复段起止小节')
    _boundary(doc,first,last)
    previous=list(doc.repeat_sections)
    doc.repeat_sections=[row for row in previous if row[:2]!=(first,last)]+[(first,last,times)]
    try:validate_repeats(doc)
    except Exception:doc.repeat_sections=previous;raise


def materialize_repeats(doc):
    import copy
    order=playback_order(doc)
    if not doc.measure_repeats and not doc.repeat_sections:return copy.deepcopy(doc),order
    layout=doc.layout;crossings=_crossings(doc,layout)
    for first,last,_ in doc.repeat_sections:_boundary(doc,first,last,crossings,layout)
    packets=measure_packets(doc);linear=copy.deepcopy(doc)
    linear.measure_repeats=set();linear.repeat_sections=[]
    assemble(linear,tuple(packets[index] for index in order))
    return linear,order


def materialize_percent(doc,index):
    if index not in doc.measure_repeats:return
    packets=measure_packets(doc);state=doc.snapshot()
    try:
        assemble(doc,packets);doc.measure_repeats.discard(index);strip_percent_contents(doc)
    except Exception:doc.restore(state);raise


def strip_percent_contents(doc):
    starts=tuple(bar.start for bar in doc.layout)
    def marked(at):return max(0,bisect_right(starts,at)-1) in doc.measure_repeats
    doc.cells=[cell for cell in doc.cells if not marked(cell.start)]
    doc.chord_changes={at:name for at,name in doc.chord_changes.items() if not marked(at)}
