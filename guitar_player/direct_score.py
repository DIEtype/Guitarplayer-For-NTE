"""Map written actions literally, without arrangement, note loss or key search."""
from bisect import bisect_right
from dataclasses import replace

from .composition import materialize_repeats
from .fingerstyle_document import HIT_X,HIT_DOT
from .game_profile import CHORD_NAMES,ROMAN
from .music import STANDARD_TUNING
from .measures import timbre_points
from .voicings import available_voicings


def compile_document(document,rows=None):
    """Keep each written string/fret/chord, or explain its hardware conflict.

    Only the annotated chord may supply X/dot notes. Numeric frets keep their
    written strings; no substitute chord or alternate fingering is searched.
    Timing feasibility belongs to the planner and never blocks score loading.
    """
    doc,_=materialize_repeats(document);score=doc.score()
    cells={cell.start:cell for cell in doc.cells};events=[];labels=[]
    tones=timbre_points(score);starts=[at for at,_ in tones];bar_starts=[bar.start for bar in doc.layout]
    tables={tone:available_voicings(rows or {},tone) for tone in ('clean','distorted')}
    selector='Zero'
    for event in score.events:
        cell=cells.get(event.start)
        if cell is None or not cell.notes:
            events.append(replace(event,chord=selector));continue
        bar=bisect_right(bar_starts,cell.start)
        def fail(reason):raise ValueError(f'第 {bar} 小节，拍位 {cell.start}：{reason}')
        name=event.chord if event.chord!='Zero' else None
        symbols=any(value in (HIT_X,HIT_DOT) for value in cell.values)
        tone=tones[max(0,bisect_right(starts,cell.start)-1)][1]
        if cell.sweep or symbols:
            if name not in CHORD_NAMES:fail('X／点或扫弦需要游戏中的同名和弦；原谱标记仍完整保留')
            if doc.capo:fail('游戏和弦不随 Capo 升调；含 Capo 的和弦弦音需另选等音方案')
            selector=name
            numeric=[(s,value) for s,value in enumerate(cell.values,1) if type(value) is int]
            if numeric:
                item=tables[tone].get(name)
                if item is None:fail(f'{name} 缺少可核对的逐弦音高表')
                if any(item.pitches[s-1]!=STANDARD_TUNING[s]+value for s,value in numeric):
                    fail('同拍的数字品位与所标和弦弦音不同，一个圆盘选区无法同时触发')
        else:
            frets={value+doc.capo for value in cell.values if type(value) is int}
            if len(frets)!=1:fail('同拍跨多个品位，游戏品格模式只能同时选一个品位；可保留原谱或另选近似转译')
            fret=frets.pop()
            if not 0<=fret<=24:fail('Capo 换算后超出游戏 0～24 品，原谱仍可编辑与试听')
            selector=ROMAN[fret-1] if fret else 'Zero'
        if selector in CHORD_NAMES:labels.append((event.start,selector))
        events.append(replace(event,chord=selector,label='直接载入'))
    return replace(score,events=tuple(events),capo=0,chord_changes=tuple(labels))


def source_listing(doc,limit=2000):
    """A bounded, read-only overview; the complete editable score stays in doc."""
    lines=['// 原谱已载入；在“图形编谱”中编辑、试听或保存。',
           '// 游戏无法原样触发的位置会单独提示；近似转译为可选操作。',
           '// 拍位  时值  和弦   1弦 2弦 3弦 4弦 5弦 6弦']
    marks=sorted(doc.chord_changes.items());starts=[at for at,_ in marks]
    for cell in doc.cells[:limit]:
        index=bisect_right(starts,cell.start)-1;name=marks[index][1] if index>=0 else '—'
        values=' '.join('-' if value is None else '●' if value==HIT_DOT else str(value) for value in cell.values)
        arrow=' ↑' if cell.sweep==101 else ' ↓' if cell.sweep else ''
        lines.append(f'{cell.start!s:>6}  {cell.beats!s:>4}  {name:<6} {values}{arrow}')
    if len(doc.cells)>limit:lines.append(f'// 其余 {len(doc.cells)-limit} 个音位在图形编谱中查看，均已完整载入。')
    if doc.measure_repeats:lines.append('// % 重复前小节：'+', '.join(str(i+1) for i in sorted(doc.measure_repeats)))
    for first,last,times in doc.repeat_sections:lines.append(f'// 反复段：第 {first+1}～{last+1} 小节，总演奏 {times} 遍')
    return '\n'.join(lines)+'\n'
