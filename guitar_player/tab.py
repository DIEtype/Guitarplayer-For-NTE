"""ASCII TAB with optional exact per-note rhythm, or a fixed-grid fallback."""
from fractions import Fraction
import re

from .game_profile import ROMAN
from .guitar import GuitarError, GuitarEvent, GuitarScore,apply_capo


def _example():
    rows = [list("-" * 32) for _ in range(6)]
    for row, column, fret in ((0, 2, 0), (0, 6, 2), (0, 10, 3), (0, 14, 2),
                              (0, 18, 0), (1, 22, 3), (1, 26, 1), (1, 30, 0)):
        rows[row][column] = str(fret)
    return ("// 六线谱：上面是最细的 1 弦，下面是最粗的 6 弦\n"
            "// 每字符格 1/4 拍；每隔四格弹一次。0 代表空弦，不是休止。\n" +
            "\n".join(label + "|" + "".join(row) + "|" for label, row in zip(("e", "B", "G", "D", "A", "E"), rows)) + "\n")


DEFAULT_TAB = _example()


def _rhythm_tokens(text, line):
    result = []
    for token in text.replace("|", " ").split():
        rest = token.lower().startswith("r:") or token.startswith("休止:")
        duration = token.split(":", 1)[1] if rest else token
        try:
            beats = Fraction(duration)
        except (ValueError, ZeroDivisionError):
            raise GuitarError(f"第 {line} 行：节奏请写拍数，例如 1、1/2、1/3、3/2；休止写 r:1/2") from None
        if not Fraction(1, 960) <= beats <= 128:
            raise GuitarError(f"第 {line} 行：逐音拍数应在 1/960～128 之间")
        result.append((rest, beats))
    if not result:
        raise GuitarError(f"第 {line} 行：节奏行不能为空")
    return result


