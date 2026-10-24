"""Measure boundaries and inherited tempo, meter and guitar tone annotations."""
from bisect import bisect_right
from dataclasses import dataclass,replace
from fractions import Fraction as F
import math

TONE_NAMES={'clean':'清音','distorted':'失真'}


def valid_meter(meter):
    return (isinstance(meter,(tuple,list)) and len(meter)==2 and all(type(n) is int for n in meter)
            and 1<=meter[0]<=16 and meter[1] in (1,2,4,8,16))


@dataclass(frozen=True)
class MeasureChange:
    bar:int
    bpm:float|None=None
    meter:tuple|None=None
    timbre:str|None=None

    def to_dict(self):return {'bar':self.bar,'bpm':self.bpm,'meter':self.meter,'timbre':self.timbre}


def parse_changes(raw):
    if not isinstance(raw,(list,tuple)):raise ValueError('小节标记格式无效')
    result=[];seen=set()
    for row in raw:
        if not isinstance(row,dict):raise ValueError('小节标记格式无效')
        bar=row.get('bar');bpm=row.get('bpm');meter=row.get('meter');tone=row.get('timbre')
        if type(bar) is not int or not 0<=bar<25000 or bar in seen:raise ValueError('小节标记位置无效或重复')
        if bpm is not None and (type(bpm) not in (int,float) or not math.isfinite(bpm) or not 20<=bpm<=360):raise ValueError('小节速度应为 20～360 BPM')
        if meter is not None and not valid_meter(meter):raise ValueError('小节拍号无效')
        if tone is not None and tone not in TONE_NAMES:raise ValueError('音色应为清音或失真')
        seen.add(bar);result.append(MeasureChange(bar,bpm,tuple(meter) if meter is not None else None,tone))
    return tuple(sorted(result,key=lambda c:c.bar))


@dataclass(frozen=True)
class Measure:
    start:F
    beats:F
    meter:tuple
    @property
    def end(self):return self.start+self.beats


def measure_layout(meter,bars,changes=()):
    marks={c.bar:c for c in changes};result=[];at=F(0);current=tuple(meter)
    for index in range(bars):
        if index in marks and marks[index].meter is not None:current=marks[index].meter
        length=F(current[0]*4,current[1]);result.append(Measure(at,length,current));at+=length
    return tuple(result)


def score_layout(score):
    count=max(1,score.measures,max((c.bar+1 for c in score.bar_changes),default=1))
    result=measure_layout(score.time_signature,count,score.bar_changes)
    while result[-1].end<score.beats:
        count+=max(1,math.ceil((score.beats-result[-1].end)/result[-1].beats))
        result=measure_layout(score.time_signature,count,score.bar_changes)
    return result


def apply_changes(score,changes=None,default_timbre=None,base_tempos=None):
    changes=score.bar_changes if changes is None else tuple(changes)
    tone=score.default_timbre if default_timbre is None else default_timbre
    if tone not in TONE_NAMES:raise ValueError('音色应为清音或失真')
    result=replace(score,bar_changes=changes,default_timbre=tone)
    base=(score.base_tempo_map if score.base_tempo_map is not None else score.tempo_map) if base_tempos is None else tuple(base_tempos)
    tempos=dict(base);bars=score_layout(result)
    if any(c.bpm is not None for c in changes):tempos.setdefault(F(0),60_000_000/score.bpm)
    for change in changes:
        if change.bpm is not None:tempos[bars[change.bar].start]=60_000_000/change.bpm
    return replace(result,tempo_map=tuple(sorted(tempos.items())),base_tempo_map=tuple(base))


def timbre_points(score):
    bars=score_layout(score);points={F(0):score.default_timbre}
    for change in score.bar_changes:
        if change.timbre is not None:points[bars[change.bar].start]=change.timbre
    result=[]
    for at,tone in sorted(points.items()):
        if not result or tone!=result[-1][1]:result.append((at,tone))
    return tuple(result)


def timbre_at(score,beat):
    points=timbre_points(score);return points[max(0,bisect_right([p[0] for p in points],F(beat))-1)][1]


def crop_from_measure(score,index):
    bars=score_layout(score)
    if not 0<=index<len(bars):raise ValueError('试听起始小节无效')
    offset=bars[index].start;events=[]
    for event in score.events:
        if event.start+event.beats<=offset:continue
        start=max(offset,event.start)
        events.append(replace(event,start=start-offset,beats=event.start+event.beats-start))
    tempo=60_000_000/score.bpm
    for at,value in score.tempo_map:
        if at>offset:break
        tempo=value
    tempos=((F(0),tempo),)+tuple((at-offset,value) for at,value in score.tempo_map if at>offset)
    changes=tuple(replace(c,bar=c.bar-index) for c in score.bar_changes if c.bar>=index)
    chord_marks=()
    if score.chord_changes:
        active=next((name for at,name in reversed(score.chord_changes) if at<=offset),'G')
        chord_marks=((F(0),active),)+tuple((at-offset,name) for at,name in score.chord_changes if at>offset)
    return replace(score,events=tuple(events),beats=score.beats-offset,bpm=60_000_000/tempo,tempo_map=tempos,
                   base_tempo_map=tempos,time_signature=bars[index].meter,measures=len(bars)-index,
                   bar_changes=changes,default_timbre=timbre_at(score,offset),chord_changes=chord_marks),offset
