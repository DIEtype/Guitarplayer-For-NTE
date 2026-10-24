"""Compact desktop editing palette with separate note and measure tools."""
import tkinter as tk
from tkinter import ttk

from .game_profile import CHORD_NAMES
from .guitar import UP,DOWN
from .measures import TONE_NAMES
from .tablature import VALUES
from .visual_editor import GRIDS,PAPER,LINE
from .fingerstyle_document import HIT_X,HIT_DOT

LOAD='#0e7380';TRANSLATE='#b45309';TEXT='#20394e';MUTED='#64768a';PANEL='#f5f7fa'


def styles(editor):
    style=ttk.Style(editor)
    style.configure('Composer.TFrame',background=PANEL)
    style.configure('Composer.TLabel',background=PANEL,foreground=TEXT)
    style.configure('Composer.Muted.TLabel',background=PANEL,foreground=MUTED,font=('Microsoft YaHei UI',9))
    style.configure('Composer.Title.TLabel',background=PANEL,foreground=TEXT,font=('Microsoft YaHei UI',17,'bold'))
    style.configure('Composer.TButton',padding=(8,5),font=('Microsoft YaHei UI',9))
    style.configure('Composer.TLabelframe',background=PANEL,borderwidth=1,relief='solid')
    style.configure('Composer.TLabelframe.Label',background=PANEL,foreground=MUTED,font=('Microsoft YaHei UI',9,'bold'))
    style.configure('Composer.TNotebook',background=PANEL,borderwidth=0,tabmargins=(0,0,0,0))
    style.configure('Composer.TNotebook.Tab',padding=(16,7),font=('Microsoft YaHei UI',10))
    style.map('Composer.TNotebook.Tab',background=[('selected',PANEL)],foreground=[('selected',LOAD)])
    for name,color,active in (('Load',LOAD,'#095863'),('Translate',TRANSLATE,'#8b4008')):
        style.configure(f'Composer.{name}.TButton',background=color,foreground='white',
                        padding=(15,10),font=('Microsoft YaHei UI',10,'bold'),borderwidth=0)
        style.map(f'Composer.{name}.TButton',background=[('active',active),('pressed',active)],foreground=[('disabled','#dde2e7')])


def button(parent,text,command,**options):
    return ttk.Button(parent,text=text,command=command,style='Composer.TButton',**options)


