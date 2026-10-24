from dataclasses import dataclass, field
import json
import math

from .guitar import UP, DOWN
from .profile import Binding

FIRST_PAGE_POINTS = {
    "G": (648 / 1023, 92 / 575), "Am": (765 / 1023, 161 / 575),
    "Bm": (765 / 1023, 288 / 575), "C": (648 / 1023, 359 / 575),
    "D": (526 / 1023, 288 / 575), "Em": (526 / 1023, 161 / 575),
}
CHORD_PAGES = (
    ('G', 'Am', 'Bm', 'C', 'D', 'Em'),
    ('Cmaj7', 'C7', 'Cm7', 'G7', 'C9', 'Cm9'),
    ('C5', 'D5', 'G5', 'A5', 'Cmaj9', 'Cadd9'),
    ('G/B', 'Am7/G', 'D/F#', 'F/A', 'A', 'Dm'),
)
CHORD_NAMES = tuple(name for page in CHORD_PAGES for name in page)
DEFAULT_CHORDS = {name: tuple(FIRST_PAGE_POINTS.values())[slot]
                  for page in CHORD_PAGES for slot, name in enumerate(page)}
CHORD_PAGE_INDEX = {name: page for page, names in enumerate(CHORD_PAGES) for name in names}
ROMAN = ("I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII",
         "XIII", "XIV", "XV", "XVI", "XVII", "XVIII", "XIX", "XX", "XXI", "XXII", "XXIII", "XXIV")


def valid_point(point):
    if not isinstance(point, (list, tuple)) or len(point) != 2:
        raise ValueError("位置需要两个 0～1 之间的数")
    if any(type(n) not in (int, float) or not math.isfinite(n) or not 0 <= n <= 1 for n in point):
        raise ValueError("相对位置必须在 0～1 之间")
    return tuple(map(float, point))


