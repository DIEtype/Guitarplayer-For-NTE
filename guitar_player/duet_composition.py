"""Two editable TAB tracks with one musical clock and independent string holds."""
import copy
from dataclasses import replace
from fractions import Fraction as F
import json
import hashlib
from bisect import bisect_right
from pathlib import Path

from .fingerstyle_document import FingerstyleDocument,FingerCell,valid_symbol
from .measures import MeasureChange,timbre_points

ROLES=('recorded','live')
TRACK_NAMES={'recorded':'录音伴奏','live':'主旋律／现场声部'}


class DuetTrackDocument(FingerstyleDocument):
    """Fixed-time note entry: changing one string cannot consume another hold."""
    focus_string=1
    base_part=''
    base_document=''

    def digest(self):
        raw=json.dumps(super().to_dict(),ensure_ascii=True,sort_keys=True,separators=(',',':'))
        return hashlib.sha256(raw.encode('utf-8')).hexdigest()

    def mark_base(self,part):self.base_part=part.fingerprint;self.base_document=self.digest()
    def matches_part(self,part):return part is not None and self.base_part==part.fingerprint and self.base_document==self.digest()

    def to_dict(self):return {**super().to_dict(),'base_part':self.base_part,'base_document':self.base_document}

    def at(self,at):
        cells=[c for c in self.cells if c.start==at]
        return next((c for c in cells if c.values[self.focus_string-1] is not None),next((c for c in cells if not c.notes),cells[0] if cells else None))

    def relocate(self,old):
        starts=[b.start for b in old];new=self.layout
        def move(at):
            i=max(0,min(len(old)-1,bisect_right(starts,at)-1));local=at-old[i].start
            if i<len(new)-1 and local>=new[i].beats and local<old[i].beats:
                raise ValueError(f'第 {i+1} 小节的音符超出新拍号；请先缩短该小节或移动超出的音，另一轨不会自动挤压。')
            return new[min(i,len(new)-1)].start+local
        self.cells=[replace(c,start=move(c.start)) for c in self.cells]
        self.move_annotations(move)
        if self.tempo_map:self.tempo_map=tuple(sorted((move(at),tempo) for at,tempo in self.tempo_map))
        self.ensure_bars()

    def set_meter(self,numerator,denominator):
        from .measures import valid_meter
        if not valid_meter((numerator,denominator)):raise ValueError('拍号分子 1～16，分母 1、2、4、8 或 16')
        if self.meter == (numerator, denominator):return
        old=self.layout;self.meter=(numerator,denominator);self.relocate(old)

    def set_bar_change(self,index,bpm=None,meter=None,timbre=None):
        from .measures import parse_changes
        if not 0<=index<self.bars:raise ValueError('小节位置无效')
        change=parse_changes([{'bar':index,'bpm':bpm,'meter':meter,'timbre':timbre}])[0];old=self.layout
        if all(v is None for v in (bpm,meter,timbre)):self.bar_changes.pop(index,None)
        else:self.bar_changes[index]=change
        if old!=self.layout:self.relocate(old)

    def write_values(self,at,values,beats,sweep=0):
        cell=FingerCell(F(at),F(beats),tuple(values),sweep)
        self.cells=[c for c in self.cells if c.start!=cell.start]+[cell];self.ensure_bars()

    def write_member(self,at,string,value,beats):
        if type(string) is not int or string not in range(1,7) or not valid_symbol(value):raise ValueError('请选择 1～6 弦与 0～24 品、X 或点')
        at=F(at);beats=F(beats);self.focus_string=string;result=[]
        # Preserve the other strings, including their original release times.
        for cell in self.cells:
            if cell.start!=at:result.append(cell);continue
            values=list(cell.values);values[string-1]=None
            if any(v is not None for v in values):result.append(replace(cell,values=tuple(values),sweep=0))
        values=[None]*6;values[string-1]=value
        result.append(FingerCell(at,beats,tuple(values)))
        self.cells=result;self.ensure_bars()

    def delete_member(self,at,string):
        self.focus_string=string;cell=self.at(F(at))
        if cell:self.write_member(at,string,None,cell.beats)

    def resize(self,at,beats):
        cell=self.at(F(at))
        if cell:
            replacement=replace(cell,beats=F(beats))
            self.cells=[replacement if c is cell else c for c in self.cells];self.ensure_bars()

    @classmethod
    def from_dict(cls,data):
        try:
            raw=data.get('cells',[]);header={**data,'cells':[]}
            header.pop('base_part', None)
            base=FingerstyleDocument.from_dict(header)
            if not isinstance(raw,list) or len(raw)>50000:raise ValueError('双轨音符数量无效')
            cells=[FingerCell(F(c['start']),F(c['beats']),tuple(c['values']),c.get('sweep',0)) for c in raw]
            result=cls(cells,base.bpm,base.meter,base.bars,base.tempo_map,tuple(base.bar_changes.values()),base.default_timbre,base.capo,
                       tuple(base.chord_changes.items()),base.measure_repeats,base.repeat_sections,base.tuning)
            result.source_bpm=base.source_bpm
            for key in ('base_part','base_document'):
                value=data.get(key,'')
                if not isinstance(value,str) or len(value)>64:raise ValueError('双轨原谱关联无效')
                setattr(result,key,value)
            return result
        except (KeyError,TypeError,ZeroDivisionError,IndexError,OverflowError) as error:
            raise ValueError('双轨原谱数据损坏，请重新选择文件') from error

    @classmethod
    def from_document(cls,doc):return cls.from_dict(doc.to_dict())

    @classmethod
    def from_part(cls,part):
        from .measures import score_layout
        doc=cls.from_document(FingerstyleDocument.from_score(part.score))
        doc.bars=max(part.score.measures or 1,len(score_layout(part.score)));doc.ensure_bars();doc.mark_base(part);return doc


