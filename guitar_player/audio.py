"""Local reference audio, synthesized from standard tuning; never game input."""
from array import array
import math
from pathlib import Path
import uuid
import wave

from .game_profile import ROMAN
from .guitar import UP, DOWN
from .music import fret_pitch
from .player import Cancelled, PreviewBackend
from .measures import timbre_points

RATE = 22050


def render_wav(score, path, cancel=None,*,pitch_events=False,strums=None):
    if score.seconds > 600:
        raise ValueError("本地试听限 10 分钟，请分段试听；游戏演奏没有此限制")
    frames = int((score.seconds + .15) * RATE)
    samples = array("f", [0.0]) * frames
    tones=timbre_points(score);tone_index=0
    for event in score.events:
        while tone_index+1<len(tones) and tones[tone_index+1][0]<=event.start:tone_index+=1
        distorted=tones[tone_index][1]=='distorted'
        if cancel and cancel.is_set():
            raise Cancelled()
        if not pitch_events and event.chord != "Zero" and event.chord not in ROMAN:
            raise ValueError("本地合成试听目前使用品格模式；游戏内置和弦的实际指法尚未核对")
        fret = 0 if pitch_events or event.chord == "Zero" else ROMAN.index(event.chord)+1
        voices = []
        ordered=event.notes
        direction=(strums or {}).get(event.start)
        if pitch_events and direction==DOWN:ordered=tuple(reversed(ordered))
        for index,note in enumerate(ordered):
            if not pitch_events and note in (UP, DOWN):
                voices.extend((s, i*.018) for i, s in enumerate(range(1,7) if note == UP else range(6,0,-1)))
            else:
                voices.append((note, index*.018 if pitch_events and direction else 0))
        for string, offset in voices:
            pitch = string if pitch_events else fret_pitch(string, fret)
            frequency = 440 * 2**((pitch-69)/12)
            start=round((score.seconds_at(event.start)+offset)*RATE)
            duration=score.seconds_at(event.start+event.beats)-score.seconds_at(event.start)
            length=min(frames-start,round(min(10,max(.10,duration*.98))*RATE))
            decay=4.5 if duration<=.75 else max(.8,min(4.5,3/duration))
            amplitude = 6500 / math.sqrt(max(1,len(voices)))
            phase_step = 2*math.pi*frequency/RATE
            for index in range(max(0,length)):
                if index % 4096 == 0 and cancel and cancel.is_set():
                    raise Cancelled()
                phase = index*phase_step
                envelope = min(1,index/55) * math.exp(-decay*index/RATE)
                envelope*=min(1,(length-index)/(RATE*.02))
                wave_value=math.sin(phase)+.28*math.sin(2*phase)+.10*math.sin(3*phase)
                value=(.8*math.tanh(3.2*(wave_value+.15*math.sin(5*phase))) if distorted else wave_value)*envelope
                samples[start+index] += value*amplitude
    pcm = array("h", (max(-32767,min(32767,round(value))) for value in samples))
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(RATE)
        stream.writeframes(pcm.tobytes())
    return Path(path)


class SoundPreviewBackend(PreviewBackend):
    def __init__(self, score, cache, cancel=None, sound=None,*,pitch_events=False,strums=None):
        if sound is None:
            import winsound
            sound = winsound
        self.sound = sound
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.source = self.cache / (uuid.uuid4().hex + ".wav")
        self.paths = [self.source]
        self.started, self.playing, self.closed = False, False, False
        try:
            render_wav(score, self.source, cancel,pitch_events=pitch_events,strums=strums)
        except Exception:
            self.source.unlink(missing_ok=True)
            raise

    def acquire_target(self):
        return "本地合成试听 · 标准定弦参考音色 · 不向游戏发送输入"

    def sync_playback(self, elapsed, paused):
        if self.closed or elapsed < 0:
            return
        if paused:
            if self.playing:
                self.sound.PlaySound(None, 0)
                self.playing = False
            return
        if self.playing:
            return
        path = self.source
        if self.started:
            # PlaySound cannot seek: resume from a new WAV slice of the source.
            with wave.open(str(self.source), "rb") as stream:
                offset = min(stream.getnframes(), max(0, round(elapsed*stream.getframerate())))
                stream.setpos(offset)
                params, data = stream.getparams(), stream.readframes(stream.getnframes()-offset)
            path = self.cache / (uuid.uuid4().hex + ".wav")
            self.paths.append(path)
            with wave.open(str(path), "wb") as stream:
                stream.setparams(params)
                stream.writeframes(data)
        self.sound.PlaySound(str(path), self.sound.SND_FILENAME | self.sound.SND_ASYNC | self.sound.SND_NODEFAULT)
        self.started = self.playing = True

    def close(self):
        if self.closed:
            return
        if self.playing:
            self.sound.PlaySound(None, 0)
        self.playing, self.closed = False, True
        for path in self.paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


class WavPreviewBackend(SoundPreviewBackend):
    """Play a stored WAV; cleanup only removes temporary resume slices."""
    def __init__(self,path,cache,sound=None):
        from .library import wav_info
        duration,_,_=wav_info(path)
        if duration>600:raise ValueError('曲库试听限 10 分钟，请先分段')
        if sound is None:
            import winsound
            sound=winsound
        self.sound=sound;self.cache=Path(cache);self.cache.mkdir(parents=True,exist_ok=True)
        self.source=Path(path);self.paths=[]
        self.started,self.playing,self.closed=False,False,False
