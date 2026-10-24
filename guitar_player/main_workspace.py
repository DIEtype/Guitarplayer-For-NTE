"""Main workflow: source actions, transport, connection, then score and game."""
import tkinter as tk
from tkinter import ttk

from . import __version__

BG='#edf2f6';PAPER='#ffffff';INK='#20394e';MUTED='#62798d'
BLUE='#326da8';TEAL='#0e7380';ROSE='#b93270'


def build(app):
    style=ttk.Style(app)
    for name,color,active in (('Source',BLUE,'#24527f'),('Compose',TEAL,'#095863')):
        style.configure(f'Workspace.{name}.TButton',background=color,foreground='white',
                        padding=(14,9),font=('Microsoft YaHei UI',10,'bold'))
        style.map(f'Workspace.{name}.TButton',background=[('active',active)])
    header=ttk.Frame(app,padding=(20,14,20,10));header.pack(fill='x');app.header=header
    app.brand_label=ttk.Label(header,text='异环 / 吉他编曲器',font=('Microsoft YaHei UI',21,'bold'))
    app.brand_label.pack(side='left')
    app.credit_label=ttk.Label(header,text=f'v{__version__}\nby Akari',foreground=MUTED,font=('Microsoft YaHei UI',9))
    app.credit_label.pack(side='left',padx=12)
    app.guide_button=ttk.Button(header,text='使用引导',command=app.open_usage_tour)
    app.guide_button.pack(side='right')

    app.source_actions=ttk.Frame(app,padding=(20,0,20,10));app.source_actions.pack(fill='x')
    app.import_button=ttk.Button(app.source_actions,text='导入 MIDI',style='Workspace.Source.TButton',command=app.import_midi)
    app.library_button=ttk.Button(app.source_actions,text='我的曲库',style='Workspace.Source.TButton',command=app.open_library)
    app.compose_button=ttk.Button(app.source_actions,text='手动编谱',style='Workspace.Compose.TButton',command=app.open_visual_editor)
    app.source_editor_button=app.compose_button
    app.open_button=ttk.Button(app.source_actions,text='打开曲谱',command=app.open_score)
    app.save_button=ttk.Button(app.source_actions,text='保存曲谱',command=app.save_score)
    app.score_tools=ttk.Menubutton(app.source_actions,text='曲谱工具 ▾')
    app.score_menu=tk.Menu(app.score_tools,tearoff=False);app.score_tools.configure(menu=app.score_menu)
    app.score_menu.add_command(label='指弹／混合转译…',command=app.open_translator)
    app.score_menu.add_command(label='预览指法适配（品格模式）',command=app.adapt_fingering)
    app.score_menu.add_separator()
    app.score_menu.add_command(label='载入试弹《小星星》',command=app.load_demo)
    app.score_menu.add_command(label='恢复上次编谱草稿…',command=app.open_fingerstyle_editor)
    app.score_menu.add_command(label='文本谱语法…',command=lambda:app.open_guide('text'))
    app.score_menu.add_command(label='完整使用说明…',command=app.open_guide)
    app.source_buttons=(app.import_button,app.library_button,app.compose_button,app.open_button,app.save_button,app.score_tools)
    app._source_compact=None
    app._build_playback_bar()

    app.controls=ttk.Frame(app,padding=(20,0,20,10));app.controls.pack(fill='x');app.target_row=app.controls
    score=ttk.Frame(app.controls);connection=ttk.Frame(app.controls);options=ttk.Frame(app.controls)
    app.control_groups=(score,connection,options);app._connection_compact=None
    ttk.Label(score,text='演奏模式').pack(side='left')
    mode_box=ttk.Combobox(score,textvariable=app.mode,values=list(app.profiles),state='readonly',width=9)
    mode_box.pack(side='left',padx=(5,14));mode_box.bind('<<ComboboxSelected>>',app.mode_changed)
    ttk.Label(score,text='BPM').pack(side='left')
    bpm=ttk.Spinbox(score,textvariable=app.bpm,from_=20,to=360,width=5,command=app.analyze)
    bpm.pack(side='left',padx=(5,12));bpm.bind('<FocusOut>',lambda e:app.analyze())
    ttk.Label(connection,text='游戏窗口').pack(side='left')
    app.window_box=ttk.Combobox(connection,textvariable=app.window,state='readonly',width=23)
    app.window_box.pack(side='left',fill='x',expand=True,padx=6)
    ttk.Button(connection,text='刷新',command=app.refresh_windows,padding=(8,4)).pack(side='left')
    app.settings_button=ttk.Menubutton(options,text='游戏设置 ▾')
    app.settings_button.pack(side='right',padx=(10,0))
    app.settings_menu=tk.Menu(app.settings_button,tearoff=False);app.settings_button.configure(menu=app.settings_menu)
    app.settings_menu.add_command(label='输入时序与日志…',command=lambda:app.open_game_settings('timing'))
    app.settings_menu.add_command(label='分模式起始页…',command=lambda:app.open_game_settings('pages'))
    app.settings_menu.add_command(label='鼠标与手柄…',command=app.open_strum_settings)
    app.settings_menu.add_separator()
    app.settings_menu.add_command(label='时间线预演（不发输入）',command=lambda:app.start(False))

    bottom=ttk.Frame(app,padding=(20,0,20,9));bottom.pack(side='bottom',fill='x')
    app.timeline=tk.Canvas(bottom,height=64,background=BG,highlightthickness=0)
    app.timeline.pack(fill='x',pady=(4,0));app.timeline.bind('<Configure>',lambda e:app.draw_timeline())
    from .widgets import StatusBanner
    app.status_banner=StatusBanner(bottom,app.status);app.status_banner.pack(fill='x',pady=(0,5))
    footline=ttk.Frame(bottom);footline.pack(fill='x')
    app.help_button=ttk.Button(footline,textvariable=app.help_notice,command=app.open_welcome,padding=(8,0))
    app.help_button.pack(side='right',padx=(12,0))
    target=ttk.Label(footline,textvariable=app.target,foreground=MUTED,wraplength=900)
    target.pack(side='left',fill='x',expand=True)
    footline.bind('<Configure>',lambda e:target.configure(wraplength=max(200,e.width-app.help_button.winfo_width()-18)))

    panes=ttk.Panedwindow(app,orient='horizontal');panes.pack(fill='both',expand=True,padx=20)
    left,right=ttk.Frame(panes),ttk.Frame(panes);panes.add(left,weight=6);panes.add(right,weight=5)
    caption=ttk.Frame(left);caption.pack(fill='x',pady=(0,7))
    ttk.Label(caption,text='曲谱',font=('Microsoft YaHei UI',12,'bold')).pack(side='left')
    notation=ttk.Combobox(caption,textvariable=app.notation,values=('六线谱 TAB','指令谱'),state='readonly',width=10)
    notation.pack(side='left',padx=8);notation.bind('<<ComboboxSelected>>',app.notation_changed)
    ttk.Label(caption,text='字符网格').pack(side='left')
    step=ttk.Combobox(caption,textvariable=app.tab_step,width=5,values=('1','1/2','1/3','1/4','1/6','1/8','1/12','1/16','1/32','1/64'))
    step.pack(side='left',padx=5);step.bind('<FocusOut>',lambda e:app.analyze())
    app.editor=tk.Text(left,wrap='none',undo=True,bg=PAPER,fg=INK,insertbackground=ROSE,
        font=('Consolas',12),relief='flat',padx=14,pady=12,spacing1=4,spacing3=5,width=46,height=12)
    scrollbar=ttk.Scrollbar(left,orient='horizontal',command=app.editor.xview);scrollbar.pack(side='bottom',fill='x',padx=(0,12))
    app.editor.configure(xscrollcommand=scrollbar.set)
    app.editor.tag_configure('playing',background='#f8e1eb');app.editor.tag_configure('comment',foreground=MUTED)
    ttk.Label(left,textvariable=app.info,foreground=MUTED,wraplength=440).pack(side='bottom',anchor='w',pady=7)
    app.editor.pack(fill='both',expand=True,padx=(0,12))

    heading=ttk.Frame(right);heading.pack(fill='x',pady=(0,7))
    ttk.Label(heading,text='游戏准备',font=('Microsoft YaHei UI',12,'bold')).pack(side='left')
    app.calibrate_button=ttk.Button(heading,text='校准布局',command=app.calibrate,padding=(8,4));app.calibrate_button.pack(side='right')
    app.input_test_button=ttk.Button(heading,text='输入测试',command=app.open_input_tests,padding=(8,4));app.input_test_button.pack(side='right',padx=(0,6))
    current=ttk.Frame(right);current.pack(fill='x',pady=(0,7))
    ttk.Label(current,text='游戏当前').pack(side='left')
    mode=ttk.Combobox(current,textvariable=app.game_mode,values=('品格','和弦'),state='readonly',width=4)
    mode.pack(side='left',padx=4);mode.bind('<<ComboboxSelected>>',lambda e:app.analyze())
    tone=ttk.Combobox(current,textvariable=app.game_timbre,values=('清音','失真'),state='readonly',width=4)
    tone.pack(side='left',padx=(0,8));tone.bind('<<ComboboxSelected>>',lambda e:app.analyze())
    ttk.Label(current,text='第').pack(side='left')
    app.page_box=ttk.Spinbox(current,textvariable=app.page,from_=1,to=5,width=3,command=app.analyze)
    app.page_box.pack(side='left',padx=3);app.page_box.bind('<FocusOut>',lambda e:app.analyze())
    ttk.Label(current,text='页').pack(side='left')
    app.preparation_hint=ttk.Label(right,text='按游戏实际状态填写；切换模式时程序自动按 Z。',foreground=MUTED)
    app.preparation_hint.pack(anchor='w',pady=(0,7))
    app.game_setup_summary=tk.StringVar()
    app.game_summary_label=ttk.Label(right,textvariable=app.game_setup_summary,foreground=TEAL,wraplength=440)
    app.game_summary_label.pack(side='bottom',anchor='w',pady=(3,7))
    app.mapping_hint=ttk.Label(right,text='按键 1→6 对应吉他 6→1 弦；扫弦在手动编谱中填写。',foreground=MUTED,wraplength=440)
    app.mapping_hint.pack(side='bottom',anchor='w',pady=5)
    app.wheel=tk.Canvas(right,background=INK,height=280,highlightthickness=0)
    app.wheel.pack(fill='both',expand=True);app.wheel.bind('<Configure>',lambda e:app.draw_wheel());app.wheel.bind('<Button-1>',app.wheel_click)
    for variable in (app.input_speed,app.sustain_notes,app.strum_input,app.coordinate_inputs):
        variable.trace_add('write',lambda *_:summary(app))
    summary(app)
    app._vertical_compact=None;app._brand_small=None;app.bind('<Configure>',app._layout_vertical,add='+');layout(app)


