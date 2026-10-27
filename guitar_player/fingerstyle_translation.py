"""Translate pitched TAB to playable chord/fret actions with an auditable report."""
from bisect import bisect_right
from dataclasses import dataclass,replace
from fractions import Fraction as F
from functools import lru_cache
import copy
import json
from pathlib import Path
import re

from .game_profile import GameProfile,ROMAN,CHORD_PAGE_INDEX
from .guitar import GuitarScore,GuitarEvent,parse_guitar,apply_capo,make_plan,UP,DOWN,event_position,split_long_rests
from .music import fret_pitch,positions_for_pitch,pitch_range_hint
from .measures import timbre_points
from .score import pitch_name
from .tab import parse_tab
from .timing import SAFE,transition_interval,pointer_free,input_resource
from .voicings import available_voicings
from .voice_roles import NoteHint
from .translation_failures import TranslationFailure,event_failure,summarize_failures

AUTO_RANGE_SHIFTS=(0,*(shift for n in range(1,25) for shift in (-n,n)))


@dataclass(frozen=True)
class PitchSource:
    score:GuitarScore  # Timing/measure metadata; event.notes here are MIDI pitches.
    name:str='当前编辑内容'
    chord_marks:tuple=()
    reference_hits:int=0
    document:object=None
    sweeps:tuple=()
    chord_capo:int=0  # Chord labels are relative to this capo; pitches are already absolute.
    note_hints:tuple=()
    protected_marks:tuple=()  # Absolute (beat, pitches) protected before arrangement.


def _is_tab(text):return bool(re.search(r'(?m)^\s*[A-Ga-g1-6]\s*\|',text))


def from_score(score,name='当前曲谱',pitches=False):
    if pitches:return PitchSource(score,name)
    events=[]
    for event in score.events:
        if not event.notes:events.append(event);continue
        if event.chord not in (*ROMAN,'Zero') or any(n not in range(1,7) for n in event.notes):
            raise ValueError('原谱请选择品格六线谱；已编成游戏和弦的谱不作为指弹原谱')
        fret=0 if event.chord=='Zero' else ROMAN.index(event.chord)+1
        events.append(replace(event,notes=tuple(fret_pitch(s,fret) for s in event.notes)))
    return PitchSource(replace(score,events=tuple(events)),name)


def from_text(text,bpm=100,step='1/4',capo=0,name='当前编辑内容'):
    if _is_tab(text):return from_score(parse_tab(text,bpm,step,capo,_pitch_output=True),name,True)
    return from_score(apply_capo(parse_guitar(text,bpm),capo),name)


def from_midi(source,track_index):
    """Keep every selected-track attack for review before mixed translation."""
    from collections import defaultdict
    from .measures import MeasureChange,valid_meter
    track=next((t for t in source.tracks if t.index==track_index),None)
    if track is None:raise ValueError('请选择有效的 MIDI 轨道')
    grouped=defaultdict(list)
    for note in track.notes:grouped[note.start].append(note)
    attacks=sorted(grouped.items());events=[];cursor=F(0)
    for i,(at,notes) in enumerate(attacks):
        if at>cursor:events.append(GuitarEvent((),cursor,at-cursor,'Zero','休止',1))
        end=min(max(n.end for n in notes),attacks[i+1][0] if i+1<len(attacks) else max(n.end for n in notes))
        if not F(1,960)<=end-at<=128:raise ValueError('MIDI 时值超出 1/960～128 拍，请先分段')
        events.append(GuitarEvent(tuple(sorted({n.pitch for n in notes})),at,end-at,'Zero','MIDI',1));cursor=end
    if cursor>100000:raise ValueError('MIDI 超过 100000 拍，请先分段')
    events=split_long_rests(events)
    marks=[];at_bar=F(0);bar_index=0;meter=source.signatures[0][1]
    if not valid_meter(meter):raise ValueError('MIDI 起始拍号超出支持范围')
    for at,next_meter in source.signatures[1:]:
        distance=(at-at_bar)/F(meter[0]*4,meter[1])
        if distance.denominator!=1 or not valid_meter(next_meter):
            raise ValueError('混合转译包含非小节边界的拍号变化，请先调整 MIDI')
        bar_index+=int(distance);at_bar=at;meter=next_meter
        if bar_index>=25000:raise ValueError('MIDI 小节过多，请先分段')
        marks.append(MeasureChange(bar_index,meter=tuple(meter)))
    score=GuitarScore(tuple(events),cursor,source.bpm,source.tempo_map,source.signatures[0][1],bar_changes=tuple(marks))
    hints=tuple(NoteHint(n.start,n.pitch,n.end,n.channel,n.velocity) for n in track.notes)
    return PitchSource(score,f'{source.path.name} · {track.name}',note_hints=hints)


