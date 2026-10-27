"""Chord-and-string notation, independent of physical game keys."""
from dataclasses import dataclass, replace
from fractions import Fraction
import re
from bisect import bisect_left,bisect_right
from functools import cached_property

UP, DOWN = 101, 102
CHORDS = ("G", "Am", "Bm", "C", "D", "Em")
DEFAULT_TEXT = """// 分解和弦练习：数字是吉他弦号，不是游戏按键
// 6 = 最粗的低音弦（游戏键 1）；1 = 最细的高音弦（游戏键 6）
{G}  6:1/2 3:1/2 2:1/2 3:1/2 | 1 3 2 3
{Em} 6:1/2 3:1/2 2:1/2 3:1/2 | 1 3 2 3
{C}  5:1/2 3:1/2 2:1/2 3:1/2 | 1 3 2 3
{D}  4:1/2 3:1/2 2:1/2 3:1/2 | 1 3 2 3
// 扫弦：↓ 从 6 弦到 1 弦；↑ 从 1 弦到 6 弦
{G} ↓:2 ↑:1/2 ↓:1/2 ↑ | 0:2
"""


class GuitarError(ValueError):
    pass


@dataclass(frozen=True)
class GuitarEvent:
    notes: tuple[int, ...]
    start: Fraction
    beats: Fraction
    chord: str
    label: str
    line: int
    source_spans: tuple = ()


def split_long_rests(events):
    """Keep silence exact while respecting the text formats' event limits.

    Sounding notes are left intact: splitting one would retrigger its attack.
    """
    result=[];minimum=Fraction(1,960);maximum=Fraction(128)
    for event in events:
        if event.notes or event.beats<=maximum:
            result.append(event);continue
        if event.start+event.beats>100000:raise GuitarError('曲谱超过 100000 拍，请先分段')
        cursor=event.start;end=event.start+event.beats
        while cursor<end:
            remaining=end-cursor;length=min(maximum,remaining)
            # Do not create an invalid tiny remainder from a valid long gap.
            if 0<remaining-length<minimum:length=remaining-minimum
            result.append(replace(event,start=cursor,beats=length));cursor+=length
    return tuple(result)


@dataclass(frozen=True)
class GuitarScore:
    events: tuple[GuitarEvent, ...]
    beats: Fraction
    bpm: float
    tempo_map: tuple = ()
    time_signature: tuple = (4,4)
    measures: int = 0
    bar_changes: tuple = ()
    default_timbre: str = 'clean'
    base_tempo_map: tuple | None = None
    capo: int = 0
    chord_changes: tuple = ()

    @cached_property
    def _tempo_index(self):
        """Prefix times belong to this immutable score, rebuilt on replace()."""
        starts=[Fraction(0)];seconds=[0.0];tempos=[500000]
        for at,tempo in self.tempo_map:
            at=Fraction(at)
            total=seconds[-1]+float(at-starts[-1])*tempos[-1]/1_000_000
            if at==starts[-1]:tempos[-1]=tempo
            else:starts.append(at);seconds.append(total);tempos.append(tempo)
        return tuple(starts),tuple(seconds),tuple(tempos)

    @cached_property
    def _position_index(self):
        """Share measure lookup across diagnostics for this immutable score."""
        from .measures import score_layout
        layout=score_layout(self)
        return layout,tuple(bar.start for bar in layout)

    def seconds_at(self,beat):
        if not self.tempo_map:
            return float(beat)*60/self.bpm
        beat=Fraction(beat);starts,seconds,tempos=self._tempo_index
        index=max(0,bisect_right(starts,beat)-1)
        return seconds[index]+float(beat-starts[index])*tempos[index]/1_000_000

    @property
    def seconds(self):
        return self.seconds_at(self.beats)

    def beat_at_seconds(self,seconds):
        elapsed=max(0,float(seconds))
        if not self.tempo_map:return Fraction(elapsed*self.bpm/60).limit_denominator(9600)
        starts,times,tempos=self._tempo_index;index=max(0,bisect_right(times,elapsed)-1)
        return starts[index]+Fraction((elapsed-times[index])*1_000_000/tempos[index]).limit_denominator(9600)


