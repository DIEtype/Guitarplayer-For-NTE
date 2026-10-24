"""Explainable voice-role inference and opt-in accompaniment thinning.

Roles are hypotheses, except for a user-selected melody channel/override.
Unknown voices are retained. No waveform or instrument identity is inferred.
"""
from bisect import bisect_left,bisect_right
from collections import defaultdict
from dataclasses import dataclass,replace
from fractions import Fraction as F
import heapq
from statistics import median

from .guitar import GuitarEvent,event_position
from .measures import score_layout,timbre_points

POLICIES={'all':'保留全部声部','sparse':'旋律＋稀疏低音','melody':'仅主旋律'}
ROLE_NAMES={'melody':'主旋律','bass':'低音伴奏','inner':'内声部','ornament':'装饰音候选','uncertain':'待核对','keep':'指定保留'}


@dataclass(frozen=True)
class NoteHint:
    start:F
    pitch:int
    end:F
    channel:int|None=None
    velocity:int|None=None
    string:int|None=None


def analyze_roles(source,melody_channel=None,overrides=(),cancel=None):
    if melody_channel is not None and (type(melody_channel) is not int or not 0<=melody_channel<=15):
        raise ValueError('主旋律通道应为 MIDI 1～16 通道')
    overrides={(F(at),pitch):role for at,pitch,role in overrides}
    if any(role not in ROLE_NAMES for role in overrides.values()):raise ValueError('未知声部标记')
    score=source.score;events=[e for e in score.events if e.notes];hints=defaultdict(list);channels=defaultdict(list)
    for hint in source.note_hints:
        hints[hint.start,hint.pitch].append(hint)
        if hint.channel is not None:channels[hint.channel].append(hint.pitch)
    if melody_channel is not None and melody_channel not in channels:raise ValueError('原谱没有所选主旋律通道的音符')
    medians={channel:median(pitches) for channel,pitches in channels.items() if len(pitches)>=2}
    inferred_channel=None
    if melody_channel is None and len(medians)>=2:
        ordered=sorted(medians,key=medians.get,reverse=True)
        if medians[ordered[0]]-medians[ordered[1]]>=12:inferred_channel=ordered[0]
    active_channel=melody_channel if melody_channel is not None else inferred_channel
    split_evidence=len(channels)>=2 or any(len(set(e.notes))>1 for e in events)
    times=[score.seconds_at(e.start) for e in events];tops=[max(e.notes) for e in events]
    from .ornaments import candidates
    ornaments={e.start for g in candidates(source) for e in g['events'] if e is not g['anchor']}
    rows=[];held=[];previous_lead=None;previous_at=None
    for index,event in enumerate(events):
        if cancel is not None and cancel.is_set():raise InterruptedError('已取消声部分析')
        at=times[index];wanted=sorted(set(event.notes));bottom=wanted[0]
        while held and held[0][1]<=event.start:heapq.heappop(held)
        held_top=-held[0][0] if held else None
        lo=bisect_left(times,at-.75);hi=bisect_right(times,at+.75)
        before=max(tops[lo:index],default=None);after=max(tops[index+1:hi],default=None)
        manual={p for p in wanted if overrides.get((event.start,p)) in ('melody','keep')}
        channel_notes={p for p in wanted if any(h.channel==active_channel for h in hints[event.start,p])} if active_channel is not None else set()
        eligible=[p for p in wanted if overrides.get((event.start,p)) not in ('bass','inner','ornament')]
        channel_notes.intersection_update(eligible)
        lead=max(channel_notes) if channel_notes else max(eligible or wanted)
        lead_basis='指定旋律通道' if melody_channel is not None and channel_notes else '通道音区分离推断' if channel_notes else '当前起音最高声部'
        # A high added voice need not displace a much more continuous lower
        # melody. Require support on both sides and leave ambiguity visible.
        if not channel_notes and previous_lead is not None and at-previous_at<=.75 and index+1<len(events):
            next_notes=events[index+1].notes
            smooth=min(eligible or wanted,key=lambda p:abs(p-previous_lead)+min(abs(p-n) for n in next_notes))
            if (lead-smooth>=12 and abs(smooth-previous_lead)<=5 and min(abs(smooth-n) for n in next_notes)<=5):
                lead=smooth;lead_basis='前后音程连续性推断'
        if manual:lead=max(manual);lead_basis='手动指定'
        low_fill=(len(wanted)==1 and before is not None and after is not None and
                  min(before,after)-lead>=12 and at-times[lo]<=.75)
        for pitch in wanted:
            pitch_hints=hints[event.start,pitch]
            explicit=overrides.get((event.start,pitch))
            selected_channel=melody_channel is not None and any(h.channel==melody_channel for h in pitch_hints)
            role='melody' if pitch==lead else 'inner';strength='较强';basis=lead_basis if pitch==lead else '同拍其他声部'
            if pitch==lead and not manual and not channel_notes:
                if low_fill or held_top is not None and held_top-pitch>=12:
                    role='bass';basis='前后高声部／仍持续的高声部下方补奏'
                    if not split_evidence or not low_fill:
                        role='uncertain';strength='待核对';basis='低音可能仍是旋律，声部分离依据不足'
                elif event.start in ornaments:
                    role='ornament';strength='待核对';basis='近邻时值／滚奏规则推断'
            if pitch!=lead and pitch==bottom and lead-pitch>=12:
                role='bass';basis='同拍最低声部与旋律相距至少八度'
            if active_channel is not None and not any(h.channel==active_channel for h in pitch_hints):
                if medians.get(active_channel,lead)-pitch>=12:
                    role='bass' if pitch==bottom else 'inner';basis='与高声部通道的音区关系'
            if selected_channel and channel_notes and pitch==max(channel_notes):role='melody';strength='指定';basis='指定旋律通道'
            if explicit:role=explicit;strength='指定';basis='手动指定'
            # A monophonic event's top note with no contrary evidence remains
            # protected; low pitch alone never means accompaniment.
            metadata=max(pitch_hints,key=lambda h:(h.velocity or 0,h.end),default=None)
            rows.append({'beat':str(event.start),'pitch':pitch,'role':role,'strength':strength,'basis':basis,
                         'channels':sorted({h.channel+1 for h in pitch_hints if h.channel is not None}),
                         'velocity':metadata.velocity if metadata else None,
                         'end':str(max((h.end for h in pitch_hints),default=event.start+event.beats)),
                         'string':metadata.string if metadata else None})
        if not low_fill and event.start not in ornaments:
            previous_lead=lead;previous_at=at
        for pitch in wanted:
            end=max((h.end for h in hints[event.start,pitch]),default=event.start+event.beats)
            heapq.heappush(held,(-pitch,end))
    return rows


