"""Game preparation controls shared with the main window, without another dialog."""
import tkinter as tk
from tkinter import ttk

from .timing import LABELS


class DuetGameControls(ttk.Frame):
    def __init__(self,parent,app,feedback):
        super().__init__(parent,padding=(12,6));self.app=app;self.feedback=feedback;self.controls=[]
        row=ttk.Frame(self);row.pack(fill='x')
        ttk.Label(row,text='游戏窗口').pack(side='left')
        self.window_box=ttk.Combobox(row,textvariable=app.window,values=tuple(app.window_items),state='readonly',width=26)
        self.window_box.pack(side='left',fill='x',expand=True,padx=6);self.controls.append(self.window_box)
        ttk.Button(row,text='刷新',command=self.refresh_windows,padding=(6,3)).pack(side='left')
        ttk.Label(row,text='输入档位').pack(side='left',padx=(12,3))
        self.speed=ttk.Combobox(row,textvariable=app.input_speed,values=tuple(LABELS),state='readonly',width=22)
        self.speed.pack(side='left');self.speed.bind('<<ComboboxSelected>>',app.input_speed_changed);self.controls.append(self.speed)
        row=ttk.Frame(self);row.pack(fill='x',pady=(6,0))
        ttk.Label(row,text='游戏当前').pack(side='left')
        for variable,values in ((app.game_mode,('品格','和弦')),(app.game_timbre,('清音','失真'))):
            box=ttk.Combobox(row,textvariable=variable,values=values,state='readonly',width=5);box.pack(side='left',padx=4)
            if variable is app.game_mode:box.bind('<<ComboboxSelected>>',lambda _e:self.mode_changed())
            self.controls.append(box)
        self.pages={mode:tk.StringVar(value=str(app.profiles['混合模式'].initial_pages[mode]+1)) for mode in ('chord','fret')}
        self.current='chord' if app.game_mode.get()=='和弦' else 'fret';self.pages[self.current].set(app.page.get())
        for mode,label,limit in (('chord','和弦页',4),('fret','品格页',5)):
            ttk.Label(row,text=label).pack(side='left',padx=(12,3))
            box=ttk.Spinbox(row,textvariable=self.pages[mode],from_=1,to=limit,width=3,command=self.apply_pages)
            box.pack(side='left');box.bind('<FocusOut>',lambda _e:self.apply_pages());box.bind('<Return>',lambda _e:self.apply_pages());self.controls.append(box)
        self.apply_button=ttk.Button(row,text='应用页码',command=self.apply_pages,padding=(6,3));self.apply_button.pack(side='left',padx=8)
        self.hint=ttk.Label(self,text='按游戏实际状态填写；品格第 5 页为空弦。两种模式各自保留页码。',foreground='#62798d')
        self.hint.pack(anchor='w',pady=(4,0));self.busy=False

    def mode_changed(self):
        current='chord' if self.app.game_mode.get()=='和弦' else 'fret'
        self.app.page.set(self.pages[current].get());self.current=current

    def refresh_windows(self):
        self.app.refresh_windows();self.window_box.configure(values=tuple(self.app.window_items))

    def refresh(self):
        app=self.app;current='chord' if app.game_mode.get()=='和弦' else 'fret'
        # Playback updates the shared page after each sent event. Never overwrite
        # an in-progress edit of a page field.
        if app.busy or current!=self.current:
            desired={mode:str(value+1) for mode,value in app.profiles['混合模式'].initial_pages.items()}
            desired[current]=app.page.get()
            for mode,value in desired.items():
                if self.pages[mode].get()!=value:self.pages[mode].set(value)
            self.current=current
        if self.busy!=app.busy:
            self.busy=app.busy
            for widget in self.controls:widget.configure(state='disabled' if app.busy else 'readonly' if isinstance(widget,ttk.Combobox) else 'normal')
            self.apply_button.configure(state='disabled' if app.busy else 'normal')

    def apply_pages(self):
        app=self.app
        if app.busy:self.feedback.set('请先停止演奏，再修改游戏起始页。');return False
        try:
            pages={mode:int(value.get())-1 for mode,value in self.pages.items()}
            if not 0<=pages['chord']<4 or not 0<=pages['fret']<5:raise ValueError
        except ValueError:self.feedback.set('页码未应用：和弦填写 1～4，品格填写 1～5；第 5 页为空弦。');return False
        current='chord' if app.game_mode.get()=='和弦' else 'fret'
        app.profiles['混合模式'].initial_pages=pages
        app.profiles['和弦模式'].initial_page=pages['chord'];app.profiles['品格模式'].initial_page=pages['fret']
        app.page.set(str(pages[current]+1));self.current=current;app.dirty=True
        return True
