"""Separate the game input connection from notation and playback scheduling."""
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
import uuid
import queue
import time

from .native import WindowsBackend
from .profile import Binding
from .timing import SAFE,selector_timing
from .game_profile import ROMAN


class InputTrace:
    def __init__(self, directory, purpose,buffered=False):
        directory=Path(directory)
        directory.mkdir(parents=True,exist_ok=True)
        self.path=directory/f"input-{uuid.uuid4().hex[:12]}.jsonl"
        self.lock=threading.Lock()
        self.buffered=buffered;self.closed=False;self.error=None
        if buffered:
            self.path.touch();self.pending=queue.SimpleQueue()
            self.worker=threading.Thread(target=self._write_pending,daemon=True);self.worker.start()
        self.record("session",purpose=purpose)

    def record(self,event,**fields):
        row={"utc":datetime.now(timezone.utc).isoformat(),"perf_ns":time.perf_counter_ns(),"event":event,**fields}
        with self.lock:
            if self.closed:return
            if self.buffered:
                if not self.error:self.pending.put(row)
                return
            with self.path.open("a",encoding="utf-8") as stream:
                stream.write(json.dumps(row,ensure_ascii=False)+"\n")

    def _write_pending(self):
        try:
            with self.path.open('a',encoding='utf-8') as stream:
                while True:
                    row=self.pending.get()
                    if row is None:break
                    if isinstance(row,tuple):stream.flush();row[1].set()
                    else:stream.write(json.dumps(row,ensure_ascii=False)+'\n')
        except Exception as error:self.error=str(error)

    def flush(self):
        if not self.buffered or self.closed:return True
        done=threading.Event();self.pending.put(('flush',done));return done.wait(1)

    def close(self):
        with self.lock:
            if self.closed:return
            self.closed=True
            if self.buffered:self.pending.put(None)
        if self.buffered:self.worker.join(1)


