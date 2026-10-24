"""Direct-entry measured TAB editor with conventional rhythm drawing."""
from fractions import Fraction as F
import math
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox,ttk

from . import __version__
from .paths import app_root
from .tablature import TabDocument,VALUES,note_duration,identify_duration,display_duration,rhythm_parts
from .game_profile import GameProfile
from .player import GuitarPlayer
from .measures import TONE_NAMES,crop_from_measure,timbre_at


INK='#213c57';MUTED='#71899a';PAPER='#ffffff';LINE='#adbdc8'
FOCUS='#147f8d';TINT='#dff1f2';BG='#edf2f6'
NAMES={1:'全音符',2:'二分',4:'四分',8:'八分',16:'十六分',32:'三十二分',64:'六十四分'}
GRIDS={'八分':F(1,2),'十六分':F(1,4),'三十二分':F(1,8),'八分三连':F(1,3),'十六分三连':F(1,6)}


def music_symbol(canvas,x,y,denominator,rest=False,color=INK,scale=1):
    """Draw vector music symbols so no specialist music font is required."""
    def line(*points,**kwargs):
        canvas.create_line(*(value*scale+(x if i%2==0 else y) for i,value in enumerate(points)),fill=color,**kwargs)
    def oval(a,b,c,d,fill):
        canvas.create_oval(x+a*scale,y+b*scale,x+c*scale,y+d*scale,outline=color,fill=fill,width=1.5)
    if rest:
        if denominator in (1,2):
            canvas.create_rectangle(x-6*scale,y-3*scale,x+6*scale,y+2*scale,fill=color,outline='')
            line(-8,-3 if denominator==1 else 2,8,-3 if denominator==1 else 2,width=1)
        elif denominator==4:
            line(-3,-15,4,-8,-3,-1,4,6,-2,11,0,15,width=2)
        else:
            count=max(1,int(math.log2(denominator))-2)
            line(5,-12,-2,15,width=1.5)
            for i in range(count):
                yy=-12+i*6
                oval(-5,yy-2,0,yy+3,color);line(-3,yy+2,4,yy,width=1.5)
        return
    oval(-7,-3,6,5,color if denominator>=4 else PAPER)
    if denominator==1:return
    line(6,0,6,-26,width=1.5)
    for i in range(max(0,int(math.log2(denominator))-2)):
        yy=-26+i*6
        line(6,yy,15,yy+6,13,yy+13,smooth=True,width=1.7)


