"""MIDI track inspection and explicit melody conversion; source files stay intact."""
from collections import defaultdict,deque
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
import sys

vendor=Path(__file__).resolve().parents[1]/"vendor"
if str(vendor) not in sys.path:sys.path.insert(0,str(vendor))
import mido

from .game_profile import ROMAN
from .guitar import GuitarEvent,GuitarScore
from .music import positions_for_pitch,pitch_range_hint
from .score import pitch_name
from .arrangement import find_fingerings,PitchNote
from .measures import MeasureChange,valid_meter


@dataclass(frozen=True)
class MidiNote:
    pitch:int
    start:Fraction
    end:Fraction
    velocity:int
    channel:int


@dataclass
class MidiTrack:
    index:int
    name:str
    notes:list


@dataclass
class MidiSource:
    path:Path
    tracks:list
    tempo_map:tuple
    signatures:tuple

    @property
    def bpm(self):return 60_000_000/self.tempo_map[0][1]


def inspect_midi(path):
    path=Path(path)
    if path.stat().st_size>20_000_000:raise ValueError("MIDI 超过 20 MB，请先分段")
    try:midi=mido.MidiFile(str(path),clip=True)
    except (FileNotFoundError,PermissionError):raise
    except (OSError,EOFError,ValueError,KeyError,TypeError) as error:
        raise ValueError('无法解析为标准 MIDI；文件可能不完整或格式不受支持') from error
    if midi.type==2 or midi.ticks_per_beat<=0:raise ValueError("目前支持 PPQ 计时的 MIDI 0／1，不支持异步 MIDI 2 或 SMPTE 计时")
    tracks=[];tempos={};signatures={Fraction(0):(4,4)}
    for index,track in enumerate(midi.tracks):
        at=0;notes=[];active=defaultdict(deque)
        for message in track:
            at+=message.time
            beat=Fraction(at,midi.ticks_per_beat)
            if message.type=="set_tempo" and (beat not in tempos or index==0):
                if message.tempo<=0:raise ValueError("MIDI 包含无效的零速度")
                tempos[beat]=message.tempo
            if message.type=="time_signature":signatures[beat]=(message.numerator,message.denominator)
            if message.type not in ("note_on","note_off") or message.channel==9:continue
            key=(message.channel,message.note)
            if message.type=="note_on" and message.velocity>0:active[key].append((beat,message.velocity))
            elif active[key]:
                start,velocity=active[key].popleft()
                if beat>start:notes.append(MidiNote(message.note,start,beat,velocity,message.channel))
        for (channel,pitch),queue in active.items():
            for start,velocity in queue:notes.append(MidiNote(pitch,start,max(beat,start+Fraction(1,4)),velocity,channel))
        if notes:
            name=track.name or f"轨道 {index}"
            try:name=name.encode('latin1').decode('utf-8')
            except (UnicodeError,AttributeError):pass
            tracks.append(MidiTrack(index,name,sorted(notes,key=lambda note:(note.start,note.pitch))))
        if sum(len(t.notes) for t in tracks)>50000:raise ValueError("MIDI 音符过多，请先选择片段")
    if not tracks:raise ValueError("MIDI 中没有可导入的非鼓轨音符")
    tempos.setdefault(Fraction(0),500000)
    return MidiSource(path,tracks,tuple(sorted(tempos.items())),tuple(sorted(signatures.items())))


def _page(fret):return (fret-1)//6 if fret else 4


