"""Measured single-voice tablature, independent of the drawing and game input."""
from dataclasses import dataclass,replace
from fractions import Fraction as F
from math import ceil
from bisect import bisect_right

from .game_profile import ROMAN
from .guitar import GuitarEvent,GuitarScore
from .measures import MeasureChange,measure_layout,score_layout,apply_changes,parse_changes,valid_meter


VALUES=(1,2,4,8,16,32,64)


def note_duration(denominator,dotted=False,triplet=False):
    return F(4,denominator)*(F(3,2) if dotted else 1)*(F(2,3) if triplet else 1)


def identify_duration(beats):
    for dotted,triplet in ((False,False),(True,False),(False,True),(True,True)):
        for denominator in VALUES:
            if note_duration(denominator,dotted,triplet)==beats:
                return denominator,dotted,triplet
    return None


def display_duration(beats):
    """Use a nearby glyph for imported values, always marked as approximate."""
    return identify_duration(beats) or (min(VALUES,key=lambda value:abs(F(4,value)-beats)),False,False)


def rhythm_parts(beats):
    choices=sorted({note_duration(value,dot,triplet) for value in VALUES
                    for dot,triplet in ((False,False),(True,False),(False,True))},reverse=True)
    remaining=F(beats);result=[]
    while remaining:
        length=next((value for value in choices if value<=remaining),remaining)
        result.append(length);remaining-=length
    return result


@dataclass(frozen=True)
class TabCell:
    start:F
    beats:F
    string:int=1
    fret:int|None=None
    strings:tuple=()

    @property
    def end(self):return self.start+self.beats

    @property
    def notes(self):return (self.strings or (self.string,)) if self.fret is not None else ()


