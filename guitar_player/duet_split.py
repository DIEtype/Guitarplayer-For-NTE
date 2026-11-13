"""Bounded joint search for two exact-pitch, time-aligned guitar parts."""
from bisect import bisect_right
import copy
from dataclasses import dataclass,replace
from fractions import Fraction as F
from functools import lru_cache
from pathlib import Path

from .fingerstyle_translation import PitchSource,Choice,_match,from_midi,sweep_choice,translate,transpose_source
from .game_profile import GameProfile,ROMAN
from .guitar import DOWN,UP,GuitarEvent,make_plan,split_long_rests,event_position
from .measures import MeasureChange, timbre_points
from .music import fret_pitch,positions_for_pitch,pitch_range_hint
from .score import pitch_name
from .timing import ADAPTIVE,selector_timing,transition_interval,pointer_free,input_resource
from .voice_roles import analyze_roles,ROLE_NAMES
from .voicings import available_voicings

STRATEGIES={'roles':'伴奏＋主旋律','playable':'可演奏优先'}
KEEP_TRACKS='保留原轨与音色'


def midi_source(midi,track_indices,melody_track=None):
    from .midi_import import MidiTrack
    chosen=[track for track in midi.tracks if track.index in set(track_indices)]
    if not chosen:raise ValueError('请至少选择一个非鼓 MIDI 轨道')
    if melody_track is not None and melody_track not in {t.index for t in chosen}:
        raise ValueError('指定的主旋律轨道也需要包含在选中的轨道中')
    merged=MidiTrack(-1,'＋'.join(t.name for t in chosen),[note for track in chosen for note in track.notes])
    result=from_midi(replace(midi,tracks=[merged]),-1)
    marked=frozenset((n.start,n.pitch) for t in chosen if t.index==melody_track for n in t.notes)
    return result,marked


def read_split_source(path,rows=None):
    from .fingerstyle_document import read_document
    from .fingerstyle_translation import read_source
    path=Path(path)
    if path.suffix.lower()=='.gscore':
        import json
        if path.stat().st_size>5_000_000:raise ValueError('原谱超过 5 MB，请先分段')
        data=json.loads(path.read_text(encoding='utf-8-sig'))
        if isinstance(data,dict) and data.get('kind')=='duet_part':
            from .duet_project import DuetPart
            from .voice_roles import NoteHint
            part=DuetPart.from_dict(data.get('part'));score=part.score;points=timbre_points(score);events=[];hints=[];sweeps=[]
            for event in score.events:
                tone=next(value for at,value in reversed(points) if at<=event.start)
                if event.chord in (*ROMAN,'Zero'):
                    fret=0 if event.chord=='Zero' else ROMAN.index(event.chord)+1
                    voiced=tuple(fret_pitch(s,fret) for s in range(1,7))
                else:voiced=available_voicings(rows or {},tone,True)[event.chord].pitches
                sweep=event.notes in ((101,),(102,));strings=range(1,7) if sweep else event.notes
                pitches=tuple(voiced[s-1] for s in strings if voiced[s-1] is not None and voiced[s-1]>=0)
                events.append(replace(event,notes=pitches));hints.extend(NoteHint(event.start,p,event.start+event.beats) for p in pitches)
                if sweep:sweeps.append((event.start,event.notes[0]))
            return PitchSource(replace(score,events=tuple(events),capo=0),part.name,sweeps=tuple(sweeps),note_hints=tuple(hints))
    try:return read_document(path).pitch_source(rows,True,path.name)
    except ValueError:
        return read_source(path,rows=rows,reference=True)


@dataclass(frozen=True)
class Lane:
    mode:str
    selector:str|None
    chord_page:int
    fret_page:int
    tone:str
    at:float=-1e9
    action:tuple=()
    attacks:tuple=()


@dataclass
class Node:
    lanes:tuple
    cost:tuple
    parent:object=None
    pair:tuple=(None,None)


@dataclass
class SplitResult:
    scores:dict
    pitched:dict
    sweeps:dict
    report:dict


