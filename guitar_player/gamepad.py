"""Optional, explicitly opened virtual Xbox controller; no import-time input.

Both sticks, string buttons and page shoulders share a complete report.
ViGEmBus is installed separately by the user.
Client ABI: https://github.com/nefarius/ViGEmClient
"""
import ctypes as C
import os
from .paths import resource_root

DRIVER_URL = 'https://github.com/nefarius/ViGEmBus/releases/tag/v1.22.0'
SUCCESS = 0x20000000
BUTTONS={'dpad_up':0x0001,'dpad_down':0x0002,'dpad_left':0x0004,'dpad_right':0x0008,
         'lb':0x0100,'rb':0x0200,'a':0x1000,'x':0x4000}
# Sector centers, clockwise from the top; XInput positive Y points up.
SELECTOR_AXES=((0,32767),(28377,16384),(28377,-16384),
               (0,-32767),(-28377,-16384),(-28377,16384))


class XUSBReport(C.Structure):
    _fields_ = [('buttons', C.c_uint16), ('left_trigger', C.c_uint8),
                ('right_trigger', C.c_uint8), ('left_x', C.c_int16),
                ('left_y', C.c_int16), ('right_x', C.c_int16), ('right_y', C.c_int16)]


def client_path():
    arch = 'x64' if C.sizeof(C.c_void_p) == 8 else 'x86'
    return resource_root()/'assets'/'gamepad'/arch/'ViGEmClient.dll'


def load_client():
    path = client_path()
    if os.name != 'nt' or not path.is_file():
        raise RuntimeError('缺少虚拟手柄客户端，请使用包含手柄组件的新版程序；鼠标扫弦仍可使用')
    try:
        api = C.CDLL(str(path))
    except OSError as error:
        raise RuntimeError('虚拟手柄客户端加载失败，请检查程序位数与运行库：'+str(error)) from error
    signatures = {
        'vigem_alloc': ([], C.c_void_p),
        'vigem_free': ([C.c_void_p], None),
        'vigem_connect': ([C.c_void_p], C.c_uint32),
        'vigem_disconnect': ([C.c_void_p], None),
        'vigem_target_x360_alloc': ([], C.c_void_p),
        'vigem_target_free': ([C.c_void_p], None),
        'vigem_target_add': ([C.c_void_p, C.c_void_p], C.c_uint32),
        'vigem_target_remove': ([C.c_void_p, C.c_void_p], C.c_uint32),
        'vigem_target_x360_update': ([C.c_void_p, C.c_void_p, XUSBReport], C.c_uint32),
    }
    for name, (args, result) in signatures.items():
        function = getattr(api, name)
        function.argtypes, function.restype = args, result
    return api


def check(result, operation):
    if result != SUCCESS:
        raise RuntimeError(f'虚拟手柄{operation}失败（0x{result:08X}）；请点主窗口“扫弦方式”检查 ViGEmBus 驱动')


def driver_status():
    """Connect to the bus only; never attach a controller or send a sweep."""
    api = load_client()
    client = api.vigem_alloc()
    if not client:
        raise RuntimeError('无法分配虚拟手柄连接')
    try:
        check(api.vigem_connect(client), '连接驱动')
        return '驱动连接正常；请用上下扫弦测试确认游戏响应'
    finally:
        try: api.vigem_disconnect(client)
        finally: api.vigem_free(client)


class GamepadSweep:
    def __init__(self, up_sign=1, api=None, trace=None, dpad_exclusive=False):
        if type(up_sign) is not int or up_sign not in (-1, 1):
            raise ValueError('摇杆方向设置无效')
        self.api = api if api is not None else load_client()
        self.client = self.target = None
        self.connected = self.attached = False
        self.active = None
        self.up_sign = up_sign
        self.axis=0;self.buttons=0;self.trace=trace
        self.left_x=self.left_y=0
        self.dpad_exclusive=dpad_exclusive
        try:
            self.client = self.api.vigem_alloc()
            if not self.client: raise RuntimeError('无法分配虚拟手柄连接')
            check(self.api.vigem_connect(self.client), '连接驱动')
            self.connected = True
            self.target = self.api.vigem_target_x360_alloc()
            if not self.target: raise RuntimeError('无法创建虚拟 Xbox 手柄')
            check(self.api.vigem_target_add(self.client, self.target), '连接设备')
            self.attached = True
            self.reset()
        except Exception:
            try: self.close()
            except Exception: pass
            raise

    def _update(self):
        if not self.attached:
            raise RuntimeError('虚拟手柄尚未连接')
        report = XUSBReport(left_x=self.left_x,left_y=self.left_y,right_y=self.axis,buttons=self.buttons)
        result=self.api.vigem_target_x360_update(self.client, self.target, report)
        if self.trace:self.trace('gamepad_report',left_x=self.left_x,left_y=self.left_y,
                                right_y=self.axis,buttons=self.buttons,accepted=result==SUCCESS,result=result)
        check(result, '发送手柄状态')

    def select_slot(self,slot):
        if type(slot) is not int or not 0<=slot<6:raise ValueError('圆盘扇区应为 0～5')
        if self.left_x or self.left_y:raise RuntimeError('左摇杆尚未回中，已停止以免圆盘选择漏触发')
        self.left_x,self.left_y=SELECTOR_AXES[slot]
        self._update()

    def release_selector(self):
        if self.left_x or self.left_y:
            self.left_x=self.left_y=0
            self._update()

    def press(self, direction):
        self.press_inputs((direction,),())

    def press_inputs(self,directions=(),buttons=()):
        if len(directions)>1 or any(d not in ('up','down') for d in directions):raise ValueError('扫弦方向无效')
        if directions and self.active is not None:raise RuntimeError('右摇杆尚未回中，已停止以免扫弦漏触发')
        if any(b not in BUTTONS for b in buttons):raise ValueError('手柄按钮无效')
        mask=self.buttons
        for button in buttons:
            if mask&BUTTONS[button]:raise RuntimeError('同一手柄按钮尚未释放')
            mask|=BUTTONS[button]
        if self.dpad_exclusive and (mask&3==3 or mask&12==12):raise RuntimeError('十字键互斥已启用，相反方向无法同时保持')
        self.buttons=mask
        if directions:
            self.active=directions[0]
            self.axis=(32767 if self.active=='up' else -32767)*self.up_sign
        self._update()

    def release(self, directions):
        self.release_inputs(directions,())

    def release_inputs(self,directions=(),buttons=()):
        changed=False
        if self.active in directions:self.axis=0;self.active=None;changed=True
        for button in buttons:
            bit=BUTTONS[button]
            if self.buttons&bit:self.buttons&=~bit;changed=True
        if changed:self._update()

    def reset(self):
        self.axis=0;self.buttons=0;self.active=None
        self.left_x=self.left_y=0
        self._update()

    def close(self):
        errors = []
        if self.attached:
            for operation in (self.reset,
                              lambda: check(self.api.vigem_target_remove(self.client,self.target),'断开设备')):
                try: operation()
                except Exception as error: errors.append(error)
            self.attached = False
        if self.target:
            try: self.api.vigem_target_free(self.target)
            finally: self.target = None
        if self.client:
            try:
                if self.connected: self.api.vigem_disconnect(self.client)
            finally:
                self.api.vigem_free(self.client)
                self.client = None
                self.connected = False
        if errors: raise errors[0]
