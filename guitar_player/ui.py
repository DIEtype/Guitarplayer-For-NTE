import copy
import json
import math
from pathlib import Path
import queue
import re
import threading
from dataclasses import replace
from fractions import Fraction
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import __version__
from .paths import app_root,resource_root
from .game_profile import GameProfile, ROMAN, CHORD_NAMES, load_profile, valid_point
from .guitar import DEFAULT_TEXT, DOWN, UP, make_plan, parse_guitar,apply_capo
from .music import fret_name
from .native import Hotkeys, WindowsBackend, pick_relative_point, windows, capture_client
from .player import GuitarPlayer, PreviewBackend
from .tab import DEFAULT_TAB, parse_tab
from .demo import DEMO_BPM, DEMO_TAB
from .diagnostics import InputProbe, InputTrace
from .timing import PRESETS,LABELS,ADAPTIVE
from .measures import TONE_NAMES,parse_changes,apply_changes

BG, PAPER, INK, MUTED, LINE, ACCENT = "#edf2f6", "#ffffff", "#20394e", "#62798d", "#d8e2eb", "#b93270"
FRET_EXAMPLE = """// 品格模式：请先用 Z 切到品格模式，起始页选择第 1 页
// 数字仍是吉他弦号。I～XII 对应第 1、2 页；Zero 是第 5 页空弦
{I} 6 5 4 3 | {II} 6 5 4 3
{VII} 6 5 4 3 | {VIII} [3 2 1]:2 0:2
{Zero} 6 5 4 3 2 1 | 0:2
"""
MIXED_EXAMPLE='// 混合谱：自动用 Z 切换；切段留休止，数字是弦号\n{G} ↓:1 0:1 ↑:1 0:1 | {III} 1:1 2:1 [1 2]:1 0:1 | {C} ↓:2 ↑:1 0:1\n'


def atomic_json(path, data):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