def build_workspace(e):
    styles(e);e.configure(bg=PANEL)
    heading=ttk.Frame(e,style='Composer.TFrame',padding=(16,9));heading.pack(fill='x')
    ttk.Label(heading,text='吉他编曲',style='Composer.Title.TLabel').pack(side='left')
    ttk.Label(heading,text='原谱工作区',style='Composer.Muted.TLabel').pack(side='left',padx=(12,0))
    e.credit_label=ttk.Label(heading,text='by Akari',style='Composer.Muted.TLabel')
    e.credit_label.pack(side='left',padx=(14,0))
    button(heading,'新建',e.clear).pack(side='right')
    button(heading,'打开原谱…',e.open_file).pack(side='right',padx=5)
    e.pattern_button=button(heading,'节奏预设…',e.pattern_dialog);e.pattern_button.pack(side='right',padx=5)
    settings=ttk.Frame(e,style='Composer.TFrame',padding=(16,0,16,8));settings.pack(fill='x')
    ttk.Label(settings,text='BPM',style='Composer.TLabel').pack(side='left')
    bpm=ttk.Spinbox(settings,textvariable=e.bpm,from_=20,to=360,width=5,command=e.settings_changed)
    bpm.pack(side='left',padx=(5,14));bpm.bind('<Return>',e.settings_changed);bpm.bind('<FocusOut>',e.settings_changed)
    ttk.Label(settings,text='拍号',style='Composer.TLabel').pack(side='left')
    for var,values in ((e.numerator,tuple(range(1,17))),(e.denominator,(1,2,4,8,16))):
        box=ttk.Combobox(settings,textvariable=var,values=values,width=3,state='readonly');box.pack(side='left',padx=3)
        box.bind('<<ComboboxSelected>>',e.settings_changed)
        if var is e.numerator:ttk.Label(settings,text='/',style='Composer.TLabel').pack(side='left')
    ttk.Label(settings,text='Capo',style='Composer.TLabel').pack(side='left',padx=(12,4))
    capo=ttk.Spinbox(settings,textvariable=e.capo,from_=0,to=24,width=3,command=e.settings_changed);capo.pack(side='left')
    capo.bind('<Return>',e.settings_changed);capo.bind('<FocusOut>',e.settings_changed)
    ttk.Label(settings,text='音色',style='Composer.TLabel').pack(side='left',padx=(12,4))
    tone=ttk.Combobox(settings,textvariable=e.timbre,values=tuple(TONE_NAMES.values()),width=5,state='readonly');tone.pack(side='left')
    tone.bind('<<ComboboxSelected>>',e.settings_changed)
    ttk.Label(settings,text='吸附',style='Composer.TLabel').pack(side='left',padx=(12,4))
    ttk.Combobox(settings,textvariable=e.grid_name,values=tuple(GRIDS),width=7,state='readonly').pack(side='left')
    button(settings,'重做',e.redo).pack(side='right');button(settings,'撤销',e.undo).pack(side='right',padx=4)
    e.tool_tabs=ttk.Notebook(e,style='Composer.TNotebook',height=130);e.tool_tabs.pack(fill='x',padx=12)
    e.note_tools=ttk.Frame(e.tool_tabs,style='Composer.TFrame',padding=(8,7))
    e.measure_tools=ttk.Frame(e.tool_tabs,style='Composer.TFrame',padding=(8,7))
    e.tool_tabs.add(e.note_tools,text='音符与奏法');e.tool_tabs.add(e.measure_tools,text='小节与反复')
    build_notes(e,e.note_tools);build_measures(e,e.measure_tools)
    context=ttk.Frame(e,style='Composer.TFrame',padding=(16,7));context.pack(fill='x')
    e.selection_info=tk.StringVar();e.clipboard_info=tk.StringVar(value='剪贴板为空')
    ttk.Label(context,textvariable=e.selection_info,style='Composer.TLabel',font=('Microsoft YaHei UI',9,'bold')).pack(side='left')
    ttk.Label(context,textvariable=e.clipboard_info,style='Composer.Muted.TLabel').pack(side='left',padx=14)
    ttk.Label(context,text='Shift 连选小节 · Ctrl+C / Ctrl+V',style='Composer.Muted.TLabel').pack(side='right')
    footer=ttk.Frame(e,style='Composer.TFrame',padding=(16,10));footer.pack(side='bottom',fill='x');e.action_footer=footer
    e.apply_button=ttk.Button(footer,text='直接载入原谱',command=lambda:e.apply(True),style='Composer.Load.TButton')
    e.apply_button.pack(side='right')
    e.translate_action_button=ttk.Button(footer,text='近似转译…',command=e.send_to_translator,style='Composer.Translate.TButton')
    e.translate_action_button.pack(side='right',padx=8)
    button(footer,'保存文件…',e.save_file).pack(side='right',padx=(0,6))
    e.preview_button=button(footer,'▶ 本小节试听 Space',e.preview);e.preview_button.pack(side='left')
    e.head_preview_button=button(footer,'从头试听',lambda:e.preview(True));e.head_preview_button.pack(side='left',padx=5)
    button(footer,'■ 停止',e.stop_preview).pack(side='left')
    from .widgets import StatusBanner
    e.feedback_banner=StatusBanner(e,e.feedback);e.feedback_banner.pack(side='bottom',fill='x',padx=12,pady=2)
    surface=ttk.Frame(e,style='Composer.TFrame');surface.pack(fill='both',expand=True,padx=12)
    bar=ttk.Scrollbar(surface,orient='vertical');bar.pack(side='right',fill='y')
    e.canvas=tk.Canvas(surface,bg=PAPER,highlightthickness=1,highlightbackground='#d8e0e8',takefocus=True)
    def scrolled(first,last):
        bar.set(first,last)
        if e.visible_id is None and not e.closed:e.visible_id=e.after_idle(e.draw_visible)
    e.canvas.configure(yscrollcommand=scrolled);e.canvas.pack(fill='both',expand=True);bar.configure(command=e.canvas.yview)
    e.canvas.bind('<Configure>',lambda event:e.draw())


