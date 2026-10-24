"""Cancellable monotonic scheduler; the preview backend never emits input."""
import math
import threading
import time


class PreviewBackend:
    def acquire_target(self):
        return "静默预演 · 不发送键盘或鼠标输入"

    def check_target(self):
        pass

    def press(self, actions):
        pass

    def release_all(self):
        pass

    def chord_down(self, chord):
        pass

    def chord_move(self,chord):
        pass

    def chord_up(self):
        pass


class Cancelled(Exception):
    pass


class Player:
    def __init__(self, emit):
        self.emit = emit
        self.lock = threading.Lock()
        self.cancel = threading.Event()
        self.thread = None
        self.paused = False
        self.pause_started = 0.0
        self.pause_total = 0.0
        self.origin = 0.0
        self._last_progress = 0.0
        self._next_target_check=0

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self, score, profile, backend, countdown=0, gate=0.82):
        if self.running:
            raise ValueError("已有演奏正在进行")
        if not 0 <= countdown <= 15 or not 0.1 <= gate <= 0.98:
            raise ValueError("倒计时或按住时长比例无效")
        self.cancel.clear()
        self.paused = False
        self.pause_total = 0
        self.origin = 0
        self._last_progress = 0
        self._next_target_check=0
        self.thread = threading.Thread(target=self._run, args=(score, profile, backend, countdown, gate), daemon=True)
        self.thread.start()

    def stop(self):
        self.cancel.set()

    def toggle_pause(self):
        with self.lock:
            if not self.running or not self.origin:
                return
            if self.paused:
                self.pause_total += time.perf_counter() - self.pause_started
                self.paused = False
            else:
                self.pause_started = time.perf_counter()
                self.paused = True
            paused = self.paused
        self.emit("state", "已暂停 · F8 继续，F9 停止" if paused else "正在演奏")

    def _wait_until(self, deadline, backend, active=()):
        released = False
        while True:
            if self.cancel.is_set():
                raise Cancelled()
            with self.lock:
                paused = self.paused
                elapsed = (self.pause_started if paused else time.perf_counter()) - self.origin - self.pause_total
            sync = getattr(backend, "sync_playback", None)
            if sync:
                sync(elapsed, paused)
            if paused:
                if not released:
                    backend.release_all()
                    released = True
                self.cancel.wait(0.008)
                continue
            now=time.perf_counter()
            if now>=self._next_target_check or elapsed>=deadline:
                backend.check_target();self._next_target_check=now+.010
            if released and active:
                actions=active() if callable(active) else active
                if actions:backend.press(actions)
                released = False
            now = time.perf_counter()
            with self.lock:elapsed=now-self.origin-self.pause_total
            if now - self._last_progress >= 0.04:
                self.emit("progress", max(0, elapsed))
                self._last_progress = now
            if elapsed >= deadline:
                return
            remaining=deadline-elapsed
            if remaining>.003:time.sleep(min(.003,remaining-.0015))
            else:time.sleep(0)

    def elapsed(self):
        with self.lock:
            return (self.pause_started if self.paused else time.perf_counter())-self.origin-self.pause_total

    def _run(self, score, profile, backend, countdown, gate):
        outcome, detail = "done", "演奏完成"
        try:
            end = time.perf_counter() + countdown
            last = None
            while time.perf_counter() < end:
                remaining = math.ceil(end - time.perf_counter())
                if remaining != last:
                    self.emit("state", f"{remaining} 秒后开始，请切换到游戏的吉他界面")
                    last = remaining
                if self.cancel.wait(min(0.02, max(0, end - time.perf_counter()))):
                    raise Cancelled()
            if self.cancel.is_set():
                raise Cancelled()
            self.emit("target", backend.acquire_target())
            self.origin = time.perf_counter()
            self.emit("state", "正在演奏")
            seconds_per_beat = 60 / score.bpm
            for index, event in enumerate(score.events):
                start = float(event.start) * seconds_per_beat
                duration = float(event.beats) * seconds_per_beat
                self._wait_until(start, backend)
                actions = tuple(profile.bindings[n] for n in event.notes if n in profile.bindings)
                if self.cancel.is_set():
                    raise Cancelled()
                backend.check_target()
                backend.press(actions)
                self.emit("note", (index, event.notes))
                self._wait_until(start + duration * gate, backend, actions)
                backend.release_all()
                self._wait_until(start + duration, backend)
            self.emit("progress", score.seconds)
        except Cancelled:
            detail = "已停止 · 已释放按键"
        except Exception as error:
            outcome, detail = "error", str(error)
        finally:
            try:
                backend.release_all()
                close = getattr(backend, "close", None)
                if close:
                    close()
            except Exception as error:
                outcome, detail = "error", f"释放输入失败：{error}"
            self.origin = 0
            self.emit("note", (-1, ()))
            self.emit(outcome, detail)


