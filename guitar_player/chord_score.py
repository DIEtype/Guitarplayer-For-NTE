"""Measured chord accompaniment: independent chord marks and string actions."""
from dataclasses import dataclass,replace
from fractions import Fraction as F

from .game_profile import CHORD_NAMES,ROMAN
from .guitar import GuitarEvent,GuitarScore,UP,DOWN,split_long_rests
from .tablature import TabDocument
from .measures import apply_changes,score_layout


def parse_chord_changes(raw,mixed=False):
    result={}
    if not isinstance(raw,(list,tuple)):raise ValueError('和弦标记格式无效')
    for row in raw:
        if not isinstance(row,(list,tuple)) or len(row)!=2:raise ValueError('和弦标记格式无效')
        at,name=F(str(row[0])),row[1]
        if not 0<=at<=100000 or name not in (CHORD_NAMES+ROMAN+('Zero',) if mixed else CHORD_NAMES) or at in result:raise ValueError('和弦标记位置或名称无效')
        result[at]=name
    return tuple(sorted(result.items()))


@dataclass(frozen=True)
class ChordCell:
    start:F
    beats:F
    notes:tuple=()

    @property
    def end(self):return self.start+self.beats
    @property
    def string(self):return self.notes[0] if self.notes and self.notes[0] in range(1,7) else 3
    @property
    def fret(self):
        return None if not self.notes else '↑' if self.notes==(UP,) else '↓' if self.notes==(DOWN,) else '×'