class InputProbe:
    def __init__(self,emit):
        self.emit=emit
        self.cancel=threading.Event()
        self.thread=None

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def stop(self):
        self.cancel.set()

    def start(self,hwnd,profile,kind,trace,backend_factory=WindowsBackend,countdown=3,timing=None):
        if self.running:
            raise ValueError("输入测试正在运行")
        if kind not in ("key","pointer","select","page",'sweep','sweep_select','strings','long_sweep','transition','pad_chords','selector_stick'):
            raise ValueError("未知的输入测试")
        if kind=='page' and profile.mode!='fret':raise ValueError('翻页测试目前用于品格模式')
        self.cancel.clear()
        self.thread=threading.Thread(target=self._run,args=(hwnd,profile,kind,trace,backend_factory,countdown,timing or SAFE),daemon=True)
        self.thread.start()

    def _wait(self,seconds,backend=None):
        import time
        end=time.perf_counter()+seconds
        while time.perf_counter()<end:
            if self.cancel.wait(min(.01,max(0,end-time.perf_counter()))):
                raise InterruptedError("输入测试已取消")
            if backend:
                backend.check_target()

    def _run(self,hwnd,profile,kind,trace,factory,countdown,timing):
        timing=selector_timing(timing,profile)
        backend=None
        message="测试完成：请根据游戏里的实际响应判断是否连通；Windows 接收事件不代表游戏已处理"
        try:
            for second in range(countdown,0,-1):
                self.emit("probe_state",f"{second} 秒后输入测试，请切换到所选游戏窗口")
                self._wait(1)
            if self.cancel.is_set():
                raise InterruptedError("输入测试已取消")
            backend=factory(hwnd,profile,trace=trace,check_aspect=False)
            target=backend.acquire_target()
            trace.record("probe_begin",kind=kind,target=target,input_speed=timing.key)
            if kind=='selector_stick':
                if profile.selector_input!='gamepad':raise ValueError('本项需要左摇杆圆盘输入')
                backend.prepare_sweeps()
                names=profile.page_selectors()
                if len(names)!=6:raise ValueError('当前页没有六个扇区，请核对模式与起始页')
                for label,hold in (('慢速确认',.3),('演奏保持',timing.mouse_hold)):
                    for slot,(direction,name) in enumerate(zip(('↑','↗','↘','↓','↙','↖'),names)):
                        self.emit('probe_state',f'{label} {hold*1000:.0f} ms：左摇杆 {direction} → {name}；不拨弦、不翻页')
                        trace.record('probe_selector',slot=slot,selector=name,direction=direction,hold_seconds=hold)
                        backend.select_stick_slot(slot,name);self._wait(hold,backend)
                        backend.chord_up();self._wait(1-hold,backend)
                message='慢速与演奏保持两轮测试完成；若仅第二轮漏选，请提高左摇杆保持毫秒数。'
            elif kind in ('strings','long_sweep','transition','pad_chords'):
                from .guitar import UP,DOWN
                if (kind in ('strings','pad_chords') or profile.strum_input=='gamepad' or profile.string_input=='gamepad'):
                    prepare=getattr(backend,'prepare_sweeps',None)
                    if prepare:prepare()
                if kind=='pad_chords':
                    if profile.dpad_exclusive:raise ValueError('请先关闭十字键互斥，再测试组合弦')
                    for strings in ((1,4),(2,3),(1,2,3,4,5,6)):
                        self.emit('probe_state','手柄组合弦：'+str(strings)+'；请核对每根弦是否都发声')
                        actions=tuple(profile.bindings[s] for s in strings)
                        backend.press(actions);self._wait(.5,backend)
                        backend.release_actions(actions);self._wait(.5,backend)
                elif kind=='strings':
                    if profile.string_input!='gamepad':raise ValueError('本项需要手柄拨弦输入')
                    for string in range(1,7):
                        action=profile.bindings[string]
                        self.emit('probe_state',f'正在测试 {string} 弦：{action.description}')
                        backend.press((action,));self._wait(.25,backend)
                        backend.release_actions((action,));self._wait(.25,backend)
                elif kind=='long_sweep':
                    action=profile.bindings[DOWN]
                    for seconds in (.1,.5,1,2,4):
                        self.emit('probe_state',f'长扫弦对照：保持 {seconds:g} 秒；请听按下瞬间有无起音，尾音可能自然衰减')
                        trace.record('probe_hold',hold_seconds=seconds,strum_input=profile.strum_input)
                        backend.press((action,));self._wait(seconds,backend)
                        backend.release_actions((action,));self._wait(.5,backend)
                else:
                    for string,direction in ((1,DOWN),(5,DOWN),(6,UP)):
                        self.emit('probe_state',f'{string} 弦拨弦 → 扫弦；拨弦方式 {profile.string_input}，扫弦方式 {profile.strum_input}')
                        trace.record('probe_transition',string=string,string_input=profile.string_input,strum_input=profile.strum_input)
                        backend.press((profile.bindings[string],));self._wait(.25,backend)
                        backend.release_actions((profile.bindings[string],));self._wait(.08,backend)
                        backend.press((profile.bindings[direction],));self._wait(2,backend)
                        backend.release_actions((profile.bindings[direction],));self._wait(.5,backend)
            elif kind in ('sweep','sweep_select'):
                from .guitar import UP,DOWN
                if profile.strum_input!='gamepad':raise ValueError('手柄扫弦测试需要右摇杆输入方式')
                prepare=getattr(backend,'prepare_sweeps',None)
                if prepare:prepare()
                names=profile.page_selectors()
                def select(name):
                    backend.chord_move(name);self._wait(timing.mouse_settle,backend)
                    backend.chord_down(name);self._wait(timing.mouse_hold,backend)
                    backend.chord_up();self._wait(timing.after_select,backend)
                if kind=='sweep_select':select(names[0])
                self.emit('probe_state','右摇杆上推并保持；随后自动回中、下推并保持。可用停止键结束。')
                backend.press((profile.bindings[UP],))
                self._wait(.4,backend)
                if kind=='sweep_select':
                    if len(names)<2:raise ValueError('当前页缺少第二个选择项，无法比较换和弦')
                    select(names[1])
                    self.emit('probe_state','旧扫弦仍保持，圆盘已切到下一和弦；请听下一次下扫弦是否使用新和弦')
                self._wait(.6,backend)
                backend.release_actions((profile.bindings[UP],))
                self._wait(timing.key_gap,backend)
                backend.press((profile.bindings[DOWN],));self._wait(1,backend)
                backend.release_actions((profile.bindings[DOWN],))
            elif kind=="key":
                self.emit("probe_state","正在发送数字键 6 三次：对应吉他 1 弦，不依赖圆盘坐标")
                for _ in range(3):
                    backend.press((Binding(1,"key","6"),))
                    self._wait(.12,backend)
                    backend.release_all()
                    self._wait(.35,backend)
            else:
                selector=next(name for name in profile.chords if profile.pages[name]==profile.initial_page)
                if kind=='page':
                    page=(profile.initial_page+1)%profile.page_count
                    selector=next(name for name in profile.chords if profile.pages[name]==page)
                    self.emit('probe_state',f'按 {timing.label}档翻到第 {page+1} 页，选择 {selector} 并拨弦；完成后请重新对齐游戏起始页')
                    backend.press((profile.page_binding('T'),));self._wait(timing.page_hold,backend);backend.release_all()
                    self._wait(timing.page_wait-timing.page_hold,backend)
                backend.chord_move(selector)
                self._wait(timing.mouse_settle if kind!='pointer' else .10,backend)
                if kind=="pointer":
                    self.emit("probe_state",f"已移动到 {selector} 区域；本项不点击、不拨弦")
                    self._wait(.6,backend)
                else:
                    backend.chord_down(selector)
                    self._wait(timing.mouse_hold,backend)
                    backend.chord_up()
                    self._wait(timing.after_select,backend)
                    backend.press((Binding(1,"key","6"),))
                    self._wait(timing.tap_ms/1000,backend)
                    backend.release_all()
            trace.record("probe_finished",kind=kind)
        except Exception as error:
            message=str(error)
            trace.record("probe_error",kind=kind,error=message)
        finally:
            if backend:
                try:
                    try:backend.release_all()
                    finally:
                        close=getattr(backend,'close',None)
                        if close:close()
                except Exception as error:
                    message=f"释放输入失败：{error}"
                    trace.record("release_error",error=str(error))
            trace.close()
            self.emit("probe_done",(message,str(trace.path)))
