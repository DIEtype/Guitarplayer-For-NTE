"""App-local music collection and persistent, cancellable reference WAV export."""
from dataclasses import dataclass,replace
from fractions import Fraction
import json
from pathlib import Path
import re
import shutil
import wave

from .guitar import parse_guitar,apply_capo
from .tab import parse_tab
from .measures import parse_changes,apply_changes

from .paths import app_root
APP_ROOT=app_root()
GROUPS={'曲谱':('scores',{'.gscore','.txt'}),'MIDI':('midi',{'.mid','.midi'}),'WAV':('audio',{'.wav'})}


@dataclass(frozen=True)
class LibraryItem:
    path:Path
    kind:str


def clean_name(name):
    name=re.sub(r'[<>:"/\\|?*\x00-\x1f]','_',str(name)).strip(' .')[:100] or '未命名乐曲'
    if name.split('.')[0].upper() in {'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))}:name='_'+name
    return name


class MusicLibrary:
    def __init__(self,root=None):
        self.root=Path(root) if root is not None else APP_ROOT/'library'
        for folder,_ in GROUPS.values():(self.root/folder).mkdir(parents=True,exist_ok=True)

    def new_path(self,name,kind,suffix):
        folder,extensions=GROUPS[kind]
        if suffix.lower() not in extensions:raise ValueError('文件类型与曲库分类不符')
        stem=clean_name(name);path=self.root/folder/(stem+suffix.lower());index=2
        while path.exists() or path.with_suffix(path.suffix+'.partial').exists():
            path=self.root/folder/f'{stem} ({index}){suffix.lower()}';index+=1
        return path

    def items(self):
        result=[]
        for kind,(folder,extensions) in GROUPS.items():
            result.extend(LibraryItem(path,kind) for path in (self.root/folder).iterdir()
                          if path.is_file() and path.suffix.lower() in extensions)
        return sorted(result,key=lambda item:item.path.stat().st_mtime,reverse=True)

    def add_file(self,source):
        source=Path(source)
        kind=next((kind for kind,(_,extensions) in GROUPS.items() if source.suffix.lower() in extensions),None)
        if kind is None:raise ValueError('请选择 .gscore、.txt、.mid、.midi 或 .wav 文件')
        if source.resolve().parent==(self.root/GROUPS[kind][0]).resolve():return source
        path=self.new_path(source.stem,kind,source.suffix);shutil.copy2(source,path)
        return path

    def export_audio(self,score,name,cancel=None):
        from .audio import render_wav
        from .player import Cancelled
        path=self.new_path(name,'WAV','.wav');temporary=path.with_suffix('.wav.partial')
        try:
            render_wav(score,temporary,cancel)
            if cancel and cancel.is_set():raise Cancelled()
            temporary.replace(path)
            return path
        finally:temporary.unlink(missing_ok=True)


def read_score(path,*,_pitch_output=False):
    """Read a score for offline preview without changing the main editor."""
    path=Path(path);raw=path.read_text(encoding='utf-8-sig')
    if path.suffix.lower()=='.gscore' and isinstance(data:=json.loads(raw),dict) and data.get('kind')=='fingerstyle_source':
        raise ValueError('这是指弹原谱；请点“载入编谱”进入原谱编写，再试听或转译')
    if path.suffix.lower()!='.gscore':
        is_tab=any(re.match(r'\s*[A-Ga-g1-6]\s*\|',line) for line in raw.splitlines())
        return (parse_tab(raw,_pitch_output=_pitch_output) if is_tab else parse_guitar(raw)),raw
    from .midi_import import scale_tempos
    from .game_profile import GameProfile
    data=json.loads(raw)
    if not isinstance(data,dict) or data.get('version') not in (1,2,3) or not isinstance(data.get('text'),str):raise ValueError('曲谱格式或版本无效')
    profile=GameProfile.from_dict(data['profile'])
    text=data['text'];bpm=float(data['bpm']);notation=data.get('notation','commands')
    if notation not in ('tab','commands'):raise ValueError('不支持的曲谱写法')
    capo=data.get('capo',0)
    score=parse_tab(text,bpm,data.get('tab_step','1/4'),capo,_pitch_output=_pitch_output) if notation=='tab' else apply_capo(parse_guitar(text,bpm),capo)
    tempos=tuple((Fraction(str(at)),float(tempo)) for at,tempo in data.get('tempo_map',[]))
    tempo_bpm=float(data.get('tempo_bpm',bpm));meter=data.get('time_signature',[4,4]);measures=data.get('measures',0)
    if (not 0<tempo_bpm<=360 or any(at<0 or not 0<tempo<60_000_001 for at,tempo in tempos)
        or (tempos and tempos[0][0]!=0) or any(a[0]>=b[0] for a,b in zip(tempos,tempos[1:]))):raise ValueError('速度变化数据无效')
    if (not isinstance(meter,list) or len(meter)!=2 or any(type(n) is not int for n in meter)
        or not 1<=meter[0]<=16 or meter[1] not in (1,2,4,8,16)
        or type(measures) is not int or not 0<=measures<=25000):raise ValueError('拍号或小节数无效')
    score=replace(score,tempo_map=scale_tempos(tempos,tempo_bpm,bpm),time_signature=tuple(meter),measures=measures)
    from .chord_score import parse_chord_changes
    score=replace(score,chord_changes=parse_chord_changes(data.get('chord_changes',[]),mixed=profile.mode=='mixed'))
    return apply_changes(score,parse_changes(data.get('bar_changes',[])),data.get('default_timbre','clean'),score.tempo_map),text


def wav_info(path):
    with wave.open(str(path),'rb') as stream:
        if stream.getcomptype()!='NONE' or stream.getframerate()<=0:raise ValueError('仅支持 PCM WAV 音频')
        return stream.getnframes()/stream.getframerate(),stream.getframerate(),stream.getnchannels()