class ChordDocument(TabDocument):
    mixed=False
    def __init__(self,cells=(),bpm=100,meter=(4,4),bars=4,tempo_map=(),bar_changes=(),default_timbre='clean',chord_changes=()):
        self.chord_changes=dict(parse_chord_changes(chord_changes,self.mixed))
        super().__init__(cells,bpm,meter,bars,tempo_map,bar_changes,default_timbre,0)

    @classmethod
    def from_score(cls,score):
        if score.capo:raise ValueError('和弦模式使用游戏固定和弦，Capo 请设为 0')
        marks=dict(parse_chord_changes(score.chord_changes,cls.mixed));previous='G'
        for event in score.events:
            if event.chord not in (CHORD_NAMES+ROMAN+('Zero',) if cls.mixed else CHORD_NAMES):
                if event.notes:raise ValueError(f'和弦模式不支持 {event.chord}，请切回品格模式或选择游戏和弦')
                continue
            if not score.chord_changes and event.chord!=previous:marks[event.start]=event.chord
            previous=event.chord
        cells=[ChordCell(e.start,e.beats,e.notes) for e in score.events if e.notes]
        base=score.base_tempo_map if score.base_tempo_map is not None else score.tempo_map
        return cls(cells,score.bpm,score.time_signature,max(score.measures or 4,len(score_layout(score))),
                   base,score.bar_changes,score.default_timbre,tuple(sorted(marks.items())))

    def snapshot(self):return super().snapshot(),tuple(sorted(self.chord_changes.items()))

    def restore(self,state):
        base,marks=state;super().restore(base);self.chord_changes=dict(marks)

    def move_annotations(self,move):self.chord_changes={F(move(at)):name for at,name in sorted(self.chord_changes.items())}
    def annotation_times(self):return tuple(self.chord_changes)

    def ensure_bars(self):
        super().ensure_bars()
        while self.chord_changes and max(self.chord_changes)>=self.layout[-1].end:
            self.bars+=1
            if self.bars>25000:raise ValueError('小节过多，请分段编曲')

    def chord_at(self,at):
        return next((name for pos,name in sorted(self.chord_changes.items(),reverse=True) if pos<=at),'G')

    def set_chord(self,at,name):
        at=F(at)
        if name not in (CHORD_NAMES+ROMAN+('Zero',) if self.mixed else CHORD_NAMES) or at<0:raise ValueError('请选择游戏和弦或有效品位')
        if any(c.start<at<c.end for c in self.cells):raise ValueError('请选择音符起点或休止位置设置和弦')
        self.chord_changes[at]=name;self.ensure_bars()

    def remove_chord(self,at):self.chord_changes.pop(F(at),None)

    def set_capo(self,capo):
        if capo!=0:raise ValueError('和弦模式使用固定和弦，暂不支持 Capo 换算')

    def make_cell(self,at,beats,string,fret):
        notes=getattr(self,'_writing_notes',None)
        return ChordCell(at,beats,notes if notes is not None else (() if fret is None else (string,)))

    def write_action(self,at,notes,beats):
        notes=tuple(sorted(set(notes)))
        if (notes not in ((),(UP,),(DOWN,)) and any(type(n) is not int or n not in range(1,7) for n in notes)):
            raise ValueError('请选择弦号 1～6、上扫或下扫')
        self._writing_notes=notes
        try:super().write(at,next((n for n in notes if n in range(1,7)),1),0 if notes else None,beats)
        finally:self._writing_notes=None

    def write(self,at,string,fret,beats):self.write_action(at,() if fret is None else (string,),beats)

    def resize(self,at,beats):
        cell=self.at(at)
        if cell:self.write_action(at,cell.notes,beats)

    def tie_signature(self,cell):return tuple(cell.notes),self.chord_at(cell.start)

    def delete_member(self,at,string):
        current=self.at(F(at))
        if current:
            notes=() if current.notes in ((UP,),(DOWN,)) else tuple(s for s in current.notes if s!=string)
            self.write_action(at,notes,current.beats)

    def toggle_string(self,at,string,beats):
        cell=self.at(at);notes=set(cell.notes) if cell and all(n in range(1,7) for n in cell.notes) else set()
        if string in notes:notes.remove(string)
        else:notes.add(string)
        self.write_action(at,notes,cell.beats if cell else beats)

    def copy_bar(self,index):
        measure=self.bar(index);active=self.chord_at(measure.start);following=self.chord_at(measure.end)
        marks={at-measure.start:name for at,name in self.chord_changes.items() if measure.start<=at<measure.end}
        super().copy_bar(index)
        self.chord_changes[measure.end]=active
        self.chord_changes.update({measure.end+at:name for at,name in marks.items()})
        if index+2<self.bars:self.chord_changes.setdefault(measure.end+measure.beats,following)
        self.ensure_bars()

    def remove_empty_bar(self,index):
        measure=self.bar(index);following=self.chord_at(measure.end)
        old=dict(self.chord_changes)
        super().remove_empty_bar(index)
        self.chord_changes={at-measure.beats if at>=measure.end else at:name for at,name in old.items()
                            if not measure.start<=at<measure.end}
        if index<self.bars:self.chord_changes.setdefault(measure.start,following)

    def fill_pattern(self,index,kind,bass=6):
        measure=self.bar(index)
        if any(c.start<measure.start<c.end or c.start<measure.end<c.end for c in self.cells):
            raise ValueError('本小节有跨小节延音，请先调整时值再填入节奏型')
        if not 1<=bass<=6:raise ValueError('低音弦应为 1～6')
        if kind=='分解：低音–3–2–3–1–3–2–3':actions=[(n,) for n in (bass,3,2,3,1,3,2,3)]
        elif kind in ('扫弦：↓ ↓ ↑ ↑ ↓ ↑','扫弦：下 下 上 上 下 上'):actions=[(DOWN,),(DOWN,),(UP,),(UP,),(DOWN,),(UP,)]
        else:raise ValueError('请选择节奏型')
        weights=(2,1,1,2,1,1) if len(actions)==6 else (1,)*8
        self.cells=[c for c in self.cells if not measure.start<=c.start<measure.end]
        at=measure.start
        for notes,weight in zip(actions,weights):
            length=measure.beats*weight/sum(weights);self.cells.append(ChordCell(at,length,notes));at+=length
        self.ensure_bars()

    def score(self):
        self.ensure_bars();events=[];cursor=F(0)
        def rest(end):
            nonlocal cursor
            while cursor<end:
                next_mark=min((at for at in self.chord_changes if cursor<at<end),default=end)
                length=min(F(128),next_mark-cursor)
                events.append(GuitarEvent((),cursor,length,self.chord_at(cursor),'休止',1));cursor+=length
        for cell in self.cells:
            rest(cell.start)
            label='↑' if cell.notes==(UP,) else '↓' if cell.notes==(DOWN,) else '+'.join(map(str,cell.notes)) or '休止'
            events.append(GuitarEvent(cell.notes,cell.start,cell.beats,self.chord_at(cell.start),label,1));cursor=cell.end
        rest(max(self.layout[-1].end,cursor))
        tempos=tuple((at,tempo*self.source_bpm/self.bpm) for at,tempo in self.tempo_map)
        score=GuitarScore(tuple(events),cursor,self.bpm,tempos,self.meter,self.bars,
                          tuple(sorted(self.bar_changes.values(),key=lambda c:c.bar)),self.default_timbre,tempos,0,
                          tuple(sorted(self.chord_changes.items())))
        return apply_changes(score)


class MixedDocument(ChordDocument):
    mixed=True


def score_to_commands(score,mixed=False):
    """Keep chord names on rests too, so changes survive text round trips."""
    tokens=[];previous=None
    for event in split_long_rests(score.events):
        if event.chord not in (CHORD_NAMES+ROMAN+('Zero',) if mixed else CHORD_NAMES):raise ValueError('曲谱包含游戏预设以外的和弦')
        if event.chord!=previous:tokens.append('{'+event.chord+'}');previous=event.chord
        notes=event.notes
        body='0' if not notes else '↑' if notes==(UP,) else '↓' if notes==(DOWN,) else str(notes[0]) if len(notes)==1 else '['+' '.join(map(str,notes))+']'
        tokens.append(f'{body}:{event.beats}')
    return '// 和弦伴奏：数字表示吉他弦号；↓ 从 6 弦到 1 弦，↑ 反向\n'+' '.join(tokens)+'\n'
