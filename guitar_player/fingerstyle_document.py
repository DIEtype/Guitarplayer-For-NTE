"""Editable source TAB: independent chord annotations and per-string symbols."""
from dataclasses import dataclass,replace
from fractions import Fraction as F
from bisect import bisect_right
import copy
import json
import math
from pathlib import Path
import re

from .tablature import TabDocument
from .guitar import GuitarEvent,GuitarScore,UP,DOWN
from .game_profile import ROMAN,CHORD_NAMES
from .measures import apply_changes,parse_changes,score_layout,timbre_points,valid_meter
from .music import STANDARD_TUNING

HIT_X='x'
HIT_DOT='dot'


def valid_symbol(value):
    return value is None or type(value) is int and 0<=value<=24 or value in (HIT_X,HIT_DOT)


def chord_name(name):
    if name=='N.C.':return name
    if not isinstance(name,str) or not re.fullmatch(r'[A-G](?:[#b])?[A-Za-z0-9+#b()/\-]{0,23}',name):
        raise ValueError('和弦名例如 G、Am、Cmaj7、D/F#，最长 24 个字符')
    return name


def parse_sequence(text):
    raw=str(text)
    # Compact entry is deliberately one digit per note. Spaces permit 10..24.
    tokens=raw.replace('，',' ').replace(',',' ').split() if re.search(r'[\s,，]',raw) else list(raw)
    result=[]
    for token in tokens:
        if token.lower()=='x':result.append(HIT_X)
        elif token in ('.','·','●'):result.append(HIT_DOT)
        elif token.isascii() and token.isdigit() and 0<=int(token)<=24:result.append(int(token))
        else:raise ValueError('连输写 020；多位品用空格分开，例如 0 12 0；X 或点也可录入')
    if not result or len(result)>512:raise ValueError('连续录入需要 1～512 个音符')
    return tuple(result)


@dataclass(frozen=True)
class FingerCell:
    start:F
    beats:F
    values:tuple=(None,)*6
    sweep:int=0

    def __post_init__(self):
        if not 0<=self.start<=100000 or not F(1,960)<=self.beats<=128 or len(self.values)!=6 or any(not valid_symbol(v) for v in self.values) or type(self.sweep) is not int or self.sweep not in (0,UP,DOWN):
            raise ValueError('原谱音位、时值或六根弦的内容无效')

    @property
    def end(self):return self.start+self.beats
    @property
    def members(self):return tuple(s for s,v in enumerate(self.values,1) if v is not None)
    @property
    def notes(self):return (self.sweep,) if self.sweep else self.members
    @property
    def string(self):return self.members[0] if self.members else 1
    @property
    def fret(self):return ('↑' if self.sweep==UP else '↓') if self.sweep else self.values[self.string-1] if self.members else None