def parse_tab(text, bpm=100, step_beats="1/4",capo=0,*,_pitch_output=False,_members_output=False):
    if type(capo) is not int or not 0<=capo<=24:raise GuitarError('Capo 应为 0～24 品')
    from .music import STANDARD_TUNING
    def notes_for(members):
        if _members_output:return tuple((s,f) for s,f,_ in members)
        return tuple(STANDARD_TUNING[s]+f+capo if _pitch_output else s for s,f,_ in members)
    if not 20 <= bpm <= 360:
        raise GuitarError("速度应在 20～360 BPM 之间")
    try:
        step = Fraction(step_beats)
    except (ValueError, TypeError, ZeroDivisionError):
        raise GuitarError("每格拍数应为正数或分数，例如 1/4、1/2") from None
    if not Fraction(1, 64) <= step <= 4:
        raise GuitarError("每格拍数应在 1/64～4 之间")
    lines, rhythms = [], {}
    for number, original in enumerate(text.splitlines(), 1):
        raw = original.split("//", 1)[0].rstrip()
        if not raw.strip():
            continue
        rhythm = re.fullmatch(r"\s*(?:rhythm|节奏)\s*[:：]\s*(.*)", raw, re.IGNORECASE)
        if rhythm:
            if len(lines) % 6 or len(lines)//6 in rhythms:
                raise GuitarError(f"第 {number} 行：节奏行应放在每段六线谱之前，每段只写一行")
            rhythms[len(lines)//6] = (_rhythm_tokens(rhythm.group(1), number), number)
            continue
        match = re.fullmatch(r"\s*([A-Ga-g1-6])\s*\|([0-9\- |]*)", raw)
        if not match:
            raise GuitarError(f"第 {number} 行：请写六线谱，例如 e|--0---2--|；滑音、击勾弦等符号暂不支持")
        body = match.group(2)
        lines.append((number, body, match.start(2)))
    if not lines or len(lines) % 6:
        raise GuitarError("六线谱每段需要恰好六行，从上到下是吉他 1～6 弦；段间可留空行")
    if any(index >= len(lines)//6 for index in rhythms):
        raise GuitarError("最后一行节奏后还需要一段六线谱")
    events, cursor = [], Fraction(0)
    for block_start in range(0, len(lines), 6):
        block = lines[block_start:block_start+6]
        lengths = {len(body.replace("|", "")) for _, body, _ in block}
        bars = {tuple(i for i, char in enumerate(body) if char == "|") for _, body, _ in block}
        if len(lengths) != 1 or len(bars) != 1 or not next(iter(lengths)):
            raise GuitarError(f"第 {block[0][0]} 行起：六根弦的字符格数与小节线必须上下对齐")
        width = next(iter(lengths))
        if width > 10000:
            raise GuitarError("一段六线谱超过 10000 格，请分段")
        attacks = {}
        for string, (number, body, offset) in enumerate(block, 1):
            for match in re.finditer(r"\d+", body):
                fret = int(match.group())
                if not 0 <= fret <= 24:
                    raise GuitarError(f"第 {number} 行：品位应为 0～24，0 是空弦")
                column = len(body[:match.start()].replace("|", ""))
                attacks.setdefault(column, []).append((string, fret, (number, offset+match.start(), len(match.group()))))
        ordered = sorted(attacks)
        # A rhythm row describes onset groups in order. Horizontal spacing only
        # aligns the six strings and no longer controls their timing.
        if block_start//6 in rhythms:
            tokens, rhythm_line = rhythms[block_start//6]
            if sum(not rest for rest, _ in tokens) != len(ordered):
                raise GuitarError(f"第 {rhythm_line} 行：节奏行需要 {len(ordered)} 个音符拍数；休止 r:拍数 不计入音符数")
            position = 0
            initial_fret = attacks[ordered[0]][0][1] if ordered else 0
            current = ROMAN[initial_fret-1] if initial_fret else "Zero"
            for rest, beats in tokens:
                if rest:
                    events.append(GuitarEvent((), cursor, beats, current, f"休止:{beats}", rhythm_line))
                else:
                    column = ordered[position]
                    members = attacks[column]
                    if not (_pitch_output or _members_output) and len({member[1] for member in members}) > 1:
                        raise GuitarError(f"第 {block[0][0]} 行起第 {column+1} 格：同拍跨品位，请改为分解拨弦")
                    fret = members[0][1]
                    current = ROMAN[fret-1] if fret else "Zero"
                    events.append(GuitarEvent(notes_for(members), cursor, beats, current,
                                              f"{fret}品:{beats}", members[0][2][0], tuple(member[2] for member in members)))
                    position += 1
                cursor += beats
            if cursor > 100000 or len(events) > 50000:
                raise GuitarError("六线谱过长，请分段演奏")
            continue
        if not ordered:
            events.append(GuitarEvent((), cursor, width*step, "Zero", "休止", block[0][0]))
        else:
            first_fret = attacks[ordered[0]][0][1]
            first_chord = ROMAN[first_fret-1] if first_fret else "Zero"
            if ordered[0]:
                events.append(GuitarEvent((), cursor, ordered[0]*step, first_chord, "休止", block[0][0]))
            for index, column in enumerate(ordered):
                members = attacks[column]
                frets = {member[1] for member in members}
                if not (_pitch_output or _members_output) and len(frets) > 1:
                    detail = "、".join(f"{s}弦{f}品" for s, f, _ in members)
                    raise GuitarError(f"第 {block[0][0]} 行起第 {column+1} 格：同拍跨品位（{detail}）。游戏一次只选一个品位，请改为分解拨弦")
                fret = members[0][1]
                end = ordered[index+1] if index+1 < len(ordered) else width
                label = f"{fret}品"
                events.append(GuitarEvent(notes_for(members), cursor+column*step,
                                          (end-column)*step, ROMAN[fret-1] if fret else "Zero", label,
                                          members[0][2][0], tuple(member[2] for member in members)))
        cursor += width*step
        if cursor > 100000 or len(events) > 50000:
            raise GuitarError("六线谱过长，请分段演奏")
    result=GuitarScore(tuple(events), cursor, float(bpm))
    return result if _pitch_output or _members_output else apply_capo(result,capo)