def sync_clock(documents,source_role):
    """Share meter and tempo; retain each track's tone, capo and written notes."""
    source=documents[source_role];other=documents['live' if source_role=='recorded' else 'recorded']
    old=other.layout
    other.bpm=source.bpm;other.source_bpm=source.source_bpm;other.meter=source.meter
    changes={}
    for index in set(source.bar_changes)|set(other.bar_changes):
        a=source.bar_changes.get(index);b=other.bar_changes.get(index)
        bpm=a.bpm if a else None;meter=a.meter if a else None;tone=b.timbre if b else None
        if bpm is not None or meter is not None or tone is not None:changes[index]=MeasureChange(index,bpm,meter,tone)
    other.bar_changes=changes
    if old!=other.layout:other.relocate(old)
    other.tempo_map=source.tempo_map
    other.repeat_sections=list(source.repeat_sections)
    bars=max(source.bars,other.bars)
    source.bars=other.bars=bars;source.ensure_bars();other.ensure_bars()
    source.bars=other.bars=max(source.bars,other.bars)


def prepare_documents(project):
    docs={}
    for role in ROLES:
        stored=project.documents.get(role)
        docs[role]=copy.deepcopy(stored) if stored is not None else DuetTrackDocument.from_part(project.parts[role]) if project.parts[role] else None
    basis=next((d for d in docs.values() if d is not None),DuetTrackDocument())
    for role in ROLES:
        if docs[role] is None:
            docs[role]=DuetTrackDocument.from_document(basis);docs[role].cells=[];docs[role].chord_changes={}
    bars=max(doc.bars for doc in docs.values())
    for role,doc in docs.items():
        part=project.parts[role];unchanged=doc.matches_part(part);doc.bars=bars
        if unchanged:doc.mark_base(part)
    return docs


def clock_matches(a,b):
    return _score_clocks_match(a.score(),b.score())


def _score_clocks_match(sa,sb):
    until=max(sa.beats,sb.beats)
    points={F(0),until,*(at for at,_ in sa.tempo_map if at<=until),*(at for at,_ in sb.tempo_map if at<=until)}
    return sa.time_signature==sb.time_signature and all(abs(sa.seconds_at(at)-sb.seconds_at(at))<.001 for at in points)


def combine_sources(documents,rows=None):
    from .fingerstyle_translation import PitchSource
    sources={role:doc.pitch_source(rows,True,TRACK_NAMES[role]) for role,doc in documents.items()}
    basis=sources['recorded'].score
    if not _score_clocks_match(basis,sources['live'].score):raise ValueError('两轨速度或拍号不一致，请先统一 BPM 与拍号，再合成或编配')
    events=[];strums={};tones={};hints=[];lead=set();targets={};lane_timbres=[]
    for role in ROLES:
        source=sources[role]
        points=timbre_points(source.score)
        lane_timbres.append(points)
        sweep_times=dict(source.sweeps)
        for event in source.score.events:
            if not event.notes:continue
            events.append(replace(event,label=role))
            tones[event.start,role]=next(tone for at,tone in reversed(points) if at<=event.start)
            if event.start in sweep_times:targets.setdefault(event.start,set()).update(event.notes)
            if role=='live':lead.update((event.start,p) for p in event.notes)
        strums.update({(at,role):direction for at,direction in source.sweeps});hints.extend(source.note_hints)
    score=replace(basis,events=tuple(sorted(events,key=lambda e:e.start)),beats=max(s.score.beats for s in sources.values()))
    explicit=set()
    for item in sources.values():
        optional_times=set(item.optional_sweeps)
        explicit.update(at for at,_ in item.sweeps if at not in optional_times)
    optional=tuple(sorted({at for source in sources.values() for at in source.optional_sweeps}-explicit))
    source=PitchSource(score,'双轨原谱',sweeps=tuple((at,direction) for (at,_),direction in strums.items()),note_hints=tuple(hints),
                       optional_sweeps=optional,sweep_targets=tuple((at,tuple(sorted(pitches))) for at,pitches in sorted(targets.items())),
                       lane_timbres=tuple(lane_timbres),lane_sweeps=tuple(strums.items()),
                       lane_sources=tuple((role,sources[role]) for role in ROLES))
    return source,{'pitch_events':True,'strums':strums,'event_timbres':tones},lead


def write_composition(path,project):
    data={'kind':'duet_composition','version':1,'parts':{k:v.to_dict() if v else None for k,v in project.parts.items()},
          'documents':{k:v.to_dict() if v else None for k,v in project.documents.items()}}
    path=Path(path);temporary=path.with_suffix('.gscore.tmp');temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8');temporary.replace(path)


def read_composition(path):
    from .duet_project import DuetProject
    path=Path(path)
    if path.stat().st_size>20_000_000:raise ValueError('双轨原谱超过 20 MB，请先分段')
    data=json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(data,dict) or data.get('kind')!='duet_composition':raise ValueError('请选择保存的双轨原谱 .gscore')
    return DuetProject.from_dict(data)
