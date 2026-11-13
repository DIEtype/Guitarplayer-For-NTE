"""Read Guitar Pro scores into the existing editable six-string TAB model."""
from base64 import b64encode
from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass
from fractions import Fraction
from io import BytesIO
import json
from pathlib import Path
import sys
from threading import Lock
import zipfile

from .guitar import DOWN, UP, GuitarScore
from .measures import MeasureChange, valid_meter
from .music import STANDARD_TUNING
from .paths import resource_root


EXTENSIONS = {'.gp', '.gpx', '.gp3', '.gp4', '.gp5'}
FILE_FILTER = ('Guitar Pro', '*.gp *.gpx *.gp3 *.gp4 *.gp5')
MAX_INPUT = 64_000_000
MAX_GPIF = 12_000_000
TICKS_PER_BEAT = 960
_PARSER_LOCK = Lock()


def _read_bytes(path):
    if path.suffix.lower() not in EXTENSIONS:
        raise ValueError('请选择 .gp、.gpx、.gp3、.gp4 或 .gp5 文件')
    if path.stat().st_size > MAX_INPUT:
        raise ValueError('Guitar Pro 文件超过 64 MB，请去掉内嵌音频或分段后重试')
    if path.suffix.lower() == '.gp':
        # Only the notation XML is needed. Skip audio and other ZIP members.
        with zipfile.ZipFile(path) as source:
            scores = [entry for entry in source.infolist() if entry.filename.rsplit('/', 1)[-1] == 'score.gpif']
            if len(scores) != 1:
                raise ValueError('找不到唯一的 Guitar Pro 谱面，请在原软件中重新保存为 .gp')
            with source.open(scores[0]) as xml:
                notation = xml.read(MAX_GPIF + 1)
            if len(notation) > MAX_GPIF:
                raise ValueError('Guitar Pro 谱面过大，请分段导入')
        score = BytesIO()
        with zipfile.ZipFile(score, 'w') as compact:
            compact.writestr('Content/score.gpif', notation)
        return score.getvalue()
    with path.open('rb') as source:
        data = source.read(MAX_INPUT + 1)
    if len(data) > MAX_INPUT:
        raise ValueError('Guitar Pro 文件超过 64 MB，请分段后重试')
    if not (data[1:20].upper() == b'FICHIER GUITAR PRO ' or data.startswith((b'BCFS', b'BCFZ'))):
        raise ValueError('文件头不是受支持的 Guitar Pro 格式，请在原软件中重新保存')
    return data


def _decode(data):
    root = resource_root()
    vendor = str(root / 'vendor')
    if vendor not in sys.path:
        sys.path.insert(0, vendor)
    try:
        import _quickjs
    except (ImportError, OSError) as error:
        raise ValueError('无法加载 Guitar Pro 解析组件，请使用完整解压的 64 位 Windows 版本') from error

    parser = root / 'assets' / 'guitar-pro'
    runtime = _quickjs.Context()
    runtime.set_memory_limit(192 * 1024 * 1024)
    runtime.set_max_stack_size(256 * 1024)
    runtime.set_time_limit(10)
    try:
        runtime.eval((parser / 'read-score.js').read_text(encoding='utf-8'))
        runtime.eval((parser / 'alphaTab.min.js').read_text(encoding='utf-8'))
        result = runtime.get('readGuitarPro')(b64encode(data).decode('ascii'), 'utf-8')
        return json.loads(result)
    except _quickjs.JSException as error:
        message = str(error)
        if any(phrase in message for phrase in ('interrupted', 'out of memory', 'stack overflow')):
            raise ValueError('Guitar Pro 谱面解析超出资源限制，请按乐段分段导入') from error
        if '小节数' in message or '文件音符' in message:
            raise ValueError(message.splitlines()[0].removeprefix('Error: ')) from error
        raise ValueError('无法解析 Guitar Pro 谱面，请确认文件完整，并在原软件中重新保存后重试') from error


@dataclass(frozen=True)
class GuitarProTrack:
    index: int
    name: str
    note_count: int
    tuning: tuple
    capo: int
    supported: bool


