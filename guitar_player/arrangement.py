"""Exact-pitch fingering selection, prioritizing executable transitions."""
from dataclasses import dataclass,replace

from .game_profile import ROMAN
from .music import positions_for_pitch,fret_pitch


@dataclass(frozen=True)
class PitchNote:
    pitch:int
    start:object
    end:object


def page(fret):return (fret-1)//6 if fret else 4


def find_fingerings(notes,clock,initial_page,timing,tap,capo=0):
    candidates=[[position for position in positions_for_pitch(note.pitch) if position[1]>=capo] for note in notes]
    if any(not options for options in candidates):raise ValueError('存在超出标准定弦 0～24 品的音符')
    if not notes:return []
    layers=[];previous_costs=[]
    for index,options in enumerate(candidates):
        gap=(clock.seconds_at(notes[index].start)-clock.seconds_at(notes[index-1].start)-tap) if index else 0
        costs=[];parents=[]
        for string,fret in options:
            if not index:
                distance=abs(page(fret)-initial_page);pages=min(distance,5-distance)
                costs.append((0,pages,0,.015*fret));parents.append(-1);continue
            scores=[]
            for before,(prev_string,prev_fret) in enumerate(candidates[index-1]):
                distance=abs(page(fret)-page(prev_fret));pages=min(distance,5-distance)
                required=timing.selection_budget(pages,not pages) if fret!=prev_fret else timing.key_gap
                previous=previous_costs[before]
                scores.append((previous[0]+int(gap+1e-9<required),previous[1]+pages,
                               previous[2]+int(fret!=prev_fret),previous[3]+.04*abs(fret-prev_fret)+.1*abs(string-prev_string)+.015*fret))
            parent=min(range(len(scores)),key=scores.__getitem__)
            costs.append(scores[parent]);parents.append(parent)
        layers.append(parents);previous_costs=costs
    selected=min(range(len(previous_costs)),key=previous_costs.__getitem__);route=[]
    for index in range(len(notes)-1,-1,-1):
        route.append(candidates[index][selected]);selected=layers[index][selected]
    return list(reversed(route))


def transition_count(score,initial_page):
    count=0;current=initial_page
    for event in score.events:
        if not event.notes:continue
        fret=0 if event.chord=='Zero' else ROMAN.index(event.chord)+1
        target=page(fret);distance=abs(target-current);count+=min(distance,5-distance);current=target
    return count


def adapt_score(score,initial_page,timing,tap):
    from .timing import ADAPTIVE
    if any(len(e.notes)>1 or any(n not in range(1,7) for n in e.notes) for e in score.events):
        return score,'多弦齐奏／扫弦保留原指法；实际输入时序保护仍生效',0
    sounded=[e for e in score.events if e.notes]
    notes=[PitchNote(fret_pitch(e.notes[0],0 if e.chord=='Zero' else ROMAN.index(e.chord)+1),e.start,e.start+e.beats) for e in sounded]
    route=iter(find_fingerings(notes,score,initial_page,timing or ADAPTIVE,tap,score.capo))
    events=[];changed=0
    for event in score.events:
        if not event.notes:events.append(event);continue
        string,fret=next(route);chord=ROMAN[fret-1] if fret else 'Zero'
        changed+=int(event.notes!=(string,) or event.chord!=chord)
        events.append(replace(event,notes=(string,),chord=chord,source_spans=()))
    result=replace(score,events=tuple(events))
    before=transition_count(score,initial_page);after=transition_count(result,initial_page)
    return result,f'标准定弦等音适配：翻页 {before} → {after} 次，改用 {changed} 处指法；音高、起音与时值保持',changed