def _shift_clock(score,lead,bars):
    tempos=list(score.tempo_map)
    if tempos:
        initial=next((value for at,value in reversed(tempos) if at<=0),60_000_000/score.bpm)
        tempos=[(F(0),initial)]+[(at+lead,value) for at,value in tempos if at>0]
    changes=tuple(replace(change,bar=change.bar+bars) for change in score.bar_changes)
    return replace(score,tempo_map=tuple(tempos),base_tempo_map=tuple(tempos),beats=score.beats+lead,
                   measures=score.measures+bars if score.measures else 0,bar_changes=changes,capo=0,chord_changes=())


def part_profile(profile,score):
    from .duet_project import score_mode
    result=copy.deepcopy(profile);mode=score_mode(score);result.mode=mode
    if mode!='mixed':
        result.page_count=4 if mode=='chord' else 5;result.initial_page=result.initial_pages[mode]
    return result


def _merge_onsets(source):
    """Regroup independent held events for routing, without changing note ends."""
    groups={}
    for event in source.score.events:
        if event.notes:groups.setdefault(event.start,[]).append(event)
    if all(len(events)==1 for events in groups.values()):return source
    from .voice_roles import NoteHint
    known={(hint.start,hint.pitch) for hint in source.note_hints};hints=list(source.note_hints);events=[]
    for at,group in sorted(groups.items()):
        for event in group:
            hints.extend(NoteHint(at,p,at+event.beats) for p in event.notes if (at,p) not in known)
        events.append(replace(group[0],notes=tuple(sorted({p for event in group for p in event.notes})),beats=max(e.beats for e in group)))
    return replace(source,score=replace(source.score,events=tuple(events)),note_hints=tuple(hints))


