"""Fit suspected MIDI ornaments to the input budget, keeping main onsets fixed.

MIDI supplies timing, not proof of musical intent. Detection is deliberately
bounded and every approximation is reported. Already playable groups are kept.
The original PitchSource and its editable document are never modified.
"""
from bisect import bisect_right
from dataclasses import replace
from fractions import Fraction as F

from .guitar import GuitarEvent
from .measures import timbre_points
from .timing import SAFE


def candidates(source,window=.100):
    score=source.score;sweeps=dict(source.sweeps)
    barriers=sorted(set([at for at,_ in score.tempo_map]+[at for at,_ in timbre_points(score)]+
                        [at for at,_ in source.chord_marks]))
    sounded=[e for e in score.events if e.notes];groups=[];group=[]
    def finish():
        if len(group)<2:return
        durations=[score.seconds_at(e.start+e.beats)-score.seconds_at(e.start) for e in group]
        short=min(durations)<=.080 and max(durations)>=min(durations)*1.8
        # Straight metrical fast passages are not classified as ornaments just
        # because their absolute duration is small.
        off_grid=any((e.start/F(1,4)).denominator!=1 for e in group)
        if not short and not off_grid:return
        if not short and len({p for e in group for p in e.notes})<sum(len(e.notes) for e in group):return
        if short:
            anchor=max(group,key=lambda e:(score.seconds_at(e.start+e.beats)-score.seconds_at(e.start),max(e.notes)))
        else:anchor=max(group,key=lambda e:(max(e.notes),-e.start))
        groups.append({'events':tuple(group),'anchor':anchor,'kind':'疑似装饰音' if short else '疑似滚奏／近同拍'})
    for event in sounded:
        if event.start in sweeps:
            finish();group=[];continue
        if group and (score.seconds_at(event.start)-score.seconds_at(group[0].start)>window+1e-9 or
                      bisect_right(barriers,event.start)!=bisect_right(barriers,group[0].start)):
            finish();group=[]
        group.append(event)
    finish()
    occupied={e.start for g in groups for e in g['events']}
    # A short low fill between two nearby high melody attacks must not have
    # the same priority as those attacks. Register/contour is a hypothesis,
    # explicitly reported; contiguous normal melody notes are left alone.
    for i,event in enumerate(sounded[:-1]):
        following=sounded[i+1]
        earlier=[e for e in sounded[max(0,i-3):i]
                 if score.seconds_at(event.start)-score.seconds_at(e.start)<=.5]
        if (not earlier or event.start in occupied or following.start in occupied or
            event.start in sweeps or following.start in sweeps):continue
        length=score.seconds_at(event.start+event.beats)-score.seconds_at(event.start)
        gap=score.seconds_at(following.start)-score.seconds_at(event.start)
        if (length<=.250 and gap<=.250 and max(following.notes)-max(event.notes)>=12 and
            max(max(e.notes) for e in earlier)-max(event.notes)>=12 and
            bisect_right(barriers,event.start)==bisect_right(barriers,following.start)):
            groups.append({'events':(event,following),'anchor':following,'kind':'疑似低音／内声部补奏'})
            occupied.update((event.start,following.start))
    groups.sort(key=lambda g:g['events'][0].start)
    protected=dict(source.protected_marks);safe=[]
    for group in groups:
        locked=[e for e in group['events'] if protected.get(e.start)]
        if len(locked)>1:continue
        if locked:group={**group,'anchor':locked[0]}
        safe.append(group)
    return safe


def _rewrite(source,changes):
    """Recreate rests from absolute times so text export also preserves beats."""
    score=source.score;attacks=[]
    for event in score.events:
        if not event.notes:continue
        updated=changes.get(event.start,event)
        if updated is not None:attacks.append(updated)
    attacks.sort(key=lambda e:e.start)
    events=[];cursor=F(0)
    def rest(end):
        nonlocal cursor
        while cursor<end:
            length=min(F(128),end-cursor)
            events.append(GuitarEvent((),cursor,length,'Zero','休止',1));cursor+=length
    for i,event in enumerate(attacks):
        rest(event.start)
        end=min(event.start+event.beats,attacks[i+1].start if i+1<len(attacks) else score.beats)
        events.append(replace(event,beats=end-event.start));cursor=end
    rest(score.beats)
    protected=[]
    for at,pitches in source.protected_marks:
        updated=changes.get(at)
        if at in changes and updated is None:continue
        protected.append((updated.start if updated is not None else at,pitches))
    return replace(source,score=replace(score,events=tuple(events)),protected_marks=tuple(protected))


def _bad_groups(groups,report):
    bad={F(row['beat']) for row in report['events'] if row.get('timing_conflict')}
    return [g for g in groups if any(e.start in bad for e in g['events'])]