def apply_capo(score,capo):
    """Keep the written relative frets, convert once to physical game frets."""
    if type(capo) is not int or not 0<=capo<=24:raise GuitarError('Capo 应为 0～24 品')
    if capo==score.capo:return score
    from .game_profile import ROMAN
    events=[]
    for event in score.events:
        if not event.notes:events.append(event);continue
        if event.chord!='Zero' and event.chord not in ROMAN:raise GuitarError('Capo 使用品格模式的六线谱或品位指令')
        physical=(0 if event.chord=='Zero' else ROMAN.index(event.chord)+1)-score.capo+capo
        if not 0<=physical<=24:raise GuitarError(f'Capo {capo} 换算后超出游戏 0～24 品，请在图形编谱中设置 Capo 以尝试等音指法')
        events.append(replace(event,chord=ROMAN[physical-1] if physical else 'Zero'))
    return replace(score,events=tuple(events),capo=capo)


def parse_guitar(text, bpm=100):
    if not 20 <= bpm <= 360:
        raise GuitarError("速度应在 20～360 BPM 之间")
    events, cursor, chord, pending = [], Fraction(0), "", False
    pattern = re.compile(r"\{[^}\n]*\}|\[[^\]\n]*\](?::[^\s|]+)?|[^\s|]+|\|")
    for line_number, original in enumerate(text.splitlines(), 1):
        line = original.split("//", 1)[0]
        for match in pattern.finditer(line):
            token = match.group()
            try:
                if match.end() < len(line) and not line[match.end()].isspace() and line[match.end()] != "|":
                    raise GuitarError("音符、和弦指令之间请用空格分隔")
                if token == "|":
                    continue
                if token.startswith("{") and token.endswith("}"):
                    name = token[1:-1].strip()
                    if not re.fullmatch(r"Zero|[IVX]+|[A-G][#b]?[A-Za-z0-9/#+()\-]*", name):
                        raise GuitarError("和弦名格式不正确，例如 {G}、{Am}")
                    if pending:
                        raise GuitarError("连续和弦指令之间需要拨弦、扫弦或休止符")
                    chord, pending = name, True
                    continue
                if token == "-":
                    if not events or pending:
                        raise GuitarError("延长符 - 应紧跟拨弦、扫弦或休止符")
                    events[-1] = replace(events[-1], beats=events[-1].beats + 1)
                    cursor += 1
                    continue
                parts = token.rsplit(":", 1)
                try:
                    beats = Fraction(parts[1]) if len(parts) == 2 else Fraction(1)
                except (ValueError, ZeroDivisionError):
                    raise GuitarError("拍数应为正数或分数，例如 :2、:1/2、:1.5") from None
                if not Fraction(1, 960) <= beats <= 128:
                    raise GuitarError("拍数应在 1/960～128 之间")
                body = parts[0]
                if body == "0":
                    notes = ()
                elif body in ("↑", "U", "u"):
                    notes = (UP,)
                elif body in ("↓", "D", "d"):
                    notes = (DOWN,)
                elif re.fullmatch(r"[1-6]", body):
                    notes = (int(body),)
                elif body.startswith("[") and body.endswith("]"):
                    members = body[1:-1].split()
                    if not members or any(not re.fullmatch(r"[1-6]", s) for s in members):
                        raise GuitarError("同时拨弦请写为 [6 3 2]:1/2，弦号为 1～6")
                    notes = tuple(sorted(set(map(int, members))))
                else:
                    raise GuitarError(f"无法识别“{token}”；使用弦号 1～6、↑、↓、0 或 {{G}}")
                if not chord:
                    raise GuitarError("请先指定和弦，例如 {G} 6 5 4 3")
                events.append(GuitarEvent(notes, cursor, beats, chord, token, line_number))
                cursor += beats
                pending = False
                if len(events) > 50000 or cursor > 100000:
                    raise GuitarError("曲谱过长，请分段演奏")
            except GuitarError as error:
                raise GuitarError(f"第 {line_number} 行，第 {match.start() + 1} 列：{error}") from None
    if pending:
        raise GuitarError("最后一个和弦指令后面还需要拨弦、扫弦或休止符")
    if not events:
        raise GuitarError("先输入曲谱，例如 {G} 6 5 4 3 | ↓:2 ↑ 0")
    return GuitarScore(tuple(events), cursor, float(bpm))


