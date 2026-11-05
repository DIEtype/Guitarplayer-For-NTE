"""Launch an in-game recording, then keep the live part on its fixed clock."""
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import re
import threading
import time

from .player import Cancelled

CLICK_SETTLE = .030
CLICK_HOLD = .040
POINTS = (('open_point', '播放录音按钮'), ('code_point', '录音码输入框'), ('import_point', '导入按钮'))
RECORD_POINTS = (('record_start_point', '开始录音按钮'), ('record_stop_point', '停止录音按钮'))
ALL_POINTS = (*POINTS, *RECORD_POINTS)


@dataclass(frozen=True)
class RecordingSync:
    code: str = ''
    open_point: tuple | None = None
    code_point: tuple | None = None
    import_point: tuple | None = None
    reference_size: tuple = (0, 0)
    launch_delay_ms: int = 0
    score_offset_ms: int = 0
    dialog_wait_ms: int = 600
    paste_wait_ms: int = 150
    import_settle_ms: int = 150
    record_start_point: tuple | None = None
    record_stop_point: tuple | None = None
    record_start_delay_ms: int = 0
    record_settle_ms: int = 150
    record_tail_ms: int = 300

    def validate(self, launch=False):
        if not isinstance(self.code, str) or len(self.code) > 256:
            raise ValueError('录音码无效，请重新填写游戏生成的录音码')
        if launch and not re.fullmatch(r'[0-9a-fA-F]{32}', self.code.strip()):
            raise ValueError('录音码应为 32 位字母 A～F 与数字；请复制游戏生成的完整录音码')
        for key, label in ALL_POINTS:
            point = getattr(self, key)
            if point is None:
                if launch and (key, label) in POINTS:raise ValueError('请先拾取“' + label + '”的位置，再保存并开始合奏')
            elif (not isinstance(point, (tuple, list)) or len(point) != 2 or
                  any(type(n) not in (int, float) or not math.isfinite(n) or not 0 <= n <= 1 for n in point)):
                raise ValueError(label + '坐标无效，请重新拾取')
        if (not isinstance(self.reference_size, (tuple, list)) or len(self.reference_size) != 2 or
                any(type(n) is not int or n < 0 or n > 16384 for n in self.reference_size)):
            raise ValueError('录音按钮的校准尺寸无效，请重新拾取所需位置')
        if launch and min(self.reference_size) < 100:
            raise ValueError('尚未记录游戏画面尺寸，请重新拾取三个位置')
        for key in ('launch_delay_ms', 'dialog_wait_ms', 'paste_wait_ms', 'import_settle_ms',
                    'record_start_delay_ms', 'record_settle_ms', 'record_tail_ms'):
            value = getattr(self, key)
            if type(value) is not int or not 0 <= value <= 5000:
                raise ValueError('起播延迟和界面等待请填 0～5000 毫秒的整数')
        if type(self.score_offset_ms) is not int or not -60000 <= self.score_offset_ms <= 60000:
            raise ValueError('现场声部偏移请填 -60000～60000 毫秒；正数晚弹，负数早弹')
        return self

    @property
    def origin_offset(self):return (self.launch_delay_ms + self.score_offset_ms) / 1000

    def validate_plan(self, plan):
        self.validate(launch=True)
        # Rest markers do not need input; selector/mode/tone preparation does.
        actions = [step.at for step in plan if step.kind in ('page', 'chord_move', 'chord_down', 'mode_down', 'timbre_down')
                   or step.kind == 'note' and step.data[1]]
        if not actions:raise ValueError('现场声部没有可演奏的音符，请先载入第二声部曲谱')
        needed = CLICK_HOLD + self.import_settle_ms / 1000 + .030
        available = min(actions) + self.origin_offset
        if available + 1e-9 < needed:
            raise ValueError(f'录音导入后首个演奏准备动作来不及：还需至少 {(needed-available)*1000:.0f} 毫秒。'
                             '请让录音声部和现场声部开头保留相同的空拍，并核对游戏当前模式、音色与页码；'
                             '程序不会单独推迟现场声部。可先使用双声部示范曲。')

    def validate_capture(self):
        self.validate()
        for key,label in RECORD_POINTS:
            if getattr(self,key) is None:raise ValueError('请先拾取“'+label+'”的位置，再开始自动录制')
        if min(self.reference_size)<100:raise ValueError('请先拾取开始录音和停止录音的位置，记录游戏画面尺寸')
        return self

    def validate_capture_plan(self,plan):
        self.validate_capture()
        actions=[step.at for step in plan if step.kind in ('page','chord_move','chord_down','mode_down','timbre_down')
                 or step.kind=='note' and step.data[1]]
        if not actions:raise ValueError('当前曲谱没有可录制的音符，请先载入伴奏曲谱')
        available=min(actions)+self.record_start_delay_ms/1000
        needed=CLICK_HOLD+self.record_settle_ms/1000+.030
        if available+1e-9<needed:
            raise ValueError(f'开启录音后演奏准备时间不足，还需至少 {(needed-available)*1000:.0f} 毫秒。'
                             '请给伴奏和现场两份谱保留相同的开头休止；可先载入双声部示范曲。')

    def to_dict(self):return {'version': 2, **asdict(self)}

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict) or data.get('version') not in (1,2):
            raise ValueError('录音合奏设置格式无效，请重新校准')
        result = cls(**{key: data[key] for key in cls.__dataclass_fields__ if key in data})
        result.validate()
        return result