def read_source(path,*,rows=None,reference=True):
    from .library import read_score
    path=Path(path)
    if path.stat().st_size>5_000_000:raise ValueError('原谱超过 5 MB，请先分段')
    raw=path.read_text(encoding='utf-8-sig')
    if path.suffix.lower()=='.gscore' and json.loads(raw).get('kind')=='fingerstyle_source':
        from .fingerstyle_document import read_document
        return read_document(path).pitch_source(rows,reference,path.name)
    tab=json.loads(raw).get('notation')=='tab' if path.suffix.lower()=='.gscore' else _is_tab(raw)
    score,_=read_score(path,_pitch_output=tab)
    return from_score(score,path.name,tab)


@dataclass(frozen=True)
class Choice:
    mode:str
    selector:str
    strings:tuple
    pitches:tuple
    omitted:tuple
    octaves:tuple=()
    measured:bool=False
    sweep:int=0

    @property
    def action(self):return (self.sweep,) if self.sweep else self.strings

    @property
    def page(self):return CHORD_PAGE_INDEX[self.selector] if self.mode=='chord' else (4 if self.selector=='Zero' else ROMAN.index(self.selector)//6)


def _match(wanted,voiced,mode,selector,measured=False,octaves=False,protected=(),resources=None):
    protected=set(protected) or {max(wanted)};melody=max(protected)
    if not protected.issubset(voiced):return None
    selected={};used=set();used_resources=set()
    resources=resources or {s:s for s in range(1,7)}
    for pitch in sorted(protected,reverse=True)+sorted(set(wanted)-protected,reverse=True):
        string=next((s for s,p in enumerate(voiced,1) if p==pitch and s not in used and resources[s] not in used_resources),None)
        if string is not None:
            selected[pitch]=(string,pitch);used.add(string);used_resources.add(resources[string])
    if not protected.issubset(selected):return None
    shifts=[]
    if octaves:
        for pitch in sorted(wanted):
            if pitch in selected:continue
            options=[(abs(p-pitch),s,p) for s,p in enumerate(voiced,1)
                     if p is not None and p>=0 and s not in used and resources[s] not in used_resources and p<=melody and p%12==pitch%12 and abs(p-pitch)==12]
            if options:
                _,s,p=min(options);selected[pitch]=(s,p);used.add(s);used_resources.add(resources[s]);shifts.append((pitch,p))
    strings=tuple(sorted(used))
    return Choice(mode,selector,strings,tuple(voiced[s-1] for s in strings),tuple(sorted(set(wanted)-selected.keys())),tuple(shifts),measured)


@dataclass
class _Node:
    state:tuple
    cost:tuple
    choice:Choice|None=None
    parent:object=None
    attacks:tuple=()


def transpose_name(semitones):
    return '原调' if not semitones else f'{"降" if semitones<0 else "升"}{abs(semitones)}半音'


def transpose_chord(name,semitones):
    """Transpose a soft chord hint, including its slash bass, with the notes."""
    if not semitones:return name
    names=('C','C#','D','Eb','E','F','F#','G','Ab','A','Bb','B')
    natural={'C':0,'D':2,'E':4,'F':5,'G':7,'A':9,'B':11}
    def move(match):
        letter,accidental=match.groups()
        pitch=natural[letter]+{'':0,'#':1,'b':-1}[accidental]
        return names[(pitch+semitones)%12]
    root=re.sub(r'^([A-G])([#b]?)',move,name)
    return re.sub(r'(?<=/)([A-G])([#b]?)$',move,root)


def transpose_source(source,semitones):
    if type(semitones) is not int or not -24<=semitones<=24:raise ValueError('整曲移调应为 -24～24 个半音')
    if not semitones and not source.chord_capo:return source
    events=[]
    for event in source.score.events:
        notes=tuple(p+semitones for p in event.notes)
        if any(not 0<=p<=127 for p in notes):raise ValueError('整曲移调后超出 MIDI 音高范围')
        events.append(replace(event,notes=notes))
    marks=tuple((at,transpose_chord(name,semitones+source.chord_capo)) for at,name in source.chord_marks)
    hints=tuple(replace(h,pitch=h.pitch+semitones) for h in source.note_hints)
    protected=tuple((at,tuple(p+semitones for p in pitches)) for at,pitches in source.protected_marks)
    return replace(source,score=replace(source.score,events=tuple(events)),chord_marks=marks,chord_capo=0,
                   note_hints=hints,protected_marks=protected)


def translate(source,rows,profile=None,timing=None,tap=.045,reference=True,octaves=False,beam=48,*,transpose=0,cancel=None,sustain_notes=False,ornaments=False,progress=None,analyze_voices=False,accompaniment='all',distorted_only=True,melody_channel=None,voice_overrides=(),avoid_bm_second=True,filter_low_bass=False,high_main_octave=False,_analysis_cache=None):
    if high_main_octave:raise ValueError('已取消主旋律局部降八度；仅支持整曲统一移调，无法容纳时保留失败诊断')
    from .timing import selector_timing
    profile=copy.deepcopy(profile or GameProfile.mixed())
    timing=selector_timing(timing,profile)
    if analyze_voices or accompaniment!='all' or melody_channel is not None or voice_overrides or filter_low_bass:
        from .voice_roles import translate_with_voices
        return translate_with_voices(source,rows,profile,timing,tap,reference,octaves,beam,transpose=transpose,cancel=cancel,
            sustain_notes=sustain_notes,ornaments=ornaments,progress=progress,accompaniment=accompaniment,
            distorted_only=distorted_only,melody_channel=melody_channel,voice_overrides=voice_overrides,avoid_bm_second=avoid_bm_second,
            filter_low_bass=filter_low_bass,_analysis_cache=_analysis_cache)
    if ornaments:
        from .ornaments import translate_ornaments
        return translate_ornaments(source,rows,profile,timing,tap,reference,octaves,beam,
                                   transpose=transpose,cancel=cancel,sustain_notes=sustain_notes,progress=progress,avoid_bm_second=avoid_bm_second)
    if profile.mode!='mixed':raise ValueError('转译使用和弦＋品格混合布局')
    if not .015<=tap<=.2:raise ValueError('按键时长应为 15～200 毫秒')
    if source.reference_hits and not reference:raise ValueError('原谱 X／点按参考指法解析；请补全实测音高表后重新读取原谱，再使用仅实测转译')
    original=source;source=transpose_source(source,transpose)
    original_events={event.start:event for event in original.score.events}
    original_marks=sorted(original.chord_marks);original_mark_starts=[at for at,_ in original_marks]
    score=source.score;sounded=[e for e in score.events if e.notes]
    if not sounded:raise ValueError('原谱没有可转换的音符')
    if len(sounded)>10000:raise ValueError('转译限 10000 个起拍，请先分段')
    points=timbre_points(score);tone_starts=[p[0] for p in points]
    tables={tone:available_voicings(rows,tone,reference) for tone in ('clean','distorted')}
    mode=profile.initial_mode or 'fret';pages=profile.initial_pages
    nodes=[_Node((mode,None,pages['chord'],pages['fret'],profile.initial_timbre),(0,0,0,0.0,0))]
    marks=sorted(source.chord_marks);mark_starts=[at for at,_ in marks]
    sweeps=dict(source.sweeps)
    protected_marks=dict(source.protected_marks)
    def marked(at):
        i=bisect_right(mark_starts,at)-1
        return marks[i][1] if i>=0 else None
    # These inputs belong to this translation, not to a particular beam node.
    # Bound the caches and discard them on return so changed layouts/tables or
    # a later song cannot reuse stale decisions.
    resources={s:input_resource(s,profile) for s in (*range(1,7),UP,DOWN)}
    fret_voicings={f:tuple(fret_pitch(s,f) for s in range(1,7)) for f in range(25)}
    gamepad_sweeps=profile.strum_input=='gamepad'
    @lru_cache(maxsize=4096)
    def event_choices(wanted,protected,tone,sweep):
        wanted=set(wanted);protected=set(protected);choices=[]
        def candidate(voiced,mode,selector,measured=False):
            if not sweep:return _match(wanted,voiced,mode,selector,measured,octaves,protected,resources)
            sounding=tuple(p for p in voiced if p is not None and p>=0)
            if set(sounding)!=wanted:return None
            return Choice(mode,selector,tuple(s for s,p in enumerate(voiced,1) if p is not None and p>=0),sounding,(),(),measured,sweep)
        for item in tables[tone].values():
            voiced=item.pitches
            if avoid_bm_second and item.name=='Bm':
                # Keep theoretical D4, but avoid the game's anomalous string.
                if sweep:continue
                voiced=(voiced[0],None,*voiced[2:])
            option=candidate(voiced,'chord',item.name,item.measured)
            if option:choices.append(option)
        for fret in sorted({f for _,f in positions_for_pitch(max(protected))}):
            option=candidate(fret_voicings[fret],'fret',ROMAN[fret-1] if fret else 'Zero')
            if option:choices.append(option)
        return tuple(choices)
    @lru_cache(maxsize=16384)
    def transition(before,selector,current,before_tone,before_action,mode,selected,action,tone):
        switch=before!=mode;count=4 if mode=='chord' else 5
        page=profile.pages[selected];distance=abs(page-current);moves=min(distance,count-distance)
        select=switch or selector!=selected;tone_change=before_tone!=tone
        prefetch=not switch and not moves and not tone_change and pointer_free(before_action,profile)
        required=(.18+timing.jitter_margin if switch else 0)+moves*timing.page_delay
        required+=(timing.selection_budget(0,prefetch) if select else 0)+(.09+timing.jitter_margin if tone_change else 0)
        required=transition_interval(before_action,action,timing,tap,required,sustain_notes,gamepad_sweeps=gamepad_sweeps)
        return required,switch,moves,select,page
    # Choice feasibility does not depend on the route. Find the same first
    # blocker before spending time building a beam for a doomed key; measured
    # voicings and protected simultaneous notes use the exact matcher above.
    prepared_events=[]
    for event_index,event in enumerate(sounded):
        if cancel is not None and cancel.is_set():raise InterruptedError('已取消转译')
        if progress and event_index==0:progress(0,len(sounded))
        wanted=set(event.notes);protected=set(protected_marks.get(event.start,())) or {max(wanted)}
        tone=points[max(0,bisect_right(tone_starts,event.start)-1)][1]
        preferred=marked(event.start)
        sweep=sweeps.get(event.start,0)
        choices=event_choices(tuple(sorted(wanted)),tuple(sorted(protected)),tone,sweep)
        if not choices:
            if sweep and preferred=='Bm' and avoid_bm_second:
                raise TranslationFailure(f'拍位 {event.start} 的 Bm 扫弦会触及临时规避的 2 弦，暂无等音扫弦按法；'
                                 '可直接载入手写原谱，或待游戏修复后关闭 Bm 异常规避',event_failure(score,event,protected,True,True))
            if sweep:raise TranslationFailure(f'拍位 {event.start} 的扫弦没有等音的游戏按法；请调整 Capo／和弦音高表，或改成逐弦拨奏',event_failure(score,event,protected,True))
            required='、'.join(pitch_name(pitch) for pitch in sorted(protected))
            position=f'{event_position(score,event)}，{score.seconds_at(event.start):.3f} 秒'
            hint=pitch_range_hint(protected)
            raise TranslationFailure(f'{position}：{required} 无法保留旋律音高；'+
                             (hint or '没有可同时保留这些指定音的按法；请检查声部标记或整体移调。'),event_failure(score,event,protected))
        prepared_events.append((event,min(wanted),tone,preferred,choices))
    previous=None
    for event_index,(event,bass,tone,preferred,choices) in enumerate(prepared_events):
        if cancel is not None and cancel.is_set():raise InterruptedError('已取消转译')
        if progress and event_index and event_index%64==0:progress(event_index,len(sounded))
        at=score.seconds_at(event.start)
        gap=at-score.seconds_at(previous.start) if previous else 1e9
        best={};attack_cache={}
        for option in choices:
            loss=len(option.omitted)*10+(20 if bass in option.omitted else 0)
            action=option.action;action_resources=tuple(resources[n] for n in action)
            octave_count=len(option.octaves);hint_miss=int(preferred in tables[tone] and option.selector!=preferred)
            for node in nodes:
                before,selector,cp,fp,before_tone=node.state
                current=cp if option.mode=='chord' else fp
                required,switch,page_moves,select,page=transition(before,selector,current,before_tone,
                    node.choice.action if node.choice else (),option.mode,option.selector,action,tone)
                attacks=();repeated=False
                if sustain_notes:
                    attack_key=node.attacks,action_resources
                    cached=attack_cache.get(attack_key)
                    if cached is None:
                        previous_attacks=dict(node.attacks)
                        repeat=max((previous_attacks.get(n,-1e9)+tap+timing.key_gap for n in action_resources),default=-1e9)
                        attacks=tuple(sorted({**{n:t for n,t in previous_attacks.items() if t+tap+timing.key_gap>at+1e-9},
                                              **{n:at for n in action_resources}}.items(),key=lambda item:str(item[0])))
                        cached=attack_cache[attack_key]=(at+1e-9<repeat,attacks)
                    repeated,attacks=cached
                conflict=gap+1e-9<required or repeated
                old=node.cost
                cost=(old[0]+int(conflict),old[1]+loss,old[2]+octave_count,
                      old[3]+switch*10+page_moves*2+select*.3+hint_miss*2,
                      old[4]+hint_miss)
                state=(option.mode,option.selector,page if option.mode=='chord' else cp,
                       page if option.mode=='fret' else fp,tone)
                key=state,attacks
                if key not in best or cost<best[key].cost:best[key]=_Node(state,cost,option,node,attacks)
        nodes=sorted(best.values(),key=lambda n:n.cost)[:beam];previous=event
    node=min(nodes,key=lambda n:n.cost);route_cost=node.cost;route=[]
    while node.choice is not None:route.append(node.choice);node=node.parent
    route.reverse();events=[];details=[];route_iter=iter(route);selector=route[0].selector
    before=None;before_at=None;attacks={};current_pages=dict(pages);before_tone=profile.initial_timbre
    for event in score.events:
        if not event.notes:
            events.append(replace(event,chord=selector,notes=(),source_spans=()));continue
        option=next(route_iter)
        selector=option.selector
        events.append(replace(event,chord=option.selector,notes=option.action,label=option.selector,source_spans=()))
        original_mark_index=bisect_right(original_mark_starts,event.start)-1
        tone=points[max(0,bisect_right(tone_starts,event.start)-1)][1]
        required=0;available=1e9
        if before is not None:
            switch=before.mode!=option.mode;count=4 if option.mode=='chord' else 5
            distance=abs(option.page-current_pages[option.mode]);moves=min(distance,count-distance)
            select=switch or before.selector!=option.selector
            prefetch=not switch and not moves and tone==before_tone and pointer_free(before.action,profile)
            preparation=(.18+timing.jitter_margin if switch else 0)+moves*timing.page_delay
            preparation+=(timing.selection_budget(0,prefetch) if select else 0)+(.09+timing.jitter_margin if tone!=before_tone else 0)
            required=transition_interval(before.action,option.action,timing,tap,preparation,sustain_notes,
                                         gamepad_sweeps=profile.strum_input=='gamepad')
            available=score.seconds_at(event.start)-score.seconds_at(before_at)
        at=score.seconds_at(event.start)
        repeat=max((attacks.get(input_resource(n,profile),-1e9)+tap+timing.key_gap-at for n in option.action),default=-1e9)
        conflict=available+1e-9<required or sustain_notes and repeat>1e-9
        details.append({'beat':str(event.start),'source':sorted(set(original_events[event.start].notes)),
                        'target':sorted(set(event.notes)),
                        'marked_chord':original_marks[original_mark_index][1] if original_mark_index>=0 else None,
                        'target_chord':marked(event.start),
                        'action':'up' if option.sweep==UP else 'down' if option.sweep==DOWN else 'pick',
                        'mode':option.mode,'selector':option.selector,'strings':list(option.strings),
                        'pitches':list(option.pitches),'omitted':list(option.omitted),
                        'main_pitch':max(protected_marks.get(event.start,()) or event.notes),
                        'protected_pitches':list(protected_marks.get(event.start,()) or (max(event.notes),)),
                        'octaves':[list(pair) for pair in option.octaves],
                        'basis':'measured' if option.mode=='chord' and option.measured else 'reference' if option.mode=='chord' else 'standard_tuning'})
        details[-1].update(timing_conflict=bool(conflict),required_ms=required*1000,
                           available_ms=None if before is None else available*1000)
        before=option;before_at=event.start;before_tone=tone;current_pages[option.mode]=option.page
        for n in option.action:attacks[input_resource(n,profile)]=at
    result=replace(score,events=tuple(events),capo=0,chord_changes=())
    warning='';switches=0;page_moves=0;selections=0
    current_mode=mode;current_selector=None;current_pages=dict(pages)
    for choice in route:
        switches+=int(current_mode!=choice.mode)
        selections+=int(current_mode!=choice.mode or current_selector!=choice.selector)
        count=4 if choice.mode=='chord' else 5
        distance=abs(profile.pages[choice.selector]-current_pages[choice.mode])
        page_moves+=min(distance,count-distance)
        current_pages[choice.mode]=profile.pages[choice.selector]
        current_mode=choice.mode;current_selector=choice.selector
    try:
        make_plan(result,profile,timing=timing,tap=tap,sustain_notes=sustain_notes)
    except ValueError as error:warning=str(error)
    chord_count=sum(c.mode=='chord' for c in route);reference_count=sum(c.mode=='chord' and not c.measured for c in route)
    omitted=sum(len(c.omitted) for c in route);shifted=sum(len(c.octaves) for c in route)
    summary=f'旋律 {len(route)} 个起拍保留；和弦 {chord_count} 处／品格 {len(route)-chord_count} 处；省略 {omitted} 音；伴奏八度调整 {shifted} 音'
    if transpose:summary=transpose_name(transpose)+'（全曲统一移调，旋律音程与节奏不变）；'+summary
    if reference_count:summary+=f'；{reference_count} 处采用无 Capo 参考指法，游戏音高尚未实测'
    elif not chord_count:summary+='；当前选出的路线全部使用品格'
    if source.reference_hits:summary+=f'；原谱 {source.reference_hits} 个 X／点按参考弦音解析，尚未实测'
    if avoid_bm_second:summary+='；已临时规避 Bm 2 弦异常（理论仍为 D4）'
    if warning:summary+='；游戏时序需调整：'+warning
    report={'version':1,'source':source.name,'reference_allowed':reference,'octave_adjustment':octaves,
            'priority':'主旋律音高与起音优先；低音、内声部与装饰音在可行时补入',
            'source_reference_hits':source.reference_hits,
            'transpose_semitones':transpose,'timing_violations':route_cost[0],'avoid_bm_second':avoid_bm_second,
            'weighted_pitch_loss':route_cost[1],'omitted_notes':omitted,'octave_shifts':shifted,
            'chord_events':chord_count,'fret_events':len(route)-chord_count,
            'page_moves':page_moves,'selection_changes':selections,
            'summary':summary,'mode_switches':switches,'timing_warning':warning,'events':details}
    if progress:progress(len(sounded),len(sounded))
    return result,report


def _key_rank(report):
    # Musical fidelity and feasible beats precede convenience. Prefer the
    # original key when two routes are otherwise equally good.
    loss=report['weighted_pitch_loss']+report.get('ornament_pitch_loss',0)+report.get('range_pitch_loss',0)
    return (bool(report['timing_warning']),report['timing_violations'],loss,
            report['octave_shifts'],report['mode_switches'],report['page_moves'],
            report['selection_changes'],report['fret_events'],abs(report['transpose_semitones']))


class KeySearchError(ValueError):
    """Keep every failed key candidate available for the comparison view."""
    def __init__(self,candidates):
        self.candidates=[dict(row) for row in candidates]
        self.diagnostics=summarize_failures(self.candidates)
        first=self.candidates[0]
        super().__init__(f'已比较 {len(self.candidates)} 个整曲移调方案，均无法保留旋律／扫弦；'
                         f'{transpose_name(first["transpose_semitones"])}：{first["error"]} '
                         '每个方案的失败原因见“移调比较”。')


def translate_best_key(source,rows,profile=None,timing=None,tap=.045,reference=True,octaves=False,beam=48,*,shifts=range(0,-25,-1),progress=None,cancel=None,sustain_notes=False,ornaments=False,analyze_voices=False,accompaniment='all',distorted_only=True,melody_channel=None,voice_overrides=(),avoid_bm_second=True,filter_low_bass=False,high_main_octave=False):
    """Compare whole-piece transpositions; never retune individual melody notes."""
    if high_main_octave:raise ValueError('已取消主旋律局部降八度；仅支持整曲统一移调，无法容纳时保留失败诊断')
    shifts=tuple(dict.fromkeys(shifts))
    if not shifts or any(type(n) is not int or not -24<=n<=24 for n in shifts):
        raise ValueError('请提供 -24～24 半音范围内的整曲移调候选')
    candidates=[];best=None;best_report=None;analysis_cache={}
    fields=('transpose_semitones','timing_violations','weighted_pitch_loss','omitted_notes','octave_shifts',
            'mode_switches','page_moves','selection_changes','chord_events','fret_events','timing_warning')
    for index,shift in enumerate(shifts):
        if cancel is not None and cancel.is_set():raise InterruptedError('已取消转译')
        try:
            result,report=translate(source,rows,profile,timing,tap,reference,octaves,beam,transpose=shift,cancel=cancel,
                                    sustain_notes=sustain_notes,ornaments=ornaments,analyze_voices=analyze_voices,
                                    accompaniment=accompaniment,distorted_only=distorted_only,
                                    melody_channel=melody_channel,voice_overrides=voice_overrides,avoid_bm_second=avoid_bm_second,
                                    filter_low_bass=filter_low_bass,_analysis_cache=analysis_cache)
        except ValueError as error:
            row={'transpose_semitones':shift,'valid':False,'error':str(error)}
            if isinstance(error,TranslationFailure):row['failure']=error.detail
            candidates.append(row)
        else:
            candidates.append({'valid':True,**{field:report[field] for field in fields},
                               'ornament_pitch_loss':report.get('ornament_pitch_loss',0),
                               'range_pitch_loss':report.get('range_pitch_loss',0),
                               'range_filtered_notes':report.get('range_filtered_notes',0),
                               'intentional_omissions':report.get('intentional_omissions',0),
                               'total_omitted_notes':report['omitted_notes']+report.get('ornaments',{}).get('omitted_notes',0)})
            if best_report is None or _key_rank(report)<_key_rank(best_report):best,best_report=result,report
        if progress:progress(index+1,len(shifts),shift)
    if best_report is None:
        raise KeySearchError(candidates)
    best_report['key_search']={'candidates':candidates,'selected':best_report['transpose_semitones'],
                             'original':next((row for row in candidates if row['transpose_semitones']==0),None)}
    prefix='自动比较 '+str(len(shifts))+' 个移调方案'
    if not best_report['transpose_semitones']:prefix+='，选择原调'
    best_report['summary']=prefix+'；'+best_report['summary']
    return best,best_report
