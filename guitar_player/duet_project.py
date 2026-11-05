"""Independent recorded/live score snapshots and remembered duet alignment."""
import copy
from dataclasses import dataclass, replace
from fractions import Fraction as F
from functools import cached_property
import hashlib
import json
import math
import re
from pathlib import Path

from .game_profile import CHORD_NAMES, ROMAN
from .guitar import parse_guitar,GuitarScore,GuitarEvent,UP,DOWN
from .chord_score import score_to_commands, parse_chord_changes
from .measures import parse_changes, valid_meter

MODE_NAMES={'chord':'和弦模式','fret':'品格模式','mixed':'混合模式'}


def score_mode(score):
    selectors={e.chord for e in score.events if e.notes}
    if not selectors:raise ValueError('曲谱没有可演奏的音符，请先选择有音符的声部')
    if selectors.issubset(CHORD_NAMES):return 'chord'
    if selectors.issubset((*ROMAN,'Zero')):return 'fret'
    return 'mixed'


@dataclass(frozen=True)
class DuetPart:
    name:str
    score:object
    source:str=''

    @property
    def mode(self):return score_mode(self.score)

    @property
    def summary(self):
        return f'{MODE_NAMES[self.mode]} · {self.score.bpm:g} BPM · {self.score.seconds:.1f} 秒'

    def score_data(self):
        s=self.score
        return {'text':score_to_commands(s,mixed=True),'bpm':float(s.bpm),'tempo_map':[[str(at),float(tempo)] for at,tempo in s.tempo_map],
                'events':[[str(e.start),str(e.beats),e.chord,list(e.notes)] for e in s.events],'beats':str(s.beats),
                'meter':list(s.time_signature),'measures':s.measures,'bar_changes':[{**c.to_dict(),'bpm':float(c.bpm) if c.bpm is not None else None} for c in s.bar_changes],
                'default_timbre':s.default_timbre,'capo':s.capo,'chord_changes':[[str(at),name] for at,name in s.chord_changes]}

    @cached_property
    def fingerprint(self):
        raw=json.dumps(self.score_data(),ensure_ascii=True,sort_keys=True,separators=(',',':'))
        return hashlib.sha256(raw.encode('utf-8')).hexdigest()

    def to_dict(self):return {'name':self.name,'source':self.source,'score':self.score_data()}

    @classmethod
    def from_dict(cls,data):
        try:return cls._from_dict(data)
        except (KeyError,TypeError,ZeroDivisionError,OverflowError,IndexError) as error:
            raise ValueError('声部曲谱数据损坏，请重新选择或导出；原文件保留') from error

    @classmethod
    def _from_dict(cls,data):
        if not isinstance(data,dict) or not isinstance(data.get('name'),str) or not 1<=len(data['name'])<=200:
            raise ValueError('合奏声部名称无效，请重新选择曲谱')
        d=data.get('score')
        if not isinstance(d,dict) or not isinstance(d.get('text'),str) or len(d['text'])>5_000_000:
            raise ValueError('合奏声部曲谱无效或过长')
        bpm=float(d['bpm'])
        if not math.isfinite(bpm) or not 20<=bpm<=360:raise ValueError('声部 BPM 应为 20～360')
        raw_events=d.get('events')
        if raw_events is None:score=parse_guitar(d['text'],bpm)
        else:
            if not isinstance(raw_events,list) or not 1<=len(raw_events)<=50000:raise ValueError('独立延音事件数量无效')
            events=[]
            for row in raw_events:
                if not isinstance(row,list) or len(row)!=4:raise ValueError('独立延音事件格式无效')
                at,beats=F(row[0]),F(row[1]);selector=row[2];notes=row[3]
                if (not 0<=at<=100000 or not F(1,960)<=beats<=128 or selector not in (*CHORD_NAMES,*ROMAN,'Zero')
                    or not isinstance(notes,list) or len(notes)>6 or len(set(notes))!=len(notes)
                    or any(type(n) is not int or n not in (*range(1,7),UP,DOWN) for n in notes)
                    or any(n in (UP,DOWN) for n in notes) and len(notes)!=1):raise ValueError('独立延音的拍位、时值或按键无效')
                if events and at<events[-1].start:raise ValueError('独立延音起拍没有按顺序保存')
                events.append(GuitarEvent(tuple(notes),at,beats,selector,selector,1))
            total=F(d['beats'])
            if not max(e.start+e.beats for e in events)<=total<=100000:raise ValueError('声部总长度不能短于音符尾音')
            score=GuitarScore(tuple(events),total,bpm)
        tempos=tuple((F(at),float(value)) for at,value in d.get('tempo_map',[]))
        if (any(at<0 or not math.isfinite(value) or not 0<value<=60_000_000 for at,value in tempos)
                or tempos and (tempos[0][0]!=0 or any(a[0]>=b[0] for a,b in zip(tempos,tempos[1:])))):
            raise ValueError('合奏声部速度变化无效')
        meter=d.get('meter',[4,4]);bars=d.get('measures',0);capo=d.get('capo',0);tone=d.get('default_timbre','clean')
        if not valid_meter(meter) or type(bars) is not int or not 0<=bars<=25000 or type(capo) is not int or not 0<=capo<=24 or tone not in ('clean','distorted'):
            raise ValueError('合奏声部拍号、音色或 Capo 无效')
        score=replace(score,tempo_map=tempos,base_tempo_map=tempos,time_signature=tuple(meter),measures=bars,
                      bar_changes=parse_changes(d.get('bar_changes',[])),default_timbre=tone,capo=capo,
                      chord_changes=parse_chord_changes(d.get('chord_changes',[]),mixed=True))
        source=data.get('source','')
        if not isinstance(source,str) or len(source)>4096:raise ValueError('声部文件路径无效')
        result=cls(data['name'],score,source);result.mode
        return result

    @classmethod
    def from_path(cls,path):
        path=Path(path)
        if path.stat().st_size>5_000_000:raise ValueError('声部曲谱超过 5 MB，请先分段')
        if path.suffix.lower() in ('.mid','.midi'):
            raise ValueError('MIDI 请先从主界面“导入 MIDI”选择轨道，生成演奏谱后点本页“使用当前谱”')
        if path.suffix.lower()=='.gscore':
            data=json.loads(path.read_text(encoding='utf-8-sig'))
            if isinstance(data,dict) and data.get('kind')=='duet_part':return cls.from_dict(data.get('part'))
            if isinstance(data,dict) and data.get('kind')=='fingerstyle_source':
                from .fingerstyle_document import read_document
                from .direct_score import compile_document
                try:score=compile_document(read_document(path))
                except ValueError as error:
                    raise ValueError('这份原谱不能直接演奏：'+str(error)+'。请先在编谱中近似转译，再用“使用当前谱”加入合奏。') from error
                return cls(path.stem,score,str(path.resolve()))
        from .library import read_score
        score,_=read_score(path)
        return cls(path.stem,score,str(path.resolve()))

    @classmethod
    def from_current(cls,app):
        if app.busy:raise ValueError('请先停止当前播放，再指定合奏声部')
        if app.unarranged_document is not None:
            raise ValueError('当前是尚不能直接演奏的原谱，请先在编谱中近似转译，再设为声部')
        score=app.analyze()
        if score is None:raise ValueError('当前曲谱有误：'+app.info.get())
        source=str(app.file_path) if app.file_path else ''
        name=Path(source).stem if source else '当前编谱（未保存）'
        result=cls(name,copy.deepcopy(score),source);result.mode
        return result