class GuitarPlayer(Player):
    def start(self, score, profile, backend, countdown=0, gate=0.72, tap=0.045,timing=None,sustain_notes=False):
        from .guitar import make_plan
        from .timing import selector_timing
        self.timing=selector_timing(timing,profile);self.live=not isinstance(backend,PreviewBackend)
        self.transaction=False;self.pause_requested=False
        if not 0.015 <= tap <= 0.2:
            raise ValueError("按键时长应在 15～200 毫秒之间")
        if isinstance(backend,PreviewBackend):
            from .guitar import Step
            steps=[]
            for index,event in enumerate(score.events):
                start=score.seconds_at(event.start)
                duration=score.seconds_at(event.start+event.beats)-start
                steps.extend((Step(start,"preview_select",event.chord),Step(start,"note",(index,event.notes)),
                              Step(start+min(duration*gate,tap),"release")))
            steps.append(Step(score.seconds,"end"))
            from .measures import timbre_points
            steps.extend(Step(score.seconds_at(at),'preview_timbre',tone) for at,tone in timbre_points(score) if at<score.beats)
            self.plan=tuple(sorted(steps,key=lambda step:step.at))
        else:
            self.plan = make_plan(score, profile, gate, tap=tap,timing=self.timing,sustain_notes=sustain_notes)
        super().start(score, profile, backend, countdown, gate)

    def toggle_pause(self):
        if getattr(self,'live',False) and self.running and self.transaction and not self.paused:
            self.pause_requested=not self.pause_requested
            self.emit('state','将在当前拨弦释放后暂停' if self.pause_requested else '已取消待暂停请求')
            return
        super().toggle_pause()

    def _run(self, score, profile, backend, countdown, gate):
        from .timing import selector_timing
        self.timing=selector_timing(self.timing,profile)
        outcome, detail = "done", "输入发送完成" if self.live else "演奏完成"
        ready={'page':-1e9,'chord_move':-1e9,'chord_down':-1e9,'chord_up':-1e9,'note':-1e9,'release':-1e9}
        hold=0;active_kind=None;note_times=[s.at for s in self.plan if s.kind=='note' and s.data[1]]
        note_holds={};pending=None
        for item in self.plan:
            if item.kind=='note' and item.data[1]:pending=(item.data[0],item.at)
            elif item.kind=='release' and pending:
                note_holds[pending[0]]=item.at-pending[1];pending=None
        independent=any(item.kind=='note_release' for item in self.plan)
        held_notes={};minimum_release={};note_ready={};pending_taps=set();controls=set()
        playing_mode=profile.initial_mode or profile.mode
        selected_by_mode={};prepared_selector=None
        for item in self.plan:
            if item.kind=='note_settled':
                index=item.data
                note_holds[index]=item.at-score.seconds_at(score.events[index].start)
        max_late=0;note_count=0
        trace=getattr(backend,'trace',None)
        try:
            prepare=getattr(backend,'prepare',None)
            if prepare:prepare(score)
            end, last = time.perf_counter() + countdown, None
            while time.perf_counter() < end:
                remaining = math.ceil(end - time.perf_counter())
                if remaining != last:
                    self.emit("state", f"{remaining} 秒后开始，请切换到游戏的吉他界面")
                    last = remaining
                if self.cancel.wait(min(0.02, max(0, end - time.perf_counter()))):
                    raise Cancelled()
            if self.cancel.is_set():
                raise Cancelled()
            self.emit("target", backend.acquire_target())
            self.origin = time.perf_counter() - min(0, self.plan[0].at)
            self.emit("state", "正在演奏")
            active = ()
            for step in self.plan:
                needs_guard=self.live and not (step.kind=='note' and not step.data[1])
                target=max(step.at,ready.get(step.kind,-1e9)) if needs_guard else step.at
                if independent:
                    if step.kind in ('note_release','note_settled'):
                        index=step.data[0] if step.kind=='note_release' else step.data
                        target=max(target,minimum_release.get(index,-1e9))
                    elif step.kind=='note':
                        target=max(target,*(note_ready.get(n,-1e9) for n in step.data[1]),-1e9)
                    def waiting_active():
                        # A paused note is physically released. Re-pressing it
                        # under a newly selected fret/chord would play a wrong
                        # pitch, so only restore the current selector's notes.
                        for n,(index,action) in list(held_notes.items()):
                            if score.events[index].chord!=selected_by_mode.get(playing_mode):
                                del held_notes[n]
                        return (*active,*(entry[1] for entry in held_notes.values()))
                else:
                    waiting_active=active
                self._wait_until(target, backend, waiting_active)
                if self.cancel.is_set():
                    raise Cancelled()
                actual=self.elapsed()
                if self.live and step.kind=='note' and step.data[1]:
                    position=note_count
                    gap=note_times[position+1]-step.at if position+1<len(note_times) else .2
                    tolerance=min(.030,max(.008,gap*.15));late=max(0,actual-step.at)
                    if late>tolerance+1e-6:
                        from .guitar import event_position,UP,DOWN
                        event=score.events[step.data[0]];location=event_position(score,event)
                        action='／'.join('上扫 1→6' if n==UP else '下扫 6→1' if n==DOWN else f'{n} 弦' for n in event.notes)
                        if trace:trace.record('timing_overrun',index=step.data[0],planned=step.at,actual=actual,lateness_ms=late*1000,
                                              allowed_ms=tolerance*1000,location=location,selector=event.chord,notes=list(event.notes))
                        raise RuntimeError(f'第 {step.data[0]+1} 个事件（{location} · {event.chord} · {action}）切换／调度迟到 {late*1000:.0f} 毫秒，允许 {tolerance*1000:.0f} 毫秒，已停止以免挤拍；可试稳拍快速档，持续卡顿需检查主机负载，重新开始前对齐游戏页')
                    max_late=max(max_late,late);note_count+=1
                if self.live and trace:
                    event_index=step.data[0] if step.kind in ('note','note_release') else step.data if step.kind=='note_settled' else None
                    trace.record('schedule',action=step.kind,planned=step.at,actual=actual,guard_until=target,
                                 lateness_ms=max(0,actual-step.at)*1000,event_index=event_index,
                                 notes=list(step.data[1]) if step.kind in ('note','note_release') else None)
                operation_started=time.perf_counter()
                if step.kind=="preview_select":
                    if profile.mode=='mixed':
                        from .mixed import selector_mode
                        self.emit('mode',selector_mode(step.data))
                    self.emit("chord",step.data)
                    self.emit("page",profile.pages.get(step.data,0))
                elif step.kind=='preview_timbre':self.emit('timbre',step.data)
                elif step.kind=='mode_down':
                    self.transaction=True;backend.key_down('Z')
                    ready['mode_up']=self.elapsed()+.050
                elif step.kind=='mode_up':
                    backend.key_up('Z');settled=self.elapsed()+.130
                    for kind in ('page','chord_move','chord_down','note','timbre_down'):
                        ready[kind]=max(ready.get(kind,-1e9),settled)
                    self.emit('mode',step.data)
                    playing_mode=step.data
                elif step.kind=='timbre_down':
                    self.transaction=True;backend.key_down('V')
                    ready['timbre_up']=self.elapsed()+.040;self.emit('timbre',step.data)
                elif step.kind=='timbre_up':
                    backend.key_up('V');ready['note']=max(ready['note'],self.elapsed()+.050)
                    for kind in ('mode_down','page','chord_move'):
                        ready[kind]=max(ready.get(kind,-1e9),self.elapsed()+.050)
                elif step.kind == "chord_move":
                    self.transaction=True
                    backend.chord_move(step.data)
                    ready['chord_down']=self.elapsed()+self.timing.mouse_settle
                elif step.kind == "chord_down":
                    prepared_selector=step.data
                    backend.chord_down(step.data)
                    ready['chord_up']=self.elapsed()+self.timing.mouse_hold
                    self.emit("chord", step.data)
                elif step.kind == "page":
                    selected_by_mode[playing_mode]=None
                    from .profile import Binding
                    key, page = step.data
                    active = (profile.page_binding(key),)
                    self.transaction=True;active_kind='page';hold=self.timing.page_hold
                    backend.press(active)
                    sent=self.elapsed();ready['release']=sent+hold
                    ready['page']=ready['chord_move']=sent+self.timing.page_wait
                    self.emit("page", page)
                elif step.kind == "chord_up":
                    backend.chord_up()
                    selected_by_mode[playing_mode]=prepared_selector
                    ready['note']=max(ready['note'],self.elapsed()+self.timing.after_select)
                elif step.kind == "note":
                    index, notes = step.data
                    # A rest may occur while a page key is held in preparation
                    # for the next note. Keep that pending release intact.
                    if notes:
                        actions=tuple(profile.bindings[n] for n in notes)
                        self.transaction=True;hold=note_holds[index]
                        if independent:
                            if any(n in held_notes for n in notes):
                                raise RuntimeError('同一按键尚未释放，已停止以免重复起音错乱')
                            for n,action in zip(notes,actions):held_notes[n]=(index,action)
                            pending_taps.add(index)
                        else:
                            active=actions;active_kind='note'
                        backend.press(actions)
                        if independent:
                            minimum_release[index]=self.elapsed()+hold
                            from .timing import pointer_free
                            if pointer_free(notes,profile):
                                # Selection/page preparation can overlap a held
                                # string, but must follow its completed key-down.
                                for kind in ('page','mode_down','timbre_down','chord_down'):
                                    ready[kind]=max(ready.get(kind,-1e9),self.elapsed()+self.timing.key_gap)
                        else:ready['release']=self.elapsed()+hold
                    self.emit("note", (index, notes))
                elif step.kind=='note_settled':
                    pending_taps.discard(step.data)
                elif step.kind=='note_release':
                    index,notes=step.data
                    owned=[n for n in notes if n in held_notes and held_notes[n][0]==index]
                    actions=tuple(held_notes[n][1] for n in owned)
                    if actions:
                        backend.release_actions(actions)
                        if any(action.kind=='mouse' for action in actions):
                            ready['chord_move']=max(ready['chord_move'],self.elapsed()+self.timing.key_gap)
                        for n in owned:
                            del held_notes[n];note_ready[n]=self.elapsed()+self.timing.key_gap
                            from .timing import input_resource
                            for other in profile.bindings:
                                if input_resource(other,profile)==input_resource(n,profile):note_ready[other]=self.elapsed()+self.timing.key_gap
                        if any(action.kind=='gamepad' for action in actions):
                            from .guitar import UP,DOWN
                            for n in (UP,DOWN):note_ready[n]=self.elapsed()+self.timing.key_gap
                    pending_taps.discard(index)
                elif step.kind == "release":
                    release=getattr(backend,'release_actions',None)
                    if release:release(active)
                    else:backend.release_all()
                    ready['note']=max(ready['note'],self.elapsed()+self.timing.key_gap)
                    active = ()
                    if active_kind=='note':
                        self.transaction=False
                        if self.pause_requested:
                            self.pause_requested=False;super().toggle_pause()
                    active_kind=None
                if independent:
                    begins={'mode_down':'mode','timbre_down':'timbre','chord_move':'chord','page':'page'}
                    ends={'mode_up':'mode','timbre_up':'timbre','chord_up':'chord','release':'page'}
                    if step.kind in begins:controls.add(begins[step.kind])
                    if step.kind in ends:controls.discard(ends[step.kind])
                    self.transaction=bool(controls or pending_taps)
                    if self.pause_requested and not self.transaction:
                        self.pause_requested=False;super().toggle_pause()
                if self.live and trace and step.kind in ('chord_move','chord_down','chord_up','page','release','note','note_release','timbre_down','timbre_up'):
                    if step.kind!='note' or step.data[1]:
                        trace.record('operation',action='release' if step.kind=='note_release' else step.kind,
                                     duration_ms=(time.perf_counter()-operation_started)*1000)
            if self.live:
                detail=f'输入发送完成 · {note_count} 次拨弦，最大调度迟到 {max_late*1000:.1f} ms；游戏响应需另行核对'
            self.emit("progress", score.seconds)
        except Cancelled:
            detail = "已停止 · 已释放按键"
        except Exception as error:
            outcome, detail = "error", str(error)
        finally:
            try:
                try:backend.release_all()
                finally:
                    close = getattr(backend, "close", None)
                    if close:close()
            except Exception as error:
                outcome, detail = "error", f"释放输入失败：{error}"
            self.origin = 0
            self.transaction=False;self.pause_requested=False
            self.emit("note", (-1, ()))
            self.emit(outcome, detail)
