"""Explicit near-onset grouping for humanized MIDI and editable source TAB.

Keep the highest note's onset, move only nearby other voices, and preserve the
original input object. Repeated pitches remain separate; string conflicts use
equivalent frets on another string without changing the source pitch content.
"""
from bisect import bisect_right
import copy
from dataclasses import replace
from fractions import Fraction as F
import math

from .guitar import GuitarScore
from .music import STANDARD_TUNING,positions_for_pitch


def _groups(items, clock, window_ms, keys, section):
    if type(window_ms) not in (int,float) or not math.isfinite(window_ms) or not 0<window_ms<=100:
        raise ValueError('近同拍窗口应为 0～100 毫秒之间的正数')
    group=[];used=set();region=None
    for item in items:
        current=keys(item);where=section(item.start)
        close=(group and current is not None and used
               and clock.seconds_at(item.start)-clock.seconds_at(group[0].start)<=window_ms/1000+1e-9
               and where==region and (item.start==group[-1].start or not used.intersection(current)))
        if not close:
            if group:yield group
            group=[];used=set();region=where
        group.append(item)
        if current is not None:used.update(current)
        else:used.clear()
    if group:yield group


def _report(window_ms):
    return {'window_ms':window_ms,'merged_groups':0,'moved_notes':0,
            'max_shift_ms':0.0,'barline_groups':0,'revoiced_notes':0,
            'unresolved_groups':0,'changes':[]}


def _assign_strings(group):
    preferred=sorted(((STANDARD_TUNING[s]+cell.values[s-1],s,cell.values[s-1])
                     for cell in group for s in cell.members),key=lambda item:(-item[0],item[1]))
    if len(preferred)>6:return None,0
    highest=preferred[0][0];best=None;best_cost=None
    def visit(index,used,route,cost):
        nonlocal best,best_cost
        if best_cost is not None and cost>=best_cost:return
        if index==len(preferred):best=list(route);best_cost=cost;return
        pitch,old_string,old_fret=preferred[index]
        options=sorted(positions_for_pitch(pitch),key=lambda pos:(pos[0]!=old_string,abs(pos[1]-old_fret)))
        for string,fret in options:
            if string in used:continue
            moved=int(string!=old_string)
            visit(index+1,used|{string},route+[(string,fret)],
                  (cost[0]+moved*int(pitch==highest),cost[1]+moved,cost[2]+abs(fret-old_fret)))
    visit(0,set(),[],(0,0,0))
    return best,(best_cost[1] if best_cost is not None else 0)


def _changed(report, group, anchor, clock, members):
    if len({item.start for item in group})<=1:return
    report['merged_groups']+=1
    for item in group:
        shift=(clock.seconds_at(anchor)-clock.seconds_at(item.start))*1000
        if abs(shift)>1e-7:
            report['moved_notes']+=members(item)
            report['max_shift_ms']=max(report['max_shift_ms'],abs(shift))
            report['changes'].append({'from':str(item.start),'to':str(anchor),'shift_ms':shift})


def cleanup_document(document, window_ms=30):
    from .fingerstyle_document import FingerCell
    result=copy.deepcopy(document);clock=document.score();report=_report(window_ms)
    tempos=[at for at,_ in clock.tempo_map]
    from .measures import timbre_points
    tones=[at for at,_ in timbre_points(clock)]
    def keys(cell):
        if cell.sweep or not cell.members or any(type(cell.values[s-1]) is not int for s in cell.members):
            return None
        return {STANDARD_TUNING[s]+cell.values[s-1] for s in cell.members}
    def section(at):return bisect_right(tempos,at),bisect_right(tones,at),document.chord_at(at)
    cells=[]
    for group in _groups(document.cells,clock,window_ms,keys,section):
        if len(group)==1:
            cells.append(group[0]);continue
        anchor_cell=max(group,key=lambda cell:(max(STANDARD_TUNING[s]+cell.values[s-1] for s in cell.members),-cell.start))
        anchor=anchor_cell.start;values=[None]*6
        first_bar=document.bar_index(group[0].start);last_bar=document.bar_index(group[-1].start)
        if first_bar!=last_bar:
            anchor=document.bar(last_bar).start;report['barline_groups']+=1
        assigned,moved=_assign_strings(group)
        if assigned is None:
            cells.extend(group);report['unresolved_groups']+=1;continue
        for string,fret in assigned:values[string-1]=fret
        report['revoiced_notes']+=moved
        end=max(cell.end for cell in group)
        cells.append(FingerCell(anchor,end-anchor,tuple(values)))
        _changed(report,group,anchor,clock,lambda cell:len(cell.members))
    result.cells=cells;result.ensure_bars()
    return result,report


def cleanup_midi(source, track_index, window_ms=30):
    track=next((track for track in source.tracks if track.index==track_index),None)
    if track is None:raise ValueError('请选择有效的 MIDI 轨道')
    clock=GuitarScore((),F(0),source.bpm,source.tempo_map)
    tempos=[at for at,_ in source.tempo_map];signatures=list(source.signatures)
    starts=[at for at,_ in signatures]
    def section(at):
        index=max(0,bisect_right(starts,at)-1)
        return index,bisect_right(tempos,at)
    report=_report(window_ms);notes=[]
    for group in _groups(sorted(track.notes,key=lambda note:(note.start,note.pitch)),clock,window_ms,
                         lambda note:{note.pitch},section):
        anchor=max(group,key=lambda note:(note.pitch,-note.start)).start
        index=max(0,bisect_right(starts,group[0].start)-1)
        base,meter=signatures[index];length=F(meter[0]*4,meter[1])
        first_bar=int((group[0].start-base)/length);last_bar=int((group[-1].start-base)/length)
        if first_bar!=last_bar:
            anchor=base+last_bar*length;report['barline_groups']+=1
        notes.extend(replace(note,start=anchor,end=note.end+anchor-note.start) for note in group)
        _changed(report,group,anchor,clock,lambda note:1)
    converted=replace(track,notes=sorted(notes,key=lambda note:(note.start,note.pitch)))
    return replace(source,tracks=[converted if item.index==track_index else item for item in source.tracks]),report


def cleanup_summary(report):
    message=(f"近同拍整理 {report['window_ms']:g} ms：合并 {report['merged_groups']} 组，"
            f"移动 {report['moved_notes']} 音，最多 {report['max_shift_ms']:.1f} ms；"
            f"跨线贴齐 {report['barline_groups']} 组，等音换弦 {report['revoiced_notes']} 音；小节长度保持")
    if report['unresolved_groups']:message+=f"；{report['unresolved_groups']} 组超出六弦可表示范围，已保留原音"
    return message