class App(tk.Tk):
    def __init__(self, enable_native=True, settings_dir=None):
        super().__init__()
        self.title(f"异环 · 吉他编曲器 v{__version__}")
        self._app_icon=None
        icon=resource_root()/'assets'/'app-icon.png'
        if icon.is_file():
            try:
                self._app_icon=tk.PhotoImage(file=str(icon));self.iconphoto(True,self._app_icon)
            except tk.TclError:pass
        width = min(1240, max(800, self.winfo_screenwidth()-60))
        height = min(820, max(500, self.winfo_screenheight()-100))
        self.geometry(f"{width}x{height}")
        self.minsize(min(940,width), min(560,height))
        self.configure(bg=BG)
        self.option_add("*Font", ("Microsoft YaHei UI", 10))
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=INK)
        style.configure("TButton", padding=(12, 8), foreground=INK)
        style.configure("Accent.TButton", background=ACCENT, foreground="white")
        style.map("Accent.TButton", background=[("active", "#95285c")])
        style.configure("Treeview", rowheight=29, background=PAPER, fieldbackground=PAPER, foreground=INK)
        self.messages, self.player = queue.Queue(), None
        self.player = GuitarPlayer(lambda kind, data: self.messages.put((kind, data)))
        self.input_probe=InputProbe(lambda kind,data:self.messages.put((kind,data)))
        self.last_input_trace=None
        self.current_input_trace=None
        self.tempo_source=()
        self.tempo_bpm=100
        self.time_signature=(4,4)
        self.measure_count=0
        self.bar_changes=();self.default_timbre='clean';self.playing_real=False
        self.capo=0;self.chord_changes=();self.mode_states={};self.active_mode='品格模式';self.origin_notice=''
        self.tab_editor=None
        self.arrangement_source=None;self.source_game_score=None;self.fingerstyle_game_score=None
        self.fingerstyle_opened_state=None
        self.unarranged_document=None;self.unarranged_reason='';self.unarranged_text=''
        self.library_window=None
        self.game_warning=""
        self.probe_feedback=tk.StringVar(value="先选定游戏窗口，再从单键测试开始")
        self.profiles = {"和弦模式": GameProfile(), "品格模式": GameProfile.frets(),"混合模式":GameProfile.mixed()}
        from .settings import read_layouts
        from .log_learning import LogEstimate
        self.settings_dir=Path(settings_dir) if settings_dir is not None else (app_root()/'settings' if enable_native else None)
        self.local_layouts=read_layouts(self.settings_dir)
        for label,profile in list(self.profiles.items()):
            if profile.mode in self.local_layouts:self.profiles[label]=copy.deepcopy(self.local_layouts[profile.mode])
        self.log_estimate=LogEstimate();self.learning_running=False
        self.learn_logs=tk.BooleanVar(value=True)
        self.coordinate_inputs=tk.BooleanVar(value=True)
        if self.settings_dir:
            try:
                saved=json.loads((self.settings_dir/'timing.json').read_text(encoding='utf-8'))
                self.learn_logs.set(saved.get('enabled') is not False)
                self.coordinate_inputs.set(saved.get('coordinate_inputs') is not False)
            except (OSError,ValueError,AttributeError):pass
        self.learning_info=tk.StringVar(value=self.log_estimate.description())
        self.mode = tk.StringVar(value="品格模式")
        self.notation = tk.StringVar(value="六线谱 TAB")
        self.tab_step = tk.StringVar(value="1/4")
        self.bpm = tk.StringVar(value="100")
        self.tap = tk.StringVar(value=str(ADAPTIVE.tap_ms))
        self.sustain_notes=tk.BooleanVar(value=True)
        self.duration_hint=tk.StringVar(value='关闭后恢复固定短按；扫弦换和弦前需留出鼠标操作时间。')
        self.strum_input=tk.StringVar(value='mouse')
        self.invert_gamepad=tk.BooleanVar(value=False)
        self.gamepad_strings=tk.BooleanVar(value=False)
        self.gamepad_pages=tk.BooleanVar(value=False)
        self.gamepad_selector=tk.BooleanVar(value=False)
        self.gamepad_select_ms=tk.StringVar(value='60')
        self.dpad_exclusive=tk.BooleanVar(value=False)
        self.strum_button_text=tk.StringVar(value='扫弦方式：鼠标')
        self.gamepad_status=tk.StringVar(value='手柄扫弦需要 ViGEmBus；可先检查驱动，再运行扫弦测试')
        if self.settings_dir:
            try:
                controls=json.loads((self.settings_dir/'controls.json').read_text(encoding='utf-8'))
                if controls.get('strum_input') in ('mouse','gamepad'):self.strum_input.set(controls['strum_input'])
                self.invert_gamepad.set(controls.get('gamepad_up_sign')==-1)
                self.gamepad_strings.set(controls.get('string_input')=='gamepad')
                self.gamepad_pages.set(controls.get('page_input')=='gamepad')
                self.gamepad_selector.set(controls.get('selector_input')=='gamepad')
                hold=controls.get('gamepad_select_ms',60)
                if type(hold) is int and 40<=hold<=200:self.gamepad_select_ms.set(str(hold))
                self.dpad_exclusive.set(controls.get('dpad_exclusive') is True)
            except (OSError,ValueError,AttributeError):pass
        self._apply_strum_input()
        self.input_speed=tk.StringVar(value=ADAPTIVE.label)
        self.timing_hint=tk.StringVar()
        self.page = tk.StringVar(value="1")
        self.game_timbre=tk.StringVar(value='清音')
        self.game_mode=tk.StringVar(value='品格')
        self.window = tk.StringVar()
        self.status = tk.StringVar(value="先写一段曲谱，或点击圆盘插入和弦与弦号")
        self.info = tk.StringVar()
        self.target = tk.StringVar(value='本地试听不发游戏输入；演奏前核对右侧的模式、音色与页码。')
        self.window_items = {}
        self.score, self.current_chord, self.current_notes, self.current_page = None, "I", (), 0
        self.playback_score=None;self.playback_profile=None;self.current_actions=()
        self.prepared_chord=None;self.prepared_page=None
        self.hotkey_ids, self.hotkeys, self.pending_parse, self.elapsed = (), None, None, 0
        self.stop_label=tk.StringVar(value='停止 F9')
        self.dirty = False
        self.file_path = None
        self.enable_native = enable_native
        self.help_notice=tk.StringVar(value='版本与下载')
        self.welcome=None
        self.usage_tour=None
        self.guide_window=None;self.game_settings_window=None
        self.preparing_audio = False
        self.audio_cancel = threading.Event()
        self._build()
        from .welcome import WelcomeController
        self.welcome=WelcomeController(self)
        from .usage_tour import UsageTour
        self.usage_tour=UsageTour(self)
        self.editor.insert("1.0", DEFAULT_TAB)
        self.editor.edit_modified(False)
        self.editor.bind("<<Modified>>", self._edited)
        self.editor.bind("<Control-s>", lambda event: self.save_score())
        self.editor.bind("<Control-o>", lambda event: self.open_score())
        self.bind('<F9>',lambda event:self.stop(),add='+')
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.after(40, self._poll)
        self.after(100, self.analyze)
        if enable_native:
            self.hotkeys = Hotkeys(lambda kind, data: self.messages.put((kind, data)))
            self.after(180, self.refresh_windows)
            self.after(250, self.refresh_learning)
            self.welcome.start()

    def open_welcome(self):
        if self.welcome is not None:return self.welcome.open()

    def open_usage_tour(self):
        if self.usage_tour is not None:return self.usage_tour.start()

    def open_guide(self,topic='start'):
        if self.guide_window is not None and self.guide_window.winfo_exists():
            self.guide_window.select(topic);self.guide_window.deiconify();self.guide_window.lift();return self.guide_window
        from .usage_guide import UsageGuide
        self.guide_window=UsageGuide(self,topic);return self.guide_window

    def open_game_settings(self,section='timing'):
        if self.game_settings_window is not None and self.game_settings_window.winfo_exists():
            self.game_settings_window.select(section);self.game_settings_window.deiconify();self.game_settings_window.lift()
            return self.game_settings_window
        if self.busy:self.status.set('请先停止播放，再修改游戏设置。');return None
        from .game_settings import GameSettings
        self.game_settings_window=GameSettings(self);self.game_settings_window.select(section);return self.game_settings_window

    @property
    def profile(self):
        return self.profiles[self.mode.get()]

    @property
    def busy(self):
        return (self.player.running or self.preparing_audio or self.input_probe.running
                or bool(self.library_window and self.library_window.busy))

    @property
    def input_timing(self):
        from .timing import selector_timing,coordinated_timing
        base=LABELS[self.input_speed.get()]
        timing=self.log_estimate.timing() if base.key=='adaptive' and self.learn_logs.get() else base
        estimate=self.log_estimate if self.learn_logs.get() and self.log_estimate.ready else None
        timing=coordinated_timing(timing,self.coordinate_inputs.get(),
            estimate.release_ms if estimate else 0,estimate.wake_ms if estimate else 0)
        return selector_timing(timing,self.profile)

    def refresh_learning(self):
        if self.learning_running:return
        self.learning_running=True
        def run():
            try:
                from .log_learning import analyze_logs
                result=analyze_logs(app_root()/'logs')
                self.messages.put(('learning_ready',result))
            except Exception as error:self.messages.put(('learning_error',str(error)))
        threading.Thread(target=run,daemon=True).start()

    def learning_changed(self):
        if self.settings_dir:
            try:
                self.settings_dir.mkdir(parents=True,exist_ok=True)
                atomic_json(self.settings_dir/'timing.json',{'enabled':self.learn_logs.get(),
                            'coordinate_inputs':self.coordinate_inputs.get()})
            except OSError as error:self.status.set('日志调优开关未保存：'+str(error))
        if not self.busy:self.analyze()

    def timing_dialog(self):
        return self.open_game_settings('timing')

    @property
    def stop_shortcut(self):
        if 2 in self.hotkey_ids:return 'F9'
        if 3 in self.hotkey_ids:return 'Ctrl+Alt+F9'
        return None

    def control_hint(self,text):
        return text.replace('F9',self.stop_shortcut or '停止按钮')

    def hotkeys_changed(self,registered):
        self.hotkey_ids=tuple(registered)
        shortcut=self.stop_shortcut
        self.stop_label.set('停止 '+shortcut if shortcut else '停止')
        self._playback_compact=None;self.after_idle(self._layout_playback_bar)
        if 3 in registered:
            self.status.set('F9 被占用，已改用 Ctrl+Alt+F9 停止；可以正常演奏。')
        elif 2 not in registered:
            self.status.set('全局停止快捷键不可用，仍可演奏；点击“停止”或切出游戏即可停止。')

    def input_speed_changed(self,event=None):
        if self.busy:
            self.stop();self.status.set('已停止当前播放；新的输入档位将在下次开始时使用')
        self.tap.set(str(self.input_timing.tap_ms));self.dirty=True;self.analyze()

    def duration_mode_changed(self):
        if self.busy:
            self.stop()
        self.dirty=True
        self.analyze()

    def _apply_strum_input(self):
        for profile in self.profiles.values():
            profile.strum_input=self.strum_input.get()
            profile.gamepad_up_sign=-1 if self.invert_gamepad.get() else 1
            profile.string_input='gamepad' if self.gamepad_strings.get() else 'keyboard'
            profile.page_input='gamepad' if self.gamepad_pages.get() else 'keyboard'
            profile.selector_input='gamepad' if self.gamepad_selector.get() else 'mouse'
            profile.gamepad_select_ms=int(self.gamepad_select_ms.get())
            profile.dpad_exclusive=self.dpad_exclusive.get()
        self.strum_button_text.set('扫弦方式：'+('右摇杆' if self.strum_input.get()=='gamepad' else '鼠标'))

    def strum_input_changed(self):
        if self.busy:self.stop()
        self._apply_strum_input()
        if self.settings_dir:
            try:
                self.settings_dir.mkdir(parents=True,exist_ok=True)
                atomic_json(self.settings_dir/'controls.json',{'strum_input':self.strum_input.get(),
                            'gamepad_up_sign':-1 if self.invert_gamepad.get() else 1,
                            'string_input':'gamepad' if self.gamepad_strings.get() else 'keyboard',
                            'page_input':'gamepad' if self.gamepad_pages.get() else 'keyboard',
                            'selector_input':'gamepad' if self.gamepad_selector.get() else 'mouse',
                            'gamepad_select_ms':int(self.gamepad_select_ms.get()),
                            'dpad_exclusive':self.dpad_exclusive.get()})
            except OSError as error:self.status.set('扫弦设置未保存：'+str(error))
        self.analyze()

    def check_gamepad_driver(self):
        self.gamepad_status.set('正在检查驱动连接…')
        def run():
            try:
                from .gamepad import driver_status
                result=driver_status()
            except Exception as error:result=str(error)
            self.messages.put(('gamepad_status',result))
        threading.Thread(target=run,daemon=True).start()

    def open_strum_settings(self):
        dialog=tk.Toplevel(self);dialog.title('输入方式 · 圆盘、拨弦与扫弦')
        dialog.geometry('820x700');dialog.transient(self)
        tests=ttk.Frame(dialog,padding=16);tests.pack(side='bottom',fill='x')
        for i,(label,kind) in enumerate((('测试上下扫弦','sweep'),('延音中换和弦','sweep_select'),
                                        ('测试手柄 1～6 弦','strings'),('长扫弦时值对照','long_sweep'),
                                        ('拨弦→扫弦衔接对照','transition'),('手柄组合弦测试','pad_chords'),
                                        ('左摇杆六扇区测试','selector_stick'))):
            ttk.Button(tests,text=label,command=lambda k=kind:self.start_input_test(k)).grid(row=i//3,column=i%3,padx=4,pady=4,sticky='ew')
        ttk.Label(dialog,text='选择输入方式',font=('Microsoft YaHei UI',15,'bold'),padding=(16,8)).pack(anchor='w')
        for value,label in (('mouse','鼠标左右键扫弦（原方式）'),
                            ('gamepad','虚拟手柄右摇杆扫弦')):
            ttk.Radiobutton(dialog,text=label,value=value,variable=self.strum_input,
                            command=self.strum_input_changed).pack(anchor='w',padx=20,pady=2)
        ttk.Label(dialog,text='默认：上推＝上扫弦（1→6 弦），下推＝下扫弦（6→1 弦）。\n按谱面时值保持摇杆，两次扫弦之间自动回中；选圆盘无需提前结束扫弦。',
                  wraplength=740,padding=(16,8)).pack(anchor='w')
        ttk.Checkbutton(dialog,text='反转摇杆上下方向',variable=self.invert_gamepad,
                        command=self.strum_input_changed).pack(anchor='w',padx=20)
        ttk.Checkbutton(dialog,text='单弦拨弦也用手柄：1～6 弦＝↓、←、→、↑、A、X',variable=self.gamepad_strings,
                        command=self.strum_input_changed).pack(anchor='w',padx=20,pady=4)
        ttk.Checkbutton(dialog,text='翻页用手柄 LB／RB（上一页／下一页）',variable=self.gamepad_pages,
                        command=self.strum_input_changed).pack(anchor='w',padx=20,pady=4)
        select_row=ttk.Frame(dialog);select_row.pack(fill='x',padx=20,pady=4)
        ttk.Checkbutton(select_row,text='左摇杆选择圆盘（试验）',variable=self.gamepad_selector,
                        command=self.strum_input_changed).pack(side='left')
        ttk.Label(select_row,text='选择保持').pack(side='left',padx=(16,4))
        hold=ttk.Combobox(select_row,textvariable=self.gamepad_select_ms,values=('40','60','90','120','150','200'),state='readonly',width=4)
        hold.pack(side='left');hold.bind('<<ComboboxSelected>>',lambda event:self.strum_input_changed())
        ttk.Label(select_row,text='毫秒').pack(side='left',padx=4)
        ttk.Checkbutton(dialog,text='十字键相反方向互斥（仅组合弦测试不通过时启用）',variable=self.dpad_exclusive,
                        command=self.strum_input_changed).pack(anchor='w',padx=20,pady=4)
        ttk.Label(dialog,text='左摇杆适用于和弦与品格：提前推出、保持后回中，拨弦起拍不变。漏选可提高保持毫秒数。\n六扇区测试含慢速与演奏保持两轮；关闭选项恢复鼠标。模式 Z、音色 V 沿用键盘。',
                  wraplength=740,foreground=MUTED,padding=(20,4)).pack(anchor='w')
        from .widgets import StatusBanner
        StatusBanner(dialog,self.gamepad_status).pack(fill='x',padx=16,pady=6)
        actions=ttk.Frame(dialog,padding=(16,0));actions.pack(fill='x')
        ttk.Button(actions,text='检查驱动',command=self.check_gamepad_driver).pack(side='left')
        def open_driver():
            import webbrowser
            from .gamepad import DRIVER_URL
            webbrowser.open(DRIVER_URL)
        ttk.Button(actions,text='打开官方驱动下载页',command=open_driver).pack(side='left',padx=8)
        ttk.Label(dialog,text='使用已安装的 ViGEmBus；首次使用可打开官方下载页。项目已停止维护，程序不会自动安装。\n先选游戏窗口、填好实际模式与起始页，再测试。',
                  foreground=MUTED,wraplength=740,padding=(16,8)).pack(anchor='w')

    def _build(self):
        from .main_workspace import build
        build(self)

    def _layout_vertical(self,event=None):
        from .main_workspace import layout
        layout(self,event)

    def _build_playback_bar(self):
        self.playback_bar = ttk.Frame(self,padding=(20,0,20,10))
        self.playback_bar.pack(fill="x")
        self.game_play_button = ttk.Button(self.playback_bar,text="5 秒后自动演奏",style="Accent.TButton",command=lambda: self.start(True))
        self.audition_button = ttk.Button(self.playback_bar,text="本地试听",command=lambda: self.start(False,audible=True))
        self.pause_button = ttk.Button(self.playback_bar,text="暂停 / 继续 F8",command=self.player.toggle_pause)
        self.stop_button = ttk.Button(self.playback_bar,textvariable=self.stop_label,command=self.stop)
        self.time_text = ttk.Label(self.playback_bar,text="00:00 / 00:00",foreground=MUTED)
        self._playback_compact = None
        self.playback_bar.bind("<Configure>",self._layout_playback_bar)
        self._layout_playback_bar()

    def _layout_playback_bar(self,event=None):
        buttons=(self.game_play_button,self.audition_button,self.pause_button,self.stop_button)
        needed=sum(button.winfo_reqwidth()+8 for button in buttons)+self.time_text.winfo_reqwidth()+8
        compact=self.playback_bar.winfo_width()-40 < needed
        if compact == self._playback_compact:
            return
        self._playback_compact=compact
        for widget in (*buttons,self.time_text):
            widget.grid_forget()
        for column in range(6):
            self.playback_bar.columnconfigure(column,weight=0,minsize=0)
        if compact:
            for column in range(4):
                self.playback_bar.columnconfigure(column,weight=1)
            self.game_play_button.grid(row=0,column=0,columnspan=2,sticky="ew",padx=(0,8),pady=(0,6))
            self.audition_button.grid(row=0,column=2,columnspan=2,sticky="ew",pady=(0,6))
            self.pause_button.grid(row=1,column=0,columnspan=2,sticky="ew",padx=(0,8))
            self.stop_button.grid(row=1,column=2,sticky="ew",padx=(0,8))
            self.time_text.grid(row=1,column=3,sticky="e")
        else:
            for column,button in enumerate(buttons):
                self.playback_bar.columnconfigure(column,weight=1)
                button.grid(row=0,column=column,sticky="ew",padx=(0,8))
            self.time_text.grid(row=0,column=4,sticky="e")

    def show_notation_help(self):
        return self.open_guide('text')

    def open_input_tests(self):
        dialog=tk.Toplevel(self)
        dialog.title("游戏输入连接测试")
        dialog.geometry("850x610")
        dialog.transient(self)
        ttk.Label(dialog,text="先验证游戏有没有收到输入，再测试曲谱",font=("Microsoft YaHei UI",14,"bold"),padding=16).pack(anchor="w")
        ttk.Label(dialog,text=self.control_hint("主窗口先选中游戏窗口。每项会倒计时 3 秒，请切回游戏吉他界面。\n单键测试不使用鼠标坐标，也不切换模式或翻页；可用 F9 停止，切出游戏也会停止。"),wraplength=630,padding=(16,0,16,16)).pack(anchor="w")
        for kind,label,description in (
            ("key","1. 测试拨弦：数字 6 三次","不依赖校准；应听到吉他 1 弦被拨三次"),
            ("pointer","2. 测试鼠标定位","移到当前页顶部区域，不点击、不拨弦"),
            ("select","3. 测试选和弦／品位并拨弦","选择当前页顶部区域；需先校准坐标"),
            ("page","4. 测试翻页并拨弦","按当前输入档位翻到下一页；测试后需重新对齐起始页"),
            ('sweep','5. 测试手柄上下扫弦','不点圆盘；上推、回中、下推、回中'),
            ('sweep_select','6. 测试扫弦延音中换和弦','先校准；旧音保持时点下一和弦，再扫弦听新和弦'),
        ):
            row=ttk.Frame(dialog,padding=(16,4))
            row.pack(fill="x")
            ttk.Button(row,text=label,command=lambda choice=kind:self.start_input_test(choice)).pack(side="left")
            ttk.Label(row,text=description,foreground=MUTED).pack(side="left",padx=12)
        from .widgets import StatusBanner
        StatusBanner(dialog,self.probe_feedback).pack(fill='x',padx=16,pady=8)
        row=ttk.Frame(dialog,padding=16)
        row.pack(fill="x")
        ttk.Button(row,text="查看最近输入记录",command=self.show_input_log).pack(side="left")
        ttk.Button(row,text='手柄按钮与长扫弦测试',command=self.open_strum_settings).pack(side='left',padx=8)
        ttk.Button(row,textvariable=self.stop_label,command=self.stop).pack(side="right")

    def start_input_test(self,kind):
        if self.busy:
            self.probe_feedback.set("先停止当前演奏或测试")
            return
        hwnd=self.window_items.get(self.window.get())
        if not hwnd:
            self.probe_feedback.set("请先在主窗口的“目标窗口”中选择游戏窗口")
            return
        trace=None
        try:
            trace=InputTrace(app_root()/"logs","connection_test:"+kind,buffered=True)
            self.last_input_trace=trace.path
            self.editor.configure(state="disabled")
            profile=copy.deepcopy(self.profile);profile.initial_page=int(self.page.get())-1
            if kind=='pointer':profile.selector_input='mouse'
            if kind=='selector_stick':profile.selector_input='gamepad'
            if kind in ('sweep','sweep_select'):
                profile.strum_input='gamepad'
                if kind=='sweep_select' and profile.selector_input=='mouse' and not profile.calibrated:
                    raise ValueError('请先校准圆盘坐标，或开启左摇杆选圆盘，再测试延音中换和弦')
            if kind in ('strings','pad_chords'):profile.string_input='gamepad'
            if kind in ('long_sweep','transition') and profile.strum_input=='mouse' and not profile.calibrated:
                raise ValueError('鼠标扫弦测试请先校准，或改用手柄扫弦再测试')
            if not 0<=profile.initial_page<profile.page_count:raise ValueError('游戏起始页无效')
            if profile.mode=='mixed' and profile.initial_mode=='chord' and profile.initial_page>=4:raise ValueError('和弦起始页只能为 1～4')
            self.input_probe.start(hwnd,profile,kind,trace,timing=self.input_timing)
        except Exception as error:
            if trace is not None and not self.input_probe.running:trace.close()
            self.editor.configure(state="normal")
            self.probe_feedback.set(str(error))

    def show_input_log(self):
        if not self.last_input_trace:
            self.probe_feedback.set("尚无输入记录，请先运行连接测试或自动演奏")
            return
        if self.current_input_trace:self.current_input_trace.flush()
        dialog=tk.Toplevel(self)
        dialog.title("最近输入记录")
        dialog.geometry("850x520")
        ttk.Label(dialog,text=str(self.last_input_trace),wraplength=800,padding=10).pack(anchor="w")
        text=tk.Text(dialog,wrap="word",font=("Consolas",9),padx=10,pady=10)
        text.pack(fill="both",expand=True)
        try:
            rows=Path(self.last_input_trace).read_text(encoding="utf-8").splitlines()[-100:]
            text.insert("1.0","\n".join(rows))
        except OSError as error:
            text.insert("1.0",str(error))
        text.configure(state="disabled")

    def accept_visual_score(self,score,summary="",chord_mode=False,origin_notice='',source_document=None):
        from .midi_import import score_to_tab
        if self.busy:raise ValueError("请先停止播放，再应用曲谱")
        if chord_mode:
            from .chord_score import score_to_commands
            text=score_to_commands(score,mixed=chord_mode=='mixed');parse_guitar(text,score.bpm)
        else:
            text=score_to_tab(score);parse_tab(text,score.bpm,capo=score.capo)
        self.unarranged_document=None;self.unarranged_reason='';self.editor.configure(state='normal')
        self.origin_notice=origin_notice
        if origin_notice:text='// 转译依据：'+origin_notice+'\n'+text
        self.mode.set('混合模式' if chord_mode=='mixed' else '和弦模式' if chord_mode else '品格模式');self.active_mode=self.mode.get()
        self.page_box.configure(to=self.profile.page_count)
        self.notation.set('指令谱' if chord_mode else '六线谱 TAB')
        self.tempo_source=score.base_tempo_map if score.base_tempo_map is not None else score.tempo_map
        self.tempo_bpm=score.bpm
        self.time_signature=score.time_signature
        self.measure_count=score.measures
        self.bar_changes=score.bar_changes;self.default_timbre=score.default_timbre
        self.capo=score.capo;self.chord_changes=score.chord_changes if chord_mode else ()
        self.bpm.set(f"{score.bpm:.6f}".rstrip('0').rstrip('.'))
        self.editor.delete("1.0","end")
        self.editor.insert("1.0",text)
        self.editor.edit_modified(False)
        self.dirty=True
        self.analyze()
        self.arrangement_source=copy.deepcopy(source_document)
        self.source_game_score=self.score if source_document is not None else None
        self.fingerstyle_game_score=self.source_game_score
        if source_document is not None:
            self.fingerstyle_document=copy.deepcopy(source_document);self.fingerstyle_path=None
        if summary or origin_notice:self.status.set((summary or origin_notice)+("；游戏动作需检查："+self.game_warning if self.game_warning else ""))

    def adapt_fingering(self,score=None):
        if self.busy:return None
        score=score or self.analyze()
        if score is None:return None
        if self.profile.mode!='fret':return score
        from .arrangement import adapt_score
        try:adapted,report,changed=adapt_score(score,int(self.page.get())-1,self.input_timing,int(self.tap.get())/1000)
        except (ValueError,KeyError) as error:
            self.status.set('指法适配失败：'+str(error));return None
        self.adaptive_report=report
        if changed:
            separators=self.editor.cget('autoseparators')
            self.editor.configure(autoseparators=False);self.editor.edit_separator()
            try:self.accept_visual_score(adapted,report)
            finally:
                self.editor.edit_separator();self.editor.configure(autoseparators=separators)
        else:self.status.set(report)
        return self.score if changed else adapted

    def open_library(self):
        if self.library_window is not None and self.library_window.winfo_exists():
            self.library_window.deiconify();self.library_window.lift();return
        try:
            from .library_ui import LibraryWindow
            self.library_window=LibraryWindow(self)
        except OSError as error:self.status.set('无法打开曲库：'+str(error))

    def open_visual_editor(self):
        if self.busy:return
        if self.tab_editor is not None and self.tab_editor.winfo_exists():
            self.tab_editor.deiconify();self.tab_editor.lift();return
        if self.unarranged_document is not None:
            self.open_fingerstyle_editor(self.unarranged_document);return
        score=self.analyze()
        if score is None:return
        try:
            from .fingerstyle_document import FingerstyleDocument
            if self.fingerstyle_game_score==score and getattr(self,'fingerstyle_document',None) is not None:
                document=self.fingerstyle_document
            elif self.source_game_score==score and self.arrangement_source is not None:
                document=self.arrangement_source
            else:document=FingerstyleDocument.from_score(score)
            self.open_fingerstyle_editor(document,game_score=score)
        except ValueError as error:self.status.set(str(error))

    def open_translator(self):
        if self.busy:return
        window=getattr(self,'translation_window',None)
        if window is not None and window.winfo_exists():window.deiconify();window.lift();return
        from .translation_ui import TranslationWindow
        self.translation_window=TranslationWindow(self)

    def open_fingerstyle_editor(self,document=None,path=None,game_score=None):
        if self.busy:return
        if self.tab_editor is not None and self.tab_editor.winfo_exists():
            self.tab_editor.deiconify();self.tab_editor.lift()
            self.tab_editor.feedback.set('请先关闭当前编谱窗口，再进入原谱编写');return
        try:
            from .fingerstyle_document import FingerstyleDocument,read_document
            from .fingerstyle_editor import VisualFingerstyleEditor
            if document is None:
                document=getattr(self,'fingerstyle_document',None);path=getattr(self,'fingerstyle_path',None)
                draft=self.settings_dir/'fingerstyle_draft.gscore' if self.settings_dir else None
                if document is None and draft and draft.exists():document=read_document(draft)
            else:self.fingerstyle_game_score=game_score
            self.fingerstyle_opened_state=document.snapshot() if document is not None and game_score is not None else None
            VisualFingerstyleEditor(self,document or FingerstyleDocument(),self.accept_fingerstyle_source,path,
                                    on_direct=self.accept_direct_document)
        except (OSError,ValueError) as error:self.status.set('原谱打开失败：'+str(error))

    def accept_direct_document(self,document):
        """Load before checking playability; approximation is a separate choice."""
        if self.busy:raise ValueError('请先停止播放，再载入原谱')
        same_arrangement=(self.source_game_score==self.score and self.arrangement_source is not None
                          and document.snapshot()==self.arrangement_source.snapshot())
        same_opened=(self.fingerstyle_game_score==self.score and document.snapshot()==self.fingerstyle_opened_state)
        if self.score is not None and (same_arrangement or same_opened):
            self.fingerstyle_document=copy.deepcopy(document)
            self.status.set('编谱内容未改变，保留当前演奏谱；无需重新转译');return
        from .direct_score import compile_document,source_listing
        from .voicings import read_voicings
        try:
            rows=read_voicings(self.settings_dir/'voicings.json') if self.settings_dir else {}
            score=compile_document(document,rows)
        except (ValueError,OSError) as error:
            self.unarranged_document=copy.deepcopy(document);self.unarranged_reason=str(error)
            self.arrangement_source=copy.deepcopy(document);self.source_game_score=None;self.fingerstyle_game_score=None
            self.fingerstyle_document=copy.deepcopy(document);self.fingerstyle_path=None
            self.mode.set('混合模式');self.active_mode='混合模式';self.notation.set('图形原谱')
            self.bpm.set(f'{document.bpm:g}');self.time_signature=document.meter;self.measure_count=document.bars
            self.capo=document.capo;self.bar_changes=tuple(document.bar_changes.values());self.default_timbre=document.default_timbre
            self.tempo_source=document.tempo_map;self.tempo_bpm=document.source_bpm;self.chord_changes=()
            self.unarranged_text=source_listing(document)
            self.editor.configure(state='normal');self.editor.delete('1.0','end');self.editor.insert('1.0',self.unarranged_text)
            self.editor.edit_modified(False);self.editor.configure(state='disabled');self.dirty=True;self.file_path=None
            self.analyze();return
        self.accept_visual_score(score,'原谱已直接载入；保留所写弦号、品位、和弦及节奏，未做近似转译',
                                 chord_mode='mixed',source_document=document)
        self.file_path=None

    def analyze_unarranged(self):
        self.score=None;self.game_warning=self.unarranged_reason
        doc=self.unarranged_document
        self.info.set(f'原谱已载入 · {doc.bars} 小节 · {len(doc.cells)} 个音位；可编辑、试听与保存')
        self.status.set('原谱已载入；原样演奏受限：'+self.unarranged_reason+'。点上方“手动编谱”可修改或试听；需要近似演奏时，在编谱窗口右下角选“近似转译…”。')
        self.draw_timeline();return None

    def accept_fingerstyle_source(self,source):
        self.open_translator()
        self.translation_window.set_source(source)

    def import_midi(self,path=None):
        if self.busy:return
        path=path or filedialog.askopenfilename(parent=self,title="导入 MIDI",filetypes=[("MIDI 文件","*.mid *.midi")])
        if not path:return
        if self.dirty and not messagebox.askyesno("导入 MIDI","当前有未保存的修改，仍要导入新曲谱吗？",parent=self):return
        try:
            from .midi_import import inspect_midi,convert_melody
            source=inspect_midi(path)
            if not 20<=source.bpm<=360:raise ValueError("文件的起始速度超出当前支持的 20～360 BPM")
        except Exception as error:
            from .import_guidance import midi_error
            self.status.set(midi_error(error,'读取'));return
        dialog=tk.Toplevel(self)
        dialog.title("MIDI 导入 · 选择旋律轨道")
        dialog.geometry("800x650");dialog.minsize(760,600)
        dialog.transient(self)
        ttk.Label(dialog,text=Path(path).name,font=("Microsoft YaHei UI",14,"bold"),padding=12).pack(anchor="w")
        tree=ttk.Treeview(dialog,columns=("notes","range"),show="tree headings",height=6)
        tree.heading("#0",text="轨道")
        tree.heading("notes",text="音符数")
        tree.heading("range",text="MIDI 音域")
        tree.column("#0",width=400);tree.column("notes",width=100);tree.column("range",width=170)
        tree.pack(fill="both",expand=True,padx=12)
        for track in source.tracks:
            from .score import pitch_name
            bottom=min(n.pitch for n in track.notes);top=max(n.pitch for n in track.notes)
            tree.insert("","end",iid=str(track.index),text=f"{track.index} · {track.name}",values=(len(track.notes),f"{pitch_name(bottom)}～{pitch_name(top)} ({bottom}～{top})"))
        tree.selection_set(str(source.tracks[0].index))
        row=ttk.Frame(dialog,padding=12);row.pack(fill="x")
        transpose=tk.StringVar(value="0");highest=tk.BooleanVar(value=True)
        ttk.Label(row,text="整轨移调（半音）").pack(side="left")
        transpose_box=ttk.Spinbox(row,textvariable=transpose,from_=-24,to=24,width=5);transpose_box.pack(side="left",padx=8)
        highest_box=ttk.Checkbutton(row,text="同拍取最高音（仅单音旋律生效）",variable=highest,state='disabled')
        highest_box.pack(side="left")
        method_row=ttk.Frame(dialog,padding=(12,0));method_row.pack(fill='x')
        ttk.Label(method_row,text='导入方式').pack(side='left')
        method=tk.StringVar(value='混合转译：主旋律优先')
        method_box=ttk.Combobox(method_row,textvariable=method,values=('混合转译：主旋律优先','指弹近似：旋律准拍优先','单音旋律'),state='readonly',width=30)
        method_box.pack(side='left',padx=8)
        range_row=ttk.Frame(dialog,padding=(12,8));range_row.pack(fill='x')
        auto_range=tk.BooleanVar(value=True);filter_low_bass=tk.BooleanVar(value=True)
        auto_box=ttk.Checkbutton(range_row,text='自动升降调（±24 半音，混合转译）',variable=auto_range,command=lambda:method_changed())
        auto_box.pack(side='left')
        filter_box=ttk.Checkbutton(range_row,text='过滤 E2 以下伴奏（主旋律不删）',variable=filter_low_bass)
        filter_box.pack(side='left',padx=8)
        cleanup_row=ttk.Frame(dialog,padding=(12,8));cleanup_row.pack(fill='x')
        near_onsets=tk.BooleanVar(value=False);near_ms=tk.StringVar(value='30')
        near_check=ttk.Checkbutton(cleanup_row,text='手动收拢近同拍',variable=near_onsets,state='disabled');near_check.pack(side='left')
        near_box=ttk.Combobox(cleanup_row,textvariable=near_ms,values=('30','50','100'),state='disabled',width=4)
        near_box.pack(side='left',padx=6)
        def method_changed(event=None):
            highest_box.configure(state='normal' if method.get()=='单音旋律' else 'disabled')
            mixed=method.get().startswith('混合')
            near_check.configure(state='disabled' if mixed else 'normal')
            near_box.configure(state='disabled' if mixed else 'readonly')
            auto_box.configure(state='normal' if mixed else 'disabled');filter_box.configure(state='normal' if mixed else 'disabled')
            transpose_box.configure(state='disabled' if mixed and auto_range.get() else 'normal')
        method_box.bind('<<ComboboxSelected>>',method_changed)
        method_changed()
        ttk.Label(cleanup_row,text='ms；高音起拍优先，大窗口会收拢快速琶音。原 MIDI 保留。',foreground=MUTED).pack(side='left')
        meter=source.signatures[0][1]
        feedback=tk.StringVar(value=f"起始 {source.bpm:g} BPM；{meter[0]}/{meter[1]}；速度节点 {len(source.tempo_map)} 个，导入后保留速度变化。鼓通道已排除。"+
                             ('包含变拍号：保留位于小节边界的标记，其余位置会在导入结果中说明。' if len(source.signatures)>1 else '')+
                             '混合转译可自动升降调；E2 以下伴奏按移调后音高过滤，主旋律／待核对音保留。原文件不变。')
        feedback_panel=ttk.Frame(dialog,padding=12);feedback_panel.pack(fill='x')
        feedback_view=tk.Text(feedback_panel,height=5,wrap='word',font=('Microsoft YaHei UI',10),
                              background='#f3f6fa',foreground=MUTED,relief='flat',padx=8,pady=6)
        feedback_scroll=ttk.Scrollbar(feedback_panel,command=feedback_view.yview);feedback_scroll.pack(side='right',fill='y')
        feedback_view.configure(yscrollcommand=feedback_scroll.set);feedback_view.pack(fill='x',expand=True)
        def render_feedback(*args):
            feedback_view.configure(state='normal');feedback_view.delete('1.0','end');feedback_view.insert('1.0',feedback.get())
            feedback_view.configure(state='disabled');feedback_view.yview_moveto(0)
        trace=feedback.trace_add('write',render_feedback);render_feedback()
        def cleanup_feedback(event):
            if event.widget is dialog:feedback.trace_remove('write',trace)
        dialog.bind('<Destroy>',cleanup_feedback,add='+')
        def accept():
            try:
                if not tree.selection():raise ValueError("先选一个轨道")
                if method.get().startswith('混合') and auto_range.get():shift=0
                else:
                    try:shift=int(transpose.get())
                    except ValueError:raise ValueError('“整轨移调”请填写 -24～24 的整数；正数升调、负数降调，0 保持原调') from None
                    if not -24<=shift<=24:raise ValueError('“整轨移调”请填写 -24～24 的整数半音，再重新导入')
                if method.get().startswith('混合'):
                    from .fingerstyle_translation import from_midi
                    from .translation_ui import AUTO_RANGE
                    original_source=from_midi(source,int(tree.selection()[0]))
                    self.accept_fingerstyle_source(original_source)
                    self.translation_window.transpose.set(AUTO_RANGE if auto_range.get() else '保持原调' if shift==0 else f'{"降" if shift<0 else "升"}{abs(shift)}半音')
                    self.translation_window.filter_low_bass.set(filter_low_bass.get())
                    dialog.destroy();self.translation_window.convert();return
                imported_source=source;cleanup_notice=''
                if near_onsets.get():
                    from .onset_cleanup import cleanup_midi,cleanup_summary
                    imported_source,cleanup_report=cleanup_midi(source,int(tree.selection()[0]),int(near_ms.get()))
                    cleanup_notice=cleanup_summary(cleanup_report)+'；'
                if method.get().startswith('指弹'):
                    from .polyphony import convert_fingerstyle
                    score,report=convert_fingerstyle(imported_source,int(tree.selection()[0]),shift,int(self.page.get())-1,
                                                   timing=self.input_timing,tap=int(self.tap.get())/1000)
                else:
                    score,report=convert_melody(imported_source,int(tree.selection()[0]),shift,highest.get(),int(self.page.get())-1,
                                               timing=self.input_timing,tap=int(self.tap.get())/1000)
                report=cleanup_notice+report
                self.accept_visual_score(score,report)
                if method.get().startswith('指弹'):
                    self.editor.insert('1.0','// '+report+'\n');self.editor.edit_modified(False)
                dialog.destroy()
                self.open_visual_editor()
            except Exception as error:
                from .import_guidance import midi_error
                feedback.set(midi_error(error));feedback_view.configure(foreground='#a62d43',background='#fff0f2')
        ttk.Button(dialog,text="导入并预览",command=accept).pack(side='bottom',anchor="e",before=tree,padx=12,pady=(0,12))
        dialog.auto_range=auto_range;dialog.filter_low_bass=filter_low_bass;dialog.method=method;dialog.transpose=transpose
        dialog.track_tree=tree;dialog.feedback=feedback;dialog.accept=accept;dialog.method_changed=method_changed
        dialog.feedback_view=feedback_view
        return dialog

    def insert(self, text):
        if self.unarranged_document is not None:
            self.open_visual_editor();return
        if self.player.running:
            return
        if self.notation.get() == "六线谱 TAB":
            self.status.set("六线谱请在对应弦行直接填写品位；扫弦等动作可切到“指令谱”编写")
            return
        self.editor.insert("insert", text)
        self.editor.focus_set()

    def example_text(self):
        if self.mode.get()=='混合模式':return MIXED_EXAMPLE
        if self.notation.get() == "六线谱 TAB":
            return DEFAULT_TAB
        return DEFAULT_TEXT if self.mode.get() == "和弦模式" else FRET_EXAMPLE

    def load_demo(self):
        if self.busy:
            return
        text = self.editor.get("1.0", "end-1c")
        if self.dirty and text not in (DEFAULT_TEXT, FRET_EXAMPLE, DEFAULT_TAB, DEMO_TAB) and not messagebox.askyesno("载入试弹曲", "当前有未保存的修改，仍要替换为试弹曲吗？", parent=self):
            return
        self.unarranged_document=None;self.editor.configure(state='normal')
        self.mode.set("品格模式")
        self.active_mode='品格模式';self.chord_changes=()
        self.origin_notice=''
        self.notation.set("六线谱 TAB")
        self.bpm.set(str(DEMO_BPM))
        self.tempo_source=()
        self.time_signature=(4,4);self.measure_count=0
        self.bar_changes=();self.default_timbre='clean'
        self.capo=0
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", DEMO_TAB)
        self.editor.edit_modified(False)
        self.dirty = True
        self.analyze()
        self.status.set("试弹曲已载入：先点“本地试听”听参考效果；进游戏试弹只需校准 I、III、V 三个区域")

    def notation_changed(self, event=None):
        if self.unarranged_document is not None:
            self.notation.set('图形原谱');self.status.set('点上方“手动编谱”修改原谱；近似转译可单独选择');return
        if self.player.running:
            self.player.stop()
        if self.notation.get() == "六线谱 TAB":
            self.mode.set("品格模式")
        self.mode_changed()

    def insert_example(self):
        if self.busy:
            return
        text = self.editor.get("1.0", "end-1c")
        if self.dirty and text not in (DEFAULT_TEXT, FRET_EXAMPLE, DEFAULT_TAB) and not messagebox.askyesno("载入示例", "当前有未保存的修改，仍要替换为示例曲谱吗？", parent=self):
            return
        self.unarranged_document=None;self.editor.configure(state='normal')
        self.notation.set('六线谱 TAB' if self.mode.get()=='品格模式' else '指令谱')
        self.editor.delete("1.0", "end")
        self.tempo_source=()
        self.time_signature=(4,4);self.measure_count=0
        self.bar_changes=();self.default_timbre='clean'
        self.capo=0
        self.chord_changes=()
        self.editor.insert("1.0", self.example_text())
        self.dirty = True
        self.analyze()

    def mixed_pages_dialog(self):
        return self.open_game_settings('pages')

    def mode_changed(self, event=None, loaded=False):
        if self.unarranged_document is not None and not loaded:
            self.mode.set(self.active_mode);self.notation.set('图形原谱')
            self.status.set('原谱已载入；演奏方式由所写音符决定，可点上方“手动编谱”修改');return
        if self.busy:
            self.stop()
        new_mode=self.mode.get()
        if not loaded and new_mode!=self.active_mode:
            attrs=('tempo_source','tempo_bpm','time_signature','measure_count','bar_changes','default_timbre','capo','chord_changes','origin_notice')
            self.mode_states[self.active_mode]=({name:getattr(self,name) for name in attrs},
                self.editor.get('1.0','end-1c'),self.bpm.get(),self.page.get(),self.notation.get())
            state=self.mode_states.get(new_mode)
            if state:
                metadata,text,bpm,page,notation=state
                for name,value in metadata.items():setattr(self,name,value)
                self.bpm.set(bpm);self.page.set(page);self.notation.set(notation)
            else:
                self.tempo_source=();self.tempo_bpm=100;self.time_signature=(4,4);self.measure_count=0
                self.bar_changes=();self.default_timbre='clean';self.capo=0;self.chord_changes=();self.origin_notice=''
                self.bpm.set('100');self.page.set('1')
                self.notation.set('指令谱' if new_mode!='品格模式' else '六线谱 TAB')
                text=self.example_text()
            self.editor.delete('1.0','end');self.editor.insert('1.0',text);self.editor.edit_modified(False)
        self.active_mode=new_mode
        if new_mode=='混合模式':
            actual='chord' if self.game_mode.get()=='和弦' else 'fret'
            self.page.set(str(self.profile.initial_pages[actual]+1))
        self.page_box.configure(to=self.profile.page_count)
        self.current_chord = "G" if self.mode.get() == "和弦模式" else "I"
        self.playback_score=None;self.playback_profile=None;self.current_notes=();self.current_actions=()
        self.prepared_chord=None;self.prepared_page=None
        if self.mode.get() != "品格模式":
            self.notation.set("指令谱")
            self.capo=0
        if self.editor.get("1.0", "end-1c") in (DEFAULT_TEXT, FRET_EXAMPLE, DEFAULT_TAB):
            self.editor.delete("1.0", "end")
            self.editor.insert("1.0", self.example_text())
        self.current_page = int(self.page.get()) - 1 if self.page.get().isdigit() else 0
        self.dirty = True
        self.draw_wheel()
        self.analyze()

    def _edited(self, event=None):
        if not self.editor.edit_modified():
            return
        self.editor.edit_modified(False)
        if self.unarranged_document is not None:
            if self.editor.get('1.0','end-1c')!=self.unarranged_text:
                self.editor.configure(state='normal');self.editor.delete('1.0','end');self.editor.insert('1.0',self.unarranged_text)
                self.editor.edit_modified(False)
            self.editor.configure(state='disabled');return
        self.chord_changes=()
        self.dirty = True
        if self.pending_parse:
            self.after_cancel(self.pending_parse)
        self.pending_parse = self.after(250, self.analyze)

    def analyze(self):
        self.pending_parse = None
        if self.busy:
            return self.score
        if self.unarranged_document is not None:return self.analyze_unarranged()
        try:
            text = self.editor.get("1.0", "end-1c")
            if self.notation.get() == "六线谱 TAB":
                if self.mode.get() != "品格模式":
                    raise ValueError("六线谱使用品格模式")
                score = parse_tab(text, float(self.bpm.get()), self.tab_step.get(),self.capo)
            else:
                score = parse_guitar(text, float(self.bpm.get()))
                if self.mode.get()=='品格模式':score=apply_capo(score,self.capo)
            if self.tempo_source:
                from .midi_import import scale_tempos
                score=replace(score,tempo_map=scale_tempos(self.tempo_source,self.tempo_bpm,score.bpm))
            score=replace(score,time_signature=self.time_signature,measures=self.measure_count)
            if self.mode.get()!='品格模式':score=replace(score,chord_changes=self.chord_changes)
            score=apply_changes(score,self.bar_changes,self.default_timbre,score.tempo_map)
            page = int(self.page.get()) - 1
            if not 0 <= page < self.profile.page_count:
                raise ValueError(f"当前模式起始页应为 1～{self.profile.page_count}")
            self.profile.initial_page = page
            self._apply_strum_input()
            self.profile.initial_timbre=next(key for key,label in TONE_NAMES.items() if label==self.game_timbre.get())
            self.profile.initial_mode='chord' if self.game_mode.get()=='和弦' else 'fret'
            if self.profile.mode=='mixed':
                limit=4 if self.profile.initial_mode=='chord' else 5
                if page>=limit:raise ValueError('和弦起始页只能为 1～4')
                self.profile.initial_pages[self.profile.initial_mode]=page
            self.current_page = page
            tap = int(self.tap.get()) / 1000
            if not .015 <= tap <= .2:
                raise ValueError("按键时长应为 15～200 毫秒")
            self.game_warning=""
            timing_config=self.input_timing
            self.timing_hint.set(f'同页约 {timing_config.selection_budget(0,True)*1000:.0f} ms · 翻页约 {timing_config.change_budget(1):.2f} s')
            if timing_config.release_margin:
                self.timing_hint.set(self.timing_hint.get()+f' · 重复音松开余量 {timing_config.release_margin*1000:.0f} ms')
            if timing_config.early_select:
                self.timing_hint.set('衔接：提前选区 · 松开余量自适应')
            if timing_config.handoff_margin and self.profile.strum_input=='gamepad':
                gap=(timing_config.key_gap+timing_config.handoff_margin)*1000
                self.duration_hint.set(f'交接预留约 {gap:.0f} ms；跨拍长音保留。'
                                       if self.sustain_notes.get() else '扫拨衔接需开启“拨弦与扫弦按谱面时值保持”。')
            else:
                self.duration_hint.set('右摇杆扫弦：选圆盘不截断延音；两次扫弦间自动回中。' if self.profile.strum_input=='gamepad'
                                       else '关闭后恢复固定短按；和弦／品格均适用。')
            try:
                plan=make_plan(score,self.profile,tap=tap,timing=timing_config,sustain_notes=self.sustain_notes.get())
                from .sustain import sweep_adjustments
                adjustments=sweep_adjustments(score,plan,timing_config)
                if adjustments:
                    first,shortened=adjustments[0]
                    if timing_config.handoff_margin and self.profile.strum_input=='gamepad':
                        self.duration_hint.set(self.duration_hint.get()+f' {len(adjustments)} 处扫弦调整。')
                    else:
                        self.duration_hint.set(f'{len(adjustments)} 处扫弦为切换提前松开；首处第 {first+1} 事件约 {shortened*1000:.0f} ms')
            except ValueError as error:self.game_warning=str(error)
            self.score = score
            timing = "逐音节奏" if re.search(r"(?im)^\s*(?:rhythm|节奏)\s*[:：]", text) else "网格节奏"
            self.info.set(f"{len(score.events)} 个节奏事件 · {float(score.beats):g} 拍 · {score.seconds:.1f} 秒 · {timing}")
            if self.game_warning:self.status.set('谱面可试听；游戏动作需要调整：'+self.game_warning)
            elif timing_config.early_select:
                self.status.set('谱面可试听；自适应输入衔接已启用。游戏演奏前请确认窗口与起始页。')
            else:self.status.set('谱面可试听；游戏连通性请先用“输入测试”实测')
            self.draw_timeline()
            self.draw_wheel()
            return score
        except (ValueError, KeyError) as error:
            self.score = None
            self.info.set(str(error))
            self.status.set('曲谱有误：'+str(error))
            self.draw_timeline()
            return None

    def refresh_windows(self):
        if not self.enable_native:
            return
        try:
            items = windows()
            self.window_items = {f"{label}  ({i + 1})": hwnd for i, (hwnd, label) in enumerate(items)}
            self.window_box.configure(values=list(self.window_items))
            if self.window.get() not in self.window_items:
                self.window.set("")
        except Exception as error:
            self.status.set(str(error))

    def stop(self):
        if self.tab_editor is not None:self.tab_editor.stop_preview()
        if self.library_window is not None:self.library_window.stop_preview()
        self.audio_cancel.set()
        self.player.stop()
        self.input_probe.stop()

    def start(self, real, audible=False):
        if self.busy:
            return
        if self.unarranged_document is not None:
            if real:self.status.set('当前未开始演奏：'+self.unarranged_reason+'。请打开上方“手动编谱”修改，或在编谱窗口右下角选“近似转译…”生成演奏谱。原谱可继续试听和保存。')
            else:
                self.open_visual_editor()
                if self.tab_editor is not None:self.tab_editor.preview(True)
            return
        if audible and self.arrangement_source is not None and self.source_game_score==self.score:
            self.open_visual_editor()
            if self.tab_editor is not None:self.tab_editor.preview(True)
            return
        score = self.analyze()
        if score is None:
            self.status.set(self.info.get())
            return
        try:
            self.playing_real=real
            profile = copy.deepcopy(self.profile)
            if audible:
                if profile.mode != "fret":
                    raise ValueError("和弦音高尚未核对；请在“游戏设置 → 时间线预演”查看动作节奏，或在游戏中试听")
                from .audio import SoundPreviewBackend
                self.preparing_audio = True
                self.audio_cancel.clear()
                self.editor.configure(state="disabled")
                self.status.set(self.control_hint("正在生成本地参考音频，可用 F9 取消…"))
                tap = int(self.tap.get()) / 1000
                def prepare():
                    try:
                        backend = SoundPreviewBackend(score, app_root()/".preview", self.audio_cancel)
                        self.messages.put(("audio_ready", (score, profile, backend, tap)))
                    except Exception as error:
                        self.messages.put(("audio_error", str(error)))
                threading.Thread(target=prepare, daemon=True).start()
                return
            if real:
                hwnd = self.window_items.get(self.window.get())
                if not hwnd:
                    raise ValueError("先在“游戏窗口”下拉框中选择游戏窗口；没有时点“刷新”")
                if self.input_timing.key=='adaptive' and profile.mode=='fret':
                    score=self.adapt_fingering(score)
                    if score is None:return
                make_plan(score,profile,tap=int(self.tap.get())/1000,timing=self.input_timing,sustain_notes=self.sustain_notes.get())
                trace=InputTrace(app_root()/"logs","auto_play",buffered=True)
                trace.record("score",events=len(score.events),seconds=score.seconds,bpm=score.bpm,input_speed=self.input_timing.key)
                trace.record('note_duration',sustain_notes=self.sustain_notes.get())
                trace.record('timing_config',**self.input_timing.__dict__,learned_margin=self.learn_logs.get() and self.log_estimate.ready)
                if self.input_timing.key=='adaptive' and profile.mode=='fret':trace.record('adaptation',summary=getattr(self,'adaptive_report','保留当前指法'))
                self.last_input_trace=trace.path
                self.current_input_trace=trace
                backend = WindowsBackend(hwnd,profile,trace=trace)
            else:
                backend = PreviewBackend()
            self._begin_playback_display(score,profile)
            self.elapsed = 0
            self.editor.configure(state="disabled")
            self.player.start(score, profile, backend, countdown=5 if real else 0, tap=int(self.tap.get()) / 1000,timing=self.input_timing,sustain_notes=self.sustain_notes.get())
        except Exception as error:
            if self.current_input_trace and not self.player.running:
                self.current_input_trace.close();self.current_input_trace=None
            self.editor.configure(state="normal")
            self.status.set(str(error))

    def _poll(self):
        try:
            while True:
                kind, data = self.messages.get_nowait()
                if kind=='gamepad_status':
                    self.gamepad_status.set(data)
                    continue
                if kind == "pause":
                    if self.tab_editor is not None and self.tab_editor.player.running:self.tab_editor.player.toggle_pause()
                    else:self.player.toggle_pause()
                elif kind == "stop":
                    self.stop()
                elif kind=="probe_state":
                    self.status.set(data)
                    self.probe_feedback.set(data)
                    self.gamepad_status.set(data)
                elif kind=="probe_done":
                    message,path=data
                    self.last_input_trace=Path(path)
                    self.editor.configure(state="normal")
                    self.status.set(message)
                    self.probe_feedback.set(message+"\n记录："+path)
                    self.gamepad_status.set(message)
                elif kind == "audio_ready":
                    self.preparing_audio = False
                    score, profile, backend, tap = data
                    try:
                        if self.audio_cancel.is_set():
                            backend.close()
                            self.editor.configure(state="normal")
                            self.status.set("本地试听已取消")
                        else:
                            self.elapsed = 0
                            self._begin_playback_display(score,profile)
                            self.player.start(score, profile, backend, tap=tap)
                    except Exception as error:
                        backend.close()
                        self.editor.configure(state="normal")
                        self.status.set(str(error))
                elif kind == "audio_error":
                    self.preparing_audio = False
                    self.editor.configure(state="normal")
                    self.status.set("本地试听已取消" if self.audio_cancel.is_set() else "试听失败：" + data)
                elif kind == "hotkeys":
                    self.hotkeys_changed(data)
                elif kind=='learning_ready':
                    self.learning_running=False;self.log_estimate=data
                    self.learning_info.set(data.description())
                    if not self.busy:
                        previous_status=self.status.get();self.analyze();self.status.set(previous_status)
                elif kind=='learning_error':
                    self.learning_running=False;self.learning_info.set('日志分析失败，继续使用默认值：'+data)
                elif kind in ("state", "done", "error"):
                    self.status.set(self.control_hint(data) if kind=='state' else data)
                    if kind in ("done", "error"):
                        if self.current_input_trace:
                            self.current_input_trace.record("playback_result",result=kind,message=data)
                            self.current_input_trace.close()
                            self.current_input_trace=None
                            if self.learn_logs.get():self.refresh_learning()
                        self.editor.configure(state="normal")
                        self.current_notes = ()
                        self.current_actions=();self.prepared_chord=None;self.prepared_page=None
                        self.draw_wheel()
                elif kind == "target":
                    self.target.set("目标：" + data)
                elif kind == "chord":
                    self.prepared_chord = data
                    self.draw_wheel()
                elif kind=='timbre':
                    if self.playing_real:self.game_timbre.set(TONE_NAMES[data])
                elif kind=='mode':
                    if self.playing_real:self.game_mode.set('和弦' if data=='chord' else '品格')
                elif kind == "page":
                    self.prepared_page = data
                    self.draw_wheel()
                elif kind == "note":
                    index, notes = data
                    event=self._display_note(index,notes)
                    self.editor.tag_remove("playing", "1.0", "end")
                    if event is not None:
                        line = event.line
                        if event.source_spans:
                            for row, column, length in event.source_spans:
                                self.editor.tag_add("playing", f"{row}.{column}", f"{row}.{column+length}")
                        else:
                            self.editor.tag_add("playing", f"{line}.0", f"{line}.end")
                        self.editor.see(f"{line}.0")
                    self.draw_wheel()
                elif kind == "progress":
                    self.elapsed = data
                    self.draw_timeline()
        except queue.Empty:
            pass
        self.after(40, self._poll)

    def _begin_playback_display(self,score,profile):
        # Keep the exact compiled/adapted score used by the player. Preparation
        # messages can arrive before its next onset and must not move this view.
        self.playback_score=score;self.playback_profile=profile
        self.current_chord='';self.current_page=profile.initial_page
        self.current_notes=();self.current_actions=()
        self.prepared_chord=None;self.prepared_page=None
        self.draw_wheel()

    def _display_note(self,index,notes):
        score=self.playback_score
        event=score.events[index] if score is not None and 0<=index<len(score.events) else None
        self.current_actions=tuple(notes) if event is not None else ()
        self.current_notes=(tuple(range(1,7)) if any(n in (UP,DOWN) for n in self.current_actions)
                            else tuple(n for n in self.current_actions if 1<=n<=6))
        if event is not None:
            self.current_chord=event.chord
            profile=self.playback_profile or self.profile
            # Mode changes recall an independent page without emitting a page
            # key event. Derive the displayed page from the sounding selector.
            self.current_page=profile.pages.get(event.chord,self.current_page)
        self.prepared_chord=None;self.prepared_page=None
        return event

    def draw_wheel(self):
        canvas = self.wheel
        canvas.delete("all")
        width, height = max(80, canvas.winfo_width()), max(40, canvas.winfo_height())
        profile=self.playback_profile or self.profile
        wheel_mode=(('fret' if self.current_chord in (*ROMAN,'Zero') else 'chord') if self.current_chord
                    else profile.initial_mode or 'fret') if profile.mode=='mixed' else profile.mode
        action=('↑ 上扫 1→6' if UP in self.current_actions else '↓ 下扫 6→1' if DOWN in self.current_actions
                else '拨 '+','.join(map(str,self.current_notes))+' 弦' if self.current_notes
                else '无新起音' if self.current_chord and self.playback_score is not None else '等待起音')
        caption=(f'演奏谱 · 第 {self.current_page+1} 页 · {"品格" if wheel_mode=="fret" else "和弦"} · {action}'
                 if self.playback_score is not None else f'布局示意 · 第 {self.current_page+1} 页 · {"品格" if wheel_mode=="fret" else "和弦"}')
        prepared=(f'提前准备：{self.prepared_chord}' if self.prepared_chord and self.prepared_chord!=self.current_chord
                  else f'提前翻页：第 {self.prepared_page+1} 页' if self.prepared_page is not None and self.prepared_page!=self.current_page else '')
        if height<180:
            self.hit_regions=[]
            canvas.create_text(width/2,height/2,text=caption+(' · '+self.current_chord if self.current_chord else '')+('\n'+prepared if prepared else ''),
                width=width-24,fill='white',font=('Microsoft YaHei UI',10,'bold'))
            return
        content_height=height-44
        cx, cy, radius = width*.5, content_height*.5, min(width*.34,content_height*.44)
        label_size=max(7,min(17,int(radius*.12)))
        labels = [name for name in profile.chords if profile.pages[name] == self.current_page and (name in (*ROMAN,'Zero'))==(wheel_mode=='fret')]
        if wheel_mode == "fret" and self.current_page == 4:
            labels = ["Zero"] * 6
        self.hit_regions = []
        for slot in range(6):
            label = labels[slot] if slot < len(labels) else "—"
            selected = label == self.current_chord and (label!='Zero' or slot==0)
            canvas.create_arc(cx-radius, cy-radius, cx+radius, cy+radius, start=60-slot*60, extent=60,
                              fill=ACCENT if selected else "#2e4b62", outline=INK, width=4)
            angle = math.radians(90 - slot * 60)
            x, y = cx + .78*radius*math.cos(angle), cy - .78*radius*math.sin(angle)
            canvas.create_text(x, y, text=label, fill="white", font=("Segoe UI",label_size,"bold"))
            if label != "—":
                self.hit_regions.append((x, y, radius*.28, "{" + label + "} "))
        inner = radius * .53
        canvas.create_oval(cx-inner, cy-inner, cx+inner, cy+inner, fill="#13293c", outline="#567083", width=3)
        for index in range(6):
            string = 6 - index
            y = cy + (index - 2.5) * inner * .27
            active = string in self.current_notes
            canvas.create_line(cx-inner*.84, y, cx+inner*.84, y, fill="#ffa1cc" if active else ACCENT,
                               width=4 if active else 2)
            if inner*.27>=10:
                canvas.create_rectangle(cx-6,y-5,cx+6,y+5,fill='#13293c',outline='')
                canvas.create_text(cx,y,text=str(7-string),fill='white',font=('Consolas',max(7,min(10,int(inner*.27*.65)))) )
            self.hit_regions.append((cx, y, inner*.12, f"{string} "))
        canvas.create_text(12,height-26,text=caption,anchor='sw',fill='#bdd0de',font=('Microsoft YaHei UI',9))
        canvas.create_text(12,height-6,text=prepared or '按起音显示 · 非游戏回读',anchor='sw',fill='#e1bf82' if prepared else '#bdd0de',font=('Microsoft YaHei UI',9))

    def wheel_click(self, event):
        if self.notation.get() == "六线谱 TAB":
            self.status.set("六线谱中请直接在对应弦行填写品位数字；圆盘用于显示当前演奏位置")
            return
        for x, y, radius, text in reversed(self.hit_regions):
            if (event.x-x)**2 + (event.y-y)**2 <= radius**2:
                self.insert(text)
                return

    def draw_timeline(self):
        canvas = self.timeline
        if not self.score:
            canvas.delete('all');self._timeline_score=None
            return
        width = max(20, canvas.winfo_width()-4)
        if getattr(self,'_timeline_score',None) is not self.score or getattr(self,'_timeline_width',None)!=width:
            canvas.delete('all');self._timeline_score=self.score;self._timeline_width=width
            self._timeline_seconds=self.score.seconds
            total=max(.01,self._timeline_seconds)
            for event in self.score.events:
                start=self.score.seconds_at(event.start)
                x=2+start/total*width
                w=(self.score.seconds_at(event.start+event.beats)-start)/total*width
                canvas.create_rectangle(x,10,x+max(.5,w-1),43,fill='#f2cddd' if event.notes else LINE,outline='')
                if w>23:canvas.create_text(x+w/2,26,text=event.label,fill=INK,font=('Consolas',9))
            canvas.create_line(0,6,0,48,fill=ACCENT,width=2,tags='timeline_playhead')
            canvas.create_text(2,60,text='节奏时间线',fill=MUTED,anchor='w',font=('Microsoft YaHei UI',9))
        x=2+min(1,self.elapsed/max(.01,self._timeline_seconds))*width
        canvas.coords('timeline_playhead',x,6,x,48)
        def stamp(value):
            return f"{int(value)//60:02d}:{int(value)%60:02d}"
        self.time_text.configure(text=f"{stamp(self.elapsed)} / {stamp(self._timeline_seconds)}")

    def save_score(self,path=None):
        if self.unarranged_document is not None:
            from .library import MusicLibrary
            path=path or filedialog.asksaveasfilename(parent=self,title='保存原谱',
                initialdir=str(MusicLibrary().root/'scores'),defaultextension='.gscore',filetypes=[('吉他原谱','*.gscore')])
            if not path:return
            try:
                self.unarranged_document.save(path);self.dirty=False;self.file_path=path
                self.status.set('原谱已保存，全部音符和节奏保留：'+Path(path).name);return Path(path)
            except (ValueError,OSError) as error:self.status.set('保存失败：'+str(error));return None
        if self.analyze() is None:
            self.status.set('保存失败：'+self.info.get());return None
        from .library import MusicLibrary
        path = path or filedialog.asksaveasfilename(parent=self, title="保存曲谱", initialdir=str(MusicLibrary().root/'scores'),
                                                   defaultextension=".gscore", filetypes=[("吉他曲谱", "*.gscore")])
        if not path:
            return
        try:
            atomic_json(path, {"version": 3 if self.profile.mode=='mixed' else 2, "text": self.editor.get("1.0", "end-1c"),
                              "bpm": float(self.bpm.get()), "tap_ms": int(self.tap.get()),
                              "sustain_notes":self.sustain_notes.get(),
                              "coordinate_inputs":self.coordinate_inputs.get(),
                              "profile": self.profile.to_dict(), "tab_step": self.tab_step.get(),
                              "notation": "tab" if self.notation.get() == "六线谱 TAB" else "commands",
                              "tempo_map":[[str(at),tempo] for at,tempo in self.tempo_source],"tempo_bpm":self.tempo_bpm,
                              "time_signature":list(self.time_signature),"measures":self.measure_count,"input_speed":self.input_timing.key,
                              "bar_changes":[c.to_dict() for c in self.bar_changes],"default_timbre":self.default_timbre,"capo":self.capo,
                              "chord_changes":[[str(at),name] for at,name in self.chord_changes],"origin_notice":self.origin_notice,
                              "source_document":self.arrangement_source.to_dict() if self.arrangement_source is not None and self.source_game_score==self.score else None})
            self.dirty, self.file_path = False, path
            self.status.set("曲谱已保存：" + Path(path).name)
            return Path(path)
        except (ValueError, OSError) as error:
            self.status.set("保存失败：" + str(error))

    def open_score(self,path=None):
        if self.busy:
            return
        path = path or filedialog.askopenfilename(parent=self, filetypes=[("曲谱", "*.gscore *.txt"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            raw = Path(path).read_text(encoding="utf-8-sig")
            source_document=None
            if Path(path).suffix.lower()=='.gscore' and isinstance(data:=json.loads(raw),dict) and data.get('kind')=='fingerstyle_source':
                from .fingerstyle_document import FingerstyleDocument
                self.open_fingerstyle_editor(FingerstyleDocument.from_dict(data),path);return
            if self.dirty and not messagebox.askyesno("打开曲谱", "当前有未保存的修改，仍要打开另一份曲谱吗？", parent=self):return
            if Path(path).suffix.lower() == ".gscore":
                data = json.loads(raw)
                if not isinstance(data, dict) or data.get("version") not in (1,2,3) or not isinstance(data.get("text"), str):
                    raise ValueError("曲谱格式或版本无效")
                profile = GameProfile.from_dict(data["profile"])
                from .settings import reuse_layout
                profile=reuse_layout(profile,self.local_layouts.get(profile.mode))
                bpm, tap = float(data["bpm"]), int(data.get("tap_ms", 45))
                sustain_notes=data.get('sustain_notes',True)
                if type(sustain_notes) is not bool:raise ValueError('按谱面时值保持设置无效')
                coordinate_inputs=data.get('coordinate_inputs',self.coordinate_inputs.get())
                if type(coordinate_inputs) is not bool:raise ValueError('自适应输入衔接设置无效')
                notation = data.get("notation", "commands")
                speed=data.get('input_speed',self.input_timing.key)
                if speed not in PRESETS:raise ValueError('输入速度档位无效')
                if speed=='smooth_mixed':speed='steady_fast';coordinate_inputs=True
                tempos=tuple((Fraction(str(at)),float(tempo)) for at,tempo in data.get("tempo_map",[]))
                tempo_bpm=float(data.get("tempo_bpm",bpm))
                meter=data.get('time_signature',[4,4]);measures=data.get('measures',0)
                changes=parse_changes(data.get('bar_changes',[]));tone=data.get('default_timbre','clean')
                capo=data.get('capo',0)
                notice=data.get('origin_notice','')
                if data.get('source_document') is not None:
                    from .fingerstyle_document import FingerstyleDocument
                    source_document=FingerstyleDocument.from_dict(data['source_document'])
                if not isinstance(notice,str) or len(notice)>4000:raise ValueError('转译来源说明无效')
                from .chord_score import parse_chord_changes
                chord_marks=parse_chord_changes(data.get('chord_changes',[]),mixed=profile.mode=='mixed')
                if type(capo) is not int or not 0<=capo<=24:raise ValueError('Capo 应为 0～24 品')
                if tone not in TONE_NAMES:raise ValueError('音色应为清音或失真')
                if (not isinstance(meter,list) or len(meter)!=2 or any(type(n) is not int for n in meter)
                    or not 1<=meter[0]<=16 or meter[1] not in (1,2,4,8,16)
                    or type(measures) is not int or not 0<=measures<=25000):
                    raise ValueError('拍号或小节数无效')
                if (any(at<0 or not 0<tempo<60_000_001 for at,tempo in tempos)
                    or not 0<tempo_bpm<=360 or (tempos and tempos[0][0]!=0)
                    or any(a[0]>=b[0] for a,b in zip(tempos,tempos[1:]))):
                    raise ValueError("速度变化数据无效")
                if notation not in ("tab", "commands"):
                    raise ValueError("不支持的曲谱写法")
                if not 20 <= bpm <= 360 or not 15 <= tap <= 200:
                    raise ValueError("速度或按键时长超出支持范围")
                mode = "混合模式" if profile.mode=='mixed' else "和弦模式" if profile.mode == "chord" else "品格模式"
                self.profiles[mode] = profile
                self.mode.set(mode)
                self.tempo_source=tempos
                self.tempo_bpm=tempo_bpm
                self.time_signature=tuple(meter);self.measure_count=measures
                self.bar_changes=changes;self.default_timbre=tone
                self.capo=capo;self.chord_changes=chord_marks;self.origin_notice=notice
                self.notation.set("六线谱 TAB" if notation == "tab" else "指令谱")
                self.tab_step.set(str(data.get("tab_step", "1/4")))
                self.bpm.set(f"{bpm:g}")
                self.tap.set(str(tap))
                self.sustain_notes.set(sustain_notes)
                self.coordinate_inputs.set(coordinate_inputs)
                self.input_speed.set(PRESETS[speed].label)
                self.page.set(str(profile.initial_page+1))
                self.game_timbre.set(TONE_NAMES[profile.initial_timbre])
                self.game_mode.set('和弦' if (profile.initial_mode or profile.mode)=='chord' else '品格')
                raw = data["text"]
            else:
                self.tempo_source=()
                self.time_signature=(4,4);self.measure_count=0
                self.bar_changes=();self.default_timbre='clean'
                self.capo=0
                self.chord_changes=()
                self.origin_notice=''
                is_tab = any(re.match(r"\s*[A-Ga-g1-6]\s*\|", line) for line in raw.splitlines())
                self.notation.set("六线谱 TAB" if is_tab else "指令谱")
                if is_tab:
                    self.mode.set("品格模式")
            self.unarranged_document=None;self.editor.configure(state='normal')
            self.editor.delete("1.0", "end")
            self.editor.insert("1.0", raw)
            self.editor.edit_modified(False)
            self.file_path, self.dirty = path, False
            self.mode_changed(loaded=True)
            self.arrangement_source=source_document
            self.source_game_score=self.score if source_document is not None else None
            self.fingerstyle_game_score=None
            self.dirty = False
        except (ValueError, OSError, KeyError, TypeError, ZeroDivisionError) as error:
            self.status.set("打开失败：" + str(error))

    def calibrate(self):
        if self.busy:
            return
        dialog = tk.Toplevel(self)
        dialog.title("游戏布局与坐标校准")
        dialog.geometry(f'760x{min(710,self.winfo_screenheight()-90)}')
        dialog.transient(self)
        editing_mode = self.mode.get()
        working = copy.deepcopy(self.profile)
        ttk.Label(dialog, text="自动校准：3 秒内切回游戏完整吉他界面，识别六根粉色琴弦。\n识别后核对下方示意图；也可导入仅含游戏客户区的 PNG，或手动拾取。", padding=14).pack(anchor="w")
        auto_row=ttk.Frame(dialog,padding=(14,0,14,8));auto_row.pack(fill='x')
        tree = ttk.Treeview(dialog, columns=("page", "x", "y"), show="tree headings", height=7)
        tree.heading("#0", text="和弦 / 品格 / 扫弦")
        for column, label in (("page", "页码"), ("x", "横向位置"), ("y", "纵向位置")):
            tree.heading(column, text=label)
            tree.column(column, width=100, anchor="center")
        tree.column("#0", width=200)
        tree.pack(fill="both", expand=True, padx=14)
        preview=tk.Canvas(dialog,height=190,bg=PAPER,highlightthickness=0);preview.pack(fill='x',padx=14,pady=6)
        preview_photo=None
        feedback = tk.StringVar(value="位置按游戏客户区保存，不包含窗口边框")
        def refresh():
            tree.delete(*tree.get_children())
            for name, point in working.chords.items():
                tree.insert("", "end", iid=name, text=name, values=(working.pages[name]+1, f"{point[0]:.4f}", f"{point[1]:.4f}"))
            tree.insert("", "end", iid="__strum", text="上下扫弦共用空白位置", values=("—", *(f"{n:.4f}" for n in working.strum_point)))
            preview.delete('all');width=700;height=190
            if preview_photo is not None:
                width=preview_photo.width();height=preview_photo.height()
                preview.create_image(0,0,image=preview_photo,anchor='nw')
            preview.create_rectangle(1,1,width-1,height-1,outline=LINE)
            for name,point in list(working.chords.items())[:6]:
                x,y=point[0]*width,point[1]*height
                preview.create_oval(x-4,y-4,x+4,y+4,fill=ACCENT,outline='')
                preview.create_text(x,y-12,text=name,fill=INK,font=('Segoe UI',9))
            x,y=working.strum_point[0]*width,working.strum_point[1]*height
            preview.create_oval(x-4,y-4,x+4,y+4,fill='#12879a',outline='')
            preview.create_text(x,y+13,text='扫弦点',fill=INK,font=('Microsoft YaHei UI',9))
        refresh()
        def accept_frame(frame):
            nonlocal preview_photo
            from .calibration import detect_wheel
            preview_photo=tk.PhotoImage(master=dialog,data=frame.thumbnail_ppm(700,190),format='PPM')
            refresh()
            wheel=detect_wheel(frame);wheel.apply(working);refresh()
            feedback.set(f'已识别六根琴弦：圆心 ({wheel.x:.0f}, {wheel.y:.0f})，六个扇区已生成。请核对扫弦点位于圆盘外，再点“应用校准”；模式、音色和起始页仍按实际填写。')
        def automatic():
            hwnd=self.window_items.get(self.window.get())
            if not hwnd:feedback.set('请先在主窗口选择目标游戏窗口');return
            feedback.set('3 秒后读取所选游戏客户区，请切回完整吉他界面；不会点击或按键')
            def finish():
                if not dialog.winfo_exists():return
                try:accept_frame(capture_client(hwnd))
                except Exception as error:feedback.set(str(error))
                finally:dialog.lift()
            from .native import activate_window
            try:activate_window(hwnd)
            except RuntimeError:feedback.set('Windows 未允许自动切换窗口，请在 3 秒内手动切回所选游戏')
            dialog.after(3000,finish)
        def image_calibration():
            path=filedialog.askopenfilename(parent=dialog,title='选择仅含完整游戏客户区的截图',filetypes=[('PNG 图像','*.png')])
            if not path:return
            try:
                from .calibration import from_photo
                photo=tk.PhotoImage(master=dialog,file=path);accept_frame(from_photo(photo))
            except Exception as error:feedback.set(str(error))
        ttk.Button(auto_row,text='自动切回游戏并截图校准',command=automatic,style='Accent.TButton').pack(side='left')
        ttk.Button(auto_row,text='从 PNG 截图识别',command=image_calibration).pack(side='left',padx=8)
        def capture():
            selected = tree.selection()
            hwnd = self.window_items.get(self.window.get())
            if not selected or not hwnd:
                feedback.set("先选择主窗口中的游戏窗口，再选一个需要校准的位置")
                return
            name = selected[0]
            feedback.set(f"3 秒后拾取 {name}，请切到游戏并放好鼠标")
            def finish():
                if not dialog.winfo_exists():
                    return
                try:
                    point, size = pick_relative_point(hwnd)
                    if name == "__strum":
                        working.strum_point = point
                    else:
                        # All pages share the same six sectors: calibrate a slot once.
                        old_point = working.chords[name]
                        for selector in list(working.chords):
                            if working.chords[selector] == old_point:
                                working.chords[selector] = point
                    working.reference_size = size
                    feedback.set(f"已拾取 {name}：{point[0]:.4f}, {point[1]:.4f}")
                    refresh()
                except Exception as error:
                    feedback.set(str(error))
            dialog.after(3000, finish)
        def save():
            working.calibrated = True
            from .settings import write_layout
            try:write_layout(self.settings_dir,working)
            except OSError as error:feedback.set('校准保存失败：'+str(error));return
            self.local_layouts[working.mode]=copy.deepcopy(working)
            self.profiles[editing_mode] = working
            self.dirty = True
            self.draw_wheel()
            dialog.destroy()
        def export():
            path = filedialog.asksaveasfilename(parent=dialog, defaultextension=".json", filetypes=[("布局文件", "*.json")])
            if path:
                try:
                    atomic_json(path, working.to_dict())
                    feedback.set("布局文件已导出")
                except OSError as error:
                    feedback.set(str(error))
        def import_layout():
            nonlocal working
            path = filedialog.askopenfilename(parent=dialog, filetypes=[("布局文件", "*.json")])
            if path:
                try:
                    loaded = load_profile(path)
                    if loaded.mode != working.mode:
                        raise ValueError("导入布局的模式与当前模式不同")
                    working = loaded
                    refresh()
                except (ValueError, OSError, TypeError) as error:
                    feedback.set(str(error))
        row = ttk.Frame(dialog, padding=14)
        row.pack(fill="x")
        ttk.Button(row, text="3 秒后拾取", command=capture).pack(side="left")
        ttk.Button(row, text="导入布局", command=import_layout).pack(side="left", padx=6)
        ttk.Button(row, text="导出布局", command=export).pack(side="left")
        ttk.Button(row, text="应用校准", command=save).pack(side="right")
        ttk.Label(dialog, textvariable=feedback, wraplength=560, padding=14).pack(anchor="w")

    def close(self):
        if self.tab_editor is not None and self.tab_editor.changed:
            self.tab_editor.close()
            if self.tab_editor is not None:return
        if self.dirty and not messagebox.askyesno("关闭", "有未保存的曲谱修改，仍要关闭吗？", parent=self):
            return
        self.stop()
        self.status.set("正在释放输入…")
        def finish():
            if self.busy:
                self.after(25, finish)
                return
            if self.hotkeys:
                self.hotkeys.close()
            if self.tab_editor is not None:self.tab_editor.destroy()
            if self.library_window is not None:self.library_window.destroy()
            if self.usage_tour is not None:self.usage_tour.close()
            if self.welcome is not None:self.welcome.close()
            if self.current_input_trace:self.current_input_trace.close();self.current_input_trace=None
            for timer in self.tk.call("after", "info"):
                self.after_cancel(timer)
            self.destroy()
        finish()


def main():
    App().mainloop()