@dataclass
class GameProfile:
    name: str = "异环吉他 · 四页和弦"
    chords: dict = field(default_factory=lambda: dict(DEFAULT_CHORDS))
    strum_point: tuple = (0.86, 0.67)
    calibrated: bool = False
    reference_size: tuple = (1023, 575)
    mode: str = "chord"
    pages: dict = field(default_factory=lambda: dict(CHORD_PAGE_INDEX))
    initial_page: int = 0
    page_count: int = 4
    initial_timbre: str = 'clean'
    initial_mode: str | None = None
    initial_pages: dict = field(default_factory=lambda: {'chord':0,'fret':0})
    strum_input: str = 'mouse'
    gamepad_up_sign: int = 1
    string_input: str = 'keyboard'
    page_input: str = 'keyboard'
    dpad_exclusive: bool = False
    selector_input: str = 'mouse'
    gamepad_select_ms: int = 60

    @classmethod
    def mixed(cls):
        profile=cls.frets()
        slots=[profile.chords[name] for name in ROMAN[:6]]
        profile.chords={**{name:slots[i] for page in CHORD_PAGES for i,name in enumerate(page)},**profile.chords}
        profile.pages={**CHORD_PAGE_INDEX,**profile.pages}
        profile.mode='mixed';profile.name='异环吉他 · 和弦＋品格';profile.initial_mode='fret'
        return profile

    @classmethod
    def frets(cls):
        slots = [(871 / 1247, 168 / 687), (990 / 1247, 237 / 687),
                 (990 / 1247, 368 / 687), (871 / 1247, 437 / 687),
                 (749 / 1247, 368 / 687), (749 / 1247, 237 / 687)]
        return cls("异环吉他 · 品格 0～24", {**{name: slots[i % 6] for i, name in enumerate(ROMAN)}, "Zero": slots[0]},
                   (0.91, 0.67), False, (1247, 687), "fret",
                   {**{name: i // 6 for i, name in enumerate(ROMAN)}, "Zero": 4}, 0, 5)

    @property
    def bindings(self):
        sweeps = ({UP: Binding(UP, 'gamepad', 'up'), DOWN: Binding(DOWN, 'gamepad', 'down')}
                  if self.strum_input == 'gamepad' else
                  {UP: Binding(UP, 'mouse', button='left'), DOWN: Binding(DOWN, 'mouse', button='right')})
        strings=({s:Binding(s,'gamepad_button',name) for s,name in enumerate(('dpad_down','dpad_left','dpad_right','dpad_up','a','x'),1)}
                 if self.string_input=='gamepad' else {s:Binding(s,'key',str(7-s)) for s in range(1,7)})
        return {**strings, **sweeps}

    def page_binding(self,key):
        return Binding(0,'gamepad_button','rb' if key=='T' else 'lb') if self.page_input=='gamepad' else Binding(0,'key',key)

    def selector_slot(self,name):
        if name not in self.chords:raise ValueError('布局没有选择项：'+str(name))
        if name=='Zero':return 0
        if name in ROMAN:return ROMAN.index(name)%6
        for page in CHORD_PAGES:
            if name in page:return page.index(name)
        raise ValueError('左摇杆暂不支持自定义圆盘选择项：'+str(name))

    def page_selectors(self):
        mode=self.initial_mode if self.mode=='mixed' else self.mode
        if mode=='chord':return CHORD_PAGES[self.initial_page]
        if self.initial_page==4:return ('Zero',)*6
        return ROMAN[self.initial_page*6:(self.initial_page+1)*6]

    def to_dict(self):
        return {"version": 1, "name": self.name, "chords": self.chords,
                "strum_point": self.strum_point, "calibrated": self.calibrated,
                "reference_size": self.reference_size,
                "mode": self.mode, "pages": self.pages, "initial_page": self.initial_page,
                "page_count": self.page_count,"initial_timbre":self.initial_timbre,"initial_mode":self.initial_mode,
                "initial_pages":self.initial_pages,
                "strum_input":self.strum_input,"gamepad_up_sign":self.gamepad_up_sign,
                "string_input":self.string_input,"page_input":self.page_input,"dpad_exclusive":self.dpad_exclusive,
                "selector_input":self.selector_input,"gamepad_select_ms":self.gamepad_select_ms}

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict) or data.get("version") != 1:
            raise ValueError("不支持的布局文件版本")
        chords = data.get("chords")
        if not isinstance(chords, dict) or not chords or len(chords) > 64:
            raise ValueError("布局文件需要 1～64 个选择项")
        size = data.get("reference_size", [1023, 575])
        if not isinstance(size, (tuple, list)) or len(size) != 2 or any(type(n) is not int or n <= 0 for n in size):
            raise ValueError("参考窗口尺寸应为两个正整数")
        mode = data.get("mode", "chord")
        pages = data.get("pages", {name: 0 for name in chords})
        initial = data.get("initial_page", 0)
        count = data.get("page_count", 5 if mode in ("fret","mixed") else 4)
        tone=data.get('initial_timbre','clean')
        initial_mode=data.get('initial_mode')
        strum_input=data.get('strum_input','mouse');up_sign=data.get('gamepad_up_sign',1)
        string_input=data.get('string_input','keyboard');page_input=data.get('page_input','keyboard')
        dpad_exclusive=data.get('dpad_exclusive',False)
        selector_input=data.get('selector_input','mouse')
        gamepad_select_ms=data.get('gamepad_select_ms',60)
        if type(gamepad_select_ms) is not int or not 40<=gamepad_select_ms<=200:raise ValueError('左摇杆选择保持应为 40～200 毫秒')
        if selector_input not in ('mouse','gamepad'):raise ValueError('圆盘输入应为鼠标或手柄')
        if type(dpad_exclusive) is not bool:raise ValueError('十字键互斥设置无效')
        if string_input not in ('keyboard','gamepad') or page_input not in ('keyboard','gamepad'):raise ValueError('拨弦与翻页输入方式无效')
        if strum_input not in ('mouse','gamepad'):raise ValueError('扫弦输入应为鼠标或手柄')
        if type(up_sign) is not int or up_sign not in (-1,1):raise ValueError('手柄扫弦方向无效')
        if initial_mode not in (None,'chord','fret'):raise ValueError('游戏初始模式无效')
        if tone not in ('clean','distorted'):raise ValueError('游戏初始音色无效')
        if type(count) is not int or not 1 <= count <= 12:
            raise ValueError("总页数应在 1～12 之间")
        if mode not in ("chord", "fret", "mixed") or type(initial) is not int or not 0 <= initial < count:
            raise ValueError("模式或起始页无效")
        if not isinstance(pages, dict) or set(pages) != set(chords) or any(type(p) is not int or not 0 <= p < count for p in pages.values()):
            raise ValueError("选择项页码超出总页数")
        if mode=='chord' and count==4 and set(chords)==set(CHORD_PAGES[0]) and all(p==0 for p in pages.values()):
            slots=[chords[name] for name in CHORD_PAGES[0]]
            chords={name:slots[i] for names in CHORD_PAGES for i,name in enumerate(names)}
            pages=dict(CHORD_PAGE_INDEX)
        initial_pages=data.get('initial_pages',{'chord':0,'fret':0})
        if not isinstance(initial_pages,dict) or set(initial_pages)!={'chord','fret'} or any(type(initial_pages[k]) is not int or not 0<=initial_pages[k]<n for k,n in (('chord',4),('fret',5))):
            raise ValueError('混合模式的和弦／品格起始页无效')
        return cls(str(data.get("name", "自定义吉他布局")),
                   {str(name): valid_point(point) for name, point in chords.items()},
                   valid_point(data.get("strum_point", [0.86, 0.67])),
                   data.get("calibrated") is True, tuple(size), mode, pages, initial, count,tone,initial_mode,dict(initial_pages),strum_input,up_sign,string_input,page_input,dpad_exclusive,selector_input,gamepad_select_ms)


def load_profile(path):
    with open(path, encoding="utf-8-sig") as stream:
        return GameProfile.from_dict(json.load(stream))
