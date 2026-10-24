"""Recovery steps for MIDI loading and pitch-translation failures."""
from .composer_guidance import file_guidance
from .translation_failures import summarize_failures


def translation_error(message,*,candidates=None):
    text=str(message)
    candidates=candidates or getattr(message,'candidates',())
    if candidates and any(row['transpose_semitones']>0 for row in candidates):
        diagnostics=summarize_failures(candidates)
        return (f'转译失败：已比较 {len(candidates)} 个升降调方案，全部未通过。'+diagnostics['summary']+
                '请在“移调比较”双击失败方案查看具体位置；核对“主旋律通道”或重新导入旋律轨。'
                '若失败位置实际是伴奏，可开启“过滤 E2 以下伴奏”，或修正“声部与去留”标记后重新生成；'
                '过滤不会删除主旋律和待核对音。主旋律只允许整体统一移调；若旋律自身跨度无法容纳，当前游戏无法原样表达。')
    if any(word in text for word in ('音域','旋律音高','保留旋律','指定音')):
        if '自动升降调' not in text:text+=' 请在“整曲移调”尝试“自动升降调（±24半音）”。'
        text+=' 低音伴奏可勾选“过滤 E2 以下伴奏”。主旋律和待核对音不会因此删除，仍失败时请核对“主旋律通道”，或重新导入更合适的旋律轨。'
    elif '同拍取最高音' in text:
        text+=' 请在 MIDI 导入窗口勾选“同拍取最高音”，或改选“混合转译：主旋律优先”。'
    elif '没有可转换的音符' in text:
        text+=' 请重新选择有音符的轨道；若设置过声部简化／低音过滤，可关闭相关选项后重试。'
    elif any(word in text for word in ('拍号','时值','小节边界')):
        text+=' 请在原 MIDI 编辑器中核对拍号位置及音符时值，再另存为 MIDI 0／1 后重试；软件不会自动挪动这些节拍。'
    elif '10000 个起拍' in text or '分段' in text:
        text+=' 请先只导入主旋律轨，或在原编辑器按乐段拆分文件，再分别转译。'
    elif '先选择' in text:
        text+=' MIDI 请从主窗口“导入 MIDI”选择轨道，并使用“混合转译：主旋律优先”。'
    return '转译失败：'+text


def midi_error(error,stage='导入'):
    if isinstance(error,(FileNotFoundError,PermissionError)):
        return file_guidance(error,'MIDI '+stage)
    text=str(error)
    if 'MIDI 2' in text or 'SMPTE' in text:
        return 'MIDI '+stage+'失败：'+text+'。请在原软件导出使用 PPQ 计时的标准 MIDI 0 或 MIDI 1，再导入；仅改文件扩展名无效。'
    if '非鼓轨音符' in text:
        return 'MIDI '+stage+'失败：'+text+'。请确认文件含旋律／吉他音符轨，且音符未全部放在鼓通道（MIDI 第 10 通道）；重新导出后导入。'
    if isinstance(error,(EOFError,UnicodeError)) or '标准 MIDI' in text:
        return 'MIDI '+stage+'失败：文件不完整或不是受支持的标准 MIDI。请从原软件重新导出 .mid／.midi（MIDI 0／1）；MP3、WAV、Guitar Pro 文件不能直接改扩展名导入。'
    if stage=='读取':
        return 'MIDI 读取失败：'+text+'。请确认所选文件可访问；文件过大或音符过多时，先选轨或分段导出后重试。'
    return translation_error(text)