class FingerstyleDocument(TabDocument):
    def __init__(self,cells=(),bpm=100,meter=(4,4),bars=4,tempo_map=(),bar_changes=(),default_timbre='clean',capo=0,chord_changes=(),measure_repeats=(),repeat_sections=()):
        self.measure_repeats=set(measure_repeats);self.repeat_sections=list(repeat_sections)
        self.chord_changes={}
        for at,name in chord_changes:
            at=F(at)
            if not 0<=at<=100000 or at in self.chord_changes:raise ValueError('原谱和弦标记位置无效或重复')
            self.chord_changes[at]=chord_name(name)
        super().__init__(cells,bpm,meter,bars,tempo_map,bar_changes,default_timbre,capo)
        from .composition import validate_repeats
        validate_repeats(self)

    @classmethod
    def from_score(cls,score):
        if isinstance(score,cls):return copy.deepcopy(score)
        cells=[];marks={at:name for at,name in score.chord_changes if name in CHORD_NAMES};previous=None
        for event in score.events:
            values=[None]*6
            sweep=event.notes[0] if event.notes in ((UP,),(DOWN,)) else 0
            for string in (range(1,7) if sweep else event.notes):
                if string not in range(1,7):raise ValueError('无法识别原谱中的弦号或扫弦')
                if event.chord in (*ROMAN,'Zero'):
                    values[string-1]=(0 if event.chord=='Zero' else ROMAN.index(event.chord)+1)-score.capo
                elif event.chord in CHORD_NAMES:values[string-1]=HIT_X
                else:raise ValueError('无法识别原谱中的弦音')
            if event.notes:cells.append(FingerCell(event.start,event.beats,tuple(values),sweep))
            if event.chord in CHORD_NAMES and event.chord!=previous:
                marks.setdefault(event.start,event.chord);previous=event.chord
        base=score.base_tempo_map if score.base_tempo_map is not None else score.tempo_map
        return cls(cells,score.bpm,score.time_signature,max(score.measures or 4,len(score_layout(score))),
                   base,score.bar_changes,score.default_timbre,score.capo,tuple(marks.items()))

    @classmethod
    def from_tab(cls,text,bpm=100,step='1/4',capo=0):
        from .tab import parse_tab
        score=parse_tab(text,bpm,step,capo,_members_output=True);cells=[]
        for event in score.events:
            if not event.notes:continue
            values=[None]*6
            for string,fret in event.notes:values[string-1]=fret
            cells.append(FingerCell(event.start,event.beats,tuple(values)))
        return cls(cells,bpm,bars=max(4,len(score_layout(score))),capo=capo)

    def snapshot(self):return super().snapshot(),tuple(sorted(self.chord_changes.items())),tuple(sorted(self.measure_repeats)),tuple(sorted(self.repeat_sections))

    def restore(self,state):
        base,marks,*repeats=state;super().restore(base);self.chord_changes=dict(marks)
        self.measure_repeats=set(repeats[0]) if repeats else set()
        self.repeat_sections=list(repeats[1]) if repeats else []

    def move_annotations(self,move):self.chord_changes={F(move(at)):name for at,name in sorted(self.chord_changes.items())}
    def annotation_times(self):return tuple(self.chord_changes)

    def ensure_bars(self):
        super().ensure_bars()
        while self.chord_changes and max(self.chord_changes)>=self.layout[-1].end:
            self.bars+=1
            if self.bars>25000:raise ValueError('小节过多，请分段编曲')

    def chord_at(self,at):
        name=next((name for pos,name in sorted(self.chord_changes.items(),reverse=True) if pos<=at),None)
        return None if name=='N.C.' else name

    def set_chord(self,at,name):
        at=F(at);name=chord_name(name)
        if at<0 or at>100000:raise ValueError('和弦标记位置无效')
        if any(c.start<at<c.end for c in self.cells):raise ValueError('请在音符起点或空位设置和弦')
        from .composition import materialize_percent
        materialize_percent(self,self.bar_index(at))
        self.chord_changes[at]=name;self.ensure_bars()

    def remove_chord(self,at):self.chord_changes.pop(F(at),None)

    def set_capo(self,capo):
        if type(capo) is not int or not 0<=capo<=24:raise ValueError('Capo 应为 0～24 品')
        self.capo=capo

    def make_cell(self,at,beats,string,fret):return FingerCell(at,beats,self._writing_values,self._writing_sweep)

    def write_values(self,at,values,beats,sweep=0):
        values=tuple(values)
        FingerCell(F(at),F(beats),values,sweep)
        from .composition import materialize_percent
        materialize_percent(self,self.bar_index(at))
        self._writing_values=values;self._writing_sweep=sweep
        try:super().write(at,1,0 if any(v is not None for v in values) else None,beats)
        finally:del self._writing_values;del self._writing_sweep

    def write_sweep(self,at,direction,beats):
        if direction not in (UP,DOWN):raise ValueError('请选择上扫或下扫')
        if not self.chord_at(F(at)):raise ValueError('请先在谱面上方标记扫弦的和弦')
        self.write_values(at,(HIT_X,)*6,beats,direction)

    def write_member(self,at,string,value,beats):
        if type(string) is not int or string not in range(1,7) or not valid_symbol(value):raise ValueError('请选择 1～6 弦与 0～24 品、X 或点')
        from .composition import materialize_percent
        materialize_percent(self,self.bar_index(at))
        current=self.at(F(at));values=list(current.values) if current and not current.sweep else [None]*6
        values[string-1]=value;self.write_values(at,values,beats)

    def write(self,at,string,fret,beats):
        if fret is None:self.write_values(at,(None,)*6,beats)
        else:self.write_member(at,string,fret,beats)

    def resize(self,at,beats):
        from .composition import materialize_percent
        materialize_percent(self,self.bar_index(at))
        current=self.at(F(at))
        if current:self.write_values(at,current.values,beats,current.sweep)

    def tie_signature(self,cell):
        dependent=any(value in (HIT_X,HIT_DOT) for value in cell.values)
        return cell.values,cell.sweep,self.chord_at(cell.start) if dependent else None

    def delete_member(self,at,string):
        from .composition import materialize_percent
        materialize_percent(self,self.bar_index(at))
        current=self.at(F(at))
        if current:self.write_member(at,string,None,current.beats)

    def insert_sequence(self,at,string,text,beats):
        values=parse_sequence(text);state=self.snapshot();cursor=F(at)
        if cursor+len(values)*F(beats)>self.edit_bounds(cursor)[1]:raise ValueError('连续录入超出本小节，请分小节录入；下一小节保持原位')
        try:
            for value in values:self.write_member(cursor,string,value,beats);cursor+=F(beats)
        except Exception:self.restore(state);raise
        return cursor

    def copy_bar(self,index):
        from .composition import copy_measures
        copy_measures(self,index,index,index+1)

    def insert_bar(self,index):
        super().insert_bar(index)
        self.measure_repeats={i+(i>=index) for i in self.measure_repeats}
        self.repeat_sections=[(a+(a>=index),b+(b>=index),n) for a,b,n in self.repeat_sections]

    def remove_empty_bar(self,index):
        if index in self.measure_repeats or any(a<=index<=b for a,b,_ in self.repeat_sections):
            raise ValueError('请先清除该小节的重复记号或反复线，再删除空小节')
        measure=self.bar(index);following=self.chord_at(measure.end);old=dict(self.chord_changes)
        super().remove_empty_bar(index)
        self.chord_changes={at-measure.beats if at>=measure.end else at:name for at,name in old.items() if not measure.start<=at<measure.end}
        if following and index<self.bars:self.chord_changes.setdefault(measure.start,following)
        self.measure_repeats={i-(i>index) for i in self.measure_repeats}
        self.repeat_sections=[(a-(a>index),b-(b>index),n) for a,b,n in self.repeat_sections]

    def score(self):
        """String-index events for drawing/preview only; resolve before translation."""
        self.ensure_bars();events=[];cursor=F(0)
        marks=sorted(self.chord_changes.items());starts=[at for at,_ in marks]
        def active(at):
            i=bisect_right(starts,at)-1
            return marks[i][1] if i>=0 else 'Zero'
        def rest(end):
            nonlocal cursor
            while cursor<end:
                length=min(F(128),end-cursor)
                events.append(GuitarEvent((),cursor,length,active(cursor),'休止',1));cursor+=length
        for cell in self.cells:
            rest(cell.start)
            events.append(GuitarEvent(cell.notes,cell.start,cell.beats,active(cell.start),'原谱',1));cursor=cell.end
        rest(max(cursor,self.layout[-1].end))
        tempos=tuple((at,tempo*self.source_bpm/self.bpm) for at,tempo in self.tempo_map)
        score=GuitarScore(tuple(events),cursor,self.bpm,tempos,self.meter,self.bars,
                         tuple(sorted(self.bar_changes.values(),key=lambda c:c.bar)),self.default_timbre,tempos,self.capo)
        return apply_changes(score)

    def pitch_source(self,rows=None,reference=True,name='原谱编写'):
        from .fingerstyle_translation import PitchSource
        if self.measure_repeats or self.repeat_sections:
            from .composition import materialize_repeats
            linear,_=materialize_repeats(self)
            return replace(linear.pitch_source(rows,reference,name),document=copy.deepcopy(self))
        from .voicings import available_voicings
        rows=rows or {};score=self.score();points=timbre_points(score);starts=[p[0] for p in points]
        tables={tone:available_voicings(rows,tone,reference) for tone in ('clean','distorted')}
        events=[];reference_hits=0;cells={c.start:c for c in self.cells};sweeps=[];hints=[]
        from .voice_roles import NoteHint
        for event in score.events:
            cell=cells.get(event.start)
            if not cell or not cell.notes:events.append(event);continue
            tone=points[max(0,bisect_right(starts,event.start)-1)][1];pitches=[];label=event.chord
            for string in cell.members:
                value=cell.values[string-1]
                if type(value) is int:pitch=STANDARD_TUNING[string]+value
                else:
                    item=tables[tone].get(label)
                    if not item:raise ValueError(f'第 {self.bar_index(cell.start)+1} 小节 {string} 弦的 X／点需要可用的和弦音高表，请先标记和弦')
                    pitch=item.pitches[string-1]
                    if pitch is not None and pitch<0 and cell.sweep:continue
                    if pitch is None or pitch<0:raise ValueError(f'{label} 的 {string} 弦标为未知或静音，请修正音高表或改写该音')
                    reference_hits+=int(not item.measured)
                pitch+=self.capo
                if not 0<=pitch<=127:raise ValueError('Capo 换算后音高超出 MIDI 范围')
                pitches.append(pitch)
                hints.append(NoteHint(cell.start,pitch,cell.end,string=string))
            events.append(replace(event,notes=tuple(pitches)))
            if cell.sweep:
                if not pitches:raise ValueError('扫弦没有可发声的弦，请核对和弦音高表')
                sweeps.append((cell.start,cell.sweep))
        return PitchSource(replace(score,events=tuple(events),capo=0),name,tuple(sorted(self.chord_changes.items())),reference_hits,copy.deepcopy(self),tuple(sweeps),self.capo,tuple(hints))

    def to_dict(self):
        return {'version':5 if self.measure_repeats or self.repeat_sections else 4,'kind':'fingerstyle_source','bpm':self.bpm,'meter':list(self.meter),'bars':self.bars,
                'tempo_map':[[str(at),tempo] for at,tempo in self.tempo_map],'source_bpm':self.source_bpm,
                'bar_changes':[c.to_dict() for c in sorted(self.bar_changes.values(),key=lambda c:c.bar)],
                'default_timbre':self.default_timbre,'capo':self.capo,'chord_marks':[[str(at),name] for at,name in sorted(self.chord_changes.items())],
                'cells':[{'start':str(c.start),'beats':str(c.beats),'values':list(c.values),'sweep':c.sweep} for c in self.cells],
                'measure_repeats':sorted(self.measure_repeats),'repeat_sections':[list(row) for row in sorted(self.repeat_sections)]}

    @classmethod
    def from_dict(cls,data):
        if not isinstance(data,dict) or data.get('version') not in (4,5) or data.get('kind')!='fingerstyle_source':raise ValueError('不是可编辑的指弹原谱文件')
        bpm=data.get('bpm',100);source_bpm=data.get('source_bpm',bpm);meter=data.get('meter',[4,4]);bars=data.get('bars',4)
        if any(type(v) not in (int,float) or not math.isfinite(v) or not 20<=v<=360 for v in (bpm,source_bpm)):raise ValueError('原谱 BPM 应为 20～360')
        if not valid_meter(meter) or type(bars) is not int or not 1<=bars<=25000:raise ValueError('原谱拍号或小节数无效')
        raw=data.get('cells',[]);marks=data.get('chord_marks',[])
        if not isinstance(raw,list) or len(raw)>50000 or not isinstance(marks,list) or len(marks)>50000:raise ValueError('原谱音符或标记过多')
        cells=[FingerCell(F(row['start']),F(row['beats']),tuple(row['values']),row.get('sweep',0)) for row in raw]
        cells.sort(key=lambda c:c.start)
        if any(b.start<a.end for a,b in zip(cells,cells[1:])):raise ValueError('原谱音位重叠，请放在同一列的不同弦上')
        tempos=tuple((F(at),float(value)) for at,value in data.get('tempo_map',[]))
        if any(at<0 or not math.isfinite(value) or not 0<value<=60_000_000 for at,value in tempos) or tempos and (tempos[0][0]!=0 or any(a[0]>=b[0] for a,b in zip(tempos,tempos[1:]))):raise ValueError('原谱速度节点无效')
        tone=data.get('default_timbre','clean');capo=data.get('capo',0)
        if tone not in ('clean','distorted') or type(capo) is not int or not 0<=capo<=24:raise ValueError('原谱音色或 Capo 无效')
        repeated=data.get('measure_repeats',[]);sections=data.get('repeat_sections',[])
        if not isinstance(repeated,list) or len(repeated)>25000 or any(type(i) is not int for i in repeated):raise ValueError('重复小节记号无效')
        if not isinstance(sections,list) or len(sections)>25000 or any(not isinstance(row,list) or len(row)!=3 or any(type(i) is not int for i in row) for row in sections):raise ValueError('反复段记号无效')
        result=cls(cells,bpm,meter,bars,tempos,parse_changes(data.get('bar_changes',[])),tone,capo,marks,repeated,[tuple(row) for row in sections])
        result.source_bpm=source_bpm;return result

    def save(self,path):
        path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
        temporary=path.with_suffix('.gscore.tmp');temporary.write_text(json.dumps(self.to_dict(),ensure_ascii=False,indent=2),encoding='utf-8');temporary.replace(path)