def translate_ornaments(source,rows,profile,timing,tap,reference,octaves,beam,*,transpose=0,cancel=None,sustain_notes=True,progress=None,avoid_bm_second=True):
    from .fingerstyle_translation import translate
    timing=timing or SAFE
    def convert(item):
        return translate(item,rows,profile,timing,tap,reference,octaves,beam,transpose=transpose,
                         cancel=cancel,sustain_notes=sustain_notes,ornaments=False,progress=progress,avoid_bm_second=avoid_bm_second)
    result,report=convert(source);groups=candidates(source);records=[]
    selected=_bad_groups(groups,report)
    working=source;changes={};early=[]
    margin=timing.jitter_margin+.004
    # Backward fitting is bounded to 120 ms and cannot cross an earlier attack
    # or a tempo/tone/chord marker. This never steals time from the main note.
    rows_by_at={F(r['beat']):r for r in report['events']}
    starts=[e.start for e in source.score.events if e.notes]
    start_indices={at:i for i,at in enumerate(starts)}
    barriers=sorted(set([at for at,_ in source.score.tempo_map]+[at for at,_ in timbre_points(source.score)]+
                        [at for at,_ in source.chord_marks]))
    for group in selected:
        members=group['events'];anchor=group['anchor']
        if anchor is not members[-1]:continue
        following=source.score.seconds_at(anchor.start);proposed={};valid=True
        for i in range(len(members)-2,-1,-1):
            event=members[i];row=rows_by_at[members[i+1].start]
            required=row['required_ms']/1000
            at=min(source.score.seconds_at(event.start),following-required-margin)
            beat=source.score.beat_at_seconds(max(0,at)).limit_denominator(9600)
            if (at<0 or source.score.seconds_at(event.start)-at>.120+1e-9 or
                bisect_right(barriers,beat)!=bisect_right(barriers,event.start)):
                valid=False;break
            proposed[event.start]=replace(event,start=beat);following=at
        first_index=start_indices[members[0].start]
        if valid and first_index:
            previous=starts[first_index-1]
            required=rows_by_at[members[0].start]['required_ms']/1000
            valid=following>=source.score.seconds_at(previous)+required+margin-1e-9
        if valid and any(e.start!=at for at,e in proposed.items()):
            changes.update(proposed);early.append(group)
    if changes:
        working=_rewrite(source,changes);result,report=convert(working)
        bad={F(r['beat']) for r in report['events'] if r.get('timing_conflict')}
        reverted=False
        for group in early:
            positions={changes.get(e.start,e).start for e in group['events']}
            # A failed early attempt is reverted before considering collapse.
            if positions.intersection(bad):
                for event in group['events']:
                    if event.start in changes:changes.pop(event.start);reverted=True
            else:
                for event in group['events']:
                    moved=changes.get(event.start,event)
                    if moved.start!=event.start:
                        records.append(_record(group,event,'提前',moved.start,source.score))
        if reverted:
            working=_rewrite(source,changes);result,report=convert(working)
    selected=_bad_groups([g for g in groups if not any(r['anchor']==str(g['anchor'].start) for r in records)],report)
    collapsed=[]
    for group in selected:
        anchor=group['anchor'];others=[e for e in group['events'] if e is not anchor]
        # A high grace note must not become the protected melody through a
        # union. Preserve the main pitch and omit that grace only as a last step.
        union=tuple(sorted({p for e in group['events'] for p in e.notes}))
        merge=max(union)==max(anchor.notes)
        for event in others:changes[event.start]=None
        changes[anchor.start]=replace(anchor,notes=union if merge else anchor.notes)
        collapsed.append((group,merge))
    if collapsed:
        working=_rewrite(source,changes);result,report=convert(working)
        bad={F(row['beat']) for row in report['events'] if row.get('timing_conflict')}
        retry=False
        for group,merge in collapsed:
            anchor=group['anchor']
            # Adding a grace pitch can force a slower selector. Keep the main
            # voice alone if that happens, then run the full route again.
            if merge and anchor.start in bad:
                changes[anchor.start]=anchor;retry=True
        if retry:
            working=_rewrite(source,changes);result,report=convert(working)
        actual={F(row['beat']):set(row['pitches']) for row in report['events']}
        for group,_ in collapsed:
            anchor=group['anchor']
            for event in group['events']:
                if event is anchor:continue
                retained=actual.get(anchor.start,set())
                for pitch in event.notes:
                    action='合并' if pitch+transpose in retained and pitch not in anchor.notes else '省略'
                    records.append(_record(group,replace(event,notes=(pitch,)),action,anchor.start,source.score))
    from .guitar import event_position
    source_events={str(e.start):e for e in source.score.events if e.notes}
    for row in records:
        event=source_events[row['from']]
        row['position']=event_position(source.score,event)
    stats={key:sum(len(r['pitches']) for r in records if r['action']==key) for key in ('提前','合并','省略')}
    report['ornament_pitch_loss']=sum(10+(20 if r['kind']=='疑似低音／内声部补奏' else 0)
        for r in records if r['action']=='省略' for _ in r['pitches'])
    report['ornaments']={'policy':'主旋律准拍，先提前，来不及则合并或省略',
                         'detected_groups':len(groups),'advance_limit_ms':120,'changes':records,
                         'advanced_notes':stats['提前'],'merged_notes':stats['合并'],'omitted_notes':stats['省略'],
                         'detection':'时值／拍位推断，不能仅凭 MIDI 确认装饰音意图'}
    report['summary']+=f"；装饰／近同拍适配：提前 {stats['提前']} 音，合并 {stats['合并']} 音，省略 {stats['省略']} 音；主音起点保持，原谱保留"
    report['arranged_pitch_events']=[{'start':str(e.start),'beats':str(e.beats),'pitches':list(e.notes)}
                                    for e in working.score.events]
    return result,report


def _record(group,event,action,to,clock):
    return {'kind':group['kind'],'anchor':str(group['anchor'].start),'from':str(event.start),
            'to':str(to),'action':action,'pitches':list(event.notes),
            'shift_ms':(clock.seconds_at(to)-clock.seconds_at(event.start))*1000}
