"""A short public-domain melody with explicit rhythm and no game page changes."""


def _phrase(notes):
    rows = [["---"] * len(notes) for _ in range(6)]
    rhythms = []
    for index, (string, fret, beats) in enumerate(notes):
        rows[string-1][index] = f"{fret:<3}".replace(" ", "-")
        rhythms.append(str(beats))
    return "节奏: " + " ".join(rhythms) + "\n" + "\n".join(
        label + "|" + "".join(row) + "|" for label, row in zip("eBGDAE", rows))


DEMO_BPM = 100
DEMO_TAB = (
    "// 《小星星》前两句；标准定弦参考，游戏音高待核对\n"
    "// 游戏用 Z 切到品格模式，起始页为第 1 页；本曲只用 I、III、V 三个区域\n"
    "// 节奏行逐个对应从左到右的拨弦，字符间距仅用于排版，不决定时值\n"
    + _phrase([(2,1,1),(2,1,1),(1,3,1),(1,3,1),(1,5,1),(1,5,1),(1,3,2)])
    + "\n\n"
    + _phrase([(1,1,1),(1,1,1),(2,5,1),(2,5,1),(2,3,1),(2,3,1),(2,1,2)])
    + "\n"
)