def read_document(path):
    path=Path(path)
    if path.stat().st_size>5_000_000:raise ValueError('原谱超过 5 MB，请先分段')
    text=path.read_text(encoding='utf-8-sig')
    if path.suffix.lower()=='.gscore':
        data=json.loads(text)
        if isinstance(data,dict) and data.get('kind')=='fingerstyle_source':return FingerstyleDocument.from_dict(data)
        if isinstance(data,dict) and data.get('notation')=='tab':
            doc=FingerstyleDocument.from_tab(data['text'],float(data['bpm']),data.get('tab_step','1/4'),data.get('capo',0))
            # Reuse the existing metadata validator without discarding cross-fret notes.
            from .library import read_score
            score,_=read_score(path,_pitch_output=True)
            doc.meter=score.time_signature;doc.bars=max(doc.bars,score.measures)
            doc.tempo_map=score.base_tempo_map if score.base_tempo_map is not None else score.tempo_map
            doc.bar_changes={c.bar:c for c in score.bar_changes};doc.default_timbre=score.default_timbre;doc.ensure_bars()
            return doc
    if re.search(r'(?m)^\s*[A-Ga-g1-6]\s*\|',text):return FingerstyleDocument.from_tab(text)
    from .library import read_score
    score,_=read_score(path);return FingerstyleDocument.from_score(score)