def build_notes(e,parent):
    rhythm=ttk.Frame(parent,style='Composer.TFrame');rhythm.pack(fill='x')
    for denominator in VALUES:
        canvas=tk.Canvas(rhythm,width=53,height=58,bg=PAPER,highlightthickness=1,highlightbackground=LINE,cursor='hand2')
        canvas.pack(side='left',padx=(0,4));canvas.bind('<Button-1>',lambda event,value=denominator:e.choose_value(value))
        e.music_buttons[denominator]=canvas
    modifiers=ttk.Frame(rhythm,style='Composer.TFrame');modifiers.pack(side='left',padx=(8,10))
    ttk.Checkbutton(modifiers,text='附点 ·',variable=e.dot_var,command=e.choose_modifiers).pack(anchor='w')
    ttk.Checkbutton(modifiers,text='三连音 3',variable=e.triplet_var,command=e.choose_modifiers).pack(anchor='w')
    tools=ttk.Frame(rhythm,style='Composer.TFrame');tools.pack(side='left')
    button(tools,'休止 R',e.put_rest).pack(side='left',padx=3);button(tools,'删音 Del',e.delete).pack(side='left')
    ties=ttk.Menubutton(tools,text='延音线 ▾');ties.pack(side='left',padx=5)
    e.tie_menu=tk.Menu(ties,tearoff=False);ties.configure(menu=e.tie_menu)
    for text,command in (('连接下一同音 T',e.tie_next),('延长所选时值',e.extend_note),('断开延音 Shift+T',e.split_tie)):
        e.tie_menu.add_command(label=text,command=command)
    row=ttk.Frame(parent,style='Composer.TFrame');row.pack(fill='x',pady=(8,0))
    ttk.Label(row,text='和弦',style='Composer.TLabel').pack(side='left')
    e.chord=tk.StringVar(value=e.doc.chord_at(e.at) or 'D')
    box=ttk.Combobox(row,textvariable=e.chord,values=CHORD_NAMES,width=9);box.pack(side='left',padx=4)
    box.bind('<Return>',lambda event:e.mark_chord());button(row,'标记此处',e.mark_chord).pack(side='left')
    button(row,'移除',lambda:e.edit(lambda:e.doc.remove_chord(e.at))).pack(side='left',padx=4)
    ttk.Separator(row,orient='vertical').pack(side='left',fill='y',padx=8)
    for text,command in (('X',lambda:e.put_hit(HIT_X)),('●',lambda:e.put_hit(HIT_DOT)),
                         ('↑ 上扫 U',lambda:e.put_sweep(UP)),('↓ 下扫 D',lambda:e.put_sweep(DOWN))):
        button(row,text,command).pack(side='left',padx=2)
    e.sequence=tk.StringVar(value='020')
    button(row,'连续录入…',e.sequence_dialog).pack(side='left',padx=4)
    e.cleanup_button=ttk.Menubutton(row,text='更多 ▾');e.cleanup_button.pack(side='right')
    more=tk.Menu(e.cleanup_button,tearoff=False);e.cleanup_button.configure(menu=more)
    e.cleanup_menu=tk.Menu(more,tearoff=False)
    for value,label in ((30,'30 ms · 细微错位'),(50,'50 ms · 轻微琶音'),(100,'100 ms · 收拢滚奏')):
        e.cleanup_menu.add_command(label=label,command=lambda ms=value:e.cleanup_onsets(ms))
    more.add_cascade(label='近同拍整理',menu=e.cleanup_menu)
    e.beat_menu=tk.Menu(more,tearoff=False)
    for text,command in (('插入空节拍… Insert',e.insert_beat_dialog),('删除整列节拍 Ctrl+Del',e.delete_beat),('清空整列音符 Shift+Del',e.clear_column)):
        e.beat_menu.add_command(label=text,command=command)
    more.add_cascade(label='节拍操作',menu=e.beat_menu)