@dataclass(frozen=True)
class GuitarProSource:
    title: str
    tracks: tuple
    bars: list
    order: list
    tempo: float
    track_data: list


def inspect_guitar_pro(path):
    path = Path(path)
    if not _PARSER_LOCK.acquire(blocking=False):
        raise ValueError('正在读取另一份 Guitar Pro 曲谱，请稍后再试')
    try:
        data = _decode(_read_bytes(path))
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as error:
        raise ValueError('Guitar Pro 压缩文件损坏、加密或使用了不支持的压缩方式') from error
    finally:
        _PARSER_LOCK.release()
    tracks = tuple(GuitarProTrack(index, track['label'], track['noteCount'],
                  tuple(pitch - track['transposition'] for pitch in track['tuning']), track['capo'], track['supported'])
                   for index, track in enumerate(data['tracks']))
    if not any(track.supported and track.note_count for track in tracks):
        raise ValueError('文件里没有可导入的六弦吉他音符。贝斯、鼓和非六弦轨道可从原软件导出 MIDI')
    return GuitarProSource(data['title'] or path.stem, tracks, data['bars'], data['order'], data['tempo'], data['tracks'])


def _beats(ticks):
    return (Fraction(str(ticks)) / TICKS_PER_BEAT).limit_denominator(100_000)


def _measure_meter(meter, length):
    if not valid_meter(meter):
        raise ValueError(f'原谱拍号 {meter[0]}/{meter[1]} 超出编辑器支持范围')
    if Fraction(4 * meter[0], meter[1]) == length:
        return meter
    for denominator in (meter[1], 4, 8, 16, 2, 1):
        numerator = length * denominator / 4
        if numerator.denominator == 1 and valid_meter((int(numerator), denominator)):
            return int(numerator), denominator
    raise ValueError('原谱有时值无法表示的弱起小节，请在 Guitar Pro 中补全该小节后导入')