def summary(app):
    speed=app.input_speed.get().split('（')[0]
    app.game_setup_summary.set('输入：'+speed+' · '+('按谱面保持时值' if app.sustain_notes.get() else '固定短按')+
                              ' · '+('右摇杆扫弦' if app.strum_input.get()=='gamepad' else '鼠标扫弦'))


def layout(app,event=None):
    if event is not None and event.widget is not app:return
    width=app.winfo_width();compact=app.winfo_height()<680
    if compact!=app._vertical_compact:
        app._vertical_compact=compact
        app.header.configure(padding=(20,10,20,6) if compact else (20,14,20,10))
        if compact:
            app.timeline.pack_forget();app.preparation_hint.pack_forget();app.mapping_hint.pack_forget()
        else:
            app.timeline.pack(fill='x',pady=(4,0),before=app.status_banner)
            app.preparation_hint.pack(anchor='w',pady=(0,7),before=app.game_summary_label)
            app.mapping_hint.pack(side='bottom',anchor='w',pady=5,after=app.game_summary_label)
    source_compact=width<900
    if source_compact!=app._source_compact:
        app._source_compact=source_compact
        for button in app.source_buttons:button.grid_forget()
        for i in range(6):app.source_actions.columnconfigure(i,weight=0)
        for i,button in enumerate(app.source_buttons):
            row,column=divmod(i,3) if source_compact else (0,i)
            button.grid(row=row,column=column,sticky='ew',padx=(0,7),pady=(0,5 if source_compact and row==0 else 0))
            app.source_actions.columnconfigure(column,weight=1)
    connection_compact=width<1080
    if connection_compact!=app._connection_compact:
        app._connection_compact=connection_compact
        score,connection,options=app.control_groups
        for group in app.control_groups:group.grid_forget()
        for i in range(3):app.controls.columnconfigure(i,weight=0)
        if connection_compact:
            score.grid(row=0,column=0,sticky='w',pady=(0,5));options.grid(row=0,column=1,sticky='e',pady=(0,5))
            connection.grid(row=1,column=0,columnspan=2,sticky='ew');app.controls.columnconfigure(0,weight=1)
        else:
            score.grid(row=0,column=0,sticky='w');connection.grid(row=0,column=1,sticky='ew')
            options.grid(row=0,column=2,sticky='e');app.controls.columnconfigure(1,weight=1)
    small=width<850
    if small!=app._brand_small:
        app._brand_small=small;app.brand_label.configure(font=('Microsoft YaHei UI',16 if small else 21,'bold'))
