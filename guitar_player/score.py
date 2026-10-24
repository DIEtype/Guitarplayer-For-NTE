"""Parse numbered notation without evaluating user text."""
from dataclasses import dataclass, replace
from fractions import Fraction
import re

NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
TONICS = {name: i for i, name in enumerate(NAMES)}
SCALE = (0, 2, 4, 5, 7, 9, 11)
NOTE = re.compile(r"([#b]?)([1-7])(['，,]*)\Z")
TOKEN = re.compile(r"\[[^\]\n]*\](?::[^\s|]+)?|[^\s|]+|\|")


class ScoreError(ValueError):
    pass


def pitch_name(midi: int) -> str:
    return f"{NAMES[midi % 12]}{midi // 12 - 1}"


def pitch_number(name: str) -> int:
    match = re.fullmatch(r"([A-Ga-g])([#b]?)(-?\d+)", name.strip())
    if not match:
        raise ValueError(f"音名格式应为 C4、F#4 或 Bb3：{name}")
    letter, accidental, octave = match.groups()
    result = (int(octave) + 1) * 12 + TONICS[letter.upper()]
    result += {"": 0, "#": 1, "b": -1}[accidental]
    if not 0 <= result <= 127:
        raise ValueError(f"音名超出支持范围：{name}")
    return result


@dataclass(frozen=True)
class Event:
    notes: tuple[int, ...]
    start: Fraction
    beats: Fraction
    label: str
    line: int
    column: int


@dataclass(frozen=True)
class Score:
    events: tuple[Event, ...]
    beats: Fraction
    bpm: float

    @property
    def seconds(self):
        return float(self.beats) * 60 / self.bpm

    @property
    def pitches(self):
        return sorted({n for event in self.events for n in event.notes})


def _degree(text: str, base: int) -> int:
    match = NOTE.fullmatch(text)
    if not match:
        raise ScoreError(f"无法识别音符“{text}”，请使用 1～7；高八度用 '，低八度用 ,")
    accidental, degree, octaves = match.groups()
    result = base + SCALE[int(degree) - 1]
    result += {"": 0, "#": 1, "b": -1}[accidental]
    result += 12 * (octaves.count("'") - octaves.count(",") - octaves.count("，"))
    if not 0 <= result <= 127:
        raise ScoreError("音符超出 C-1～G9 的范围")
    return result


def parse_score(text: str, bpm: float = 100, tonic: str = "C", octave: int = 4) -> Score:
    if not 20 <= bpm <= 360:
        raise ScoreError("速度应在 20～360 BPM 之间")
    if tonic not in TONICS or not 0 <= octave <= 8:
        raise ScoreError("请选择有效的调号和 0～8 的基准八度")
    base = (octave + 1) * 12 + TONICS[tonic]
    events = []
    cursor = Fraction(0)
    for line_number, original in enumerate(text.splitlines(), 1):
        line = original.split("//", 1)[0]
        for match in TOKEN.finditer(line):
            token = match.group()
            if token == "|":
                continue
            try:
                if token == "-":
                    if not events:
                        raise ScoreError("延长符 - 前面需要一个音符、和弦或休止符")
                    events[-1] = replace(events[-1], beats=events[-1].beats + 1)
                    cursor += 1
                    continue
                parts = token.rsplit(":", 1)
                body = parts[0]
                try:
                    beats = Fraction(parts[1]) if len(parts) == 2 else Fraction(1)
                except (ValueError, ZeroDivisionError):
                    raise ScoreError("时值请写成正数或分数，例如 :2、:1/2、:1.5") from None
                if not Fraction(1, 64) <= beats <= 128:
                    raise ScoreError("单个音的拍数应在 1/64～128 之间")
                if body == "0":
                    notes = ()
                elif body.startswith("[") and body.endswith("]"):
                    members = body[1:-1].split()
                    if not members:
                        raise ScoreError("和弦不能为空，例如 [1 3 5]:2")
                    notes = tuple(sorted({_degree(member, base) for member in members}))
                else:
                    notes = (_degree(body, base),)
                events.append(Event(notes, cursor, beats, token, line_number, match.start() + 1))
                cursor += beats
                if len(events) > 50000:
                    raise ScoreError("曲谱超过 50000 个事件，请分段演奏")
            except ScoreError as error:
                raise ScoreError(f"第 {line_number} 行，第 {match.start() + 1} 列：{error}") from None
    if not events:
        raise ScoreError("先输入一段简谱，例如 1 2 3:2 | [1 3 5]:2 0")
    return Score(tuple(events), cursor, float(bpm))