def _bend_attacks(attacks, bends, clock):
    """Fit discrete pitches within a bend, leaving all original attacks fixed."""
    from .timing import ADAPTIVE

    starts = sorted({attack[0] for attack in attacks})
    counts = Counter(attack[0] for attack in attacks)
    converted = set()
    def page(fret):
        return (fret - 1) // 6 if fret else 4
    for attack, at, duration, points in bends:
        start, string, end, fret, note_id = attack
        if counts[start] != 1 or duration < Fraction(1, 2) or id(attack) in converted:
            continue
        peak_at, peak = max(points, key=lambda point: point[1])
        if points[0][1] or peak < 2 or peak % 2 or fret + peak // 2 > 24:
            continue
        # Keep time to hear the peak when the last control point is the peak.
        steps = [(at + duration * min(Fraction(3, 4), Fraction(peak_at, 60)), fret + peak // 2)]
        if points[-1][1] == 0:
            release = next((offset for offset, value in points if offset > peak_at and value == 0), 60)
            steps.append((at + duration * Fraction(release, 60), fret))
        elif points[-1][1] != peak:
            continue
        next_index = bisect_right(starts, start)
        following = starts[next_index] if next_index < len(starts) else end
        following_minimum = ADAPTIVE.lead + ADAPTIVE.key_gap + ADAPTIVE.page_delay
        if next_index < len(starts) and clock.seconds_at(following) - clock.seconds_at(steps[-1][0]) < following_minimum:
            continue
        boundaries = [start, *(point for point, _ in steps), min(end, following)]
        frets = [fret, *(value for _, value in steps)]
        for i, (left, right) in enumerate(zip(boundaries, boundaries[1:])):
            minimum = (ADAPTIVE.tap_ms / 1000) + ADAPTIVE.key_gap
            if i + 1 < len(frets):
                minimum = ADAPTIVE.lead + ADAPTIVE.key_gap
                distance = abs(page(frets[i]) - page(frets[i + 1]))
                minimum += min(distance, 5 - distance) * ADAPTIVE.page_delay
            if clock.seconds_at(right) - clock.seconds_at(left) + 1e-9 < minimum:
                break
        else:
            converted.add(id(attack))
            attack[2] = steps[0][0]
            yield [[point, string, after, value, note_id]
                   for (point, value), after in zip(steps, [*(point for point, _ in steps[1:]), end])]


def import_guitar_pro_track(source, index, approximate_bends=True):
    from .duet_composition import DuetTrackDocument
    from .fingerstyle_document import FingerCell, chord_name

    if not 0 <= index < len(source.tracks):
        raise ValueError('请选择有效的 Guitar Pro 轨道')
    track = source.tracks[index]
    if not track.supported or not track.note_count:
        raise ValueError('请选择有音符的六弦吉他轨道')
    order = source.order
    if not order or len(order) > 25000:
        raise ValueError('展开反复后的小节数超出范围，请分段导入')
    raw = source.track_data[index]
    position = Fraction(0)
    base_meter = tuple(source.bars[order[0]][:2])
    meter = base_meter
    changes = []
    tempo_map = {Fraction(0): source.tempo}
    chord_marks = {}
    last_note = {}
    attacks = []
    timeline = []
    notices = set()
    bends = []
    brush_directions = {}
    muted_hits = 0
    muted_notes = 0
    slides = 0
    if list(order) != list(range(len(source.bars))):
        notices.add('反复按演奏顺序展开')
    if track.tuning != tuple(STANDARD_TUNING.values()):
        notices.add('已保留原定弦。游戏演奏需近似转译')

    for bar_index, original_index in enumerate(order):
        numerator, denominator, ticks, tempos, swing = source.bars[original_index]
        length = _beats(ticks)
        current_meter = _measure_meter((numerator, denominator), length)
        if current_meter != (numerator, denominator):
            notices.add('弱起按实际时值导入')
        if current_meter != meter:
            changes.append(MeasureChange(bar_index, meter=current_meter))
            meter = current_meter
        if swing:
            notices.add('摇摆节奏按记谱时值导入')
        for ratio, bpm in tempos:
            tempo_map[position + length * Fraction(str(ratio)).limit_denominator(100_000)] = bpm

        beats = raw['bars'][original_index] if original_index < len(raw['bars']) else ()
        for start_ticks, duration_ticks, chord, notes, effects, *brush in beats:
            start = position + _beats(start_ticks)
            duration = _beats(duration_ticks)
            timeline.append((start, duration, original_index + 1, chord, notes, effects, brush[0] if brush else ''))
            if len(timeline) > 50000:
                raise ValueError('展开后的节拍超过 50000 个，请分段导入')
        position += length
        if position > 100000:
            raise ValueError('原谱超过 100000 拍，请分段导入')

    for start, duration, bar, chord, notes, effects, brush in sorted(timeline, key=lambda beat: beat[0]):
        if start < 0 or duration < Fraction(1, TICKS_PER_BEAT):
            notices.add('略过曲首拍前或无时值的装饰音')
            continue
        if chord:
            try:
                chord_marks[start] = chord_name(chord.replace('♯', '#').replace('♭', 'b'))
            except ValueError:
                notices.add('部分和弦名称未保留')
        if effects:
            notices.add('特殊奏法按普通拨弦导入')
        fully_muted = notes and all(note[4] for note in notes)
        if fully_muted:
            muted_hits += 1
        brushed = [note for note in notes if not note[4]]
        if brush and len(brushed) >= 2 and all(note[3] is None for note in brushed):
            brush_directions[start] = (UP if brush == 'up' else DOWN, frozenset(note[0] for note in brushed))
        for string, fret, note_id, tie_origin, dead, effect, *technique in notes:
            if dead:
                muted_notes += int(not fully_muted)
                continue
            if type(string) is not int or type(fret) is not int or not (1 <= string <= 6 and 0 <= fret <= 24):
                raise ValueError(f'第 {bar} 小节含无弦号或超出 0～24 品的音符')
            points = technique[0] if technique else None
            slide = len(technique) > 1 and technique[1]
            slides += int(bool(slide))
            if effect and not (points or slide):
                notices.add('特殊奏法按普通拨弦导入')
            end = start + duration
            previous = last_note.get(string)
            if tie_origin is not None and previous and previous[4] == tie_origin and previous[2] == start and previous[3] == fret:
                previous[2] = end
                previous[4] = note_id
                if points:
                    bends.append((previous, start, duration, points))
                continue
            if previous and previous[2] > start:
                if previous[0] == start:
                    if previous[3] != fret:
                        raise ValueError(f'第 {bar} 小节同拍同弦有不同品位，请先分开声部')
                    previous[2] = max(previous[2], end)
                    previous[4] = note_id
                    continue
                previous[2] = start
                notices.add('同弦重拨时结束前一个音')
            attack = [start, string, end, fret, note_id]
            attacks.append(attack)
            last_note[string] = attack
            if points:
                bends.append((attack, start, duration, points))
            if len(attacks) > 50000:
                raise ValueError('展开后的音符超过 50000 个，请分段导入')

    if not attacks:
        raise ValueError('这条轨道没有可导入的有音高音符')
    if any(not 20 <= bpm <= 360 for bpm in tempo_map.values()):
        raise ValueError('原谱速度超出 20～360 BPM，请在 Guitar Pro 中调整后导入')
    tempos = []
    for start, bpm in sorted(tempo_map.items()):
        micros = 60_000_000 / bpm
        if not tempos or tempos[-1][1] != micros:
            tempos.append((start, micros))
    if bends:
        clock = GuitarScore((), position, tempo_map[0], tuple(tempos))
        additions = list(_bend_attacks(attacks, bends, clock)) if approximate_bends else []
        attacks.extend(attack for group in additions for attack in group)
        if len(attacks) > 50000:
            raise ValueError('推弦近似后超过 50000 个音符，请取消换品近似或分段导入')
        if additions:
            notices.add(f'{len(additions)} 处长推弦用换品重拨近似')
        if len(additions) < len(bends):
            notices.add(f'{len(bends) - len(additions)} 处推弦保留原品与延音')
    if slides:
        notices.add(f'{slides} 处滑音保留已写的品位和拍点')
    if muted_hits:
        notices.add(f'{muted_hits} 处纯闷击按原时值留空')
    if muted_notes:
        notices.add(f'{muted_notes} 个与有声音符同拍的闷音已略过')
    groups = defaultdict(lambda: [None] * 6)
    for start, string, end, fret, _ in attacks:
        groups[start, end - start][string - 1] = fret
    cells = []
    for (start, length), values in sorted(groups.items()):
        sweep, strings = brush_directions.get(start, (0, frozenset()))
        if sweep and strings != {i for i, value in enumerate(values, 1) if value is not None}:
            sweep = 0
        cells.append(FingerCell(start, length, tuple(values), sweep))
    swept = sum(bool(cell.sweep) for cell in cells)
    if swept:
        notices.add(f'保留 {swept} 处扫弦方向，游戏按法由近似转译匹配')
    document = DuetTrackDocument(cells, bpm=tempo_map[0], meter=base_meter, bars=len(order),
        tempo_map=tempos, bar_changes=changes, capo=track.capo, tuning=track.tuning,
        default_timbre='distorted' if raw['program'] in (29, 30) else 'clean', chord_changes=chord_marks.items())
    return document, tuple(sorted(notices))


def import_guitar_pro_duet(source, recorded_index, live_index, approximate_bends=True):
    from .duet_composition import TRACK_NAMES
    from .duet_project import DuetProject

    if recorded_index == live_index:
        raise ValueError('录音伴奏和现场声部请选择不同的六弦吉他轨道')
    project = DuetProject()
    notices = {}
    for role, index in (('recorded', recorded_index), ('live', live_index)):
        try:
            project.documents[role], notices[role] = import_guitar_pro_track(source, index, approximate_bends)
        except ValueError as error:
            raise ValueError(f'{TRACK_NAMES[role]}导入失败：{error}') from error
    return project, notices