def prepare_accompaniment(source,policy='all',distorted_only=True,melody_channel=None,overrides=(),cancel=None,*,filter_low_bass=False,transpose=0,_analysis_cache=None):
    if policy not in POLICIES:raise ValueError('未知伴奏保留策略')
    if cancel is not None and cancel.is_set():raise InterruptedError('已取消声部分析')
    if _analysis_cache is None:rows=analyze_roles(source,melody_channel,overrides,cancel)
    else:
        # Voice inference uses the original pitches, not the chosen key. Keep
        # only that inference across this search; range filtering is reapplied
        # per key, and mutable decisions/output never enter the shared rows.
        key=id(source),melody_channel,tuple(tuple(row) for row in overrides)
        cached=_analysis_cache.get(key)
        if cached is None or cached[0] is not source:
            cached=_analysis_cache[key]=(source,analyze_roles(source,melody_channel,overrides,cancel))
        rows=[{**row,'channels':list(row['channels'])} for row in cached[1]]
    score=source.score
    tones=timbre_points(score);tone_starts=[at for at,_ in tones];bars=score_layout(score);starts=[b.start for b in bars]
    by_at=defaultdict(list)
    for row in rows:by_at[F(row['beat'])].append(row)
    def applies(at):return not distorted_only or tones[max(0,bisect_right(tone_starts,at)-1)][1]=='distorted'
    # Keep one bass attack per notated beat. Prefer an on-beat, longer, stronger
    # source note, rather than arbitrarily keeping whichever was parsed first.
    buckets=defaultdict(list)
    for row in rows:
        at=F(row['beat'])
        if row['role']=='bass' and applies(at):
            index=max(0,bisect_right(starts,at)-1);bar=bars[index];unit=F(4,bar.meter[1]);offset=(at-bar.start)/unit
            buckets[index,int(offset)].append(row)
    chosen=set()
    for group in buckets.values():
        def rank(row):
            at=F(row['beat']);bar=bars[max(0,bisect_right(starts,at)-1)];unit=F(4,bar.meter[1])
            offset=(at-bar.start)/unit
            return (offset%1,-float(F(row['end'])-at),-(row['velocity'] or 0),row['pitch'])
        row=min(group,key=rank);chosen.add((row['beat'],row['pitch']))
    sweeps=dict(source.sweeps);events=[];protected=[];omitted=0;filtered=[]
    existing_protected=dict(source.protected_marks)
    for event in score.events:
        if not event.notes:events.append(event);continue
        kept=[];main=[]
        for row in by_at[event.start]:
            keep=True;why='原声部保留'
            if event.start in sweeps:why='明确标注的扫弦保留完整和弦'
            elif applies(event.start) and policy!='all' and row['strength']!='待核对':
                if row['role']=='inner':keep=False;why='按策略简化内声部'
                elif row['role']=='bass':
                    keep=policy=='sparse' and (row['beat'],row['pitch']) in chosen
                    why='每拍保留一处低音骨架' if keep else '按策略简化低音'
            hard=row['role'] in ('melody','keep') or row['pitch'] in existing_protected.get(event.start,())
            if hard:main.append(row['pitch']);keep=True;why='保护旋律／指定保留'
            row['range_filtered']=False
            if (filter_low_bass and keep and row['pitch']+transpose<40 and not hard
                    and row['strength']!='待核对' and event.start not in sweeps):
                keep=False;row['range_filtered']=True;why='低于 E2 的伴奏已过滤（移调后）'
                filtered.append({'beat':row['beat'],'pitch':row['pitch'],'target_pitch':row['pitch']+transpose,
                                 'role':row['role'],'reason':why,'position':event_position(score,event),
                                 'seconds':score.seconds_at(event.start)})
            row['kept']=keep;row['decision']=why
            if keep:kept.append(row['pitch'])
            else:omitted+=1
        if main:protected.append((event.start,tuple(sorted(main))))
        events.append(replace(event,notes=tuple(sorted(kept))))
    prepared=replace(source,score=replace(score,events=tuple(events)),protected_marks=tuple(protected))
    report={'policy':policy,'policy_name':POLICIES[policy],'distorted_only':distorted_only,
            'melody_channel':None if melody_channel is None else melody_channel+1,
            'overrides':[[str(at),pitch,role] for at,pitch,role in overrides],
            'intentional_omissions':omitted,'rows':rows,'range_filtered':filtered,
            'summary':f'声部策略：{POLICIES[policy]}'+('（仅失真段）' if distorted_only else '')+f'；主动简化 {omitted} 音；待核对音保留，原谱保留'}
    if filter_low_bass:report['summary']+=f'；其中 E2 以下伴奏过滤 {len(filtered)} 音，主旋律／待核对音／明确扫弦不删，详见“声部与去留”'
    return prepared,report


