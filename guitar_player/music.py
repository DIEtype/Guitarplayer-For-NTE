"""Reference tuning only; the game's samples still require measurement."""
from .score import pitch_name

# Indexed by actual guitar string number, 1 = thinnest/highest.
STANDARD_TUNING = {1: 64, 2: 59, 3: 55, 4: 50, 5: 45, 6: 40}


def fret_pitch(string, fret, tuning=None):
    if string not in STANDARD_TUNING or not 0 <= fret <= 24:
        raise ValueError("弦号为 1～6，品位为 0～24")
    return (tuning or STANDARD_TUNING)[string] + fret


def fret_name(string, fret):
    return pitch_name(fret_pitch(string, fret))


def positions_for_pitch(pitch, allowed_frets=range(25), tuning=None):
    tuning = tuning or STANDARD_TUNING
    return [(string, fret) for string in range(1, 7) for fret in allowed_frets
            if tuning[string] + fret == pitch]


def pitch_range_hint(pitches):
    """Explain standard-tuning limits without changing or discarding any note."""
    pitches=tuple(pitches)
    low=min(STANDARD_TUNING.values());high=max(STANDARD_TUNING.values())+24
    if not pitches or all(low<=pitch<=high for pitch in pitches):return ''
    bottom,top=min(pitches),max(pitches)
    detail=(f'标准定弦 0～24 品音域为 {pitch_name(low)}～{pitch_name(high)}（MIDI {low}～{high}）；'
            f'待保留音域 {pitch_name(bottom)}～{pitch_name(top)}。')
    minimum=low-bottom;maximum=high-top
    if minimum>maximum:
        return detail+'这些音的跨度超过标准音域，整体升降调无法全部容纳；请检查所选轨道或声部。'
    if minimum>24 or maximum<-24:
        return detail+'所需整体移调超过当前 ±24 半音范围；请检查所选轨道或声部。'
    if minimum>0:
        return detail+f'按音域计算至少需整体升 {minimum} 半音；自动降调不会升高低音。请在转译窗口“整曲移调”选择“自动升降调（±24半音）”，或手动升调；同时核对所选旋律轨道。'
    return detail+f'按音域计算至少需整体降 {-maximum} 半音；可尝试整曲降调。'
