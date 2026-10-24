"""Less frequent input budgets and per-mode starting pages in one place."""
import tkinter as tk
from tkinter import ttk

from .timing import LABELS


class GameSettings(tk.Toplevel):
    def __init__(self,app):
        super().__init__(app);self.app=app;self.title('游戏设置 · 输入与起始页');self.transient(app)
        width=min(720,self.winfo_screenwidth()-60);height=min(590,self.winfo_screenheight()-90)
        self.geometry(f'{width}x{height}');self.minsize(min(650,width),min(530,height))
        self.configure(background='#edf2f6')
        ttk.Label(self,text='游戏设置',font=('Microsoft YaHei UI',18,'bold'),padding=(18,14)).pack(anchor='w')
        ttk.Label(self,text='日常使用可保持默认设置；按实际游戏状态核对起始页。',foreground='#62798d',padding=(18,0,18,10)).pack(anchor='w')
        footer=ttk.Frame(self,padding=14);footer.pack(side='bottom',fill='x')
        ttk.Button(footer,text='完成',command=self.destroy).pack(side='right')
        ttk.Button(footer,text='这些设置有什么用？',command=lambda:app.open_guide('timing')).pack(side='left')
        self.tabs=ttk.Notebook(self);self.tabs.pack(fill='both',expand=True,padx=16,pady=(0,10))
        self.sections={name:ttk.Frame(self.tabs,padding=16) for name in ('timing','pages')}
        for key,title in (('timing','输入时序与日志'),('pages','分模式起始页')):self.tabs.add(self.sections[key],text=title)
        self.build_timing();self.build_pages()

    def select(self,section):self.tabs.select(self.sections.get(section,self.sections['timing']))

    def build_timing(self):
        app=self.app;viewport=self.sections['timing']
        scrollbar=ttk.Scrollbar(viewport,orient='vertical');scrollbar.pack(side='right',fill='y')
        self.timing_canvas=tk.Canvas(viewport,background='#edf2f6',highlightthickness=0,yscrollcommand=scrollbar.set)
        self.timing_canvas.pack(fill='both',expand=True);scrollbar.configure(command=self.timing_canvas.yview)
        parent=ttk.Frame(self.timing_canvas);item=self.timing_canvas.create_window(0,0,window=parent,anchor='nw')
        parent.bind('<Configure>',lambda e:self.timing_canvas.configure(scrollregion=self.timing_canvas.bbox('all')))
        self.timing_canvas.bind('<Configure>',lambda e:self.timing_canvas.itemconfigure(item,width=e.width))
        self.bind('<MouseWheel>',lambda e:self.timing_canvas.yview_scroll(-int(e.delta/120),'units') if str(self.tabs.select())==str(viewport) else None)
        row=ttk.Frame(parent);row.pack(fill='x',pady=(0,10))
        ttk.Label(row,text='输入档位').pack(side='left')
        app.speed_box=ttk.Combobox(row,textvariable=app.input_speed,values=tuple(LABELS),state='readonly',width=28)
        app.speed_box.pack(side='left',padx=10);app.speed_box.bind('<<ComboboxSelected>>',app.input_speed_changed)
        row=ttk.Frame(parent);row.pack(fill='x',pady=(0,10))
        ttk.Label(row,text='最短按键').pack(side='left')
        tap=ttk.Spinbox(row,textvariable=app.tap,from_=15,to=200,width=5,command=app.analyze)
        tap.pack(side='left',padx=10);tap.bind('<FocusOut>',lambda e:app.analyze())
        ttk.Label(row,text='毫秒；这是最短触发时间，不是谱面音符时值。').pack(side='left')
        app.sustain_box=ttk.Checkbutton(parent,text='拨弦与扫弦按谱面时值保持',variable=app.sustain_notes,command=app.duration_mode_changed)
        app.sustain_box.pack(anchor='w',pady=3)
        app.coordination_box=ttk.Checkbutton(parent,text='自适应输入衔接（适用于所有档位）',variable=app.coordinate_inputs,command=app.learning_changed)
        app.coordination_box.pack(anchor='w',pady=3)
        def note(**options):
            label=ttk.Label(parent,wraplength=610,**options);label.pack(anchor='w',fill='x',pady=5)
            label.bind('<Configure>',lambda e:label.configure(wraplength=max(200,e.width)))
            return label
        note(textvariable=app.duration_hint,foreground='#62798d')
        note(textvariable=app.timing_hint,foreground='#62798d')
        ttk.Separator(parent).pack(fill='x',pady=(0,10))
        logs=ttk.Frame(parent);logs.pack(fill='x')
        ttk.Checkbutton(logs,text='根据本机日志预留输入余量',variable=app.learn_logs,command=app.learning_changed).pack(side='left')
        self.reanalyze_button=ttk.Button(logs,text='重新分析最近日志',command=app.refresh_learning,padding=(8,4))
        self.reanalyze_button.pack(side='right')
        note(textvariable=app.learning_info,foreground='#20394e')
        note(text='只统计本机输入与调度耗时，不判断游戏是否发声。样本不足时使用默认值；\n不会改变 BPM 或音符起拍，也不能自动修复游戏漏音。',foreground='#62798d')

    def build_pages(self):
        app=self.app;parent=self.sections['pages'];profile=app.profiles['混合模式']
        ttk.Label(parent,text='和弦和品格模式各自记住一个页码。',font=('Microsoft YaHei UI',12,'bold')).pack(anchor='w')
        ttk.Label(parent,text='仅在混合演奏时使用：填写 Z 切换前，两种模式分别停在哪一页。\n若两种模式都停在第一页，保持 1／1 即可。程序按这些页码计算翻页路线。',wraplength=610).pack(anchor='w',pady=12)
        self.pages={}
        for mode,label,count in (('chord','和弦模式 · 1～4 页',4),('fret','品格模式 · 1～5 页，最后一页为空弦',5)):
            row=ttk.Frame(parent);row.pack(fill='x',pady=8)
            self.pages[mode]=tk.StringVar(value=str(profile.initial_pages[mode]+1))
            ttk.Spinbox(row,textvariable=self.pages[mode],from_=1,to=count,width=4).pack(side='left',padx=(0,12))
            ttk.Label(row,text=label).pack(side='left')
        self.page_info=tk.StringVar()
        ttk.Label(parent,textvariable=self.page_info,wraplength=610,foreground='#a62d43').pack(anchor='w',pady=10)
        self.apply_pages_button=ttk.Button(parent,text='应用起始页',command=self.apply_pages);self.apply_pages_button.pack(anchor='w')
        ttk.Button(parent,text='鼠标与手柄设置…',command=app.open_strum_settings).pack(anchor='w',pady=(22,0))

    def apply_pages(self):
        try:
            pages={mode:int(value.get())-1 for mode,value in self.pages.items()}
            if not 0<=pages['chord']<4 or not 0<=pages['fret']<5:raise ValueError
        except ValueError:
            self.page_info.set('起始页未应用：和弦请填 1～4，品格请填 1～5，再点击“应用起始页”。');return False
        app=self.app
        if app.busy:self.page_info.set('请先停止当前播放，再应用起始页。');return False
        app.profiles['混合模式'].initial_pages=pages
        if app.mode.get()=='混合模式':
            actual='chord' if app.game_mode.get()=='和弦' else 'fret';app.page.set(str(pages[actual]+1));app.analyze()
        app.dirty=True;self.page_info.set('已应用：和弦第 '+str(pages['chord']+1)+' 页，品格第 '+str(pages['fret']+1)+' 页。')
        return True