def translate_with_voices(source,rows,profile,timing,tap,reference,octaves,beam,*,transpose=0,cancel=None,
                          sustain_notes=False,ornaments=False,progress=None,accompaniment='all',
                          distorted_only=True,melody_channel=None,voice_overrides=(),avoid_bm_second=True,filter_low_bass=False,_analysis_cache=None):
    from .fingerstyle_translation import translate
    prepared,voices=prepare_accompaniment(source,accompaniment,distorted_only,melody_channel,voice_overrides,cancel,
                                        filter_low_bass=filter_low_bass,transpose=transpose,_analysis_cache=_analysis_cache)
    result,report=translate(prepared,rows,profile,timing,tap,reference,octaves,beam,transpose=transpose,cancel=cancel,
                            sustain_notes=sustain_notes,ornaments=ornaments,progress=progress,avoid_bm_second=avoid_bm_second)
    actual={F(row['beat']):row for row in report['events']}
    protected=dict(prepared.protected_marks);originals={e.start:e for e in source.score.events}
    filtered_at=defaultdict(list)
    for row in voices['range_filtered']:filtered_at[F(row['beat'])].append(row['target_pitch'])
    for row in report['events']:
        row['melody_pitches']=[p+transpose for p in protected.get(F(row['beat']),())]
        if F(row['beat']) in originals:row['source']=sorted(set(originals[F(row['beat'])].notes))
        row['range_filtered']=filtered_at[F(row['beat'])]
    moves=defaultdict(list)
    for row in report.get('ornaments',{}).get('changes',[]):
        for pitch in row['pitches']:moves[F(row['from']),pitch].append(row)
    for row in voices['rows']:
        if not row['kept']:row['output']=row['decision'] if row['range_filtered'] else '按策略简化';continue
        at=F(row['beat']);change=next(iter(moves.get((at,row['pitch']),())),None)
        if change and change['action']=='省略':row['output']='时序适配省略';continue
        target=F(change['to']) if change else at;output=actual.get(target,{})
        if row['pitch']+transpose in output.get('pitches',()):
            row['output']=change['action'] if change else '演奏保留'
        elif any(pair[0]==row['pitch']+transpose for pair in output.get('octaves',())):
            row['output']='伴奏八度替代'
        else:row['output']='转译省略'
    report['total_omitted_notes']=report['omitted_notes']+report.get('ornaments',{}).get('omitted_notes',0)
    report['voices']=voices;report['intentional_omissions']=voices['intentional_omissions']
    report['range_filter']={'enabled':filter_low_bass,'minimum_pitch':40,'transpose_semitones':transpose,'changes':voices['range_filtered']}
    report['range_filtered_notes']=len(voices['range_filtered'])
    report['range_pitch_loss']=sum(30 if row['role']=='bass' else 10 for row in voices['range_filtered'])
    report['melody_attacks']=len(prepared.protected_marks)
    report['summary']+='；'+voices['summary']
    return result,report
