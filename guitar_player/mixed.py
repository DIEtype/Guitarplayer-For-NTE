"""A measured Z-switching plan with independent page memory in each mode."""
from .game_profile import CHORD_NAMES,ROMAN
from .guitar import Step,GuitarError,event_position
from .timing import SAFE,transition_interval,pointer_free,input_resource,validate_resources,selector_timing
from .measures import timbre_points


def selector_mode(name):
    if name in CHORD_NAMES:return 'chord'
    if name in (*ROMAN,'Zero'):return 'fret'
    raise GuitarError('未知游戏选择项：'+str(name))


def make_mixed_plan(score,profile,tap=.045,timing=None,independent=False):
    timing=selector_timing(timing,profile)
    if score.capo:raise GuitarError('混合谱暂不支持 Capo，请使用实际品位')
    mode=profile.initial_mode or 'fret';pages=dict(profile.initial_pages)
    selected={'chord':None,'fret':None};previous=-1e9;steps=[];previous_notes=()
    tones=list(timbre_points(score));tone=profile.initial_timbre
    previous_start=-1e9;key_attacks={}
    for index,event in enumerate(score.events):
        validate_resources(event.notes,profile)
        start=score.seconds_at(event.start)
        if not event.notes:
            steps.append(Step(start,'note',(index,())));continue
        target=selector_mode(event.chord)
        if event.chord not in profile.chords:raise GuitarError('布局缺少 '+event.chord)
        count=4 if target=='chord' else 5
        page=profile.pages[event.chord]
        if not 0<=page<count:raise GuitarError('选择项页码无效：'+event.chord)
        forward=(page-pages[target])%count;back=(pages[target]-page)%count
        delta=forward if forward<=back else -back
        switch=target!=mode
        select=switch or selected[target]!=event.chord
        desired=next((value for at,value in reversed(tones) if at<=event.start),score.default_timbre)
        tone_change=desired!=tone
        tone_lead=.09+timing.jitter_margin if tone_change else 0
        prefetch=not switch and not delta and not tone_change and pointer_free(previous_notes,profile)
        selection=timing.selection_budget(0,prefetch) if select else 0
        required=(.18+timing.jitter_margin if switch else 0)+abs(delta)*timing.page_delay+selection+tone_lead
        interval=transition_interval(previous_notes,event.notes,timing,tap,required,independent,
                                     gamepad_sweeps=profile.strum_input=='gamepad')
        earliest=previous_start+interval
        if independent:
            earliest=max(earliest,*(key_attacks.get(input_resource(n,profile),-1e9)+tap+timing.key_gap for n in event.notes))
        if start+1e-9<earliest:
            raise GuitarError(f'第 {index+1} 个事件（{event_position(score,event)}）：切模式／翻页／选品需 {max(required,timing.key_gap):.3f} 秒，可用 {max(0,start-previous):.3f} 秒；MIDI 近同拍可先整理，独立快速音请留出休止或降低 BPM')
        at=start-required
        # With latched notes, prepare the next selector during the old hold.
        # Keep Z/V changes on their existing schedule and never delay a note.
        ahead=timing.early_select and independent and select and not switch and not tone_change and pointer_free(previous_notes,profile)
        if ahead:at=min(at,previous_start+tap+timing.key_gap)
        if tone_change:
            steps.extend((Step(at,'timbre_down',desired),Step(at+.04,'timbre_up')))
            at+=tone_lead;tone=desired
        if switch:
            steps.extend((Step(at,'mode_down',target),Step(at+.05,'mode_up',target)))
            at+=.18+timing.jitter_margin;mode=target
        for offset in range(abs(delta)):
            new_page=(pages[target]+(offset+1)*(1 if delta>0 else -1))%count
            steps.extend((Step(at,'page',('T' if delta>0 else 'R',new_page)),Step(at+timing.page_hold,'release')))
            at+=timing.page_delay
        if select:
            selection_at=at if ahead else start-timing.lead
            steps.extend((Step(selection_at,'chord_move',event.chord),
                          Step(selection_at+timing.mouse_settle,'chord_down',event.chord),
                          Step(selection_at+timing.minimum_select,'chord_up')))
        steps.extend((Step(start,'note',(index,event.notes)),Step(start+tap,'release',index)))
        pages[target]=page;selected[target]=event.chord;previous=start+tap;previous_notes=event.notes
        previous_start=start
        for n in event.notes:key_attacks[input_resource(n,profile)]=start
    steps.append(Step(max(score.seconds,previous),'end'))
    return tuple(sorted(steps,key=lambda s:s.at))
