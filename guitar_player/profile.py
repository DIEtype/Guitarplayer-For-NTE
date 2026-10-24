"""Versioned mapping files. No game keys are assumed."""
from dataclasses import asdict, dataclass
import json

from .score import pitch_name, pitch_number

MODIFIERS = {"SHIFT", "CTRL", "ALT"}
KEY_CODES = {**{chr(i): i for i in range(65, 91)}, **{str(i): 48 + i for i in range(10)},
             "SHIFT": 0x10, "CTRL": 0x11, "ALT": 0x12, "SPACE": 0x20,
             "TAB": 0x09, "ENTER": 0x0D, "LEFT": 0x25, "UP": 0x26,
             "RIGHT": 0x27, "DOWN": 0x28, "MINUS": 0xBD, "EQUAL": 0xBB,
             "COMMA": 0xBC, "PERIOD": 0xBE, "SLASH": 0xBF,
             "SEMICOLON": 0xBA, "QUOTE": 0xDE, "LBRACKET": 0xDB, "RBRACKET": 0xDD,
             **{f"F{i}": 0x6F + i for i in range(1, 13) if i not in (8, 9)}}


def parse_keys(text):
    keys = tuple(part.strip().upper() for part in text.split("+"))
    if not text.strip() or any(key not in KEY_CODES for key in keys):
        raise ValueError("请输入字母、数字或 SHIFT+A 等组合；F8/F9 保留给暂停与停止")
    if len(set(keys)) != len(keys) or sum(key not in MODIFIERS for key in keys) != 1:
        raise ValueError("每个音符绑定一个普通键，可搭配 SHIFT、CTRL 或 ALT")
    return tuple(key for key in keys if key in MODIFIERS) + tuple(key for key in keys if key not in MODIFIERS)


@dataclass(frozen=True)
class Binding:
    pitch: int
    kind: str = "none"
    key: str = ""
    x: int = 0
    y: int = 0
    button: str = "left"
    row: int = 0
    column: int = 0

    @property
    def description(self):
        if self.kind == "key":
            return self.key
        if self.kind == "mouse":
            return f"{'左' if self.button == 'left' else '右'}键 ({self.x}, {self.y})"
        if self.kind == 'gamepad':
            return '右摇杆'+('上推' if self.key == 'up' else '下推')
        if self.kind == 'gamepad_button':
            return {'dpad_down':'十字键↓','dpad_left':'十字键←','dpad_right':'十字键→','dpad_up':'十字键↑',
                    'a':'手柄 A','x':'手柄 X','lb':'手柄 LB','rb':'手柄 RB'}.get(self.key,self.key)
        return "未配置"


@dataclass
class Profile:
    name: str
    bindings: dict[int, Binding]

    @classmethod
    def draft(cls):
        pitches = [12 * (octave + 1) + degree for octave in (3, 4, 5) for degree in (0, 2, 4, 5, 7, 9, 11)]
        return cls("异环 · 吉他（待截图校准）", {
            pitch: Binding(pitch, row=i // 7, column=i % 7) for i, pitch in enumerate(pitches)
        })

    def validate_score(self, score):
        missing = [pitch_name(n) for n in score.pitches
                   if n not in self.bindings or self.bindings[n].kind == "none"]
        if missing:
            raise ValueError("以下音符尚未绑定：" + "、".join(missing))
        for event in score.events:
            actions = [self.bindings[n] for n in event.notes]
            if sum(a.kind == "mouse" for a in actions) > 1:
                raise ValueError(f"第 {event.line} 行和弦包含多个鼠标音符；单指针无法同时按住不同位置")
            keyboard = [parse_keys(a.key) for a in actions if a.kind == "key"]
            modifiers = {tuple(sorted(set(keys) & MODIFIERS)) for keys in keyboard}
            if len(modifiers) > 1:
                raise ValueError(f"第 {event.line} 行和弦混合了不同修饰键，无法同时准确发出")
            primary = [key for keys in keyboard for key in keys if key not in MODIFIERS]
            if len(primary) != len(set(primary)):
                raise ValueError(f"第 {event.line} 行和弦中多个音符绑定了同一个按键")

    def to_dict(self):
        return {"version": 1, "name": self.name,
                "bindings": [{**asdict(binding), "pitch": pitch_name(binding.pitch)}
                             for binding in self.bindings.values()]}

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict) or data.get("version") != 1:
            raise ValueError("不支持的键位文件版本")
        entries = data.get("bindings")
        if not isinstance(entries, list) or len(entries) > 128:
            raise ValueError("键位文件需要 bindings 列表，最多 128 个音符")
        bindings, slots = {}, set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("每条键位配置都应是一个对象")
            pitch = pitch_number(str(entry.get("pitch", "")))
            kind = entry.get("kind", "none")
            if kind not in ("none", "key", "mouse"):
                raise ValueError("输入方式应为 none、key 或 mouse")
            key = "+".join(parse_keys(str(entry.get("key", "")))) if kind == "key" else ""
            values = {field: entry.get(field, 0) for field in ("x", "y", "row", "column")}
            if any(type(value) is not int for value in values.values()):
                raise ValueError("坐标、行和列必须是整数")
            if not 0 <= values["row"] < 12 or not 0 <= values["column"] < 24:
                raise ValueError("布局支持 1～12 行、1～24 列")
            button = entry.get("button", "left")
            if button not in ("left", "right"):
                raise ValueError("鼠标按钮应为 left 或 right")
            slot = (values["row"], values["column"])
            if pitch in bindings or slot in slots:
                raise ValueError("音名或界面位置重复，请分别设置")
            slots.add(slot)
            bindings[pitch] = Binding(pitch, kind, key, button=button, **values)
        return cls(str(data.get("name", "自定义吉他布局")), bindings)


def read_profile(path):
    with open(path, encoding="utf-8-sig") as stream:
        return Profile.from_dict(json.load(stream))