@dataclass(frozen=True)
class Step:
    at: float
    kind: str
    data: object = None


def event_position(score,event):
    layout,starts=score._position_index
    index=max(0,bisect_right(starts,event.start)-1);bar=layout[index]
    beat=(event.start-bar.start)/Fraction(4,bar.meter[1])+1
    return f'第 {index+1} 小节第 {float(beat):.3f} 拍'


def make_plan(score, profile, gate=0.72, lead=None, tap=0.045,timing=None,sustain_notes=False):
    """Schedule chord clicks ahead of the beat, after the previous note release."""
    from .timing import selector_timing
    timing=selector_timing(timing,profile)
    if profile.mode=='mixed':
        from .mixed import make_mixed_plan
        plan=make_mixed_plan(score,profile,tap,timing,independent=sustain_notes)
        if sustain_notes:
            from .sustain import sustain_actions
            return sustain_actions(score,profile,plan,tap,timing)
        return plan
    from .timing import SAFE,pointer_free,input_resource,validate_resources
    timing=timing or SAFE
    lead=timing.lead if lead is None else lead
    if lead+1e-9<timing.minimum_select:raise GuitarError('选品准备时间小于选择输入所需的保持时间')
    steps, previous_chord, previous_release = [], None, -1e9
    previous_notes=()
    previous_start=-1e9;key_attacks={}
    from .measures import timbre_points
    tone_points=timbre_points(score);tone_starts=[score.seconds_at(at) for at,_ in tone_points]
    current_page = profile.initial_page
    for index, event in enumerate(score.events):
        validate_resources(event.notes,profile)
        start=score.seconds_at(event.start)
        duration=score.seconds_at(event.start+event.beats)-start
        if not event.notes:
            steps.append(Step(start,"note",(index,())))
            continue
        if event.chord not in profile.chords:
            raise GuitarError(f"第 {event.line} 行：尚未配置和弦 {event.chord}")
        earliest=(max((key_attacks.get(input_resource(n,profile),-1e9)+tap+timing.key_gap for n in event.notes),default=-1e9)
                  if sustain_notes else previous_release+timing.key_gap)
        if previous_notes and not pointer_free(previous_notes,profile):
            earliest=max(earliest,previous_release+timing.key_gap)
        if start+1e-9<earliest:
            gap=start-(previous_release-tap)
            raise GuitarError(f'第 {event.line} 行、第 {index+1} 个事件（{event_position(score,event)}）：连续拨弦过密，实际起音间隔 {gap*1000:.1f} 毫秒，需保留 {tap*1000:.0f} 毫秒按下和 {timing.key_gap*1000:.0f} 毫秒松开间隔；MIDI 错位可先用“近同拍整理”，独立快速音需降低 BPM 或改编')
        if event.chord != previous_chord:
            target_page = profile.pages[event.chord]
            forward = (target_page - current_page) % profile.page_count
            backward = (current_page - target_page) % profile.page_count
            page_delta = forward if forward <= backward else -backward
            required = lead + abs(page_delta) * timing.page_delay
            # Number keys and controller sweeps can keep sounding while the
            # pointer selects another pitch. Mouse sweeps must release first.
            preparation_after=(previous_start+timing.key_gap if sustain_notes and previous_notes
                               and pointer_free(previous_notes,profile) else previous_release)
            available = start - preparation_after
            if page_delta and available+1e-9 < required:
                raise GuitarError(f"第 {event.line} 行、第 {index+1} 个事件：翻页需要 {required:.2f} 秒间隔，当前可用 {max(0,available):.3f} 秒（{timing.label}档）；可选择更快输入档、调整指法或降低 BPM")
            prefetch=timing.cursor_prefetch and not page_delta and pointer_free(previous_notes,profile)
            selection_gap=lead-(timing.mouse_settle if prefetch else 0)
            if available+1e-9 < selection_gap:
                raise GuitarError(f"第 {event.line} 行、第 {index+1} 个事件（{event_position(score,event)}）：品位切换间隔不足 {selection_gap*1000:.0f} 毫秒（含确认等待，{timing.label}档），实际起音间隔 {(start-previous_start)*1000:.1f} 毫秒；可用装饰音适配／近同拍整理、调整指法或 BPM")
            preparation_at=start-required
            same_tone=bisect_right(tone_starts,previous_start)==bisect_right(tone_starts,start)
            if timing.early_select and sustain_notes and same_tone and pointer_free(previous_notes,profile):
                preparation_at=min(preparation_at,previous_start+tap+timing.key_gap)
            for page_step in range(abs(page_delta)):
                at = preparation_at + page_step * timing.page_delay
                page = (current_page + (page_step + 1) * (1 if page_delta > 0 else -1)) % profile.page_count
                steps.append(Step(at, "page", ("T" if page_delta > 0 else "R", page)))
                steps.append(Step(at + timing.page_hold, "release"))
            selection_at=preparation_at+abs(page_delta)*timing.page_delay
            steps.append(Step(selection_at,"chord_move",event.chord))
            steps.append(Step(selection_at+timing.mouse_settle,"chord_down",event.chord))
            steps.append(Step(selection_at+timing.minimum_select,"chord_up"))
            previous_chord = event.chord
            current_page = target_page
        steps.append(Step(start, "note", (index, event.notes)))
        release = start + tap
        steps.append(Step(release, "release",index))
        previous_release = release
        previous_start=start
        for n in event.notes:key_attacks[input_resource(n,profile)]=start
        previous_notes=event.notes
    tone=profile.initial_timbre;tone_hold=.040;tone_settle=.050;tone_lead=tone_hold+tone_settle+timing.jitter_margin
    sounded=[(score.seconds_at(e.start),score.seconds_at(e.start)+
              (timing.key_gap if sustain_notes and pointer_free(e.notes,profile) else tap))
             for e in score.events if e.notes]
    onsets=[start for start,end in sounded]
    for at,target in tone_points:
        if at>=score.beats:continue
        if target==tone:continue
        boundary=score.seconds_at(at)
        position=bisect_left(onsets,boundary)
        previous=sounded[position-1][1] if position else -1e9
        next_note=sounded[position][0] if position<len(sounded) else score.seconds+tone_lead
        press_at=max(boundary-tone_lead,previous+timing.key_gap)
        if press_at+tone_lead>next_note+1e-9:
            raise GuitarError(f'音色切换到{target}需要约 {tone_lead*1000:.0f} 毫秒准备时间，请在标记处留出间隔或降低 BPM')
        steps.extend((Step(press_at,'timbre_down',target),Step(press_at+tone_hold,'timbre_up')))
        tone=target
    if profile.initial_mode is not None and profile.initial_mode != profile.mode and sounded:
        first=min(s.at for s in steps)
        steps.extend((Step(first-.18,'mode_down',profile.mode),Step(first-.13,'mode_up',profile.mode)))
    steps.append(Step(max(score.seconds,previous_release), "end"))
    plan=tuple(sorted(steps,key=lambda step:step.at))
    if sustain_notes:
        from .sustain import sustain_actions
        return sustain_actions(score,profile,plan,tap,timing)
    return plan
