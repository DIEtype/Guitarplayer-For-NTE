"""Compact desktop editing palette with separate note and measure tools."""
import tkinter as tk
from tkinter import ttk

from .game_profile import CHORD_NAMES
from .guitar import UP,DOWN
from .measures import TONE_NAMES
from .tablature import VALUES
from .visual_editor import GRIDS,PAPER,LINE
from .fingerstyle_document import HIT_X,HIT_DOT

LOAD='#0e7380';TRANSLATE='#b45309';TEXT='#20394e';MUTED='#64768a';PANEL='#f5f7fa';DESK='#dbe3ec'


def styles(editor):
    style=ttk.Style(editor)
    style.configure('Composer.TFrame',background=PANEL)
    style.configure('Composer.TLabel',background=PANEL,foreground=TEXT)
    style.configure('Composer.Muted.TLabel',background=PANEL,foreground=MUTED,font=('Microsoft YaHei UI',9))
    style.configure('Composer.Title.TLabel',background=PANEL,foreground=TEXT,font=('Microsoft YaHei UI',17,'bold'))
    style.configure('Composer.TButton',padding=(6,3),font=('Microsoft YaHei UI',9))
    style.configure('Composer.TLabelframe',background=PANEL,borderwidth=1,relief='solid')
    style.configure('Composer.TLabelframe.Label',background=PANEL,foreground=MUTED,font=('Microsoft YaHei UI',9,'bold'))
    style.configure('Composer.TNotebook',background=PANEL,borderwidth=0,tabmargins=(0,0,0,0))
    style.configure('Composer.TNotebook.Tab',padding=(8,7),font=('Microsoft YaHei UI',9))
    style.map('Composer.TNotebook.Tab',background=[('selected',PANEL)],foreground=[('selected',LOAD)])
    for name,color,active in (('Load',LOAD,'#095863'),('Translate',TRANSLATE,'#8b4008')):
        style.configure(f'Composer.{name}.TButton',background=color,foreground='white',
                        padding=(15,10),font=('Microsoft YaHei UI',10,'bold'),borderwidth=0)
        style.map(f'Composer.{name}.TButton',background=[('disabled','#d4dde3'),('active',active),('pressed',active)],foreground=[('disabled','#657682')])


def button(parent,text,command,**options):
    return ttk.Button(parent,text=text,command=command,style='Composer.TButton',**options)