def read_settings(directory):
    if directory is None:return RecordingSync(), ''
    try:
        data = json.loads((Path(directory) / 'recording-sync.json').read_text(encoding='utf-8'))
        return RecordingSync.from_dict(data), ''
    except FileNotFoundError:return RecordingSync(), ''
    except (OSError, ValueError, TypeError) as error:
        return RecordingSync(), '录音合奏设置未读取：' + str(error) + '；请重新填写并保存。'


class RecordingControls:
    """Cancellable clicks shared by capture and replay, with identical edges."""
    def __init__(self,backend,cancel,clock=None):
        self.backend=backend;self.cancel=cancel;self.clock=clock or time.perf_counter

    def check(self):
        if self.cancel.is_set():raise Cancelled()
        self.backend.check_target()

    def wait(self,seconds):self.wait_until(self.clock()+seconds)

    def wait_until(self,end):
        while True:
            self.check()
            remaining = end - self.clock()
            if remaining <= 0:return
            if self.cancel.wait(min(.010, remaining)):raise Cancelled()

    def click(self,point,on_press=None):
        self.check();self.backend.recording_move(point);self.wait(CLICK_SETTLE)
        self.check();before=self.clock();sent=self.backend.recording_down();after=self.clock()
        # Native input provides its dispatch timestamp, before logging or UI
        # bookkeeping. A test/legacy backend falls back to the call's start.
        edge=sent if type(sent) in (int,float) and before<=sent<=after else before
        if on_press:on_press(edge)
        self.wait_until(edge+CLICK_HOLD);self.backend.recording_up()
        return edge

    def shortcut(self,key):
        self.check();self.backend.key_down('CTRL');self.backend.key_down(key)
        self.wait(CLICK_HOLD)
        self.backend.key_up(key);self.backend.key_up('CTRL')


def launch_recording(backend, settings, cancel, emit, clock=None):
    """Return score time zero, anchored to import mouse-down, never job end."""
    settings.validate(launch=True)
    controls=RecordingControls(backend,cancel,clock)

    try:
        backend.check_recording_layout(settings.reference_size)
        emit('state', '录音合奏：打开录音导入窗口…')
        controls.click(settings.open_point);controls.wait(settings.dialog_wait_ms / 1000)
        controls.click(settings.code_point)
        # Do not paste whatever happens to be on the system clipboard.
        backend.check_recording_code(settings.code.strip())
        controls.shortcut('A');controls.shortcut('V');controls.wait(settings.paste_wait_ms / 1000)
        emit('state', '录音合奏：导入录音，建立共同时间轴…')
        trigger = controls.click(settings.import_point)
        origin = trigger + settings.origin_offset
        trace = getattr(backend, 'trace', None)
        if trace:trace.record('recording_sync', import_clock=trigger, score_origin=origin,
                              launch_delay_ms=settings.launch_delay_ms, score_offset_ms=settings.score_offset_ms)
        controls.wait(settings.import_settle_ms / 1000)
        return origin
    finally:
        backend.release_all()


class RecordingCapture:
    """Track whether start was actually sent, including interrupted clicks."""
    def __init__(self,backend,settings,cancel,emit,clock=None):
        self.backend=backend;self.settings=settings;self.cancel=cancel;self.emit=emit
        self.controls=RecordingControls(backend,cancel,clock)
        self.started=False;self.stopped=False;self.origin=None

    def start(self):
        self.settings.validate_capture()
        self.backend.check_recording_layout(self.settings.reference_size)
        self.emit('state','自动录制：开启游戏录音，建立曲谱时间零点…')
        def pressed(edge):
            self.started=True;self.origin=edge+self.settings.record_start_delay_ms/1000
            trace=getattr(self.backend,'trace',None)
            if trace:trace.record('recording_capture_start',start_clock=edge,score_origin=self.origin,
                                  start_delay_ms=self.settings.record_start_delay_ms)
        try:
            self.controls.click(self.settings.record_start_point,pressed)
            self.controls.wait(self.settings.record_settle_ms/1000)
            return self.origin
        finally:self.backend.release_all()

    def finish(self,score_seconds=0,cleanup=False):
        if not self.started or self.stopped:return
        # On F9, release music first and still attempt the calibrated stop in
        # the foreground game. Never reactivate a window after focus loss.
        if cleanup:
            controls=RecordingControls(self.backend,threading.Event(),self.controls.clock)
        else:
            controls=self.controls
            controls.wait_until(self.origin+score_seconds+self.settings.record_tail_ms/1000)
        controls.check();self.backend.check_recording_layout(self.settings.reference_size)
        self.emit('state','自动录制：停止游戏录音…')
        def pressed(edge):
            self.stopped=True
            trace=getattr(self.backend,'trace',None)
            if trace:trace.record('recording_capture_stop',stop_clock=edge,score_origin=self.origin,interrupted=cleanup)
        try:controls.click(self.settings.record_stop_point,pressed)
        finally:self.backend.release_all()
