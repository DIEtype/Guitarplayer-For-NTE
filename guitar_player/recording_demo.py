"""A public-domain two-part exercise with a shared two-bar count-in."""
from dataclasses import replace

from .game_profile import ROMAN
from .guitar import parse_guitar

BPM = 96
LEAD_BEATS = 8


def demo_parts():
    # G major: melody on the first string. Each row is one 4/4 measure.
    melody = (((3,1),(3,1),(10,1),(10,1)), ((12,1),(12,1),(10,2)),
              ((8,1),(8,1),(7,1),(7,1)), ((5,1),(5,1),(3,2)),
              ((10,1),(10,1),(8,1),(8,1)), ((7,1),(7,1),(5,2)),
              ((10,1),(10,1),(8,1),(8,1)), ((7,1),(7,1),(5,2)),
              ((3,1),(3,1),(10,1),(10,1)), ((12,1),(12,1),(10,2)),
              ((8,1),(8,1),(7,1),(7,1)), ((5,1),(5,1),(3,2)))
    live = '{III} 0:8 | ' + ' | '.join(' '.join('{'+ROMAN[fret-1]+'} 1:'+str(beats) for fret,beats in bar) for bar in melody)
    harmony = ('G','C','G','D','G','D','G','D','G','C','G','G')
    recorded = '{G} 0:8 | ' + ' | '.join('{'+chord+'} ↓:2 ↑:2' for chord in harmony)
    return {name: replace(parse_guitar(text, BPM), time_signature=(4,4), measures=14)
            for name,text in (('recorded',recorded),('live',live))}


def load_part(app, part):
    from tkinter import messagebox
    if app.busy:raise ValueError('请先停止当前播放，再载入示范声部')
    if app.dirty and not messagebox.askyesno('载入双声部示范', '当前有未保存的修改，仍要载入示范声部吗？', parent=app):return False
    if app.tab_editor is not None and app.tab_editor.winfo_exists():
        raise ValueError('请先保存并关闭编谱窗口，再载入示范声部')
    label = '录音伴奏' if part == 'recorded' else '现场旋律'
    score = demo_parts()[part]
    action='请在“① 自动录制”校准开始／停止录音，再点“5 秒后自动录制”。' if part=='recorded' else '请填入自动录制后保存的新录音码，再开始合奏；现场偏移先用 0。'
    app.accept_visual_score(score, '《小星星》'+label+'已载入：96 BPM，前奏休止 5 秒。'+action, chord_mode=part=='recorded')
    app.file_path=None
    return True