def build_workspace(e):
    styles(e);e.configure(bg=PANEL)
    e.workspace_shell=ttk.Frame(e,style='Composer.TFrame');e.workspace_shell.place(relwidth=1,relheight=1)
    heading=ttk.Frame(e.workspace_shell,style='Composer.TFrame',padding=(12,5));heading.pack(fill='x')
    ttk.Label(heading,text='双轨编谱' if hasattr(e,'documents') else '吉他编曲',style='Composer.Title.TLabel').pack(side='left')
    e.credit_label=ttk.Label(heading,text='by Akari',style='Composer.Muted.TLabel')
    e.credit_label.pack(side='left',padx=(14,0))
    if not hasattr(e,'documents'):
        e.duet_button=button(heading,'双轨编谱…',e.open_duet_workspace);e.duet_button.pack(side='left',padx=12)
    button(heading,'新建',e.clear).pack(side='right')
    button(heading,'打开原谱…',e.open_file).pack(side='right',padx=5)
    e.guitar_pro_button=button(heading,'导入 Guitar Pro…',lambda:e.parent.import_guitar_pro(editor=e))
    e.guitar_pro_button.pack(side='right')
    settings=ttk.Frame(e.workspace_shell,style='Composer.TFrame',padding=(12,0,12,5));settings.pack(fill='x')
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
    footer=ttk.Frame(e.workspace_shell,style='Composer.TFrame',padding=(12,5));footer.pack(side='bottom',fill='x');e.action_footer=footer
    e.apply_button=ttk.Button(footer,text='直接载入原谱',command=lambda:e.apply(True),style='Composer.Load.TButton')
    e.apply_button.pack(side='right')
    e.translate_action_button=ttk.Button(footer,text='近似转译…',command=e.send_to_translator,style='Composer.Translate.TButton')
    e.translate_action_button.pack(side='right',padx=8)
    button(footer,'保存文件…',e.save_file).pack(side='right',padx=(0,6))
    e.preview_button=button(footer,'▶ 本小节试听 Space',e.preview);e.preview_button.pack(side='left')
    e.head_preview_button=button(footer,'从头试听',lambda:e.preview(True));e.head_preview_button.pack(side='left',padx=5)
    button(footer,'■ 停止',e.stop_preview).pack(side='left')
    from .widgets import ScrollableStatusBanner
    e.feedback_banner=ScrollableStatusBanner(e.workspace_shell,e.feedback,lines=2);e.feedback_banner.pack(side='bottom',fill='x',padx=8)
    e.workspace_body=ttk.Frame(e.workspace_shell,style='Composer.TFrame');e.workspace_body.pack(fill='both',expand=True,padx=8,pady=3)
    toolbar=ttk.Frame(e.workspace_body,style='Composer.TFrame',width=248);toolbar.pack(side='left',fill='y',padx=(0,6));toolbar.pack_propagate(False)
    e.tool_tabs=ttk.Notebook(toolbar,style='Composer.TNotebook');e.tool_tabs.pack(fill='both',expand=True)
    e.note_tools=ttk.Frame(e.tool_tabs,style='Composer.TFrame',padding=(5,5))
    e.measure_tools=ttk.Frame(e.tool_tabs,style='Composer.TFrame',padding=(5,5))
    e.tool_tabs.add(e.note_tools,text='音符与奏法');e.tool_tabs.add(e.measure_tools,text='小节与反复')
    build_notes(e,e.note_tools);build_measures(e,e.measure_tools)
    surface=ttk.Frame(e.workspace_body,style='Composer.TFrame');surface.pack(side='right',fill='both',expand=True)
    bar=ttk.Scrollbar(surface,orient='vertical');bar.pack(side='right',fill='y')
    e.canvas=tk.Canvas(surface,bg=PAPER,highlightthickness=1,highlightbackground=DESK,takefocus=True)
    def scrolled(first,last):
        bar.set(first,last)
        if e.visible_id is None and not e.closed:e.visible_id=e.after_idle(e.draw_visible)
    e.canvas.configure(yscrollcommand=scrolled);e.canvas.pack(fill='both',expand=True);bar.configure(command=e.canvas.yview)
    def resized(event):
        if event.width != getattr(e,'_last_canvas_width',None):
            e._last_canvas_width=event.width
            e.draw(event.width)
        elif e.visible_id is None:
            e.visible_id=e.after_idle(e.draw_visible)
    e.canvas.bind('<Configure>',resized)


