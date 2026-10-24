"""Locate the guitar wheel by its six pink, evenly spaced string lines."""
from dataclasses import dataclass
import math
import re
import statistics

from .game_profile import CHORD_PAGES, ROMAN


@dataclass(frozen=True)
class Frame:
    width: int
    height: int
    data: bytes
    channels: int = 3
    bgr: bool = False

    def __post_init__(self):
        if not 100 <= self.width <= 8192 or not 100 <= self.height <= 8192:
            raise ValueError('画面尺寸无效或过大')
        if self.channels not in (3, 4) or len(self.data) != self.width * self.height * self.channels:
            raise ValueError('画面像素格式无效')

    def pink(self, x, y):
        at = (y * self.width + x) * self.channels
        a, g, c = self.data[at:at + 3]
        r, b = (c, a) if self.bgr else (a, c)
        return r >= 120 and 35 <= b <= r * 1.15 and g < r * .65 and b > r * .32

    def thumbnail_ppm(self,width=700,height=190):
        scale=min(width/self.width,height/self.height,1)
        w,h=max(1,int(self.width*scale)),max(1,int(self.height*scale))
        pixels=bytearray(w*h*3)
        for y in range(h):
            for x in range(w):
                at=(int(y/scale)*self.width+int(x/scale))*self.channels
                rgb=self.data[at:at+3]
                if self.bgr:rgb=rgb[::-1]
                out=(y*w+x)*3;pixels[out:out+3]=rgb
        return f'P6\n{w} {h}\n255\n'.encode()+pixels


def from_photo(photo):
    """Tk decodes PNG/PPM without adding an image dependency to the player."""
    raw = photo.tk.call(str(photo), 'data', '-format', 'ppm')
    if isinstance(raw, str):
        raw = raw.encode('latin1')
    header = re.match(rb'P6\s+(\d+)\s+(\d+)\s+255\s', raw)
    if not header:
        raise ValueError('无法读取图像像素，请使用游戏客户区的 PNG 截图')
    return Frame(int(header[1]), int(header[2]), raw[header.end():])


@dataclass(frozen=True)
class Wheel:
    x: float
    y: float
    spacing: float
    width: int
    height: int

    @property
    def radius(self):
        return self.spacing * 10

    def slots(self):
        radius = self.spacing * 8
        return tuple(((self.x + radius * math.sin(i * math.pi / 3)) / (self.width - 1),
                      (self.y - radius * math.cos(i * math.pi / 3)) / (self.height - 1)) for i in range(6))

    @property
    def strum(self):
        # Left of the wheel, away from page controls, mode buttons and close.
        return ((self.x - self.spacing * 12) / (self.width - 1), self.y / (self.height - 1))

    def apply(self, profile):
        slots = self.slots()
        if any(not .015 < n < .985 for point in (*slots, self.strum) for n in point):
            raise ValueError('圆盘接近画面边缘，请确认截图只有完整游戏客户区')
        chords={name: slots[i] for page in CHORD_PAGES for i, name in enumerate(page)}
        frets={**{name: slots[i % 6] for i, name in enumerate(ROMAN)}, 'Zero': slots[0]}
        profile.chords=chords if profile.mode=='chord' else frets if profile.mode=='fret' else {**chords,**frets}
        profile.strum_point = self.strum
        profile.reference_size = self.width, self.height
        profile.calibrated = True


def detect_wheel(frame):
    """Fail closed unless a complete, consistent set of six lines is found."""
    w, h = frame.width, frame.height
    stride = max(1, w // 1400)
    gap = max(4, round(w * .012))
    groups = []
    for y in range(int(h * .10), int(h * .82)):
        segments = []
        left = last = None
        for x in range(int(w * .36), int(w * .96), stride):
            if frame.pink(x, y):
                if left is None:
                    left = x
                elif x - last > gap:
                    segments.append((left, last))
                    left = x
                last = x
        if left is not None:
            segments.append((left, last))
        for left, right in segments:
            length = right - left
            if not w * .035 <= length <= w * .19:
                continue
            center = (left + right) / 2
            match = next((g for g in reversed(groups) if y - g[1] <= 1 and abs(g[2] - center) < w * .012), None)
            if match:
                match[1] = y
                match[2] = (match[2] * match[4] + center) / (match[4] + 1)
                match[3] = max(match[3], length)
                match[4] += 1
            else:
                groups.append([y, y, center, length, 1])
    lines = sorted(((g[0] + g[1]) / 2, g[2], g[3]) for g in groups if g[1] - g[0] <= max(4, h * .009))
    candidates = []
    for first in lines:
        for second in lines:
            d = second[0] - first[0]
            if not h * .012 <= d <= h * .045 or abs(first[1] - second[1]) > w * .012:
                continue
            chosen = [first, second]
            for i in range(2, 6):
                nearby = [line for line in lines if abs(line[0] - first[0] - i * d) <= max(1.5, d * .16)
                          and abs(line[1] - first[1]) <= w * .012]
                if not nearby:
                    break
                chosen.append(min(nearby, key=lambda line: abs(line[0] - first[0] - i * d)))
            if len(chosen) != 6:
                continue
            spacing = (chosen[-1][0] - chosen[0][0]) / 5
            width = statistics.median(line[2] for line in chosen)
            if not 4.5 <= width / spacing <= 9.5:
                continue
            deviation = sum(abs(line[0] - chosen[0][0] - i * spacing) for i, line in enumerate(chosen))
            wheel = Wheel(statistics.median(line[1] for line in chosen),
                          (chosen[0][0] + chosen[-1][0]) / 2, spacing, w, h)
            if wheel.y - wheel.radius < 0 or wheel.y + wheel.radius > h * .90:
                continue
            candidates.append((deviation, wheel))
    if not candidates:
        raise ValueError('没有可靠识别到六根粉色琴弦；请打开完整吉他圆盘、移开遮挡后重试，或使用手动拾取')
    candidates.sort(key=lambda item: item[0])
    best = candidates[0][1]
    if any(abs(other.x - best.x) + abs(other.y - best.y) > best.spacing * 3 for _, other in candidates[1:]):
        raise ValueError('画面存在多个可能的圆盘，请仅保留游戏客户区，或使用手动拾取')
    return best