class VisualTabEditor(tk.Toplevel):
    document_type=TabDocument
    chord_mode=False
    heading_text=None
    apply_label='应用并关闭'

    def __init__(self,parent,score,on_apply):
        document=self.document_type.from_score(score)
        super().__init__(parent)
        self.title(f'六线谱编曲 v{__version__} · 直接输入 · by Akari')
        self.geometry(f'{min(1180,self.winfo_screenwidth()-60)}x{min(820,self.winfo_screenheight()-90)}')
        self.minsize(820,min(560,self.winfo_screenheight()-90))
        self.configure(bg=BG);self.transient(parent)
        self.parent=parent;self.on_apply=on_apply;self.doc=document
        self.at=F(0);self.string=1;self.value=4;self.dotted=False;self.triplet=False
        self.typed='';self.typed_at=0;self.typed_location=None
        self.undo_stack=[];self.redo_stack=[];self.applied_state=self.doc.snapshot()
        self.feedback=tk.StringVar(value='点击弦线 → 输入 0～24 品 → 点音符图形选时值 → → 写下一个音')
        self.summary=tk.StringVar();self.bpm=tk.StringVar(value=f'{self.doc.bpm:g}')
        self.numerator=tk.StringVar(value=str(self.doc.meter[0]));self.denominator=tk.StringVar(value=str(self.doc.meter[1]))
        self.timbre=tk.StringVar(value=TONE_NAMES[self.doc.default_timbre])
        self.capo=tk.StringVar(value=str(self.doc.capo))
        self.grid_name=tk.StringVar(value='十六分');self.dot_var=tk.BooleanVar();self.triplet_var=tk.BooleanVar()
        self.messages=queue.Queue();self.player=GuitarPlayer(lambda kind,data:self.messages.put((kind,data)))
        self.audio_cancel=threading.Event();self.audio_lock=threading.Lock();self.preparing=False
        self.preview_score=None;self.preview_offset=F(0);self.play_at=None;self.closed=False
        self.bar_boxes=[];self.music_buttons={};self.visible_id=None;self.visible_bars=set()
        self.bar_menu=tk.Menu(self,tearoff=False)
        self._build();self.protocol('WM_DELETE_WINDOW',self.close)
        self.bind('<Control-z>',lambda event:self.undo());self.bind('<Control-y>',lambda event:self.redo())
        self.bind('<Control-s>',lambda event:self.save_file())
        self.bind('<Escape>',lambda event:self.stop_preview())
        self.canvas.bind('<Button-1>',self.click)
        self.canvas.bind('<Button-3>',self.context_menu)
        self.canvas.bind('<Key>',self.key)
        self.canvas.bind('<Left>',lambda event:self.move_horizontal(-1))
        self.canvas.bind('<Right>',lambda event:self.move_horizontal(1))
        self.canvas.bind('<Up>',lambda event:self.select_cursor(self.at,max(1,self.string-1)))
        self.canvas.bind('<Down>',lambda event:self.select_cursor(self.at,min(6,self.string+1)))
        self.canvas.bind('<Delete>',lambda event:self.delete())
        self.canvas.bind('<BackSpace>',lambda event:self.backspace())
        self.canvas.bind('<Control-Delete>',lambda event:self.delete_beat())
        self.canvas.bind('<Shift-Delete>',lambda event:self.clear_column())
        self.canvas.bind('<Insert>',lambda event:self.insert_beat_dialog())
        self.canvas.bind('<space>',lambda event:self.preview())
        self.canvas.bind('<F8>',lambda event:self.player.toggle_pause())
        self.canvas.bind('<F9>',lambda event:self.stop_preview())
        self.canvas.bind('<MouseWheel>',lambda event:self.canvas.yview_scroll(-int(event.delta/120),'units'))
        self.poll_id=self.after(40,self._poll);self.refresh_id=self.after_idle(self.refresh)
        self.focus_id=self.after_idle(self.initial_focus)
        parent.tab_editor=self

    def initial_focus(self):
        if self.winfo_viewable() and not self.attributes('-disabled'):
            self.grab_set();self.canvas.focus_set()

    @property
    def changed(self):return self.doc.snapshot()!=self.applied_state

    @property
    def beats(self):return note_duration(self.value,self.dotted,self.triplet)

    def _build(self):
        heading=ttk.Frame(self,padding=(16,10,16,6));heading.pack(fill='x')
        ttk.Label(heading,text=self.heading_text or ('和弦伴奏' if self.chord_mode else '六线谱'),font=('Microsoft YaHei UI',18,'bold')).pack(side='left')
        ttk.Label(heading,textvariable=self.summary,foreground=MUTED).pack(side='left',padx=18)
        ttk.Button(heading,text='新建空谱',command=self.clear).pack(side='right')
        tone_box=ttk.Combobox(heading,textvariable=self.timbre,values=tuple(TONE_NAMES.values()),state='readonly',width=6)
        tone_box.pack(side='right',padx=6);tone_box.bind('<<ComboboxSelected>>',self.settings_changed)
        ttk.Label(heading,text='默认音色').pack(side='right')
        if not self.chord_mode or getattr(self,'allow_capo',False):
            capo_box=ttk.Spinbox(heading,textvariable=self.capo,from_=0,to=24,width=3,command=self.settings_changed)
            capo_box.pack(side='right',padx=6);capo_box.bind('<Return>',self.settings_changed);capo_box.bind('<FocusOut>',self.settings_changed)
            ttk.Label(heading,text='Capo').pack(side='right')
        settings=ttk.Frame(self,padding=(16,0,16,6));settings.pack(fill='x')
        ttk.Label(settings,text='默认 BPM').pack(side='left')
        bpm_box=ttk.Spinbox(settings,textvariable=self.bpm,from_=20,to=360,width=6,command=self.settings_changed)
        bpm_box.pack(side='left',padx=(6,16))
        bpm_box.bind('<Return>',self.settings_changed);bpm_box.bind('<FocusOut>',self.settings_changed)
        ttk.Label(settings,text='默认拍号').pack(side='left')
        for variable,values,width in ((self.numerator,tuple(range(1,17)),3),(self.denominator,(1,2,4,8,16),3)):
            box=ttk.Combobox(settings,textvariable=variable,values=values,width=width,state='readonly')
            box.pack(side='left',padx=4);box.bind('<<ComboboxSelected>>',self.settings_changed)
            if variable is self.numerator:ttk.Label(settings,text='/').pack(side='left')
        ttk.Label(settings,text='鼠标吸附').pack(side='left',padx=(18,4))
        ttk.Combobox(settings,textvariable=self.grid_name,values=tuple(GRIDS),width=10,state='readonly').pack(side='left')
        ttk.Button(settings,text='重做',command=self.redo).pack(side='right')
        ttk.Button(settings,text='撤销',command=self.undo).pack(side='right',padx=4)
        rhythm=ttk.Frame(self,padding=(16,0,16,6));rhythm.pack(fill='x')
        ttk.Label(rhythm,text='时值',foreground=MUTED).pack(side='left',padx=(0,8))
        for denominator in VALUES:
            button=tk.Canvas(rhythm,width=53,height=58,bg=PAPER,highlightthickness=1,highlightbackground=LINE,cursor='hand2')
            button.pack(side='left',padx=(0,4))
            button.bind('<Button-1>',lambda event,value=denominator:self.choose_value(value))
            self.music_buttons[denominator]=button
        ttk.Checkbutton(rhythm,text='附点 ·',variable=self.dot_var,command=self.choose_modifiers).pack(side='left',padx=(8,6))
        ttk.Checkbutton(rhythm,text='三连音 3',variable=self.triplet_var,command=self.choose_modifiers).pack(side='left')
        ties=ttk.Menubutton(rhythm,text='延音线 T ▾');ties.pack(side='left',padx=8)
        self.tie_menu=tk.Menu(ties,tearoff=False);ties.configure(menu=self.tie_menu)
        self.tie_menu.add_command(label='连接下一同音 T',command=self.tie_next)
        self.tie_menu.add_command(label='再延长一个所选时值',command=self.extend_note)
        self.tie_menu.add_command(label='断开跨小节延音，下小节重拨 Shift+T',command=self.split_tie)
        actions=ttk.Frame(self,padding=(16,0,16,8));actions.pack(fill='x')
        insert=ttk.Menubutton(actions,text='插入小节 ▾');insert.pack(side='left',padx=(0,5))
        menu=tk.Menu(insert,tearoff=False);insert.configure(menu=menu)
        menu.add_command(label='在本小节前插入',command=lambda:self.insert_bar(False))
        menu.add_command(label='在本小节后插入',command=lambda:self.insert_bar(True))
        menu.add_command(label='在末尾追加',command=self.add_bar)
        for label,callback in (('小节设置',self.measure_dialog),('复制小节',self.copy_bar),('删除空小节',self.remove_bar),('休止 R',self.put_rest),('删音 Del',self.delete)):
            ttk.Button(actions,text=label,command=callback).pack(side='left',padx=(0,5))
        beats=ttk.Menubutton(actions,text='节拍操作 ▾');beats.pack(side='left')
        self.beat_menu=tk.Menu(beats,tearoff=False);beats.configure(menu=self.beat_menu)
        self.beat_menu.add_command(label='选择时值，插入空节拍… Insert',command=self.insert_beat_dialog)
        self.beat_menu.add_command(label='删除整列节拍（本小节前移） Ctrl+Del',command=self.delete_beat)
        self.beat_menu.add_command(label='清除整列音符，保留时值 Shift+Del',command=self.clear_column)
        if self.chord_mode:self.build_chord_controls()
        else:
            row=ttk.Frame(self,padding=(16,0,16,5));row.pack(fill='x')
            self.same_fret=tk.BooleanVar(value=False)
            ttk.Checkbutton(row,text='同品多弦：在同一音位选另一根弦，再键入品位加入齐奏',variable=self.same_fret).pack(side='left')
        footer=ttk.Frame(self,padding=(16,8));footer.pack(side='bottom',fill='x')
        self.preview_button=ttk.Button(footer,text='▶ 本小节试听 Space',command=self.preview)
        self.preview_button.pack(side='left')
        self.head_preview_button=ttk.Button(footer,text='从头试听',command=lambda:self.preview(True))
        self.head_preview_button.pack(side='left',padx=5)
        ttk.Button(footer,text='■ 停止  Esc',command=self.stop_preview).pack(side='left',padx=5)
        ttk.Button(footer,text=self.apply_label,command=lambda:self.apply(True),style='Accent.TButton').pack(side='right')
        ttk.Button(footer,text='保存文件…',command=self.save_file).pack(side='right',padx=5)
        from .widgets import StatusBanner
        self.feedback_banner=StatusBanner(self,self.feedback)
        self.feedback_banner.pack(side='bottom',fill='x',padx=16,pady=3)
        surface=ttk.Frame(self);surface.pack(fill='both',expand=True,padx=12)
        bar=ttk.Scrollbar(surface,orient='vertical');bar.pack(side='right',fill='y')
        self.canvas=tk.Canvas(surface,bg=PAPER,highlightthickness=0,takefocus=True)
        def scrolled(first,last):
            bar.set(first,last)
            if self.visible_id is None and not self.closed:
                self.visible_id=self.after_idle(self.draw_visible)
        self.canvas.configure(yscrollcommand=scrolled)
        self.canvas.pack(fill='both',expand=True);bar.configure(command=self.canvas.yview)
        self.canvas.bind('<Configure>',lambda event:self.draw())

    def snapshot(self):return self.doc.snapshot(),self.at,self.string,self.value,self.dotted,self.triplet

    def restore(self,state):
        doc,self.at,self.string,self.value,self.dotted,self.triplet=state
        self.doc.restore(doc)
        self.bpm.set(f'{self.doc.bpm:g}');self.numerator.set(str(self.doc.meter[0]));self.denominator.set(str(self.doc.meter[1]))
        self.timbre.set(TONE_NAMES[self.doc.default_timbre])
        self.capo.set(str(self.doc.capo))
        self.typed='';self.refresh()

    def edit(self,operation):
        before=self.snapshot();self.stop_preview()
        try:
            operation()
            if self.doc.snapshot()!=before[0]:
                self.undo_stack.append(before);self.undo_stack=self.undo_stack[-150:];self.redo_stack.clear()
            self.refresh();return True
        except (ValueError,ZeroDivisionError) as error:
            self.doc.restore(before[0]);self.feedback.set(str(error));return False

    def undo(self):
        if self.undo_stack:
            self.stop_preview();self.redo_stack.append(self.snapshot());self.restore(self.undo_stack.pop())

    def redo(self):
        if self.redo_stack:
            self.stop_preview();self.undo_stack.append(self.snapshot());self.restore(self.redo_stack.pop())

    def settings_changed(self,event=None):
        try:
            bpm=float(self.bpm.get());num=int(self.numerator.get());den=int(self.denominator.get());capo=int(self.capo.get())
            if not 20<=bpm<=360:raise ValueError('速度应为 20～360 BPM')
            def change():
                self.doc.set_capo(capo)
                self.doc.set_meter(num,den);self.doc.bpm=bpm
                self.doc.default_timbre=next(key for key,label in TONE_NAMES.items() if label==self.timbre.get())
            return self.edit(change)
        except ValueError as error:self.feedback.set(str(error));return False

    def choose_value(self,value):
        self.typed='';self.typed_location=None
        self.value=value
        self.edit(lambda:self.doc.resize(self.at,self.beats))
        self.canvas.focus_set()

    def choose_modifiers(self):
        self.typed='';self.typed_location=None
        self.dotted=self.dot_var.get();self.triplet=self.triplet_var.get()
        self.edit(lambda:self.doc.resize(self.at,self.beats))
        self.canvas.focus_set()

    def select_cursor(self,at,string=None):
        at=max(F(0),F(at))
        if at>=self.doc.layout[-1].end:
            self.edit(lambda:setattr(self.doc,'bars',self.doc.bars+int((at-self.doc.layout[-1].end)//self.doc.layout[-1].beats)+1))
        self.at=at
        if string is not None:self.string=string
        cell=self.doc.at(self.at)
        if cell:
            value=identify_duration(cell.beats)
            if value:self.value,self.dotted,self.triplet=value
        self.typed='';self.typed_location=None;self.refresh();self.reveal_cursor()

    def move_horizontal(self,direction):
        cell=self.doc.at(self.at)
        if direction>0:at=cell.end if cell else self.at+self.beats
        else:
            previous=[c.start for c in self.doc.cells if c.start<self.at]
            at=max(previous) if previous else max(F(0),self.at-self.beats)
        self.select_cursor(at)

    def click(self,event):
        x=event.x;y=self.canvas.canvasy(event.y)
        for index,(left,top,width) in enumerate(self.bar_boxes):
            if not (left<=x<=left+width and top<=y<=top+252):continue
            first=left+58;span=width-78
            measure=self.doc.bar(index)
            local=max(0,min(1,(x-first)/span))*float(measure.beats)
            grid=min(GRIDS[self.grid_name.get()],measure.beats)
            beat=min(measure.beats-grid,max(F(0),round(local/float(grid))*grid))
            at=measure.start+beat
            candidates=[c for c in self.doc.cells if c.start<measure.end and c.end>measure.start]
            near=min(candidates,key=lambda c:abs(first+float(max(c.start,measure.start)-measure.start)/float(measure.beats)*span-x),default=None)
            if near and abs(first+float(max(near.start,measure.start)-measure.start)/float(measure.beats)*span-x)<13:at=near.start
            string=max(1,min(6,round((y-top-55)/22)+1))
            if near and y>top+177:string=near.string
            self.select_cursor(at,string);self.canvas.focus_set();return

    def key(self,event):
        char=event.char
        if char and char in '0123456789':
            now=time.monotonic();location=(self.at,self.string)
            typed=self.typed+char if now-self.typed_at<.9 and self.typed_location==location else char
            if len(typed)>2 or int(typed)>24-self.doc.capo:typed=char
            if int(typed)>24-self.doc.capo:
                self.feedback.set(f'Capo {self.doc.capo} 下相对品位范围为 0～{24-self.doc.capo}；按方向键开始下一个音');return 'break'
            cell=self.doc.at(self.at);beats=cell.beats if cell else self.beats
            writer=self.doc.write_group if getattr(self,'same_fret',None) is not None and self.same_fret.get() else self.doc.write
            if self.edit(lambda:writer(self.at,self.string,int(typed),beats)):
                self.typed=typed;self.typed_at=now;self.typed_location=location
            return 'break'
        if char.lower()=='r':self.put_rest();return 'break'
        if char.lower()=='t':
            self.split_tie() if char=='T' else self.tie_next();return 'break'
        if char.lower()=='v':
            index=self.doc.bar_index(self.at);change=self.doc.bar_changes.get(index)
            tone='distorted' if timbre_at(self.doc.score(),self.at)=='clean' else 'clean'
            self.set_measure_change(change.bpm if change else None,change.meter if change else None,tone)
            return 'break'
        if char=='+':self.choose_value(VALUES[max(0,VALUES.index(self.value)-1)]);return 'break'
        if char=='-':self.choose_value(VALUES[min(len(VALUES)-1,VALUES.index(self.value)+1)]);return 'break'

    def put_rest(self):
        self.edit(lambda:self.doc.write(self.at,self.string,None,self.beats));self.canvas.focus_set()

    def tie_next(self):
        self.edit(lambda:self.doc.tie_next(self.at));self.canvas.focus_set()

    def extend_note(self):
        current=self.doc.at(self.at)
        if current:self.edit(lambda:self.doc.resize(self.at,current.beats+self.beats))
        else:self.feedback.set('请先选中需要延音的音符')
        self.canvas.focus_set()

    def split_tie(self):
        self.edit(lambda:self.doc.split_tie(self.at));self.canvas.focus_set()

    def delete(self):
        return self.clear_note()

    def delete_beat(self):
        if self.edit(lambda:self.doc.delete_beat(self.at,self.beats)):
            self.feedback.set('已删除当前节拍，本小节后续音符前移；末尾补空，下一小节保持原位')
        self.typed='';self.typed_location=None;self.canvas.focus_set();return 'break'

    def insert_beat(self,beats=None):
        duration=self.beats if beats is None else F(beats)
        success=self.edit(lambda:self.doc.insert_beat(self.at,duration))
        if success:
            self.feedback.set('已插入空节拍，本小节后续音符后移；下一小节保持原位')
        self.typed='';self.typed_location=None;self.canvas.focus_set();return success

    def insert_beat_dialog(self):
        dialog=tk.Toplevel(self);dialog.title('插入空节拍 · 选择时值');dialog.transient(self);dialog.resizable(False,False)
        dialog.denominator=tk.IntVar(value=self.value)
        dot=tk.BooleanVar(value=self.dotted);triplet=tk.BooleanVar(value=self.triplet)
        ttk.Label(dialog,text='在当前位置前插入休止占位；本小节后续音符后移，下一小节保持原位。',padding=14).pack()
        row=ttk.Frame(dialog,padding=(14,0));row.pack();buttons={}
        def select(value):
            dialog.denominator.set(value)
            for number,canvas in buttons.items():canvas.configure(bg=TINT if number==value else PAPER,highlightbackground=FOCUS if number==value else LINE)
        for value in VALUES:
            canvas=tk.Canvas(row,width=53,height=58,bg=PAPER,highlightthickness=1,highlightbackground=LINE,cursor='hand2')
            canvas.pack(side='left',padx=4);music_symbol(canvas,24,32,value)
            canvas.create_text(27,49,text=NAMES[value],fill=INK,font=('Microsoft YaHei UI',8))
            canvas.bind('<Button-1>',lambda event,n=value:select(n));buttons[value]=canvas
        select(self.value)
        options=ttk.Frame(dialog,padding=14);options.pack(fill='x')
        ttk.Checkbutton(options,text='附点 ·',variable=dot).pack(side='left',padx=8)
        ttk.Checkbutton(options,text='三连音 3',variable=triplet).pack(side='left',padx=8)
        message=tk.StringVar();ttk.Label(dialog,textvariable=message,foreground='#a62d43',wraplength=510,padding=(14,0)).pack()
        def close():
            dialog.destroy()
            if self.winfo_viewable():self.grab_set();self.canvas.focus_set()
        def apply():
            if self.insert_beat(note_duration(dialog.denominator.get(),dot.get(),triplet.get())):close()
            else:message.set(self.feedback.get())
        bottom=ttk.Frame(dialog,padding=14);bottom.pack(fill='x')
        ttk.Button(bottom,text='插入空节拍',command=apply,style='Accent.TButton').pack(side='right')
        ttk.Button(bottom,text='取消',command=close).pack(side='right',padx=8)
        dialog.protocol('WM_DELETE_WINDOW',close)
        if self.winfo_viewable():dialog.grab_set()
        return dialog

    def clear_note(self):
        if self.edit(lambda:self.doc.delete_member(self.at,self.string)):
            self.feedback.set('已删除当前弦的音符；同拍其他弦、时值与后续音符保持原位')
        self.typed='';self.typed_location=None;self.canvas.focus_set();return 'break'

    def clear_column(self):
        cell=self.doc.at(self.at)
        if cell:self.edit(lambda:self.doc.write(self.at,self.string,None,cell.beats))
        self.typed='';self.typed_location=None;self.canvas.focus_set();return 'break'

    def backspace(self):
        cell=self.doc.at(self.at);location=(self.at,self.string)
        if cell and self.typed_location==location and len(self.typed)>1:
            typed=self.typed[:-1]
            if self.edit(lambda:self.doc.write(self.at,self.string,int(typed),cell.beats)):
                self.typed=typed;self.typed_location=location;self.typed_at=time.monotonic()
            self.canvas.focus_set();return 'break'
        return self.clear_note()

    def clear(self):
        def change():
            self.doc.cells=[];self.doc.bars=4;self.doc.tempo_map=();self.doc.bar_changes={};self.doc.default_timbre='clean';self.doc.capo=0
        self.timbre.set('清音');self.capo.set('0')
        self.edit(change);self.select_cursor(0,1)

    def add_bar(self):
        index=self.doc.bars
        self.edit(lambda:self.doc.insert_bar(index));self.select_cursor(self.doc.bar(index).start)

    def insert_bar(self,after=False):
        index=self.doc.bar_index(self.at)+int(after)
        if self.edit(lambda:self.doc.insert_bar(index)):self.select_cursor(self.doc.bar(index).start)

    def set_measure_change(self,bpm=None,meter=None,timbre=None):
        index=self.doc.bar_index(self.at);offset=self.at-self.doc.bar(index).start
        def change():
            self.doc.set_bar_change(index,bpm,meter,timbre)
            measure=self.doc.bar(index);self.at=measure.start+min(offset,max(F(0),measure.beats-F(1,16)))
        return self.edit(change)

    def measure_dialog(self):
        index=self.doc.bar_index(self.at);change=self.doc.bar_changes.get(index)
        dialog=tk.Toplevel(self);dialog.title(f'第 {index+1} 小节 · 变速、变拍与音色');dialog.geometry('520x390');dialog.minsize(520,390);dialog.transient(self)
        bpm=tk.StringVar(value=f'{change.bpm:g}' if change and change.bpm is not None else '')
        meter=tk.StringVar(value=f'{change.meter[0]}/{change.meter[1]}' if change and change.meter else '继承')
        tone=tk.StringVar(value=TONE_NAMES[change.timbre] if change and change.timbre else '继承')
        ttk.Label(dialog,text=f'第 {index+1} 小节起生效',font=('Microsoft YaHei UI',15,'bold'),padding=16).pack(anchor='w')
        for label,var,values in (('速度 BPM（留空继承）',bpm,None),('拍号',meter,('继承','2/4','3/4','4/4','6/8','7/8','9/8','12/8')),
                                  ('音色',tone,('继承','清音','失真'))):
            row=ttk.Frame(dialog,padding=(16,5));row.pack(fill='x');ttk.Label(row,text=label,width=23).pack(side='left')
            if values:ttk.Combobox(row,textvariable=var,values=values,width=14,state='readonly' if label=='音色' else 'normal').pack(side='left')
            else:ttk.Entry(row,textvariable=var,width=16).pack(side='left')
        info=tk.StringVar(value='未设置时沿用前面的标记；没有标记则使用全曲默认值。设置持续到下一处变更。')
        label=ttk.Label(dialog,textvariable=info,wraplength=480,padding=16);label.pack(anchor='w')
        def finish():
            dialog.destroy()
            if self.winfo_viewable():self.grab_set()
        def apply():
            try:
                from .measures import valid_meter
                if bpm.get().strip():
                    try:tempo=float(bpm.get())
                    except ValueError:raise ValueError('“速度 BPM”请填写 20～360 的数字，例如 120；留空可继承前面的速度。') from None
                    if not 20<=tempo<=360:raise ValueError('“速度 BPM”请填写 20～360；留空可继承前面的速度。')
                else:tempo=None
                try:signature=None if meter.get().strip() in ('','继承') else tuple(map(int,meter.get().split('/')))
                except ValueError:raise ValueError('“拍号”请按 3/4、6/8 等格式填写；分子 1～16，分母 1、2、4、8 或 16；也可选“继承”。') from None
                if signature is not None and not valid_meter(signature):raise ValueError('“拍号”分子须为 1～16，分母须为 1、2、4、8 或 16，例如 4/4；也可选“继承”。')
                timbre=None if tone.get()=='继承' else next(key for key,label in TONE_NAMES.items() if label==tone.get())
                if self.set_measure_change(tempo,signature,timbre):finish()
                else:info.set('未应用标记：'+self.feedback.get());label.configure(foreground='#a62d43')
            except (ValueError,StopIteration) as error:info.set('未应用标记：'+(str(error) or '请在“音色”框选择“继承”“清音”或“失真”。'));label.configure(foreground='#a62d43')
        ttk.Button(dialog,text='应用标记',command=apply,style='Accent.TButton').pack(side='bottom',anchor='e',before=label,padx=16,pady=6)
        dialog.protocol('WM_DELETE_WINDOW',finish)
        if self.winfo_viewable():dialog.grab_set()
        dialog.feedback=info;dialog.bpm=bpm;dialog.meter=meter;dialog.apply_change=apply
        return dialog

    def context_menu(self,event):
        self.click(event);menu=self.bar_menu;menu.delete(0,'end')
        for label,command in (('从本小节试听',self.preview),('小节设置',self.measure_dialog),
                              ('连接下一同音（延音线 T）',self.tie_next),('断开跨小节延音（下小节重拨）',self.split_tie),
                              ('删除当前弦音符（保留时值） Del',self.delete),('清除整列音符（保留时值） Shift+Del',self.clear_column),
                              ('选择时值，插入空节拍… Insert',self.insert_beat_dialog),('删除整列节拍（本小节前移） Ctrl+Del',self.delete_beat),
                              ('在本小节前插入',lambda:self.insert_bar(False)),('在本小节后插入',lambda:self.insert_bar(True))):
            menu.add_command(label=label,command=command)
        for label,command in self.extra_bar_commands():menu.add_command(label=label,command=command)
        try:menu.tk_popup(event.x_root,event.y_root)
        finally:menu.grab_release()

    def extra_bar_commands(self):return ()

    def copy_bar(self):
        index=self.doc.bar_index(self.at)
        if self.edit(lambda:self.doc.copy_bar(index)):self.select_cursor(self.doc.bar(index+1).start)

    def remove_bar(self):
        index=self.doc.bar_index(self.at)
        if self.edit(lambda:self.doc.remove_empty_bar(index)):
            self.select_cursor(self.doc.bar(min(index,self.doc.bars-1)).start)

    def refresh(self):
        self.dot_var.set(self.dotted);self.triplet_var.set(self.triplet)
        for value,canvas in self.music_buttons.items():
            canvas.delete('all');canvas.configure(bg=TINT if value==self.value else PAPER,
                                                  highlightbackground=FOCUS if value==self.value else LINE)
            music_symbol(canvas,24,32,value)
            canvas.create_text(27,49,text=NAMES[value],fill=INK,font=('Microsoft YaHei UI',8))
        self.summary.set(f'{self.doc.bars} 小节   ·   默认 {self.doc.bpm:g} BPM')
        cell=self.doc.at(self.at)
        note='休止符' if cell and cell.fret is None else f'{cell.fret} 品' if cell else '空位，输入数字即可写入'
        if cell and identify_duration(cell.beats) is None:note+=f'（特殊时值 {cell.beats} 拍，点音符图形可重设）'
        self.feedback.set(f'第 {self.doc.bar_index(self.at)+1} 小节  ·  {self.string} 弦  ·  {note}    |    右键设置小节，V 标记切换音色；← → 移音位')
        self.draw()

    def reveal_cursor(self):
        index=self.doc.bar_index(self.at)
        if index>=len(self.bar_boxes):return
        top=self.bar_boxes[index][1];visible=self.canvas.canvasy(0);height=self.canvas.winfo_height()
        if height<100:return
        region=self.canvas.cget('scrollregion').split()
        full=float(region[3]) if region else 1
        y=top+55+(self.string-1)*22
        if y-30<visible:self.canvas.yview_moveto(max(0,(y-42)/full))
        elif y+40>visible+height:self.canvas.yview_moveto(max(0,(y+52-height)/full))

    def draw(self,width=None):
        if not hasattr(self,'canvas') or self.closed:return
        c=self.canvas;c.delete('all')
        self.visible_bars=set()
        width=max(780,width or c.winfo_width());columns=2 if width>=960 else 1
        measure_width=(width-40-(columns-1)*24)/columns
        self.bar_boxes=[(20+(i%columns)*(measure_width+24),20+(i//columns)*274,measure_width) for i in range(self.doc.bars)]
        height=40+math.ceil(self.doc.bars/columns)*274;c.configure(scrollregion=(0,0,width,height))
        self.draw_score=self.doc.score();self.draw_tempos=dict(self.draw_score.tempo_map)
        self.draw_columns=columns
        self.draw_visible()
        self.draw_playhead()

    def draw_visible(self):
        """Keep only the viewport and one neighboring row on the Tk canvas."""
        self.visible_id=None
        if self.closed or not self.bar_boxes:return
        if self.doc.bars<=32:
            wanted=set(range(self.doc.bars))
        else:
            top=max(0,self.canvas.canvasy(0));height=max(300,self.canvas.winfo_height())
            first=max(0,int((top-20)//274)-1)*self.draw_columns
            last=min(self.doc.bars,(int((top+height-20)//274)+2)*self.draw_columns)
            wanted=set(range(first,last))
        for index in self.visible_bars-wanted:self.canvas.delete(f'bar_{index}')
        for index in sorted(wanted-self.visible_bars):
            before=set(self.canvas.find_all())
            self.draw_bar(index,*self.bar_boxes[index])
            for item in set(self.canvas.find_all())-before:self.canvas.addtag_withtag(f'bar_{index}',item)
        self.visible_bars=wanted
        self.canvas.tag_raise('playhead')

    def draw_bar(self,index,left,top,width):
        measure=self.doc.bar(index);c=self.canvas;first=left+58;span=width-78;start=measure.start;end=measure.end
        def px(at):return first+float(at-start)/float(measure.beats)*span
        c.create_text(left+43,top+13,text=str(index+1),anchor='w',fill=MUTED,font=('Consolas',10,'bold'))
        change=self.doc.bar_changes.get(index);marks=[];mark_x=left+70
        if index==0 and self.doc.capo:
            c.create_text(mark_x,top+13,text=f'Capo {self.doc.capo}',anchor='w',fill=FOCUS,font=('Microsoft YaHei UI',10,'bold'),tags='measure_mark')
            mark_x+=72
        if index==0 or start in self.draw_tempos:
            music_symbol(c,mark_x+4,top+16,4,color=FOCUS,scale=.4);mark_x+=18
            marks.append(f'= {60_000_000/self.draw_tempos[start]:g}' if start in self.draw_tempos else f'= {self.doc.bpm:g}')
        if index==0 or change and change.meter is not None:marks.append(f'{measure.meter[0]}/{measure.meter[1]}')
        if index==0 or change and change.timbre is not None:marks.append(TONE_NAMES[timbre_at(self.draw_score,start)])
        if marks:c.create_text(mark_x,top+13,text='   '.join(marks),anchor='w',fill=FOCUS,font=('Microsoft YaHei UI',10,'bold'),tags='measure_mark')
        if self.doc.bar_index(self.at)==index:
            x=px(self.at)
            c.create_rectangle(x-11,top+39,x+11,top+177,fill='#f0f8f9',outline='')
        for beat in range(1,math.ceil(measure.beats)):
            x=px(start+beat);c.create_line(x,top+42,x,top+174,fill='#edf2f5',dash=(2,5))
        for string,label in enumerate(('e','B','G','D','A','E'),1):
            y=top+55+(string-1)*22
            c.create_text(left+20,y,text=label,fill=MUTED,font=('Consolas',11))
            c.create_line(left+43,y,left+width-8,y,fill=LINE)
        c.create_line(left+43,top+55,left+43,top+165,fill=INK,width=1.3)
        c.create_line(left+width-8,top+55,left+width-8,top+165,fill=INK,width=1.3)
        fragments=[]
        for cell in self.doc.cells:
            if cell.start>=end or cell.end<=start:continue
            at=max(start,cell.start);length=min(end,cell.end)-at;x=px(at)
            continued=cell.start<start
            value=identify_duration(length);den,dot,trip=display_duration(length)
            if cell.fret is None:
                music_symbol(c,x,top+107,den,True,color=INK)
            else:
                for string in cell.notes:
                    if string not in range(1,7):continue
                    y=top+55+(string-1)*22
                    symbol=self.note_text(cell,string)
                    text=f'({symbol})' if continued else symbol
                    half=8 if len(text)==1 else 13 if len(text)==2 else 21
                    c.create_rectangle(x-half,y-11,x+half,y+11,fill=PAPER,outline='')
                    if symbol=='●' and not continued:c.create_oval(x-4,y-4,x+4,y+4,fill=INK,outline='')
                    else:c.create_text(x,y,text=text,fill=INK,font=('Consolas',15,'bold'))
                    if continued:c.create_arc(x-15,y-25,x+15,y-10,start=0,extent=180,style='arc',outline=MUTED,tags='tie')
                    if cell.end>end:
                        c.create_arc(x+8,y-30,left+width-12,y,start=0,extent=180,style='arc',outline=MUTED,tags='tie')
                if den==1:
                    c.create_oval(x-7,top+192,x+7,top+199,outline=INK,width=1.2)
                else:c.create_line(x,top+182,x,top+220,fill=INK,width=1.4)
                if den==2:c.create_oval(x-5,top+181,x+5,top+187,outline=INK,width=1)
            if dot:c.create_oval(x+7,top+192,x+10,top+195,fill=INK,outline='')
            if not value:
                duration_ms=(self.draw_score.seconds_at(at+length)-self.draw_score.seconds_at(at))*1000
                c.create_text(x+7,top+245,text=f'~{duration_ms:.1f} ms',fill='#b94c28',font=('Consolas',8,'bold'),tags='special_duration')
            fragments.append((cell,at,length,x,den,trip))
        # Beam adjacent short notes in a beat group; isolated notes keep flags.
        groups=[];group=[]
        unit=F(3,2) if measure.meter[1]==8 and measure.meter[0]%3==0 else F(1)
        for item in fragments:
            cell,at,length,x,den,trip=item
            eligible=cell.fret is not None and den>=8
            if group and (not eligible or at!=group[-1][1]+group[-1][2] or (at-start)//unit!=(group[0][1]-start)//unit):
                groups.append(group);group=[]
            if eligible:group.append(item)
        if group:groups.append(group)
        for group in groups:
            for j,item in enumerate(group):
                cell,at,length,x,den,trip=item
                flags=int(math.log2(den))-2
                for flag in range(flags):
                    y=top+220-flag*6
                    if len(group)>1:
                        next_item=group[j+1] if j+1<len(group) else None
                        prev_item=group[j-1] if j else None
                        if next_item and int(math.log2(next_item[4]))-2>flag:
                            c.create_line(x,y,next_item[3],y,fill=INK,width=3)
                        elif not prev_item or int(math.log2(prev_item[4]))-2<=flag:
                            c.create_line(x,y,x+(10 if next_item or not prev_item else -10),y,fill=INK,width=3)
                    else:c.create_line(x,y,x+10,y-7,x+8,y-15,fill=INK,smooth=True,width=1.6)
        triplets=[];triplet_group=[]
        for item in fragments:
            if triplet_group and (not item[5] or item[1]!=triplet_group[-1][1]+triplet_group[-1][2] or len(triplet_group)==3):
                triplets.append(triplet_group);triplet_group=[]
            if item[5]:triplet_group.append(item)
        if triplet_group:triplets.append(triplet_group)
        for group in triplets:
            x1=group[0][3]-8;x2=group[-1][3]+8;mid=(x1+x2)/2;y=top+239
            if x2-x1>25:
                c.create_line(x1,y-4,x1,y,mid-7,y,fill=INK)
                c.create_line(mid+7,y,x2,y,x2,y-4,fill=INK)
            c.create_text(mid,y,text='3',fill=INK,font=('Consolas',10,'italic'))
        if not fragments and index not in getattr(self.doc,'measure_repeats',()):music_symbol(c,left+width/2,top+106,1,True,color='#c2cdd4')
        else:
            for at,length in self.doc.rest_spans(index):
                for part in rhythm_parts(length):
                    value=identify_duration(part);den,dot,trip=display_duration(part)
                    x=px(at);music_symbol(c,x,top+107,den,True,color=MUTED)
                    if dot:c.create_oval(x+7,top+104,x+10,top+107,fill=MUTED,outline='')
                    if trip:c.create_text(x,top+136,text='3',fill=MUTED,font=('Consolas',9))
                    if value is None:c.create_text(x+9,top+128,text='*',fill=MUTED,font=('Consolas',9))
                    at+=part
        if self.doc.bar_index(self.at)==index:
            x=px(self.at);y=top+55+(self.string-1)*22
            c.create_rectangle(x-12,y-12,x+12,y+12,outline=FOCUS,width=2)
            if not self.doc.at(self.at):
                c.create_text(x,y,text='·',fill=FOCUS,font=('Consolas',18))
                music_symbol(c,x,top+202,self.value,color='#79aeb4',scale=.7)

    def note_text(self,cell,string):return str(cell.fret)

    def draw_playhead(self):
        self.canvas.delete('playhead')
        if self.play_at is None:return
        index=self.doc.bar_index(self.play_at)
        if index>=len(self.bar_boxes):return
        left,top,width=self.bar_boxes[index]
        measure=self.doc.bar(index)
        x=left+58+float(self.play_at-measure.start)/float(measure.beats)*(width-78)
        self.canvas.create_line(x,top+36,x,top+247,fill='#c44177',width=2,tags='playhead')

    def score(self):
        if not self.settings_changed():raise ValueError(self.feedback.get())
        return self.doc.score()

    def apply(self,close=False):
        self.stop_preview()
        try:
            self.on_apply(self.score());self.applied_state=self.doc.snapshot()
            if close:self.destroy()
            else:self.feedback.set('已应用到主曲谱，BPM、拍号与小节一并保留')
            return True
        except ValueError as error:self.feedback.set(str(error));return False

    def save_file(self):
        if self.apply():self.parent.save_score()

    def preview_audio(self,score,index):return score,{}

    def preview_segment(self,from_start=False):
        source=self.score();index=0 if from_start else self.doc.bar_index(self.at)
        score,self.preview_offset=crop_from_measure(source,index)
        audio,options=self.preview_audio(score,index)
        return score,audio,options,index

    def playback_position(self,beat):return self.preview_offset+beat

    def preview(self,from_start=False):
        if self.player.running or self.preparing:self.stop_preview();return
        try:
            score,audio_score,audio_options,index=self.preview_segment(from_start);self.preview_score=score
            self.audio_cancel=threading.Event();cancel=self.audio_cancel;self.preparing=True
            self.preview_button.configure(text='生成试听中…');self.feedback.set(f'正在生成第 {index+1} 小节起的参考音频，Esc 可取消')
            def prepare():
                try:
                    from .audio import SoundPreviewBackend
                    backend=SoundPreviewBackend(audio_score,app_root()/'.preview',cancel,**audio_options)
                    with self.audio_lock:
                        if cancel.is_set():backend.close()
                        else:self.messages.put(('audio_ready',(backend,score,cancel)))
                except Exception as error:self.messages.put(('audio_error',(str(error),cancel)))
            threading.Thread(target=prepare,daemon=True).start()
        except ValueError as error:self.feedback.set(str(error))

    def stop_preview(self):
        self.audio_cancel.set();self.preparing=False;self.player.stop();self.play_at=None
        if hasattr(self,'canvas') and not self.closed:
            self.canvas.delete('playhead');self.preview_button.configure(text='▶ 本小节试听 Space')

    def _poll(self):
        if self.closed:return
        try:
            while True:
                kind,data=self.messages.get_nowait()
                if kind=='audio_ready':
                    backend,score,cancel=data
                    if cancel is not self.audio_cancel or cancel.is_set():backend.close()
                    else:
                        self.preparing=False
                        try:
                            self.player.start(score,GameProfile.frets(),backend)
                            self.preview_button.configure(text='■ 停止试听  Space')
                        except ValueError as error:backend.close();self.feedback.set(str(error))
                elif kind=='audio_error':
                    message,cancel=data
                    if cancel is self.audio_cancel:
                        self.preparing=False;self.feedback.set('试听已取消' if cancel.is_set() else '试听失败：'+message)
                        self.preview_button.configure(text='▶ 本小节试听 Space')
                elif kind=='note' and data[0]>=0:
                    self.play_at=self.playback_position(self.preview_score.events[data[0]].start);self.draw_playhead()
                elif kind=='progress':
                    score=self.preview_score
                    self.play_at=self.playback_position(score.beat_at_seconds(data));self.draw_playhead()
                    index=self.doc.bar_index(self.play_at)
                    if index<len(self.bar_boxes):
                        top=self.bar_boxes[index][1];visible=self.canvas.canvasy(0);height=self.canvas.winfo_height()
                        if height>100 and (top+55<visible or top+165>visible+height):
                            total=float(self.canvas.cget('scrollregion').split()[3]);self.canvas.yview_moveto(max(0,(top-10)/total))
                elif kind in ('done','error'):
                    self.play_at=None;self.draw_playhead();self.preview_button.configure(text='▶ 本小节试听 Space')
                    self.feedback.set(str(data) if kind=='error' else '试听结束，可继续编辑')
        except queue.Empty:pass
        self.poll_id=self.after(40,self._poll)

    def close(self):
        self.stop_preview()
        if self.changed:
            answer=messagebox.askyesnocancel('保留六线谱编辑','将本窗口的编辑应用到主曲谱？',parent=self)
            if answer is None:return
            if answer and not self.apply():return
        self.destroy()

    def destroy(self):
        self.stop_preview();self.closed=True
        for timer in (self.poll_id,self.refresh_id,self.focus_id,self.visible_id):
            try:
                if timer is not None:self.after_cancel(timer)
            except tk.TclError:pass
        with self.audio_lock:
            try:
                while True:
                    kind,data=self.messages.get_nowait()
                    if kind=='audio_ready':data[0].close()
            except queue.Empty:pass
        if getattr(self.parent,'tab_editor',None) is self:self.parent.tab_editor=None
        super().destroy()