def build_measures(e,parent):
    e.range_first=tk.StringVar(value='1');e.range_last=tk.StringVar(value='1');e.paste_buttons=[]
    groups=[]
    for column,title in enumerate(('选择范围','复制与粘贴','插入与设置','重复记号')):
        group=ttk.LabelFrame(parent,text=title,style='Composer.TLabelframe',padding=(7,7));group.grid(row=0,column=column,sticky='nsew',padx=(0,7 if column<3 else 0))
        parent.columnconfigure(column,weight=1);groups.append(group)
    a=groups[0];row=ttk.Frame(a,style='Composer.TFrame');row.pack(fill='x')
    ttk.Label(row,text='从',style='Composer.TLabel').pack(side='left')
    e.range_first_box=ttk.Spinbox(row,textvariable=e.range_first,from_=1,to=25000,width=4);e.range_first_box.pack(side='left',padx=3)
    ttk.Label(row,text='至',style='Composer.TLabel').pack(side='left')
    e.range_last_box=ttk.Spinbox(row,textvariable=e.range_last,from_=1,to=25000,width=4);e.range_last_box.pack(side='left',padx=3)
    row=ttk.Frame(a,style='Composer.TFrame');row.pack(fill='x',pady=(7,0))
    button(row,'选定',e.select_measure_range).pack(side='left');button(row,'仅当前',e.clear_measure_selection).pack(side='left',padx=4)
    b=groups[1]
    for i,(text,command) in enumerate((('复制',e.copy_selection),('覆盖粘贴',lambda:e.paste_selection(False)),
                                      ('插入粘贴',lambda:e.paste_selection(True)),('指定复制…',e.copy_range_dialog))):
        tool=button(b,text,command);tool.grid(row=i//2,column=i%2,sticky='ew',padx=2,pady=3)
        if i in (1,2):e.paste_buttons.append(tool)
    c=groups[2]
    for i,(text,command) in enumerate((('前插小节',lambda:e.insert_bar(False)),('后插小节',lambda:e.insert_bar(True)),
                                      ('小节设置…',e.measure_dialog),('删除空小节',e.remove_bar))):
        button(c,text,command).grid(row=i//2,column=i%2,sticky='ew',padx=2,pady=3)
    d=groups[3]
    button(d,'% 前小节',e.repeat_previous).grid(row=0,column=0,padx=2,pady=3)
    button(d,'反复段…',e.repeat_dialog).grid(row=0,column=1,padx=2,pady=3)
    clear=ttk.Menubutton(d,text='展开／清除 ▾');clear.grid(row=1,column=0,columnspan=2,sticky='ew',padx=2,pady=3)
    menu=tk.Menu(clear,tearoff=False);clear.configure(menu=menu)
    menu.add_command(label='展开 % 为可编辑音符',command=e.unfold_percent)
    menu.add_command(label='清除当前段落反复线',command=e.clear_repeat_section)


def update_context(e):
    if not hasattr(e,'selection_info'):return
    first,last=e.selected_measures()
    e.selection_info.set(f'第 {first+1} 小节' if first==last else f'第 {first+1}–{last+1} 小节 · 已选 {last-first+1} 小节')
    focused=e.focus_get()
    if focused not in (e.range_first_box,e.range_last_box):
        e.range_first.set(str(first+1));e.range_last.set(str(last+1))
    e.clipboard_info.set(f'剪贴板：{len(e.measure_clipboard)} 小节' if e.measure_clipboard else '剪贴板为空')
    for tool in e.paste_buttons:tool.configure(state='normal' if e.measure_clipboard else 'disabled')