class DuetProject:
    def __init__(self):
        self.parts={'recorded':None,'live':None}
        self.documents={'recorded':None,'live':None}
        self.take_state='none';self.take_id='';self.take_name=''
        self.bound_code='';self.bound_part_id='';self.bound_name=''
        self.offsets={}
        self.split_summary=''
        self.archive_id='';self.archive_name=''

    def set_part(self,role,part):
        if role not in self.parts:raise ValueError('请选择录音伴奏或现场声部')
        old=self.parts[role]
        if (old.fingerprint if old else '')!=(part.fingerprint if part else ''):self.archive_id='';self.archive_name=''
        self.parts[role]=part
        self.documents[role]=None
        self.split_summary=''

    def bind_code(self,code):
        code=code.strip()
        if code==self.bound_code:return
        self.bound_code=code
        part=self.parts['recorded']
        self.bound_part_id=part.fingerprint if code and part is not None else ''
        self.bound_name=part.name if code and part is not None else ''

    def associate_code(self,code):
        self.bound_code='';self.bind_code(code)

    def validate_replay(self):
        live=self.parts['live'];recorded=self.parts['recorded']
        if live is None:raise ValueError('请先在青色“现场声部”选择曲谱；合奏将自动演奏这份谱，无需切换主窗口')
        if recorded is not None and self.bound_part_id and recorded.fingerprint!=self.bound_part_id:
            raise ValueError('录音伴奏已更换，但录音码仍关联“'+self.bound_name+'”。请重新录制并填写新码；若新选曲谱就是该录音，点“关联所选伴奏”。')
        if recorded is not None:
            until=min(recorded.score.beats,live.score.beats)
            points={F(0),until,*(at for at,_ in recorded.score.tempo_map if at<=until),*(at for at,_ in live.score.tempo_map if at<=until)}
            if any(abs(recorded.score.seconds_at(at)-live.score.seconds_at(at))>.001 for at in points):
                raise ValueError('两份声部的 BPM 或速度变化不一致，请先使用相同的音乐时间轴；现场偏移只能修正固定起播差异')
        return live

    def remember_offset(self,code,settings):
        if not re.fullmatch(r'[0-9a-fA-F]{32}',code):return
        self.offsets.pop(code,None)
        self.offsets[code]={'score_offset_ms':settings.score_offset_ms,'launch_delay_ms':settings.launch_delay_ms}
        while len(self.offsets)>24:self.offsets.pop(next(iter(self.offsets)))

    def begin_take(self,part):self.take_id=part.fingerprint;self.take_name=part.name;self.take_state='recording'
    def finish_take(self,completed):self.take_state='complete' if completed else 'interrupted'

    def to_dict(self):
        return {'version':1,'parts':{role:part.to_dict() if part else None for role,part in self.parts.items()},
                'documents':{role:doc.to_dict() if doc else None for role,doc in self.documents.items()},
                'take_state':self.take_state,'take_id':self.take_id,'take_name':self.take_name,
                'bound_code':self.bound_code,'bound_part_id':self.bound_part_id,'bound_name':self.bound_name,'offsets':self.offsets,'split_summary':self.split_summary,
                'archive_id':self.archive_id,'archive_name':self.archive_name}

    @classmethod
    def from_dict(cls,data):
        if not isinstance(data,dict) or data.get('version')!=1 or not isinstance(data.get('parts'),dict):raise ValueError('合奏项目格式无效')
        result=cls()
        for role in result.parts:
            raw=data['parts'].get(role);result.parts[role]=DuetPart.from_dict(raw) if raw is not None else None
        documents=data.get('documents',{})
        if not isinstance(documents,dict):raise ValueError('双轨原谱格式无效')
        from .duet_composition import DuetTrackDocument
        for role in result.documents:
            if documents.get(role) is not None:result.documents[role]=DuetTrackDocument.from_dict(documents[role])
        for key in ('take_state','take_id','take_name','bound_code','bound_part_id','bound_name'):
            value=data.get(key,getattr(result,key))
            if not isinstance(value,str) or len(value)>256:raise ValueError('合奏项目录音关联无效')
            setattr(result,key,value)
        legacy=data['parts'].get('recorded')
        if result.parts['recorded'] is not None and isinstance(legacy,dict) and 'events' not in legacy.get('score',{}):
            payload=json.dumps(legacy['score'],ensure_ascii=True,sort_keys=True,separators=(',',':'))
            old_id=hashlib.sha256(payload.encode('utf-8')).hexdigest();new_id=result.parts['recorded'].fingerprint
            if result.bound_part_id==old_id:result.bound_part_id=new_id
            if result.take_id==old_id:result.take_id=new_id
        if result.take_state=='recording':result.take_state='interrupted'
        if result.take_state not in ('none','complete','interrupted'):raise ValueError('录制状态无效')
        offsets=data.get('offsets',{})
        if not isinstance(offsets,dict) or len(offsets)>24:raise ValueError('合奏偏移记录无效')
        for code,values in offsets.items():
            if not isinstance(code,str) or len(code)>256 or not isinstance(values,dict):raise ValueError('录音偏移记录无效')
            offset=values.get('score_offset_ms');delay=values.get('launch_delay_ms')
            if type(offset) is not int or not -60000<=offset<=60000 or type(delay) is not int or not 0<=delay<=5000:raise ValueError('录音偏移数值无效')
        result.offsets=offsets
        summary=data.get('split_summary','')
        if not isinstance(summary,str) or len(summary)>2048:raise ValueError('自动拆分摘要无效')
        result.split_summary=summary
        identity=data.get('archive_id','');name=data.get('archive_name','')
        if not isinstance(identity,str) or identity and not re.fullmatch('[0-9a-f]{32}',identity):raise ValueError('合奏曲库编号无效')
        if not isinstance(name,str) or len(name)>200:raise ValueError('合奏曲库名称无效')
        result.archive_id=identity;result.archive_name=name
        return result


def read_project(directory):
    if directory is None:return DuetProject(),''
    path=Path(directory)/'duet-project.json'
    try:
        if path.stat().st_size>20_000_000:raise ValueError('合奏项目超过 20 MB，请重新分段选择声部')
        return DuetProject.from_dict(json.loads(path.read_text(encoding='utf-8'))),''
    except FileNotFoundError:return DuetProject(),''
    except (OSError,ValueError,TypeError,KeyError) as error:return DuetProject(),'合奏声部未读取：'+str(error)+'；请重新选择两份曲谱。'


def write_project(directory,project):
    if directory is None:return
    path=Path(directory)/'duet-project.json';path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(project.to_dict(),ensure_ascii=False,indent=2),encoding='utf-8');temporary.replace(path)
