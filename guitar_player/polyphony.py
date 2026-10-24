"""Exact-pitch fingerstyle approximation; melody onsets are immutable."""
from collections import defaultdict
from dataclasses import replace
from fractions import Fraction as F
from math import ceil

from .guitar import GuitarEvent,make_plan
from .game_profile import GameProfile,ROMAN
from .music import positions_for_pitch,fret_pitch
from .midi_import import convert_melody,_page
from .timing import SAFE


def _budget(before,after,timing):
    distance=abs(_page(before)-_page(after))
    return timing.change_budget(min(distance,5-distance)) if before!=after else timing.key_gap


def convert_fingerstyle(source,track_index,transpose=0,initial_page=0,timing=None,tap=None):
    timing=timing or SAFE;tap=timing.tap_ms/1000 if tap is None else tap
    base,_=convert_melody(source,track_index,transpose,True,initial_page,timing,tap)
    track=next(t for t in source.tracks if t.index==track_index)
    grouped=defaultdict(list)
    for note in track.notes:grouped[note.start].append(note)
    groups=sorted(grouped.items());layers=[];costs=[];candidates=[]
    for index,(at,notes) in enumerate(groups):
        pitches={n.pitch+transpose for n in notes};melody=max(pitches);bass=min(pitches)
        options=[]
        for melody_string,fret in positions_for_pitch(melody):
            strings=tuple(s for s in range(1,7) if fret_pitch(s,fret) in pitches)
            retained={fret_pitch(s,fret) for s in strings}
            missing=len(pitches-retained)+(5 if bass not in retained else 0)
            options.append((fret,strings,missing))
        candidates.append(options);current=[];parents=[]
        for fret,strings,missing in options:
            if not index:
                distance=abs(_page(fret)-initial_page)
                current.append((0,missing,min(distance,5-distance),fret));parents.append(-1);continue
            gap=base.seconds_at(at)-base.seconds_at(groups[index-1][0])-tap
            possibilities=[]
            for prev,(before,_,_) in enumerate(candidates[index-1]):
                old=costs[prev];distance=abs(_page(before)-_page(fret))
                possibilities.append((old[0]+int(gap+1e-9<_budget(before,fret,timing)),old[1]+missing,
                                      old[2]+min(distance,5-distance),old[3]+abs(before-fret)))
            parent=min(range(len(possibilities)),key=possibilities.__getitem__)
            current.append(possibilities[parent]);parents.append(parent)
        costs=current;layers.append(parents)
    selected=min(range(len(costs)),key=costs.__getitem__);route=[]
    for index in range(len(groups)-1,-1,-1):
        route.append(candidates[index][selected]);selected=layers[index][selected]
    route.reverse();actions=[];retained_count=0;rolled=0;omitted=0
    for index,((at,notes),(fret,strings,_)) in enumerate(zip(groups,route)):
        pitches={n.pitch+transpose for n in notes};kept={fret_pitch(s,fret) for s in strings}
        end=min(max(n.end for n in notes),groups[index+1][0] if index+1<len(groups) else base.beats)
        actions.append([at,end,fret,strings]);retained_count+=len(kept)
        bass=min(pitches);omitted+=len(pitches-kept)
        if bass in kept:continue
        # At most one delayed bass, within 180 ms, and only if the next melody is safe.
        next_at=base.seconds_at(groups[index+1][0]) if index+1<len(groups) else base.seconds
        next_fret=route[index+1][0] if index+1<len(route) else None
        choices=[]
        for string,bass_fret in positions_for_pitch(bass):
            seconds=base.seconds_at(at)+tap+_budget(fret,bass_fret,timing)+timing.jitter_margin
            beat=F(ceil(base.beat_at_seconds(seconds)*960-1e-7),960)
            seconds=base.seconds_at(beat)
            reserve=_budget(bass_fret,next_fret,timing) if next_fret is not None else 0
            if (beat-at>=F(1,960) and beat<end-F(1,960) and seconds-base.seconds_at(at)<=.180
                and seconds+tap+reserve+timing.jitter_margin<=next_at+1e-9):
                choices.append((seconds,bass_fret,string,beat))
        if choices:
            _,bass_fret,string,beat=min(choices)
            actions[-1][1]=beat;actions.append([beat,end,bass_fret,(string,)])
            rolled+=1;omitted-=1
    actions.sort();events=[];cursor=F(0)
    for at,end,fret,strings in actions:
        name=ROMAN[fret-1] if fret else 'Zero'
        if at>cursor:events.append(GuitarEvent((),cursor,at-cursor,name,'休止',1))
        events.append(GuitarEvent(strings,at,end-at,name,'+'.join(map(str,strings)),1));cursor=end
    result=replace(base,events=tuple(events),beats=max(base.beats,cursor))
    profile=GameProfile.frets();profile.initial_page=initial_page
    warning=''
    try:make_plan(result,profile,timing=timing,tap=tap)
    except ValueError as error:warning='；游戏演奏需调整：'+str(error)
    report=(f'指弹近似：{len(groups)} 个旋律起拍保持；同拍保留 {retained_count} 音；'
            f'延后补低音 {rolled} 音（最多 180 毫秒）；省略 {omitted} 个不同音高。原 MIDI 不改写；音长按下一起拍截断。')
    return result,report+warning