class TabDocument:
    def __init__(self,cells=(),bpm=100,meter=(4,4),bars=4,tempo_map=(),bar_changes=(),default_timbre='clean',capo=0):
        self.cells=list(cells)
        self.bpm=float(bpm);self.source_bpm=float(bpm)
        self.meter=tuple(meter);self.bars=max(1,int(bars));self.tempo_map=tuple(tempo_map)
        self.bar_changes={c.bar:c for c in bar_changes};self.default_timbre=default_timbre
        self.capo=capo
        self._layout_key=None;self._layout=()
        self.ensure_bars()

    @classmethod
    def from_score(cls,score):
        if any(any(n not in range(1,7) for n in e.notes) for e in score.events):
            raise ValueError("品格编辑支持同品多弦，扫弦请使用和弦或混合编谱")
        cells=[TabCell(e.start,e.beats,e.notes[0],(0 if e.chord=='Zero' else ROMAN.index(e.chord)+1)-score.capo,e.notes if len(e.notes)>1 else ())
               for e in score.events if e.notes]
        meter=score.time_signature
        bars=max(score.measures or 4,len(score_layout(score)))
        base=score.base_tempo_map if score.base_tempo_map is not None else score.tempo_map
        return cls(cells,score.bpm,meter,bars,base,score.bar_changes,score.default_timbre,score.capo)

    @property
    def bar_beats(self):return F(self.meter[0]*4,self.meter[1])

    @property
    def layout(self):
        key=(self.meter,self.bars,tuple(sorted(self.bar_changes.values(),key=lambda c:c.bar)))
        if key!=self._layout_key:
            self._layout=measure_layout(self.meter,self.bars,key[2]);self._starts=tuple(b.start for b in self._layout);self._layout_key=key
        return self._layout

    def bar_index(self,at):
        self.layout
        return max(0,min(self.bars-1,bisect_right(self._starts,F(at))-1))

    def bar(self,index):return self.layout[index]

    def ensure_bars(self):
        self.cells.sort(key=lambda c:c.start)
        self.bars=max(self.bars,max(self.bar_changes,default=-1)+1,1)
        end=max((c.end for c in self.cells),default=F(0))
        while self.layout[-1].end<end:
            self.bars+=max(1,ceil((end-self.layout[-1].end)/self.layout[-1].beats))
        if self.bars>25000:raise ValueError('小节过多，请分段编曲')

    def at(self,at):return next((c for c in self.cells if c.start==at),None)

    def rest_spans(self,index):
        measure=self.bar(index);start=measure.start;end=measure.end;cursor=start;gaps=[]
        for cell in self.cells:
            if cell.start>=end:break
            if cell.end<=start:continue
            if cell.start>cursor:gaps.append((cursor,min(cell.start,end)-cursor))
            cursor=max(cursor,min(cell.end,end))
        if cursor<end:gaps.append((cursor,end-cursor))
        return gaps

    def snapshot(self):
        return (tuple(self.cells),self.bpm,self.meter,self.bars,self.tempo_map,self.source_bpm,
                tuple(sorted(self.bar_changes.values(),key=lambda c:c.bar)),self.default_timbre,self.capo)

    def restore(self,state):
        cells,self.bpm,self.meter,self.bars,self.tempo_map,self.source_bpm,changes,self.default_timbre,self.capo=state
        self.cells=list(cells);self.bar_changes={c.bar:c for c in changes}

    def move_annotations(self,move):
        """Hook for timed annotations owned by another editor document."""

    def annotation_times(self):return ()

    def edit_bounds(self,at):
        if at<self.layout[-1].end:
            bar=self.bar(self.bar_index(at));return bar.start,bar.end
        last=self.layout[-1]
        start=last.end+(at-last.end)//last.beats*last.beats
        return start,start+last.beats

    def reflow_in_bar(self,start,end,delta,skip=None):
        """Prepare a local ripple atomically; nothing may spill into the next bar."""
        following=[c for c in self.cells if c is not skip and start<=c.start<end]
        if delta and any(c.end>end or c.end+delta>end for c in following):
            raise ValueError('本小节空间不足或有跨小节延音；请先缩短／删除本小节节拍，下一小节保持原位')
        if delta and any(t+delta>=end for t in self.annotation_times() if start<=t<end):
            raise ValueError('本小节末尾有和弦标记，空间不足；下一小节保持原位')
        selected={id(c) for c in following}
        return [replace(c,start=c.start+delta) if id(c) in selected else c for c in self.cells if c is not skip]

    def make_cell(self,at,beats,string,fret):return TabCell(at,beats,string,fret)

    def write(self,at,string,fret,beats):
        at,beats=F(at),F(beats)
        if not 0<=at<=100000 or not F(1,960)<=beats<=128:raise ValueError("音符时值超出范围")
        if not 1<=string<=6 or fret is not None and not 0<=fret<=24-self.capo:raise ValueError(f"弦号 1～6；Capo {self.capo} 下相对品位应为 0～{24-self.capo}")
        current=self.at(at)
        _,bar_end=self.edit_bounds(at)
        if at+beats>bar_end:
            # A held event may occupy empty time in subsequent measures. It is
            # still one attack; extending it never ripples later measures.
            if any(c is not current and c.start<at+beats and c.end>at for c in self.cells):
                raise ValueError('跨小节延音与已有音符重叠；请先留出延音位置，后续小节不会自动移动')
            cell=self.make_cell(at,beats,string,fret)
            if isinstance(cell,TabCell) and current and len(current.notes)>1 and string in current.notes and fret is not None:
                cell=replace(cell,strings=current.notes)
            self.cells=[c for c in self.cells if c is not current]+[cell]
            self.ensure_bars();return
        if current:
            delta=beats-current.beats
            boundary=current.end
            cells=self.reflow_in_bar(boundary,bar_end,delta,current)
        else:
            if any(c.start<at<c.end for c in self.cells):
                raise ValueError("此处仍在前一个音符内；先选它修改时值，或移动到下一个空位")
            next_start=min((c.start for c in self.cells if at<c.start<bar_end),default=at+beats)
            delta=max(F(0),at+beats-next_start);boundary=next_start
            cells=self.reflow_in_bar(boundary,bar_end,delta)
        cell=self.make_cell(at,beats,string,fret)
        if isinstance(cell,TabCell) and current and len(current.notes)>1 and string in current.notes and fret is not None:
            cell=replace(cell,strings=current.notes)
        self.cells=cells+[cell]
        self.move_annotations(lambda t:t+delta if boundary<=t<bar_end else t)
        self.ensure_bars()

    def write_group(self,at,string,fret,beats):
        current=self.at(at)
        notes=set(current.notes) if current else set()
        notes.add(string)
        self.write(at,string,fret,beats)
        self.cells=[replace(c,strings=tuple(sorted(notes))) if c.start==at else c for c in self.cells]

    def resize(self,at,beats):
        current=self.at(at)
        if current:
            self.write(at,current.string,current.fret,beats)
            self.cells=[replace(c,strings=current.strings) if c.start==at else c for c in self.cells]

    def tie_signature(self,cell):return cell.fret,tuple(cell.notes)

    def tie_next(self,at):
        current=self.at(F(at));following=self.at(current.end) if current else None
        if current is None or not current.notes:raise ValueError('请先选中需要延音的音符')
        if following is None or not following.notes:raise ValueError('下一同音不紧接当前音；可选更长时值延续到空位')
        if self.tie_signature(current)!=self.tie_signature(following):raise ValueError('延音线只能连接相同弦上的同音；不同音符请分别拨奏')
        from .measures import timbre_at
        score=self.score()
        if timbre_at(score,current.start)!=timbre_at(score,following.start):raise ValueError('音色不同的两个音需要分别拨奏')
        if current.beats+following.beats>128:raise ValueError('单个延音最长 128 拍')
        self.cells=[replace(c,beats=c.beats+following.beats) if c is current else c for c in self.cells if c is not following]
        self.ensure_bars()

    def split_tie(self,at):
        current=self.at(F(at))
        if current is None:raise ValueError('请先选中跨小节的延音起点')
        _,boundary=self.edit_bounds(current.start)
        if current.end<=boundary:raise ValueError('当前音没有跨小节延音')
        self.cells=[replace(c,beats=boundary-c.start) if c is current else c for c in self.cells]
        self.cells.append(replace(current,start=boundary,beats=current.end-boundary));self.ensure_bars()
        return boundary

    def delete(self,at):self.cells=[c for c in self.cells if c.start!=at]

    def delete_member(self,at,string):
        current=self.at(F(at))
        if current is None or string not in current.notes:return
        remaining=tuple(s for s in current.notes if s!=string)
        replacement=replace(current,string=remaining[0],strings=remaining if len(remaining)>1 else ()) if remaining else replace(current,fret=None,strings=())
        self.cells=[replacement if c is current else c for c in self.cells]

    def insert_beat(self,at,beats=F(1)):
        at,beats=F(at),F(beats)
        if not 0<=at<=100000 or not F(1,960)<=beats<=128:raise ValueError('插入节拍位置或时值无效')
        _,end=self.edit_bounds(at)
        if at+beats>end:raise ValueError('插入节拍超出本小节；请选择更短时值或插入整小节')
        if any(c.start<at<c.end for c in self.cells):raise ValueError('请在音符起点或空位插入节拍')
        cells=self.reflow_in_bar(at,end,beats)
        before=self.snapshot()
        try:
            self.cells=cells
            self.move_annotations(lambda t:t+beats if at<=t<end else t)
            self.write(at,1,None,beats)
        except Exception:self.restore(before);raise

    def delete_beat(self,at,beats=F(1)):
        at=F(at);beats=F(beats)
        if at<0 or beats<=0:raise ValueError('删除节拍位置或时值无效')
        _,end=self.edit_bounds(at);current=self.at(at)
        if any(c.start<at<c.end for c in self.cells):raise ValueError('请选中音符起点再删除节拍')
        cut=current.end if current else min(end,at+beats,min((c.start for c in self.cells if c.start>at),default=end))
        if cut>end:
            self.delete(at);self.ensure_bars();return end-at
        cells=self.reflow_in_bar(cut,end,at-cut,current)
        # A following mark wins if deleting the beat brings it onto this mark.
        self.move_annotations(lambda t:at if at<=t<cut else t+at-cut if cut<=t<end else t)
        self.cells=cells;self.ensure_bars()
        return cut-at

    def set_meter(self,numerator,denominator):
        if not valid_meter((numerator,denominator)):raise ValueError("拍号分子 1～16，分母 1、2、4、8 或 16")
        self.meter=(numerator,denominator);self.ensure_bars()

    def set_capo(self,capo):
        if type(capo) is not int or not 0<=capo<=24:raise ValueError('Capo 应为 0～24 品')
        if any(c.fret is not None and c.fret+capo>24 for c in self.cells):
            if any(len(c.notes)>1 for c in self.cells):raise ValueError('多弦齐奏超出 24 品，请先调整该组品位或降低 Capo')
            from .arrangement import PitchNote,find_fingerings
            from .music import fret_pitch
            from .timing import ADAPTIVE
            notes=[PitchNote(fret_pitch(c.string,c.fret)+capo,c.start,c.end) for c in self.cells if c.fret is not None]
            route=iter(find_fingerings(notes,self.score(),0,ADAPTIVE,.04,capo))
            cells=[]
            for cell in self.cells:
                if cell.fret is None:cells.append(cell);continue
                string,physical=next(route);cells.append(replace(cell,string=string,fret=physical-capo,strings=()))
            self.cells=cells
        self.capo=capo

    def set_bar_change(self,index,bpm=None,meter=None,timbre=None):
        if not 0<=index<self.bars:raise ValueError('小节位置无效')
        change=parse_changes([{'bar':index,'bpm':bpm,'meter':meter,'timbre':timbre}])[0]
        old=self.layout;old_starts=[b.start for b in old]
        if all(value is None for value in (bpm,meter,timbre)):self.bar_changes.pop(index,None)
        else:self.bar_changes[index]=change
        new=self.layout
        if old!=new:
            def move(at):
                i=max(0,min(len(old)-1,bisect_right(old_starts,at)-1))
                return new[i].start+at-old[i].start
            cells=[];previous=F(0);relocations={}
            for cell in self.cells:
                at=max(move(cell.start),previous);relocations[cell.start]=at
                cells.append(replace(cell,start=at));previous=at+cell.beats
            self.cells=cells
            self.tempo_map=tuple(sorted({move(at):tempo for at,tempo in self.tempo_map}.items()))
            self.move_annotations(lambda t:relocations.get(t,move(t)))
        self.ensure_bars()

    def insert_bar(self,index):
        if not 0<=index<=self.bars:raise ValueError('插入小节位置无效')
        boundary=self.bar(index).start if index<self.bars else self.layout[-1].end
        self.bar_changes={i+(i>=index):replace(c,bar=i+(i>=index)) for i,c in self.bar_changes.items()}
        self.bars+=1;length=self.bar(index).beats
        self.cells=[replace(c,start=c.start+length) if c.start>=boundary else
                    replace(c,beats=c.beats+length) if c.end>boundary else c for c in self.cells]
        self.move_annotations(lambda t:t+length if t>=boundary else t)
        if self.tempo_map:
            values={at+length if at>=boundary else at:tempo for at,tempo in self.tempo_map}
            values.setdefault(F(0),self.tempo_map[0][1]);self.tempo_map=tuple(sorted(values.items()))
        self.ensure_bars()

    def copy_bar(self,index):
        measure=self.bar(index);start=measure.start;end=measure.end
        if any(c.start<start<c.end or c.start<end<c.end for c in self.cells):
            raise ValueError("本小节有跨小节延音，请先调整该音符时值再复制")
        copied=[replace(c,start=c.start+measure.beats) for c in self.cells if start<=c.start<end]
        change=self.bar_changes.get(index)
        tempo_copy={};active=None
        for at,tempo in self.tempo_map:
            if at<=start:active=tempo
            if start<at<end:tempo_copy[at+measure.beats]=tempo
        if active is not None:tempo_copy[end]=active
        self.insert_bar(index+1);self.cells+=copied
        if tempo_copy:self.tempo_map=tuple(sorted({**dict(self.tempo_map),**tempo_copy}.items()))
        if change:self.bar_changes[index+1]=replace(change,bar=index+1)
        self.ensure_bars()

    def remove_empty_bar(self,index):
        measure=self.bar(index);start=measure.start;end=measure.end
        if self.bars<=1:raise ValueError("至少保留一个小节")
        if any(c.start<end and c.end>start for c in self.cells):raise ValueError("请先清除本小节的音符，再删除空小节")
        self.cells=[replace(c,start=c.start-measure.beats) if c.start>=end else c for c in self.cells]
        removed=self.bar_changes.get(index);following=self.bar_changes.get(index+1,MeasureChange(index+1))
        self.bar_changes={i-(i>index):replace(c,bar=i-(i>index)) for i,c in self.bar_changes.items() if i!=index}
        if removed and index<self.bars-1:
            self.bar_changes[index]=MeasureChange(index,following.bpm if following.bpm is not None else removed.bpm,
                following.meter if following.meter is not None else removed.meter,
                following.timbre if following.timbre is not None else removed.timbre)
        if self.tempo_map:
            active=self.tempo_map[0][1]
            for at,tempo in self.tempo_map:
                if at>end:break
                active=tempo
            values={at-measure.beats if at>=end else at:tempo for at,tempo in self.tempo_map if not start<=at<end}
            values[start]=active;values.setdefault(F(0),active);self.tempo_map=tuple(sorted(values.items()))
        self.bars-=1

    def score(self):
        events=[];cursor=F(0)
        def rest(end):
            nonlocal cursor
            while cursor<end:
                length=min(F(128),end-cursor)
                events.append(GuitarEvent((),cursor,length,'Zero','休止',1));cursor+=length
        for cell in self.cells:
            rest(cell.start)
            events.append(GuitarEvent(cell.notes,cell.start,cell.beats,
                                     'Zero' if cell.fret is None or cell.fret+self.capo==0 else ROMAN[cell.fret+self.capo-1],
                                     '休止' if cell.fret is None else str(cell.fret),1))
            cursor=cell.end
        rest(max(self.layout[-1].end,cursor))
        tempos=tuple((at,tempo*self.source_bpm/self.bpm) for at,tempo in self.tempo_map)
        score=GuitarScore(tuple(events),cursor,self.bpm,tempos,self.meter,self.bars,
                          tuple(sorted(self.bar_changes.values(),key=lambda c:c.bar)),self.default_timbre,tempos,self.capo)
        return apply_changes(score)