def convert_melody(source,track_index,transpose=0,highest=True,initial_page=0,timing=None,tap=None):
    from .timing import SAFE
    timing=timing or SAFE;tap=timing.tap_ms/1000 if tap is None else tap
    if not .015<=tap<=.2:raise ValueError('按键时长应为 15～200 毫秒')
    if type(transpose) is not int or not -24<=transpose<=24:raise ValueError('整轨移调应为 -24～24 的整数半音；请修改“整轨移调”输入框')
    if not 0<=initial_page<5:raise ValueError('品格起始页应为 1～5；请在主窗口修正起始页后重新导入')
    track=next((track for track in source.tracks if track.index==track_index),None)
    if track is None:raise ValueError("请选择有效的 MIDI 轨道")
    groups=defaultdict(list)
    for note in track.notes:groups[note.start].append(note)
    chosen=[]
    for at,notes in sorted(groups.items()):
        distinct={note.pitch:note for note in notes}
        if len(distinct)>1 and not highest:raise ValueError("该轨道包含同时起音的和弦；请明确选择“同拍取最高音”或换一个单音轨道")
        chosen.append(max(distinct.values(),key=lambda note:note.pitch))
    pitches=[note.pitch+transpose for note in chosen]
    candidates=[positions_for_pitch(pitch) for pitch in pitches]
    missing=sorted({pitch_name(pitch) for pitch,options in zip(pitches,candidates) if not options})
    clock=GuitarScore((),Fraction(0),source.bpm,source.tempo_map)
    if missing:
        first=next(note for note,options in zip(chosen,candidates) if not options)
        position=f'首处位于 {clock.seconds_at(first.start):.3f} 秒（拍位 {first.start}）'
        raise ValueError('超出标准定弦 0～24 品音域：'+'、'.join(missing[:10])+'；'+position+'。'+
                         pitch_range_hint(pitches)+'不会逐音折八度；曲长不是这项音域检查的条件。')
    route=find_fingerings([PitchNote(note.pitch+transpose,note.start,note.end) for note in chosen],clock,initial_page,timing,tap)
    events=[];cursor=Fraction(0);overlaps=0
    for index,(note,(string,fret)) in enumerate(zip(chosen,route)):
        chord=ROMAN[fret-1] if fret else "Zero"
        if note.start>cursor:events.append(GuitarEvent((),cursor,note.start-cursor,chord,"休止",1))
        next_at=chosen[index+1].start if index+1<len(chosen) else note.end
        end=min(note.end,next_at)
        if note.end>next_at:overlaps+=1
        events.append(GuitarEvent((string,),note.start,end-note.start,chord,pitch_name(note.pitch+transpose),1))
        cursor=end
    if any(not Fraction(1,960)<=event.beats<=128 for event in events):
        raise ValueError("文件含超出 1/960～128 拍的音符或休止，请先分段或量化；不会默默改变节奏")
    if cursor>100000:raise ValueError("曲谱超过 100000 拍，请先分段")
    marks=[];at_bar=Fraction(0);bar_index=0;meter=source.signatures[0][1];skipped=0
    if not valid_meter(meter):raise ValueError('起始拍号超出编辑器支持范围')
    for at,next_meter in source.signatures[1:]:
        distance=(at-at_bar)/Fraction(meter[0]*4,meter[1])
        if distance.denominator!=1 or not valid_meter(next_meter):skipped+=1;continue
        bar_index+=int(distance);at_bar=at;meter=next_meter
        if bar_index>=25000:raise ValueError('拍号标记小节过远，请先分段')
        marks.append(MeasureChange(bar_index,meter=tuple(meter)))
    score=GuitarScore(tuple(events),cursor,source.bpm,source.tempo_map,source.signatures[0][1],bar_changes=tuple(marks))
    report=f"原轨 {len(track.notes)} 音 → 旋律 {len(chosen)} 音；同拍未选 {len(track.notes)-len(chosen)} 音；截短重叠尾音 {overlaps} 处；速度节点 {len(source.tempo_map)} 个"
    if marks or skipped:report+=f'；变拍号已保留 {len(marks)} 处，无法按小节边界放置 {skipped} 处'
    return score,report


def score_to_tab(score):
    blocks=[]
    for offset in range(0,len(score.events),8):
        chunk=score.events[offset:offset+8]
        rows=[[] for _ in range(6)];rhythms=[]
        for event in chunk:
            if any(note not in range(1,7) for note in event.notes):raise ValueError("六线谱导出只支持弦号 1～6")
            fret=((0 if event.chord=="Zero" else ROMAN.index(event.chord)+1)-score.capo) if event.notes else 0
            if event.notes and fret<0:raise ValueError('音符位于 Capo 以下，无法显示为相对六线谱')
            rhythms.append(("r:" if not event.notes else "")+str(event.beats))
            for string,row in enumerate(rows,1):
                row.append(str(fret).ljust(4,"-") if string in event.notes else "----")
        blocks.append("节奏: "+" ".join(rhythms)+"\n"+"\n".join(label+"|"+"".join(row)+"|" for label,row in zip("eBGDAE",rows)))
    return "\n\n".join(blocks)+"\n"


def scale_tempos(tempo_map,source_bpm,requested_bpm):
    return tuple((Fraction(at),tempo*source_bpm/requested_bpm) for at,tempo in tempo_map)
