"""Explicit, unmeasured game-input timing presets; musical timing stays intact."""
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class InputTiming:
    key:str
    label:str
    page_wait:float
    mouse_settle:float
    mouse_hold:float
    after_select:float
    page_hold:float
    tap_ms:int
    key_gap:float=.010
    jitter_margin:float=0
    cursor_prefetch:bool=False
    select_margin:float|None=None
    page_margin:float=0
    release_margin:float=0
    early_select:bool=False
    handoff_margin:float=0

    @property
    def minimum_select(self):return self.mouse_settle+self.mouse_hold

    @property
    def lead(self):return self.minimum_select+self.after_select+(self.jitter_margin if self.select_margin is None else self.select_margin)

    @property
    def page_delay(self):return self.page_wait+self.page_margin

    def change_budget(self,pages):return pages*self.page_delay+self.lead

    def selection_budget(self,pages=0,can_prefetch=False):
        return self.change_budget(pages)-(self.mouse_settle if not pages and can_prefetch and self.cursor_prefetch else 0)


SAFE=InputTiming('safe','兼容',.180,.075,.020,.025,.035,45)
FAST=InputTiming('fast','快速',.080,.030,.015,.015,.025,30)
TURBO=InputTiming('turbo','极速（试验）',.050,.010,.010,.010,.015,20)
ADAPTIVE=InputTiming('adaptive','稳拍自适应（推荐）',.100,.020,.040,.040,.040,40,.020,.012,True)
STEADY_FAST=InputTiming('steady_fast','稳拍快速（留出调度余量）',.080,.030,.015,.015,.025,30,
                        .010,.015,True,page_margin=.010,release_margin=.015)
SMOOTH_MIXED=replace(STEADY_FAST,key='smooth_mixed',label='扫拨衔接（试验）',
                     early_select=True,handoff_margin=.025)
PRESETS={item.key:item for item in (ADAPTIVE,STEADY_FAST,SMOOTH_MIXED,SAFE,FAST,TURBO)}
LABELS={item.label:item for item in PRESETS.values() if item.key!='smooth_mixed'}


def coordinated_timing(timing,enabled=True,release_ms=0,wake_ms=0):
    """Share host-side coordination across presets, keeping their input waits."""
    if not enabled:return replace(timing,early_select=False,handoff_margin=0)
    overhead=max(0,release_ms)+max(0,wake_ms)
    repeat=max(.015,min(.080,(overhead+5)/1000))
    handoff=max(.025,repeat)
    return replace(timing,early_select=True,handoff_margin=handoff,
                   release_margin=max(timing.release_margin,repeat))


def selector_timing(timing,profile):
    """A stick needs a sustained axis report, not cursor travel plus a click.

    Spend the preparation budget on holding the stick before the musical onset.
    This is an explicit user setting, not a measured game response time.
    """
    timing=timing or SAFE
    if getattr(profile,'selector_input','mouse')!='gamepad':return timing
    milliseconds=getattr(profile,'gamepad_select_ms',60)
    if type(milliseconds) is not int or not 40<=milliseconds<=200:
        raise ValueError('左摇杆选择保持应为 40～200 毫秒')
    return replace(timing,mouse_settle=0,mouse_hold=milliseconds/1000,
                   after_select=.020,cursor_prefetch=False)


def pointer_free(notes, profile):
    """Held string keys and controller sweeps leave the pointer available."""
    from .guitar import UP, DOWN
    return bool(notes) and all(n in range(1,7) or
                              (getattr(profile,'strum_input','mouse')=='gamepad' and n in (UP,DOWN))
                              for n in notes)


def input_resource(note, profile):
    from .guitar import UP, DOWN
    if getattr(profile,'strum_input','mouse')=='gamepad' and note in (UP,DOWN):return 'right_stick'
    if getattr(profile,'string_input','keyboard')=='gamepad' and getattr(profile,'dpad_exclusive',False):
        if note in (1,4):return 'dpad_vertical'
        if note in (2,3):return 'dpad_horizontal'
    return note


def validate_resources(notes,profile):
    resources=[input_resource(n,profile) for n in notes]
    if len(resources)!=len(set(resources)):
        raise ValueError('已启用十字键互斥：同拍相反方向（1／4 弦或 2／3 弦）冲突，请关闭互斥、改用键盘或重新转译')


def transition_interval(before,after,timing,tap,preparation=0,independent=False,gamepad_sweeps=False):
    """Minimum onset interval, distinct from the length of a sounding note.

    Number keys latch their pitch on key-down. With selective releases, another
    key and selector preparation need not wait for the old key's tap to end.
    Mouse sweeps own the pointer; controller sweeps use a shared stick axis.
    The legacy release-all path stays serial.
    Non-adjacent repeats also need checking against their own last key-down.
    """
    if not before:return 0.0
    from .guitar import UP, DOWN
    if not independent or any(n not in range(1,7) and not (gamepad_sweeps and n in (UP,DOWN)) for n in before):
        return tap+max(preparation,timing.key_gap)
    control=preparation+timing.key_gap if preparation else 0.0
    shared_stick=gamepad_sweeps and any(n in (UP,DOWN) for n in before) and any(n in (UP,DOWN) for n in after)
    repeat=tap+timing.key_gap if set(before).intersection(after) or shared_stick else 0.0
    return max(control,repeat)
