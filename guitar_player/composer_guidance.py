"""Actionable instructions for score-editing failures and form validation."""
import json


def integer_field(value,label,minimum,maximum):
    text=str(value).strip()
    if not text.isascii() or not text.isdecimal():
        raise ValueError(f'“{label}”请填写 {minimum}～{maximum} 的整数，再重试。')
    number=int(text)
    if not minimum<=number<=maximum:
        raise ValueError(f'“{label}”超出范围；请填写 {minimum}～{maximum}。')
    return number


def measure_range(first,last,bars):
    a=integer_field(first,'起始小节',1,bars)-1
    b=integer_field(last,'结束小节',1,bars)-1
    if a>b:raise ValueError('起始小节不能晚于结束小节；请交换两个小节号，再重试。')
    return a,b


def edit_guidance(message):
    if '→' in message:return message
    if '延音' in message and any(word in message for word in ('边界','插入位置','断开')):
        return message+'。请选中边界处延音，点“音符与奏法 → 延音线 → 断开延音”（Shift+T），或扩大范围以包含完整延音。'
    if '反复' in message and any(word in message for word in ('相交','重叠','嵌套','清除')):
        return message+'。请选中相交段的小节，在“小节与反复 → 重复记号 → 展开／清除”中清除当前段落反复线，再重试。'
    if '前一小节拍号一致' in message or '来源小节长度不同' in message:
        return message+'。请在“小节与反复 → 小节设置”中将本小节拍号改为与前一小节一致。'
    if '第 1 小节不能重复' in message or '% 重复小节必须位于第 2' in message:
        return message+'。请先选中第 2 小节或更后的小节，再点“% 前小节”。'
    if '连续录入超出' in message:
        return message+'。请减少本次输入的音符数，或在“音符与奏法”选择更短时值后重试；剩余音符可在下一小节继续录入。'
    if '清除本小节的音符' in message:
        return message+'。请逐列选中本小节音符，按 Shift+Del 清空整列；全部清空后再点“小节与反复 → 删除空小节”。'
    if '空间不足' in message:
        return message+'。请在“音符与奏法”缩短本小节内的音符时值，或将多出的音符移到新插入的小节后再重试。'
    if '需要可用的和弦音高表' in message or '请先标记和弦' in message:
        return message+'。请在“音符与奏法 → 和弦”选择游戏支持的和弦，点“标记此处”；数字品位无需和弦音高表。'
    if '标为未知或静音' in message:
        return message+'。请在谱面将该弦的 X／点改为数字品位，或选择可发声的弦；只有核实过音高后，才在“近似转译… → 和弦音高表”中修正表值。'
    return message


def file_guidance(error,action='保存'):
    if isinstance(error,PermissionError):
        return f'{action}失败：没有文件访问权限。请关闭占用该文件的程序，或选择有写入权限的文件夹后重试。'
    if isinstance(error,FileNotFoundError):
        return f'{action}失败：找不到文件或文件夹。请重新选择文件，并确认文件未被移动或删除。'
    if isinstance(error,UnicodeError):
        return f'{action}失败：文件编码无法识别。请使用软件保存的 .gscore，或将 TAB 文本另存为 UTF-8 后重试。'
    if isinstance(error,json.JSONDecodeError):
        return f'{action}失败：曲谱文件内容不完整或格式有误。请重新导出 .gscore 或从备份恢复；原文件未被覆盖。'
    if isinstance(error,(KeyError,TypeError,IndexError,OverflowError)):
        return f'{action}失败：曲谱内容缺失或格式不受支持。请使用软件保存的 .gscore／TAB 文本，或从原软件重新导出后重试。'
    if isinstance(error,OSError):
        return f'{action}失败：{error}。请确认文件夹可访问、磁盘有剩余空间；保存时可另选位置重试。'
    return f'{action}失败：{edit_guidance(str(error))}'