def arrange_original_tracks(source,rows,profile,timing,tap,*,transpose=0,preroll_bars=2,
                            protected_keys=(),cancel=None,progress=None,sustain_notes=True):
    """Approximate each authored track using only that track's notes and tone."""
    if not source.lane_sources:raise ValueError('请从双轨编谱打开“自动编配双轨…”，以保留 Guitar Pro 的原始轨道和音色')
    if type(preroll_bars) is not int or not 1<=preroll_bars<=8:raise ValueError('共同前奏应为 1～8 小节')
    clock=source.score
    lead=F(clock.time_signature[0]*4,clock.time_signature[1])*preroll_bars
    end=clock.beats+lead;preroll_seconds=_shift_clock(clock,lead,preroll_bars).seconds_at(lead)
    if end>100000:raise ValueError('加入共同前奏后超过 100000 拍，请先分段编配')
    scores={};pitched={};sweeps={};details=[];summaries=[];clock_error=0.0
    keys=set(protected_keys)
    total=len(source.lane_sources)
    for index,(role,lane) in enumerate(source.lane_sources):
        if cancel is not None and cancel.is_set():raise InterruptedError('已取消编配')
        scores[role]=pitched[role]=None;sweeps[role]={}
        if not any(event.notes for event in lane.score.events):continue
        lane=_merge_onsets(lane)
        overrides=tuple((e.start,p,'keep') for e in lane.score.events for p in set(e.notes) if (role,e.start,p) in keys)
        def report_progress(done,count):
            if progress:progress(index*100+done*100//max(1,count),total*100)
        try:
            arranged,report=translate(lane,rows,profile,timing,tap,transpose=transpose,
                                      cancel=cancel,progress=report_progress,sustain_notes=sustain_notes,
                                      analyze_voices=True,voice_overrides=overrides)
            if report['timing_warning']:raise ValueError(report['timing_warning'])
            # Prepend identical silence for recording sync. The separate tone
            # maps travel with their own parts, including changes inside the song.
            shifted=_shift_clock(arranged,lead,preroll_bars);origin=shifted.seconds_at(lead)
            clock_error=max(clock_error,abs(origin-preroll_seconds))
            rest=GuitarEvent((),F(0),lead,'Zero','休止',1)
            attacks={F(row['beat']):row for row in report['events']}
            note_ends={}
            for hint in lane.note_hints:
                key=hint.start,hint.pitch+transpose
                note_ends[key]=max(hint.end,note_ends.get(key,hint.end))
            game=[rest];audio=[rest]
            for event in arranged.events:
                row=attacks.get(event.start) if event.notes else None
                shifted_event=replace(event,start=event.start+lead)
                if row is None:
                    game.append(shifted_event);audio.append(shifted_event);continue
                pitches=row['pitches']
                if row['action']!='pick':sweeps[role][shifted_event.start,role]=UP if row['action']=='up' else DOWN
                groups={}
                if row['action']!='pick' or not sustain_notes:
                    groups[event.beats]=(event.notes,pitches)
                else:
                    for string,pitch in zip(event.notes,pitches):
                        beats=note_ends.get((event.start,pitch),event.start+event.beats)-event.start
                        strings,group=groups.setdefault(beats,([],[]));strings.append(string);group.append(pitch)
                for beats,(strings,group) in groups.items():
                    game.append(replace(shifted_event,notes=tuple(strings),beats=beats))
                    audio.append(replace(shifted_event,notes=tuple(group),beats=beats,label=role))
                for pitch in row['source']:
                    kept=pitch+transpose in pitches
                    reason=row.get('strum_approximation','原起拍与音高保留') if kept else '原轨按法／时序近似省略'
                    details.append({'beat':row['beat'],'original_pitch':pitch,'output_pitch':pitch+transpose,
                                    'output_beat':str(shifted_event.start),'role':'melody' if pitch+transpose in row['protected_pitches'] else 'inner',
                                    'part':role if kept else 'omitted','source_part':role,'selector':row['selector'] if kept else '',
                                    'reason':lane.name+' · '+reason})
                clock_error=max(clock_error,abs(shifted.seconds_at(shifted_event.start)-origin-clock.seconds_at(event.start)))
            score=replace(shifted,events=split_long_rests(game),beats=end)
            plan=make_plan(score,part_profile(profile,score),timing=timing,tap=tap,sustain_notes=sustain_notes)
            first=min(step.at for step in plan if step.kind in ('page','chord_move','mode_down','timbre_down') or step.kind=='note' and step.data[1])
            if first<.25:raise ValueError('共同前奏太短，请增加共同前奏小节数')
        except ValueError as error:
            raise ValueError(f'{lane.name}：{error}。可单独调整该轨，或改用“可演奏优先”允许跨轨分配。') from error
        scores[role]=score;pitched[role]=replace(score,events=split_long_rests(audio));summaries.append(f'{lane.name} · {report["summary"]}')
    if not summaries:raise ValueError('两轨均没有可演奏的音符')
    if clock_error>1e-7:raise ValueError(f'原轨编配时间轴校验未通过，最大起拍误差 {clock_error*1000:.3f} ms')
    details.sort(key=lambda row:F(row['beat']))
    omitted=sum(row['part']=='omitted' for row in details)
    report={'version':1,'strategy':'original_tracks','source':source.name,'timing':timing.label,'transpose':transpose,
            'preroll_bars':preroll_bars,'preroll_beats':str(lead),'notes':details,'input_notes':len(details),
            'kept_notes':len(details)-omitted,'omitted_notes':omitted,'melody_to_recorded':0,
            'summary':KEEP_TRACKS+'。'+'。'.join(summaries),
            'clock_check':{'max_onset_error_ms':clock_error*1000,'common_preroll_seconds':preroll_seconds,
                           'policy':'独立编配，保持原轨、原音色和原起拍；两轨增加相同前奏'}}
    if progress:progress(total*100,total*100)
    return SplitResult(scores,pitched,sweeps,report)


def split_source(source,rows=None,profile=None,timing=None,tap=.04,*,strategy='roles',melody_keys=(),protected_keys=(),
                 transpose=0,preroll_bars=2,beam=12,cancel=None,progress=None,sustain_notes=True):
    if strategy not in STRATEGIES:raise ValueError('请选择伴奏＋主旋律或可演奏优先')
    if type(preroll_bars) is not int or not 1<=preroll_bars<=8:raise ValueError('共同前奏应为 1～8 小节')
    if not .015<=tap<=.2:raise ValueError('最短按键应为 15～200 毫秒')
    if type(beam) is not int or not 1<=beam<=32:raise ValueError('拆分搜索宽度应为 1～32')
    original=source;source=_merge_onsets(transpose_source(source,transpose))
    marked={(F(at),pitch+transpose) for at,pitch in melody_keys}
    manual={(F(at),pitch+transpose) for at,pitch in protected_keys}
    clock=source.score;events=[e for e in clock.events if e.notes]
    if not 20<=clock.bpm<=360:raise ValueError('原谱起始速度超出 20～360 BPM，请先在导入时整体调整音乐速度')
    if not events:raise ValueError('原谱没有可拆分的音符')
    if len(events)>20000:raise ValueError('自动拆分限 20000 个起音，请先分段；不是按曲长限制')
    if progress:progress(0,len(events))
    roles=analyze_roles(source,cancel=cancel)
    role_rows={(F(row['beat']),row['pitch']):row for row in roles}
    if marked:
        for key,row in role_rows.items():
            if key in marked:row.update(role='melody',basis='指定主旋律轨道',strength='指定')
            elif row['role'] in ('melody','uncertain','keep'):row.update(role='inner',basis='来自其他选中轨道',strength='较强')
    manual_times={at for at,pitch in manual}
    for key,row in role_rows.items():
        if key in manual:row.update(role='keep',basis='手动指定此拍主旋律',strength='指定')
        elif key[0] in manual_times and key not in marked and row['role'] in ('melody','uncertain'):
            row.update(role='inner',basis='此拍已另行指定旋律',strength='指定')
    protected=dict(source.protected_marks);sweeps=dict(source.sweeps);optional_sweeps=set(source.optional_sweeps)
    sweep_targets=dict(source.sweep_targets)
    profile=copy.deepcopy(profile or GameProfile.mixed());timing=selector_timing(timing or ADAPTIVE,profile)
    resources={s:input_resource(s,profile) for s in (*range(1,7),101,102)}
    lane_timbres=source.lane_timbres or (timbre_points(clock),)*2
    tone_starts=tuple(tuple(at for at,_ in points) for points in lane_timbres)
    tables={tone:available_voicings(rows or {},tone,True) for tone in ('clean','distorted')}
    fret_tables={f:tuple(fret_pitch(s,f) for s in range(1,7)) for f in range(25)}

    @lru_cache(maxsize=2048)
    def choices(wanted,required,tone,sweep=0):
        wanted=set(wanted);required=set(required)
        if not wanted:return ()
        anchors=[required] if required else [{min(wanted)},{max(wanted)}]
        found={}
        for mode,selector,voiced,measured in [*(('chord',v.name,v.pitches,v.measured) for v in tables[tone].values()),
                *(('fret',ROMAN[f-1] if f else 'Zero',voiced,False) for f,voiced in fret_tables.items())]:
            if sweep:
                sounding=tuple(p for p in voiced if p is not None and p>=0)
                if set(sounding)!=wanted:continue
                option=Choice(mode,selector,tuple(s for s,p in enumerate(voiced,1) if p is not None and p>=0),sounding,(),(),measured,sweep)
                found[mode,selector,option.action]=option;continue
            for anchor in anchors:
                option=_match(wanted,voiced,mode,selector,measured,False,anchor,resources)
                if option:found[mode,selector,option.action]=option
        return tuple(found.values())

    @lru_cache(maxsize=512)
    def pairs(wanted,main,bass,tones,sweep,approximate=False,target=()):
        w=set(wanted);m=set(main);b=set(bass);found={}
        recorded_tone,live_tone=tones
        def add(recorded,live):
            rp=set(recorded.pitches) if recorded else set();lp=set(live.pitches) if live else set()
            if rp&lp or not m.issubset(rp|lp):return
            missing=w-rp-lp;loss=sum(3 if p in b else 1 for p in missing)
            role_cost=len(m-lp)*10+len(b&lp)*3+len(lp-m-b)
            key=(recorded,live);found[key]=(recorded,live,loss,role_cost)
        if sweep:
            target=target or wanted
            for lane,tone in enumerate(tones):
                strums=list(choices(target,target,tone,sweep))
                if approximate:
                    for item in tables[tone].values():
                        option=sweep_choice(set(target),m.intersection(target),item,sweep,True)
                        if option and option.omitted:strums.append(option)
                for option in strums:
                    remain=tuple(sorted(w-set(option.pitches)))
                    fallback_picks=choices(remain,tuple(sorted(m-set(option.pitches))),tones[1-lane])
                    for picked in (None,*fallback_picks):
                        if lane==0:add(option,picked)
                        else:add(picked,option)
            # Prefer a real strum, but retain exact-pitch two-lane picking as
            # an explicitly reported fallback for unavailable or late strums.
            fallback=pairs(wanted,main,bass,tones,0)
            return tuple(sorted((*found.values(),*((a,b,loss,role+20) for a,b,loss,role in fallback)),key=lambda p:(p[2],p[3])))
        else:
            live_targets={wanted,tuple(sorted(m)) if m else (max(w),)}
            for target in live_targets:
                for live in choices(target,tuple(sorted(m)),live_tone):
                    remain=tuple(sorted(w-set(live.pitches)))
                    if remain:
                        for recorded in choices(remain,tuple(sorted(m-set(live.pitches))),recorded_tone):add(recorded,live)
                    add(None,live)
            recorded_targets={wanted,(min(w),)}
            for target in recorded_targets:
                for recorded in choices(target,(),recorded_tone):
                    remain=tuple(sorted(w-set(recorded.pitches)))
                    if remain:
                        for live in choices(remain,tuple(sorted(m-set(recorded.pitches))),live_tone):add(recorded,live)
                    add(recorded,None)
            if not m:add(None,None)
        ordered=sorted(found.values(),key=lambda item:(item[2],item[3]))
        # Retain alternatives with different selectors, plus single-lane
        # placements that can bridge a busy other lane. This is bounded search.
        kept=ordered[:128]
        extra=[p for p in ordered[128:] if p[0] is None or p[1] is None]
        return tuple(kept+extra[:64])

    @lru_cache(maxsize=32768)
    def advance(state,choice,at,tone):
        if choice is None:return state,0.0
        page=profile.pages[choice.selector];current=state.chord_page if choice.mode=='chord' else state.fret_page
        count=4 if choice.mode=='chord' else 5;distance=abs(page-current);moves=min(distance,count-distance)
        switch=state.mode!=choice.mode;select=switch or state.selector!=choice.selector;tone_change=state.tone!=tone
        prefetch=not switch and not moves and not tone_change and pointer_free(state.action,profile)
        preparation=(.18+timing.jitter_margin if switch else 0)+moves*timing.page_delay
        preparation+=(timing.selection_budget(0,prefetch) if select else 0)+(.09+timing.jitter_margin if tone_change else 0)
        required=transition_interval(state.action,choice.action,timing,tap,preparation,sustain_notes,gamepad_sweeps=profile.strum_input=='gamepad')
        if at-state.at+1e-9<required:return None
        attacks=dict(state.attacks)
        if any(attacks.get(resources[n],-1e9)+tap+timing.key_gap>at+1e-9 for n in choice.action):return None
        attacks={key:value for key,value in attacks.items() if value+tap+timing.key_gap>at+1e-9}
        attacks.update({resources[n]:at for n in choice.action})
        new=Lane(choice.mode,choice.selector,page if choice.mode=='chord' else state.chord_page,
                 page if choice.mode=='fret' else state.fret_page,tone,at,choice.action,tuple(sorted(attacks.items(),key=lambda x:str(x[0]))))
        return new,switch*10+moves*2+select*.3+tone_change

    descriptions=[]
    for event in events:
        if cancel is not None and cancel.is_set():raise InterruptedError('已取消自动拆分')
        wanted=tuple(sorted(set(event.notes)))
        tones=tuple(points[max(0,bisect_right(starts,event.start)-1)][1] for points,starts in zip(lane_timbres,tone_starts))
        event_rows=[role_rows[event.start,p] for p in wanted]
        main={r['pitch'] for r in event_rows if r['role'] in ('melody','keep','uncertain')}
        main.update(protected.get(event.start,()))
        strummed=set(sweep_targets.get(event.start,()))
        if event.start in sweeps and not marked and not protected.get(event.start):
            manually_protected={p for p in main if (event.start,p) in manual}
            if strummed:
                main=manually_protected | (main-strummed)
            elif not manually_protected:
                main=set()
        bass={r['pitch'] for r in event_rows if r['role']=='bass'}
        descriptions.append((event,main,bass,event_rows,tones))

    # A playable accompaniment now can leave both lanes busy at the next
    # melody attack. Before trimming the beam, reserve routes for that note.
    following_melody=[None]*len(descriptions)
    upcoming=None
    for index in range(len(descriptions)-1,-1,-1):
        following_melody[index]=upcoming
        event,main,_,_,tones=descriptions[index]
        if main:
            upcoming=(clock.seconds_at(event.start),tones,tuple(sorted(main)))
    # Both page wheels take at most two steps via the shortest route.
    preparation_window=(.27+2*timing.jitter_margin+2*timing.page_delay+
                        timing.selection_budget()+tap+timing.key_gap)

    initial=Lane(profile.initial_mode or 'fret',None,profile.initial_pages['chord'],profile.initial_pages['fret'],profile.initial_timbre)
    nodes=[Node((initial,initial),(0,0.0,0.0))]
    original_at={event.start:event for event in original.score.events}
    for index,(event,main,bass,event_rows,tones) in enumerate(descriptions):
        if cancel is not None and cancel.is_set():raise InterruptedError('已取消自动拆分')
        if progress and index%16==0:progress(index,len(events))
        wanted=tuple(sorted(set(event.notes)))
        options=pairs(wanted,tuple(sorted(main)),tuple(sorted(bass)),tones,sweeps.get(event.start,0),
                      event.start in optional_sweeps,sweep_targets.get(event.start,()))
        at=clock.seconds_at(event.start);best={}
        for node in nodes:
            for recorded,live,loss,role_cost in options:
                a=advance(node.lanes[0],recorded,at,tones[0]);b=advance(node.lanes[1],live,at,tones[1])
                if a is None or b is None:continue
                movement=a[1]+b[1]
                cost=(node.cost[0]+loss,node.cost[1]+(role_cost if strategy=='roles' else movement),
                      node.cost[2]+(movement if strategy=='roles' else role_cost))
                states=(a[0],b[0]);candidate=Node(states,cost,node,(recorded,live))
                if states not in best or cost<best[states].cost:best[states]=candidate
        if not best:
            names='、'.join(pitch_name(p) for p in sorted(main or set(wanted)))
            location=event_position(original.score,original_at[event.start])
            hint=pitch_range_hint(main) if main else ''
            raise ValueError(f'{location}（{at:.3f} 秒）：当前双路搜索未找到按原起拍保留 {names} 的可行方案。'+
                             (hint or '可能是短时间内换品／翻页过密或同拍需更多独立选区。')+
                             '可核对主旋律轨道、整体降低 BPM 或重新编配；不会单独降八度或静默删除主旋律。')
        ranked=sorted(best.values(),key=lambda node:node.cost)
        upcoming=following_melody[index]
        if upcoming and upcoming[0]-at<preparation_window:
            next_at,next_tones,next_pitches=upcoming
            next_options=pairs(next_pitches,next_pitches,(),next_tones,0)
            ready=[]
            for node in ranked:
                if any(advance(node.lanes[0],a,next_at,next_tones[0]) is not None and
                       advance(node.lanes[1],b,next_at,next_tones[1]) is not None for a,b,_,_ in next_options):
                    ready.append(node)
                    if len(ready)==beam:break
            if ready:ranked=ready
        nodes=ranked[:beam]
    node=min(nodes,key=lambda node:node.cost);route=[]
    while node.parent is not None:route.append(node.pair);node=node.parent
    route.reverse()
    lead=F(clock.time_signature[0]*4,clock.time_signature[1])*preroll_bars
    template=_shift_clock(clock,lead,preroll_bars);lanes=[[],[]];pitched=[[],[]];markers=[{},{}];details=[]
    end_hints={};approximated_sweeps=0;revoiced_sweeps=[]
    for hint in source.note_hints:
        key=hint.start,hint.pitch;end_hints[key]=max(end_hints.get(key,F(0)),hint.end)
    for (event,main,bass,event_rows,_),pair in zip(descriptions,route):
        output={}
        sweep_approx=event.start in sweeps and not any(choice is not None and choice.sweep for choice in pair)
        approximated_sweeps+=int(sweep_approx)
        for lane,choice in enumerate(pair):
            if choice is None:continue
            ends=[end_hints.get((event.start,p),event.start+event.beats) for p in choice.pitches]
            start=event.start+lead;label='recorded' if not lane else 'live'
            groups={}
            if choice.sweep or not sustain_notes:groups[max(ends)]=(list(choice.action),list(choice.pitches))
            else:
                for string,pitch,end in zip(choice.strings,choice.pitches,ends):
                    actions,pitches=groups.setdefault(end,([],[]));actions.append(string);pitches.append(pitch)
            for end,(actions,pitches) in groups.items():
                beats=end-event.start
                if not F(1,960)<=beats<=128:raise ValueError('拆分声部含超出 1/960～128 拍的长音，请先分段或修改延音')
                lanes[lane].append(GuitarEvent(tuple(actions),start,beats,choice.selector,choice.selector,1))
                pitched[lane].append(GuitarEvent(tuple(pitches),start,beats,'Zero',label,1))
                for p in pitches:output[p]=(label,choice.selector,end)
            if choice.sweep:markers[lane][start,label]=choice.sweep
            if choice.sweep:
                target=set(sweep_targets.get(event.start,event.notes));played=set(choice.pitches)
                if played!=target:
                    revoiced_sweeps.append({'beat':str(event.start),'selector':choice.selector,
                        'added':sorted(played-target),'omitted':sorted(target-played)})
        for row in event_rows:
            pitch=row['pitch'];kept=output.get(pitch);original_end=end_hints.get((event.start,pitch),event.start+event.beats)
            details.append({**row,'part':kept[0] if kept else 'omitted','selector':kept[1] if kept else '',
                            'output_beat':str(event.start+lead),'original_pitch':pitch-transpose,'output_pitch':pitch,
                            'main':pitch in main,'source_end':str(original_end),'grouped_end':str(kept[2]) if kept else None,
                            'reason':('原扫弦改为双路齐奏近似，保留音高和首拍，未复现逐弦滚动' if sweep_approx else '原起拍、精确音高保留') if kept else '标准 0～24 品音域之外的伴奏，未折八度' if not positions_for_pitch(pitch)
                                     else '两路选区／输入预算内未保留的伴奏或装饰音'})
    scores={};audio={};sweep_map={};clipped=0;extended=0;duration_grouped=0;actual_ends={}
    for index,label in enumerate(('recorded','live')):
        if not lanes[index]:scores[label]=None;audio[label]=None;sweep_map[label]={};continue
        game=[];sound=[];cursor=F(0)
        for event,note in zip(lanes[index],pitched[index]):
            if event.start>cursor:
                rest=GuitarEvent((),cursor,event.start-cursor,event.chord,'休止',1);game.append(rest);sound.append(replace(rest,chord='Zero'))
            game.append(event);sound.append(note);cursor=max(cursor,event.start+event.beats)
        end=max(template.beats,cursor)
        if cursor<end:
            rest=GuitarEvent((),cursor,end-cursor,game[-1].chord,'休止',1);game.append(rest);sound.append(replace(rest,chord='Zero'))
        lane_clock=template
        if source.lane_timbres:
            points=lane_timbres[index];tones=dict(points)
            _,starts=template._position_index
            changes=[];previous=points[0][1]
            changes_at={change.bar:change for change in template.bar_changes}
            for bar,at in enumerate(starts):
                change=changes_at.get(bar,MeasureChange(bar))
                tone=tones.get(at-lead,previous)
                if tone!=previous:changes.append(replace(change,timbre=tone))
                elif change.bpm is not None or change.meter is not None:changes.append(replace(change,timbre=None))
                previous=tone
            lane_clock=replace(template,default_timbre=points[0][1],bar_changes=tuple(changes))
        score=replace(lane_clock,events=split_long_rests(game),beats=end)
        plan=make_plan(score,part_profile(profile,score),tap=tap,timing=timing,sustain_notes=sustain_notes)
        first=min(step.at for step in plan if step.kind in ('page','chord_move','mode_down','timbre_down') or step.kind=='note' and step.data[1])
        if first<.25:raise ValueError('共同前奏不足以在开录／导入后准备首音，请增加共同前奏小节数；两份谱需要同步增加，不能只推迟一个声部')
        releases={}
        for step in plan:
            if step.kind=='note_release':
                for n in step.data[1]:releases[step.data[0],n]=step.at
            elif not sustain_notes and step.kind=='release' and type(step.data) is int:
                for n in score.events[step.data].notes:releases[step.data,n]=step.at
        normalized_sound=split_long_rests(sound)
        for event_index,(event,sounded) in enumerate(zip(score.events,normalized_sound)):
            if not event.notes:continue
            for n in event.notes:
                release=releases.get((event_index,n),score.seconds_at(event.start+event.beats))
                expected_end=score.seconds_at(event.start+event.beats)
                if release+1e-6<expected_end:clipped+=1
                if release>expected_end+1e-6:extended+=1
            if event.notes in ((101,),(102,)):
                sweep_release=releases.get((event_index,event.notes[0]),score.seconds_at(event.start+event.beats))
                for p in sounded.notes:actual_ends[label,event.start,p]=sweep_release-score.seconds_at(lead)
            else:
                for n,p in zip(event.notes,sounded.notes):actual_ends[label,event.start,p]=releases.get((event_index,n),score.seconds_at(event.start+event.beats))-score.seconds_at(lead)
        scores[label]=score;audio[label]=replace(lane_clock,events=split_long_rests(sound),beats=end);sweep_map[label]=markers[index]
    shared_end=max((s.beats for s in scores.values() if s is not None),default=template.beats)
    if shared_end>100000:raise ValueError('加入共同前奏后超过 100000 拍，请先分段拆分')
    scores={k:replace(s,beats=shared_end) if s is not None else None for k,s in scores.items()}
    audio={k:replace(s,beats=shared_end) if s is not None else None for k,s in audio.items()}
    duration_grouped=sum(row['part']!='omitted' and row['source_end']!=row['grouped_end'] for row in details)
    for row in details:
        key=row['part'],F(row['output_beat']),row['output_pitch']
        if key in actual_ends:
            actual=actual_ends[key];expected=clock.seconds_at(F(row['source_end']));row['actual_release_seconds']=actual
            if abs(actual-expected)>.001:
                row['hold_change_ms']=(actual-expected)*1000
                row['reason']+=f'；按键约提前松开 {abs(actual-expected)*1000:.0f} ms' if actual<expected else f'；按键约多保持 {(actual-expected)*1000:.0f} ms'
    kept=sum(row['part']!='omitted' for row in details);omitted=len(details)-kept
    moved=sum(row['main'] and row['part']=='recorded' for row in details)
    report={'version':1,'strategy':strategy,'source':original.name,'timing':timing.label,'transpose':transpose,
            'preroll_bars':preroll_bars,'preroll_beats':str(lead),'notes':details,'input_notes':len(details),'kept_notes':kept,
            'omitted_notes':omitted,'melody_to_recorded':moved,'grouped_duration_notes':duration_grouped,'shortened_holds':clipped,'extended_holds':extended,
            'approximated_sweeps':approximated_sweeps,'revoiced_sweeps':revoiced_sweeps,
            'reference_voicings':any(c is not None and c.mode=='chord' and not c.measured for pair in route for c in pair),
            'summary':f'{STRATEGIES[strategy]}：原谱 {len(details)} 音，保留 {kept} 音，省略伴奏／装饰音 {omitted} 音；共同前奏 {preroll_bars} 小节；生成 {sum(s is not None for s in scores.values())} 份谱，逐份通过“{timing.label}”输入检查。',
            'limitations':'使用有限宽度搜索；声部为规则推断，可指定 MIDI 主旋律轨道。数字键可分别安排松键，整扫按组松开。报告中的保持时长来自输入计划，游戏的实际音长由音色和衰减决定。'}
    if moved:report['summary']+=f' 为保留起拍，{moved} 个旋律音移入录音声部。'
    if approximated_sweeps:report['summary']+=f' {approximated_sweeps} 处扫弦受按法或时序限制，改为双路齐奏近似，未复现扫弦滚动。'
    if revoiced_sweeps:report['summary']+=f' {len(revoiced_sweeps)} 处扫弦保留方向并替换内声部。'
    if duration_grouped or clipped:report['summary']+=f' 同拍时值合组 {duration_grouped} 音，输入需提前松开 {clipped} 处。'
    if extended:report['summary']+=f' 最短触发时间增加按住时长 {extended} 处。'
    clock_error=0.0
    for score in scores.values():
        if score is None:continue
        origin=score.seconds_at(lead)
        for event in score.events:
            if event.notes:
                clock_error=max(clock_error,abs(score.seconds_at(event.start)-origin-clock.seconds_at(event.start-lead)))
    if clock_error>1e-7:raise ValueError(f'拆分时间轴校验未通过，最大起拍误差 {clock_error*1000:.3f} ms；未保存为合奏，请保留原谱并重新导入')
    report['clock_check']={'max_onset_error_ms':clock_error*1000,'common_preroll_seconds':template.seconds_at(lead),
                           'policy':'两轨共用原曲时钟；仅增加相同前奏，不逐音平移或累加等待'}
    if progress:progress(len(events),len(events))
    return SplitResult(scores,audio,sweep_map,report)
