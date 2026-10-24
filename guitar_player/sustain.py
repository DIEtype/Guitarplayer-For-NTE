"""Keep string keys and sweep buttons down until their musical note ends.

Selection and page-key preparation keep their existing budgets. Note releases
are separate, so preparing a later selector cannot release an earlier string.
"""
from bisect import bisect_right
from .guitar import Step, UP, DOWN
from .timing import SAFE,input_resource


def sustain_actions(score, profile, plan, tap, timing=None):
    timing=timing or SAFE
    release_margin=max(0,timing.release_margin)
    eligible={index for index,event in enumerate(score.events)
              if event.notes and all(note in (*range(1,7),UP,DOWN) for note in event.notes)}
    if not eligible:
        return plan
    ends={};next_attack={};next_sweep=float('inf');next_event=None
    pointer_moves=[step.at for step in plan if step.kind=='chord_move']
    for index in range(len(score.events)-1,-1,-1):
        event=score.events[index]
        if not event.notes:
            continue
        start=score.seconds_at(event.start)
        end=score.seconds_at(event.start+event.beats)
        sweep=any(note in (UP,DOWN) for note in event.notes)
        handoff=(profile.strum_input=='gamepad' and timing.handoff_margin and next_event is not None
                 and sweep!=next_event[1] and end<=next_event[0]+1e-9)
        for note in event.notes:
            release=start+tap
            if index in eligible:
                release=max(release,min(end,next_attack.get(input_resource(note,profile),float('inf'))-timing.key_gap-release_margin))
                if handoff:
                    # Move a naturally ending release off the next attack's
                    # deadline. Explicitly overlapping voices keep their hold.
                    release=min(release,max(start+tap,next_event[0]-timing.key_gap-timing.handoff_margin))
                if note in (UP,DOWN):
                    # Held buttons must be up before moving to a selector or
                    # moving back to the strum area for another sweep.
                    position=bisect_right(pointer_moves,start+1e-9)
                    move=pointer_moves[position] if position<len(pointer_moves) else float('inf')
                    pointer_limit=move-timing.key_gap if profile.strum_input=='mouse' else float('inf')
                    reserved=max(start+tap,next_sweep-timing.key_gap-release_margin)
                    release=min(release,next_sweep-timing.key_gap,reserved,pointer_limit)
                    if release<start+tap-1e-9:
                        raise ValueError('扫弦保持与鼠标选择操作冲突，请增加间隔或降低 BPM')
            ends[index,note]=release
            next_attack[input_resource(note,profile)]=start
        if any(note in (UP,DOWN) for note in event.notes):next_sweep=start
        next_event=(start,sweep)
    steps=[]
    for step in plan:
        if step.kind=='release' and type(step.data) is int:
            index=step.data
            steps.append(Step(step.at,'note_settled',index))
            releases={}
            for note in score.events[index].notes:
                releases.setdefault(ends[index,note],[]).append(note)
            for at,notes in releases.items():
                steps.append(Step(at,'note_release',(index,tuple(notes))))
        elif step.kind!='end':
            steps.append(step)
    # At a shared boundary, let the previous key up precede the next key down.
    steps.sort(key=lambda step:(step.at,0 if step.kind=='note_release' else 1))
    end=max(score.seconds,max((step.at for step in steps),default=0))
    steps.append(Step(end,'end'))
    return tuple(steps)


def sweep_adjustments(score, plan, timing=None):
    """Describe sweep holds shortened for selection or input handoff."""
    timing=timing or SAFE
    result=[]
    for step in plan:
        if step.kind!='note_release' or not any(n in (UP,DOWN) for n in step.data[1]):
            continue
        index=step.data[0];event=score.events[index]
        shortened=score.seconds_at(event.start+event.beats)-step.at
        if shortened>timing.key_gap+1e-6:result.append((index,shortened))
    return tuple(result)