def build_notes(e,parent):
    rhythm=ttk.Frame(parent,style='Composer.TFrame');rhythm.pack(fill='x')
    for i,denominator in enumerate(VALUES):
        canvas=tk.Canvas(rhythm,width=53,height=58,bg=PAPER,highlightthickness=1,highlightbackground=LINE,cursor='hand2')
        canvas.grid(row=i//4,column=i%4,padx=0,pady=(0,2));canvas.bind('<Button-1>',lambda event,value=denominator:e.choose_value(value))
        e.music_buttons[denominator]=canvas
    modifiers=ttk.Frame(parent,style='Composer.TFrame');modifiers.pack(fill='x',pady=(3,0))
    ttk.Checkbutton(modifiers,text='附点',variable=e.dot_var,command=e.choose_modifiers).pack(side='left',padx=(1,9))
    ttk.Checkbutton(modifiers,text='三连音',variable=e.triplet_var,command=e.choose_modifiers).pack(side='left')
    tools=ttk.Frame(parent,style='Composer.TFrame');tools.pack(fill='x',pady=(3,0))
    button(tools,'休止 R',e.put_rest,width=7).pack(side='left');button(tools,'删音 Del',e.delete,width=8).pack(side='left',padx=3)
    ties=ttk.Menubutton(tools,text='延音 ▾',width=7);ties.pack(side='left')
    e.tie_menu=tk.Menu(ties,tearoff=False);ties.configure(menu=e.tie_menu)
    for text,command in (('连接下一同音 T',e.tie_next),('延长所选时值',e.extend_note),('断开延音 Shift+T',e.split_tie)):
        e.tie_menu.add_command(label=text,command=command)
    ttk.Separator(parent).pack(fill='x',pady=5)
    row=ttk.Frame(parent,style='Composer.TFrame');row.pack(fill='x')
    ttk.Label(row,text='和弦',style='Composer.TLabel').pack(side='left')
    e.chord=tk.StringVar(value=e.doc.chord_at(e.at) or 'D')
    box=ttk.Combobox(row,textvariable=e.chord,values=CHORD_NAMES,width=6);box.pack(side='left',padx=3)
    box.bind('<Return>',lambda event:e.mark_chord());button(row,'标记',e.mark_chord,width=5).pack(side='left')
    button(row,'移除',lambda:e.edit(lambda:e.doc.remove_chord(e.at)),width=5).pack(side='left',padx=3)
    row=ttk.Frame(parent,style='Composer.TFrame');row.pack(fill='x',pady=(4,0))
    for text,command in (('X',lambda:e.put_hit(HIT_X)),('●',lambda:e.put_hit(HIT_DOT)),
                         ('上扫 U',lambda:e.put_sweep(UP)),('下扫 D',lambda:e.put_sweep(DOWN))):
        button(row,text,command,width=3 if text in ('X','●') else 7).pack(side='left',padx=(0,3))
    row=ttk.Frame(parent,style='Composer.TFrame');row.pack(fill='x',pady=(4,0))
    e.sequence=tk.StringVar(value='020')
    button(row,'连续录入…',e.sequence_dialog,width=11).pack(side='left',padx=4)
    e.cleanup_button=ttk.Menubutton(row,text='更多 ▾',width=7);e.cleanup_button.pack(side='left')
    more=tk.Menu(e.cleanup_button,tearoff=False);e.cleanup_button.configure(menu=more)
    e.cleanup_menu=tk.Menu(more,tearoff=False)
    for value,label in ((30,'30 ms · 细微错位'),(50,'50 ms · 轻微琶音'),(100,'100 ms · 收拢滚奏')):
        e.cleanup_menu.add_command(label=label,command=lambda ms=value:e.cleanup_onsets(ms))
    more.add_cascade(label='近同拍整理',menu=e.cleanup_menu)
    e.beat_menu=tk.Menu(more,tearoff=False)
    for text,command in (('插入空节拍… Insert',e.insert_beat_dialog),('删除整列节拍 Ctrl+Del',e.delete_beat),('清空整列音符 Shift+Del',e.clear_column)):
        e.beat_menu.add_command(label=text,command=command)
    more.add_cascade(label='节拍操作',menu=e.beat_menu)
    presets=ttk.Frame(parent,style='Composer.TFrame');presets.pack(fill='x',pady=(6,0))
    e.pattern_button=button(presets,'节奏预设…',e.pattern_dialog);e.pattern_button.pack(side='left')
    button(presets,'存为预设…',e.save_rhythm_dialog).pack(side='left',padx=4)


def build_measures(e,parent):
    e.range_first=tk.StringVar(value='1');e.range_last=tk.StringVar(value='1');e.paste_buttons=[]
    e.selection_info=tk.StringVar();e.clipboard_info=tk.StringVar(value='剪贴板为空')
    groups=[]
    for title in ('选择范围','复制与粘贴','插入与设置','重复记号'):
        group=ttk.LabelFrame(parent,text=title,style='Composer.TLabelframe',padding=(4,2));group.pack(fill='x',pady=(0,2))
        groups.append(group)
    a=groups[0];row=ttk.Frame(a,style='Composer.TFrame');row.pack(fill='x')
    ttk.Label(row,text='从',style='Composer.TLabel').pack(side='left')
    e.range_first_box=ttk.Spinbox(row,textvariable=e.range_first,from_=1,to=25000,width=4);e.range_first_box.pack(side='left',padx=3)
    ttk.Label(row,text='至',style='Composer.TLabel').pack(side='left')
    e.range_last_box=ttk.Spinbox(row,textvariable=e.range_last,from_=1,to=25000,width=4);e.range_last_box.pack(side='left',padx=3)
    row=ttk.Frame(a,style='Composer.TFrame');row.pack(fill='x',pady=(3,0))
    button(row,'选定',e.select_measure_range).pack(side='left');button(row,'仅当前',e.clear_measure_selection).pack(side='left',padx=4)
    b=groups[1]
    for i,(text,command) in enumerate((('复制',e.copy_selection),('覆盖粘贴',lambda:e.paste_selection(False)),
                                      ('插入粘贴',lambda:e.paste_selection(True)),('指定复制…',e.copy_range_dialog))):
        tool=button(b,text,command);tool.grid(row=i//2,column=i%2,sticky='ew',padx=1,pady=1)
        if i in (1,2):tool.configure(state='disabled');e.paste_buttons.append(tool)
    c=groups[2]
    for i,(text,command) in enumerate((('前插小节',lambda:e.insert_bar(False)),('后插小节',lambda:e.insert_bar(True)),
                                      ('小节设置…',e.measure_dialog),('删除空小节',e.remove_bar))):
        button(c,text,command).grid(row=i//2,column=i%2,sticky='ew',padx=1,pady=1)
    d=groups[3]
    button(d,'% 前小节',e.repeat_previous).grid(row=0,column=0,padx=1,pady=1)
    button(d,'反复段…',e.repeat_dialog).grid(row=0,column=1,padx=1,pady=1)
    clear=ttk.Menubutton(d,text='展开／清除 ▾');clear.grid(row=1,column=0,columnspan=2,sticky='ew',padx=1,pady=1)
    menu=tk.Menu(clear,tearoff=False);clear.configure(menu=menu)
    menu.add_command(label='展开 % 为可编辑音符',command=e.unfold_percent)
    menu.add_command(label='清除当前段落反复线',command=e.clear_repeat_section)
    status=ttk.Frame(parent,style='Composer.TFrame');status.pack(fill='x')
    ttk.Label(status,textvariable=e.selection_info,style='Composer.Muted.TLabel').pack(side='left')
    ttk.Label(status,textvariable=e.clipboard_info,style='Composer.Muted.TLabel').pack(side='right')


def update_context(e):
    if not hasattr(e,'selection_info'):return
    first,last=e.selected_measures()
    selection=f'第 {first+1} 小节' if first==last else f'第 {first+1}–{last+1} 小节 · 已选 {last-first+1} 小节'
    if e.selection_info.get()!=selection:e.selection_info.set(selection)
    focused=e.focus_get()
    if focused not in (e.range_first_box,e.range_last_box):
        for variable,value in ((e.range_first,str(first+1)),(e.range_last,str(last+1))):
            if variable.get()!=value:variable.set(value)
    clipboard=f'已复制 {len(e.measure_clipboard)} 小节' if e.measure_clipboard else '剪贴板为空'
    if e.clipboard_info.get()!=clipboard:
        e.clipboard_info.set(clipboard)
        for tool in e.paste_buttons:tool.configure(state='normal' if e.measure_clipboard else 'disabled')
