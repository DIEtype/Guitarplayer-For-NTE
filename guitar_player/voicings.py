"""Explicit six-string pitch tables; reference fingerings are never measurements."""
from dataclasses import dataclass
import json
from pathlib import Path

from .game_profile import CHORD_NAMES
from .music import fret_pitch
from .score import pitch_name,pitch_number

# Low string 6 to high string 1. These are reference shapes, not game captures.
REFERENCE_SHAPES={
    'G':'320003','Am':'x02210','Bm':'x24432','C':'x32010','D':'xx0232','Em':'022000',
    'Cmaj7':'x32000','C7':'x32310','Cm7':'x35343','G7':'320001','C9':'x32333','Cm9':'x3133x',
    'C5':'x355xx','D5':'x577xx','G5':'355xxx','A5':'x022xx','Cmaj9':'x3243x','Cadd9':'x32033',
    'G/B':'x20033','Am7/G':'302010','D/F#':'200232','F/A':'x03211','A':'x02220','Dm':'xx0231',
}


@dataclass(frozen=True)
class Voicing:
    name:str
    tone:str
    pitches:tuple  # string 1..6; None unknown, -1 muted
    measured:bool=False

    def __post_init__(self):
        if self.name not in CHORD_NAMES or self.tone not in ('clean','distorted') or len(self.pitches)!=6:
            raise ValueError('和弦音高表的名称、音色或弦数无效')
        if type(self.measured) is not bool or any(p is not None and (type(p) is not int or not -1<=p<=127) for p in self.pitches):
            raise ValueError('每根弦应填 MIDI 0～127、音名、x 或 ?')

    @property
    def complete(self):return all(p is not None for p in self.pitches) and any(p is not None and p>=0 for p in self.pitches)


def parse_pitch(text):
    text=str(text).strip()
    if text in ('','?'):return None
    if text.lower() in ('x','mute','静音'):return -1
    if text.isascii() and text.isdigit():
        value=int(text)
        if 0<=value<=127:return value
        raise ValueError('MIDI 音高应在 0～127')
    return pitch_number(text)


def format_pitch(value):return '?' if value is None else 'x' if value==-1 else pitch_name(value)


def reference_voicings(tone='clean'):
    return {name:Voicing(name,tone,tuple(-1 if f=='x' else fret_pitch(s,int(f)) for s,f in enumerate(reversed(shape),1)))
            for name,shape in REFERENCE_SHAPES.items()}


def read_voicings(path):
    path=Path(path)
    if not path.exists():return {}
    data=json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(data,dict) or data.get('version')!=1 or not isinstance(data.get('rows'),list) or len(data['rows'])>48:
        raise ValueError('和弦音高表格式或版本无效')
    result={}
    for row in data['rows']:
        if not isinstance(row,dict):raise ValueError('和弦音高表行格式无效')
        raw=row.get('pitches')
        if not isinstance(raw,list):raise ValueError('和弦音高表应有六个音高')
        item=Voicing(row.get('name'),row.get('tone'),tuple(raw),row.get('measured',False))
        key=(item.tone,item.name)
        if key in result:raise ValueError('和弦音高表存在重复项')
        result[key]=item
    return result


def write_voicings(path,rows):
    data={'version':1,'string_order':[1,2,3,4,5,6],
          'rows':[{'name':v.name,'tone':v.tone,'pitches':list(v.pitches),'measured':v.measured} for v in rows.values()]}
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.json.tmp');temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(path)


def available_voicings(rows,tone,reference=True):
    result=reference_voicings(tone) if reference else {}
    for (row_tone,name),item in rows.items():
        if row_tone==tone:
            # An incomplete measured entry must not fall back to guessed pitches.
            result.pop(name,None)
            if item.complete and (reference or item.measured):result[name]=item
    return result
