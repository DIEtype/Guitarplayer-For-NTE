"""Documented Windows APIs only. This module has no import-time input effects."""
import ctypes as C
from ctypes import wintypes as W
import os
import threading

from .profile import KEY_CODES, MODIFIERS, parse_keys

ULONG_PTR = C.c_size_t


class MOUSEINPUT(C.Structure):
    _fields_ = [("dx", W.LONG), ("dy", W.LONG), ("mouseData", W.DWORD),
                ("dwFlags", W.DWORD), ("time", W.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(C.Structure):
    _fields_ = [("wVk", W.WORD), ("wScan", W.WORD), ("dwFlags", W.DWORD),
                ("time", W.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(C.Structure):
    _fields_ = [("uMsg", W.DWORD), ("wParamL", W.WORD), ("wParamH", W.WORD)]


class INPUTUNION(C.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(C.Structure):
    _anonymous_ = ("value",)
    _fields_ = [("type", W.DWORD), ("value", INPUTUNION)]


def user32():
    if os.name != "nt":
        raise RuntimeError("自动演奏需要 Windows")
    api = C.WinDLL("user32", use_last_error=True)
    api.SendInput.argtypes = [W.UINT, C.POINTER(INPUT), C.c_int]
    api.SendInput.restype = W.UINT
    api.MapVirtualKeyW.argtypes = [W.UINT, W.UINT]
    api.MapVirtualKeyW.restype = W.UINT
    api.GetForegroundWindow.restype = W.HWND
    api.GetWindowThreadProcessId.argtypes = [W.HWND, C.POINTER(W.DWORD)]
    api.GetClientRect.argtypes = [W.HWND, C.POINTER(W.RECT)]
    api.ClientToScreen.argtypes = [W.HWND, C.POINTER(W.POINT)]
    api.GetWindowTextLengthW.argtypes = [W.HWND]
    api.GetWindowTextW.argtypes = [W.HWND, W.LPWSTR, C.c_int]
    api.IsWindow.argtypes = [W.HWND]
    api.IsWindowVisible.argtypes = [W.HWND]
    api.IsIconic.argtypes = [W.HWND]
    api.RegisterHotKey.argtypes = [W.HWND, C.c_int, W.UINT, W.UINT]
    api.UnregisterHotKey.argtypes = [W.HWND, C.c_int]
    api.PeekMessageW.argtypes = [C.POINTER(W.MSG), W.HWND, W.UINT, W.UINT, W.UINT]
    api.GetSystemMetrics.argtypes = [C.c_int]
    api.GetCursorPos.argtypes = [C.POINTER(W.POINT)]
    api.SetCursorPos.argtypes = [C.c_int,C.c_int]
    api.SetCursorPos.restype = W.BOOL
    return api


def title(api, hwnd):
    buffer = C.create_unicode_buffer(api.GetWindowTextLengthW(hwnd) + 1)
    api.GetWindowTextW(hwnd, buffer, len(buffer))
    return buffer.value


def client_rect(api, hwnd):
    rect, point = W.RECT(), W.POINT(0, 0)
    if not api.GetClientRect(hwnd, C.byref(rect)) or not api.ClientToScreen(hwnd, C.byref(point)):
        raise RuntimeError("无法读取游戏窗口的位置")
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if width <= 0 or height <= 0:
        raise RuntimeError("游戏窗口已最小化或没有可用画面")
    return point.x, point.y, width, height


def windows():
    api, items = user32(), []
    callback_type = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
    def visit(hwnd, _):
        if api.IsWindowVisible(hwnd) and not api.IsIconic(hwnd):
            pid = W.DWORD()
            api.GetWindowThreadProcessId(hwnd, C.byref(pid))
            label = title(api, hwnd)
            if label and pid.value != os.getpid():
                items.append((int(hwnd), label))
        return True
    callback = callback_type(visit)
    api.EnumWindows.argtypes = [callback_type, W.LPARAM]
    api.EnumWindows(callback, 0)
    return items


def pick_relative_point(hwnd):
    api, point = user32(), W.POINT()
    if int(api.GetForegroundWindow() or 0) != hwnd:
        raise RuntimeError("拾取时请让所选游戏窗口处于前台")
    api.GetCursorPos(C.byref(point))
    left, top, width, height = client_rect(api, hwnd)
    if not left <= point.x < left + width or not top <= point.y < top + height:
        raise RuntimeError("鼠标需要放在游戏画面内")
    return ((point.x - left) / max(1, width - 1), (point.y - top) / max(1, height - 1)), (width, height)


def activate_window(hwnd):
    api=user32()
    api.SetForegroundWindow.argtypes=[W.HWND];api.SetForegroundWindow.restype=W.BOOL
    if not api.IsWindow(hwnd):raise RuntimeError('所选窗口已关闭')
    api.ShowWindow.argtypes=[W.HWND,C.c_int]
    if api.IsIconic(hwnd):api.ShowWindow(hwnd,9)
    if not api.SetForegroundWindow(hwnd):raise RuntimeError('Windows 未允许自动切换窗口')


def capture_client(hwnd):
    """Read only the selected foreground client area; no files or input."""
    from .calibration import Frame
    api = user32()
    if not api.IsWindow(hwnd) or int(api.GetForegroundWindow() or 0) != hwnd or api.IsIconic(hwnd):
        raise RuntimeError('自动校准时请让选中的游戏窗口处于前台，显示完整吉他界面')
    rectangle = client_rect(api, hwnd)
    width, height = rectangle[2:]
    if width > 8192 or height > 8192:
        raise RuntimeError('游戏客户区过大，请缩小窗口后校准')
    api.GetDC.argtypes = [W.HWND]; api.GetDC.restype = W.HDC
    api.ReleaseDC.argtypes = [W.HWND, W.HDC]
    gdi = C.WinDLL('gdi32', use_last_error=True)
    gdi.CreateCompatibleDC.argtypes = [W.HDC]; gdi.CreateCompatibleDC.restype = W.HDC
    gdi.CreateCompatibleBitmap.argtypes = [W.HDC, C.c_int, C.c_int]; gdi.CreateCompatibleBitmap.restype = W.HBITMAP
    gdi.SelectObject.argtypes = [W.HDC, W.HANDLE]; gdi.SelectObject.restype = W.HANDLE
    gdi.DeleteObject.argtypes = [W.HANDLE]; gdi.DeleteDC.argtypes = [W.HDC]
    gdi.BitBlt.argtypes = [W.HDC, C.c_int, C.c_int, C.c_int, C.c_int, W.HDC, C.c_int, C.c_int, W.DWORD]
    gdi.GetDIBits.argtypes = [W.HDC, W.HBITMAP, W.UINT, W.UINT, C.c_void_p, C.c_void_p, W.UINT]
    class Header(C.Structure):
        _fields_ = [('size', W.DWORD), ('width', W.LONG), ('height', W.LONG),
                    ('planes', W.WORD), ('bits', W.WORD), ('compression', W.DWORD),
                    ('image_size', W.DWORD), ('x_resolution', W.LONG), ('y_resolution', W.LONG),
                    ('colors', W.DWORD), ('important', W.DWORD)]
    # Read the composited screen crop: a DirectX window's own DC can be black.
    dc = api.GetDC(None)
    memory = bitmap = previous = None
    try:
        if not dc:
            raise RuntimeError('无法读取游戏画面，请改用窗口化／无边框模式或导入 PNG 截图')
        memory = gdi.CreateCompatibleDC(dc)
        bitmap = gdi.CreateCompatibleBitmap(dc, width, height)
        if not memory or not bitmap:
            raise RuntimeError('无法创建校准图像')
        previous = gdi.SelectObject(memory, bitmap)
        if not previous or previous == C.c_void_p(-1).value:
            previous = None
            raise RuntimeError('无法读取校准图像')
        if not gdi.BitBlt(memory, 0, 0, width, height, dc, rectangle[0], rectangle[1], 0x40CC0020):
            raise RuntimeError('游戏画面读取失败，请改用窗口化／无边框模式或导入 PNG 截图')
        gdi.SelectObject(memory, previous); previous = None
        header = Header(C.sizeof(Header), width, -height, 1, 32, 0, width * height * 4, 0, 0, 0, 0)
        pixels = C.create_string_buffer(width * height * 4)
        if gdi.GetDIBits(memory, bitmap, 0, height, pixels, C.byref(header), 0) != height:
            raise RuntimeError('游戏图像像素读取失败')
        if int(api.GetForegroundWindow() or 0) != hwnd or client_rect(api, hwnd) != rectangle:
            raise RuntimeError('读取时游戏窗口发生变化，请保持前台和窗口大小后重试')
        return Frame(width, height, pixels.raw, 4, True)
    finally:
        if previous and memory:gdi.SelectObject(memory, previous)
        if bitmap:gdi.DeleteObject(bitmap)
        if memory:gdi.DeleteDC(memory)
        if dc:api.ReleaseDC(None, dc)


def integrity_level(pid):
    """Return Windows mandatory integrity RID, or None if access is denied."""
    kernel=C.WinDLL("kernel32",use_last_error=True)
    advapi=C.WinDLL("advapi32",use_last_error=True)
    kernel.OpenProcess.argtypes=[W.DWORD,W.BOOL,W.DWORD]
    kernel.OpenProcess.restype=W.HANDLE
    kernel.CloseHandle.argtypes=[W.HANDLE]
    advapi.OpenProcessToken.argtypes=[W.HANDLE,W.DWORD,C.POINTER(W.HANDLE)]
    advapi.GetTokenInformation.argtypes=[W.HANDLE,C.c_int,C.c_void_p,W.DWORD,C.POINTER(W.DWORD)]
    advapi.GetSidSubAuthorityCount.argtypes=[C.c_void_p]
    advapi.GetSidSubAuthorityCount.restype=C.POINTER(W.BYTE)
    advapi.GetSidSubAuthority.argtypes=[C.c_void_p,W.DWORD]
    advapi.GetSidSubAuthority.restype=C.POINTER(W.DWORD)
    process=kernel.OpenProcess(0x1000,False,pid)
    token=W.HANDLE()
    if not process:return None
    try:
        if not advapi.OpenProcessToken(process,0x8,C.byref(token)):return None
        size=W.DWORD()
        advapi.GetTokenInformation(token,25,None,0,C.byref(size))
        if not size.value:return None
        buffer=C.create_string_buffer(size.value)
        if not advapi.GetTokenInformation(token,25,buffer,size,C.byref(size)):return None
        sid=C.cast(buffer,C.POINTER(C.c_void_p)).contents.value
        count=advapi.GetSidSubAuthorityCount(sid).contents.value
        return advapi.GetSidSubAuthority(sid,count-1).contents.value
    finally:
        if token:kernel.CloseHandle(token)
        kernel.CloseHandle(process)


class WindowsBackend:
    def __init__(self, hwnd, profile, trace=None, check_aspect=True, gamepad_factory=None):
        self.api, self.hwnd, self.profile = user32(), hwnd, profile
        self.held_keys, self.held_buttons = [], set()
        self.target_rect = None
        self.trace=trace
        self.check_aspect=check_aspect
        self.prepared_chord=None
        self.gamepad=None
        self.gamepad_factory=gamepad_factory
        pid = W.DWORD()
        self.api.GetWindowThreadProcessId(hwnd, C.byref(pid))
        self.target_pid = pid.value

    def _ensure_gamepad(self):
        if self.gamepad is None:
            from .gamepad import GamepadSweep
            factory=self.gamepad_factory or GamepadSweep
            self.gamepad=factory(up_sign=self.profile.gamepad_up_sign,trace=self._log,dpad_exclusive=self.profile.dpad_exclusive)
            self._log('gamepad_connected',up_sign=self.profile.gamepad_up_sign)
        return self.gamepad

    def prepare(self,score):
        from .guitar import UP,DOWN
        if self.profile.selector_input=='gamepad':
            for event in score.events:
                if event.notes:self.profile.selector_slot(event.chord)
        if (self.profile.strum_input=='gamepad' and any(n in (UP,DOWN) for e in score.events for n in e.notes)
                or self.profile.string_input=='gamepad' and any(n in range(1,7) for e in score.events for n in e.notes)
                or self.profile.page_input=='gamepad' or self.profile.selector_input=='gamepad'):
            self.prepare_sweeps()

    def prepare_sweeps(self):
        self._ensure_gamepad()

    def acquire_target(self):
        self.check_target()
        self.target_rect = client_rect(self.api, self.hwnd)
        width, height = self.target_rect[2:]
        reference = self.profile.reference_size
        own_level,target_level=integrity_level(os.getpid()),integrity_level(self.target_pid)
        self._log("target",hwnd=self.hwnd,pid=self.target_pid,title=title(self.api,self.hwnd),
                  client=self.target_rect,python_pid=os.getpid(),own_integrity=own_level,target_integrity=target_level,
                  input_struct_size=C.sizeof(INPUT))
        if own_level is not None and target_level is not None and target_level>own_level:
            raise RuntimeError("游戏进程的权限级别高于程序，Windows 会拦截模拟输入；请让程序与游戏以相同权限运行")
        uses_mouse=self.profile.selector_input=='mouse' or self.profile.strum_input=='mouse'
        if self.check_aspect and uses_mouse and abs((width / height) / (reference[0] / reference[1]) - 1) > 0.04:
            raise RuntimeError("游戏窗口宽高比与布局校准时不同，请重新拾取和弦与扫弦位置")
        return title(self.api, self.hwnd)

    def check_target(self):
        if not self.api.IsWindow(self.hwnd) or int(self.api.GetForegroundWindow() or 0) != self.hwnd:
            raise RuntimeError("目标游戏窗口不在前台，演奏已停止")
        pid = W.DWORD()
        self.api.GetWindowThreadProcessId(self.hwnd, C.byref(pid))
        if pid.value != self.target_pid:
            raise RuntimeError("目标窗口所属进程发生变化，请重新选择游戏窗口")
        if self.target_rect:
            rect = client_rect(self.api, self.hwnd)
            if rect[2:] != self.target_rect[2:]:
                raise RuntimeError("游戏窗口尺寸发生变化，演奏已停止，请检查校准后重新开始")
            self.target_rect = rect

    def _send(self, entries):
        if not entries:
            return
        array = (INPUT * len(entries))(*entries)
        C.set_last_error(0)
        count = self.api.SendInput(len(entries), array, C.sizeof(INPUT))
        error=C.get_last_error()
        self._log("send_input",requested=len(entries),accepted=int(count),win_error=error,
                  inputs=[{"type":int(entry.type),"scan":int(entry.ki.wScan),"flags":int(entry.ki.dwFlags)}
                          if entry.type==1 else {"type":int(entry.type),"x":int(entry.mi.dx),"y":int(entry.mi.dy),"flags":int(entry.mi.dwFlags)}
                          for entry in entries])
        if count != len(entries):
            raise RuntimeError(f"Windows 只接受了 {count}/{len(entries)} 个输入事件；请检查游戏与程序的权限及输入兼容性")

    def _log(self,event,**fields):
        trace=getattr(self,"trace",None)
        if trace:trace.record(event,**fields)

    def _key(self, key, up=False):
        code = self.api.MapVirtualKeyW(KEY_CODES[key], 0)
        flags = 0x0008 | (0x0002 if up else 0)
        if key in ("LEFT", "RIGHT", "UP", "DOWN"):
            flags |= 0x0001
        return INPUT(type=1, ki=KEYBDINPUT(0, code, flags, 0, 0))

    def _button(self, button, up=False):
        flags = {("left", False): 0x0002, ("left", True): 0x0004,
                 ("right", False): 0x0008, ("right", True): 0x0010}[(button, up)]
        return INPUT(type=0, mi=MOUSEINPUT(0, 0, 0, flags, 0, 0))

    def _move(self, point):
        left, top, width, height = self.target_rect or client_rect(self.api, self.hwnd)
        x, y = left + round(point[0] * (width - 1)), top + round(point[1] * (height - 1))
        vx, vy, vw, vh = [self.api.GetSystemMetrics(i) for i in (76, 77, 78, 79)]
        return INPUT(type=0, mi=MOUSEINPUT(round((x - vx) * 65535 / max(1, vw - 1)),
                                          round((y - vy) * 65535 / max(1, vh - 1)),
                                          0, 0x8000 | 0x4000 | 0x0001, 0, 0))

    def chord_move(self,chord):
        self.check_target()
        if self.profile.selector_input=='gamepad':
            self.profile.selector_slot(chord)
            self.prepared_chord=chord
            return
        point=self.profile.chords[chord]
        left,top,width,height=self.target_rect or client_rect(self.api,self.hwnd)
        x,y=left+round(point[0]*(width-1)),top+round(point[1]*(height-1))
        C.set_last_error(0)
        moved=bool(self.api.SetCursorPos(x,y))
        error=C.get_last_error()
        self._send([self._move(point)])
        observed=W.POINT()
        self.api.GetCursorPos(C.byref(observed))
        self._log("pointer",selector=chord,requested=[x,y],observed=[observed.x,observed.y],set_cursor_ok=moved,win_error=error)
        if not moved:
            raise RuntimeError(f"鼠标定位失败，Windows 错误码 {error}")
        self.prepared_chord=chord

    def chord_down(self, chord):
        self.check_target()
        if getattr(self,'prepared_chord',None)!=chord:
            self.chord_move(chord)
        if self.profile.selector_input=='gamepad':
            self.select_stick_slot(self.profile.selector_slot(chord),chord)
            return
        self.held_buttons.add("left")
        self._send([self._button("left")])

    def select_stick_slot(self,slot,selector=None):
        self.check_target()
        self._ensure_gamepad().select_slot(slot)
        self._log('gamepad_selector',slot=slot,selector=selector)

    def chord_up(self):
        if self.profile.selector_input=='gamepad':
            if self.gamepad is not None:self.gamepad.release_selector()
            return
        if 'left' in self.held_buttons:
            self._send([self._button('left',True)]);self.held_buttons.discard('left')

    def key_down(self,key):
        self.check_target()
        if key not in self.held_keys:self.held_keys.append(key)
        self._send([self._key(key)])

    def key_up(self,key):
        if key in self.held_keys:
            self._send([self._key(key,True)]);self.held_keys.remove(key)

    def release_actions(self,actions):
        keys=[];buttons=set()
        for action in actions:
            if action.kind=='key':keys.extend(k for k in parse_keys(action.key) if k in self.held_keys and k not in keys)
            elif action.kind=='mouse' and action.button in self.held_buttons:buttons.add(action.button)
        try:
            self._send([self._key(key,True) for key in reversed(keys)]+[self._button(button,True) for button in buttons])
        finally:
            gamepad=getattr(self,'gamepad',None)
            if gamepad is not None:gamepad.release_inputs({a.key for a in actions if a.kind=='gamepad'},
                                                        {a.key for a in actions if a.kind=='gamepad_button'})
        self.held_keys=[key for key in self.held_keys if key not in keys];self.held_buttons.difference_update(buttons)

    def press(self, actions):
        self.check_target()
        sweeps=[a.key for a in actions if a.kind=='gamepad']
        pad_buttons=[a.key for a in actions if a.kind=='gamepad_button']
        if len(sweeps)>1:raise RuntimeError('右摇杆不能同时上下扫弦')
        keys, buttons = [], []
        for action in actions:
            if action.kind == "key":
                keys.extend(key for key in parse_keys(action.key) if key not in keys)
            elif action.kind == "mouse":
                buttons.append(action.button)
        entries = [self._key(key) for key in keys]
        if buttons:
            entries.append(self._move(self.profile.strum_point))
            entries.extend(self._button(button) for button in buttons)
        self.held_keys = list(dict.fromkeys([*self.held_keys,*keys]))
        self.held_buttons.update(buttons)
        self._log("press",keys=keys,buttons=buttons)
        self._send(entries)
        if sweeps or pad_buttons:
            self._ensure_gamepad().press_inputs(sweeps,pad_buttons)
            if sweeps:self._log('gamepad_stick',direction=sweeps[0])

    def release_all(self):
        keys, buttons = list(self.held_keys), set(self.held_buttons)
        try:
            self._send([self._key(key, True) for key in reversed(keys)] +
                       [self._button(button, True) for button in buttons])
            self.held_keys.clear()
            self.held_buttons.clear()
        finally:
            gamepad=getattr(self,'gamepad',None)
            if gamepad is not None:gamepad.reset()

    def close(self):
        gamepad=getattr(self,'gamepad',None)
        if gamepad is not None:
            try:gamepad.close()
            finally:self.gamepad=None


class Hotkeys:
    def __init__(self, emit):
        self.emit, self.stop_event = emit, threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        api, registered = user32(), []
        try:
            if api.RegisterHotKey(None,1,0x4000,0x77):registered.append(1)
            if api.RegisterHotKey(None,2,0x4000,0x78):registered.append(2)
            elif api.RegisterHotKey(None,3,0x4000|0x0002|0x0001,0x78):registered.append(3)
            self.emit("hotkeys", tuple(registered))
            message = W.MSG()
            while not self.stop_event.wait(0.008):
                while api.PeekMessageW(C.byref(message), None, 0x0312, 0x0312, 1):
                    if message.wParam in registered:
                        self.emit("pause" if message.wParam == 1 else "stop", None)
        finally:
            for ident in registered:
                api.UnregisterHotKey(None, ident)

    def close(self):
        self.stop_event.set()
