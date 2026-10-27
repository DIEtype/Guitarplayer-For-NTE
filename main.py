import ctypes
import os
from pathlib import Path
import sys
import traceback

if os.name == "nt":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (OSError, AttributeError):
        pass
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('DIEtype.NTEGuitarPlayer')
    except (OSError,AttributeError):
        pass

def launch():
    self_test = "--self-test" in sys.argv
    from guitar_player.paths import app_root
    try:
        from guitar_player.bootstrap import ensure_administrator
        if not ensure_administrator(__file__):return 0
        from guitar_player.ui import App
        app = App(enable_native=not self_test)
        if self_test:
            app.withdraw()
            app.update()
            if app.analyze() is None:
                raise RuntimeError(app.info.get())
            from guitar_player.updates import parse_release
            available=parse_release({'tag_name':'v99.0.0','draft':False,'prerelease':False},'0.12.8')
            if not available.newer:raise RuntimeError('更新提示未正确比较版本号')
            welcome=app.welcome.open(check=False)
            app.update_idletasks()
            if app.welcome.running or 'Bilibili' not in welcome.tutorial_button.cget('text'):
                raise RuntimeError('欢迎窗缺少教程入口或离线检查意外发起网络请求')
            welcome.close()
            guide=app.open_guide('pages');app.update_idletasks()
            if guide.topic!='pages' or '各自保留页码' not in guide.text.get('1.0','end'):
                raise RuntimeError('内置教程未显示混合起始页说明')
            guide.destroy()
            settings=app.open_game_settings('pages');app.update_idletasks()
            if settings.pages['chord'].get()!='1' or settings.pages['fret'].get()!='1':
                raise RuntimeError('分模式起始页设置初始值异常')
            settings.destroy()
            if not app.open_usage_tour() or app.usage_tour.index!=0:
                raise RuntimeError('无法重新打开操作引导')
            app.usage_tour.next()
            if app.usage_tour.index!=1:raise RuntimeError('操作引导无法进入下一步')
            app.usage_tour.finish()
            if not app.usage_tour.seen or app.usage_tour.active:raise RuntimeError('操作引导未正确记录完成状态')
            from guitar_player.guitar import parse_guitar,UP
            from guitar_player.game_profile import GameProfile
            from dataclasses import replace
            from fractions import Fraction
            from guitar_player.chord_score import score_to_commands
            from guitar_player.midi_import import score_to_tab
            from guitar_player.tab import parse_tab
            gap_source=parse_guitar('{G} 0:1 1:1')
            long_gap=replace(gap_source,events=(replace(gap_source.events[0],beats=Fraction(198)),
                replace(gap_source.events[1],start=Fraction(198))),beats=Fraction(199))
            restored=parse_guitar(score_to_commands(long_gap),long_gap.bpm)
            fret_gap=replace(long_gap,events=tuple(replace(e,chord='Zero') for e in long_gap.events))
            tab_gap=parse_tab(score_to_tab(fret_gap),fret_gap.bpm)
            if restored.events[-1].start!=198 or tab_gap.events[-1].start!=198 or restored.beats!=199:
                raise RuntimeError('长休止导出改变了后续起拍')
            wheel_score=parse_guitar('{G} 1 {XII} ↑ {Zero} 2')
            app._begin_playback_display(wheel_score,GameProfile.mixed())
            app._display_note(0,(1,))
            app.messages.put(('chord','XII'));app.messages.put(('page',1));app._poll()
            if app.current_chord!='G' or app.current_page!=0:
                raise RuntimeError('提前选区错误改变了圆盘起音位置')
            app._display_note(1,(UP,));app.draw_wheel()
            if app.current_page!=1 or app.current_notes!=(1,2,3,4,5,6):
                raise RuntimeError('圆盘混合页码或扫弦显示异常')
            from guitar_player.fingerstyle_document import FingerstyleDocument
            from fractions import Fraction
            document=FingerstyleDocument(bars=2)
            document.write_member(0,1,8,1);document.write_member(0,2,9,1);document.write_member(1,1,3,1)
            app.open_fingerstyle_editor(document)
            editor=app.tab_editor;editor.withdraw();editor.select_cursor(0,2);editor.delete()
            if editor.doc.at(0).values[:2]!=(8,None) or editor.doc.at(1).start!=1:
                raise RuntimeError('逐弦删除影响了其他音符或节拍')
            editor.select_cursor(0,1);editor.delete_beat();editor.insert_beat()
            if editor.doc.at(0).notes or editor.doc.at(1).values[0]!=3:
                raise RuntimeError('节拍删除／插入未保留后续音符')
            editor.destroy()
            app.dirty = False
            app.close()
            from guitar_player.midi_import import mido
            import io
            sample=mido.MidiFile();sample.tracks.append(mido.MidiTrack())
            output=io.BytesIO();sample.save(file=output)
            output.seek(0);mido.MidiFile(file=output)
            from guitar_player.paths import resource_root
            from guitar_player.midi_import import inspect_midi
            from guitar_player.polyphony import convert_fingerstyle
            from guitar_player.guitar import parse_guitar,make_plan
            from guitar_player.game_profile import GameProfile
            from guitar_player.timing import ADAPTIVE
            source=inspect_midi(resource_root()/'examples'/'指弹近似_导入演示.mid')
            converted,_=convert_fingerstyle(source,source.tracks[0].index,timing=ADAPTIVE,tap=.04)
            if not converted.events:raise RuntimeError('MIDI 转换未生成音符')
            make_plan(parse_guitar('{G} 1:2 0:2 {III} [1 2]:2'),GameProfile.mixed())
            for selector,profile in (('G',GameProfile()),('I',GameProfile.frets())):
                held=make_plan(parse_guitar('{'+selector+'} 1 ↓',100),profile,tap=.04,sustain_notes=True)
                releases=[step.at for step in held if step.kind=='note_release']
                if len(releases)!=2 or abs(releases[0]-.6)>1e-6 or abs(releases[1]-1.2)>1e-6:
                    raise RuntimeError('拨弦／扫弦未按谱面时值保持')
            from guitar_player.onset_cleanup import cleanup_document
            from guitar_player.fingerstyle_document import FingerCell
            short=FingerstyleDocument([FingerCell(Fraction(0),Fraction(1,60),(None,None,None,None,None,13)),
                                      FingerCell(Fraction(1,60),Fraction(1,4),(None,17,None,None,None,None))],bpm=60)
            grouped,grouping=cleanup_document(short)
            if len(grouped.cells)!=1 or grouping['merged_groups']!=1 or len(short.cells)!=2:
                raise RuntimeError('MIDI 近同拍整理未保留原谱或未合并音符')
            from guitar_player.fingerstyle_translation import read_source,translate,translate_best_key
            from guitar_player.game_profile import CHORD_NAMES
            original=read_source(resource_root()/'examples'/'指弹原谱_多品齐奏.tab')
            arranged,report=translate(original,{},timing=ADAPTIVE,tap=.04)
            if not any(e.chord in CHORD_NAMES for e in arranged.events):raise RuntimeError('和弦转译未生成和弦事件')
            from guitar_player.voice_roles import prepare_accompaniment
            from guitar_player.fingerstyle_translation import PitchSource
            from guitar_player.guitar import GuitarEvent,GuitarScore
            voices=PitchSource(GuitarScore((GuitarEvent((43,55,67),Fraction(0),Fraction(1),'Zero','原谱',1),),
                                          Fraction(4),100,default_timbre='distorted'))
            thinned,voice_report=prepare_accompaniment(voices,'sparse')
            if thinned.score.events[0].notes!=(43,67) or voices.score.events[0].notes!=(43,55,67):
                raise RuntimeError('声部简化未保留旋律与低音，或改写了原谱')
            if voice_report['intentional_omissions']!=1:raise RuntimeError('声部简化报告不正确')
            tied=read_source(resource_root()/'examples'/'跨小节延音_示例.gscore')
            if not any(e.start==3 and e.beats==3 for e in tied.score.events if e.notes):raise RuntimeError('跨小节延音未保留')
            translate_best_key(tied,{},timing=ADAPTIVE,tap=.04,shifts=(0,-1))
            hybrid=GameProfile(strum_input='gamepad')
            plan=make_plan(parse_guitar('{G} ↑ {Am} ↓',100),hybrid,
                           timing=ADAPTIVE,tap=.04,sustain_notes=True)
            second_move=[s.at for s in plan if s.kind=='chord_move'][1]
            first_release=next(s.at for s in plan if s.kind=='note_release' and s.data[0]==0)
            if second_move>=first_release:raise RuntimeError('手柄扫弦未保留换和弦期间的延音')
            from guitar_player.gamepad import load_client
            load_client()  # Check packaged DLL exports only; do not create a controller.
            pad_profile=GameProfile(string_input='gamepad',page_input='gamepad')
            if [pad_profile.bindings[s].key for s in range(1,7)]!=['dpad_down','dpad_left','dpad_right','dpad_up','a','x']:
                raise RuntimeError('手柄六弦映射无效')
            if pad_profile.page_binding('R').key!='lb' or pad_profile.page_binding('T').key!='rb':
                raise RuntimeError('手柄翻页映射无效')
            from guitar_player.gamepad import SELECTOR_AXES
            from guitar_player.game_profile import CHORD_PAGES,ROMAN
            selector_profile=GameProfile.mixed();selector_profile.selector_input='gamepad'
            for page in (*CHORD_PAGES,*(ROMAN[i:i+6] for i in range(0,24,6))):
                if [selector_profile.selector_slot(name) for name in page]!=list(range(6)):
                    raise RuntimeError('左摇杆圆盘扇区映射无效')
            if selector_profile.selector_slot('Zero')!=0 or SELECTOR_AXES[0]!=(0,32767):
                raise RuntimeError('左摇杆空弦或方向映射无效')
            from guitar_player.timing import FAST
            stick_plan=make_plan(parse_guitar('{G} 1:1/4 {Am} 2:1/4',100),
                                 GameProfile(selector_input='gamepad'),timing=FAST,tap=.03,sustain_notes=True)
            pushes=[s.at for s in stick_plan if s.kind=='chord_down']
            centers=[s.at for s in stick_plan if s.kind=='chord_up']
            if any(abs(up-down-.060)>1e-6 for down,up in zip(pushes,centers)):
                raise RuntimeError('左摇杆快速档未保留独立选择保持')
            onsets=[s.at for s in stick_plan if s.kind=='note']
            if len(onsets)!=2 or abs(onsets[1]-.15)>1e-6:raise RuntimeError('左摇杆准备改变了音符起拍')
            from guitar_player.timing import STEADY_FAST
            reserved=make_plan(parse_guitar('{G} ↓:1/2 ↓:1/2',100),GameProfile(strum_input='gamepad'),
                               timing=STEADY_FAST,tap=.03,sustain_notes=True)
            release=next(s.at for s in reserved if s.kind=='note_release')
            if abs(release-.275)>1e-6:raise RuntimeError('稳拍快速未保留重复扫弦松开余量')
            from guitar_player.rhythm_patterns import apply_pattern
            from guitar_player.composition import set_measure_repeat,set_repeat_section,playback_order
            from guitar_player.timing import coordinated_timing
            composed=FingerstyleDocument(bars=3,chord_changes=((Fraction(0),'G'),))
            apply_pattern(composed,0,0,'strum_quarters');set_measure_repeat(composed,1);set_repeat_section(composed,0,1,2)
            if playback_order(composed)!=(0,1,0,1,2) or len(composed.pitch_source().sweeps)!=16:
                raise RuntimeError('节奏组或重复记号未正确展开')
            if FingerstyleDocument.from_dict(composed.to_dict()).snapshot()!=composed.snapshot():
                raise RuntimeError('重复记号保存后无法恢复')
            from guitar_player.rhythm_library import capture_pattern,save_pattern,read_patterns
            from guitar_player.composer_guidance import measure_range
            from tempfile import TemporaryDirectory
            with TemporaryDirectory() as preset_folder:
                preset_path=Path(preset_folder)/'rhythm_patterns.json'
                pattern=capture_pattern(composed,0,'自定义扫弦');save_pattern(preset_path,pattern)
                target=FingerstyleDocument(bars=2,chord_changes=((Fraction(0),'D'),))
                apply_pattern(target,0,0,read_patterns(preset_path)[pattern.key])
                if len(target.cells)!=4 or not all(c.sweep for c in target.cells):
                    raise RuntimeError('自定义节奏预设未正确保存或应用')
                before=target.snapshot()
                try:measure_range('abc','2',target.bars)
                except ValueError as error:
                    if '起始小节' not in str(error) or '整数' not in str(error):raise RuntimeError('范围输入错误缺少具体指引')
                else:raise RuntimeError('范围输入未拒绝无效小节号')
                if target.snapshot()!=before:raise RuntimeError('无效范围输入改变了曲谱')
            coordinated=coordinated_timing(FAST)
            if not coordinated.early_select or coordinated.tap_ms!=FAST.tap_ms or coordinated.page_wait!=FAST.page_wait:
                raise RuntimeError('通用衔接改变了输入档位的基础参数')
            from guitar_player.direct_score import compile_document
            from guitar_player.voicings import reference_voicings
            direct=FingerstyleDocument(bars=1,chord_changes=((Fraction(0),'D'),))
            direct.insert_sequence(0,1,'020',Fraction(1,2));literal=compile_document(direct)
            if [e.chord for e in literal.events if e.notes]!=['Zero','II','Zero']:
                raise RuntimeError('直接载入替换了原写品位')
            bm=FingerstyleDocument(bars=1,chord_changes=((Fraction(0),'Bm'),));bm.write_member(0,2,'x',1)
            _,guarded=translate(bm.pitch_source(),{},timing=ADAPTIVE)
            if reference_voicings()['Bm'].pitches[1]!=62 or any(r['selector']=='Bm' and 2 in r['strings'] for r in guarded['events']):
                raise RuntimeError('Bm 理论 D4 或临时异常规避未保留')
            from guitar_player.fingerstyle_translation import AUTO_RANGE_SHIFTS
            low_melody=PitchSource(GuitarScore((GuitarEvent((36,),Fraction(0),Fraction(1),'Zero','低音旋律',1),),Fraction(4),100))
            _,raised=translate_best_key(low_melody,{},reference=False,timing=ADAPTIVE,shifts=AUTO_RANGE_SHIFTS,filter_low_bass=True)
            if raised['transpose_semitones']<4 or len(raised['key_search']['candidates'])!=49 or raised['range_filtered_notes']:
                raise RuntimeError('自动升降调未升高低音主旋律，或错误过滤了主音')
            low_bass=PitchSource(GuitarScore((GuitarEvent((36,64),Fraction(0),Fraction(1),'Zero','低音与旋律',1),),Fraction(4),100))
            _,filtered=translate(low_bass,{},reference=False,timing=ADAPTIVE,filter_low_bass=True)
            if filtered['range_filtered_notes']!=1 or filtered['events'][0]['pitches']!=[64] or low_bass.score.events[0].notes!=(36,64):
                raise RuntimeError('E2 以下伴奏过滤未保留主旋律或改变了原谱')
            from guitar_player.fingerstyle_translation import KeySearchError
            from guitar_player.import_guidance import translation_error
            wide=PitchSource(GuitarScore((GuitarEvent((40,),Fraction(0),Fraction(1),'Zero','低音',1),
                                         GuitarEvent((91,),Fraction(2),Fraction(1),'Zero','高音',1)),Fraction(4),120))
            try:translate_best_key(wide,{},reference=False,timing=ADAPTIVE,shifts=AUTO_RANGE_SHIFTS)
            except KeySearchError as error:
                diagnostic=translation_error(error)
                if '降3半音' not in diagnostic or 'C#2' not in diagnostic:raise RuntimeError('升降调失败未解释高低音之间的冲突')
            else:raise RuntimeError('严格模式错误接受了超宽音域')
            try:translate(wide,{},reference=False,timing=ADAPTIVE,high_main_octave=True)
            except ValueError as error:
                if '已取消' not in str(error):raise RuntimeError('未明确拒绝主旋律局部降八度')
            else:raise RuntimeError('仍允许主旋律局部降八度')
            from guitar_player import __version__
            receipt=app_root()/"startup_check.txt"
            result="Startup check passed: Tk, TAB, MIDI, onset cleanup, mixed planner, held notes and sweeps, chord translation, voice roles, note editing, beat insertion, ties, bidirectional key search, failure diagnostics, strict melody registers, protected low-bass filtering, left-stick selection, hybrid sweep planner/client, shared coordination, rhythm presets, repeat notation and actionable range validation.\nVersion: "+__version__+'\n'+str(app_root())
            receipt.write_text(result,encoding="utf-8")
            if sys.stdout:print(result)
        else:
            app.mainloop()
        return 0
    except Exception:
        details = traceback.format_exc()
        log_path = app_root()/"startup_error.log"
        try:
            log_path.write_text(
                f"Python: {sys.version}\nExecutable: {sys.executable}\nWorking directory: {os.getcwd()}\n\n{details}",
                encoding="utf-8",
            )
        except OSError:
            pass
        if sys.stderr:print(details, file=sys.stderr)
        if os.name == "nt" and not self_test:
            ctypes.windll.user32.MessageBoxW(
                None, f"程序启动失败。\n\n报错详情已写入：\n{log_path}\n\n{details.splitlines()[-1]}",
                "吉他编曲器 · 启动失败", 0x10,
            )
        return 1

if __name__ == "__main__":
    raise SystemExit(launch())
