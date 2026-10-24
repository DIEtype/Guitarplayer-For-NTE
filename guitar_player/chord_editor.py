"""Chord marks above six strings, with direct picking and strum entry."""
from fractions import Fraction as F
import tkinter as tk
from tkinter import ttk

from . import __version__
from .chord_score import ChordDocument
from .game_profile import CHORD_PAGES,CHORD_PAGE_INDEX,GameProfile
from .guitar import UP,DOWN
from .player import PreviewBackend
from .visual_editor import VisualTabEditor,INK,MUTED,PAPER,FOCUS

CHORD_COLOR='#b93270'
PATTERNS=('分解：低音–3–2–3–1–3–2–3','扫弦：下 下 上 上 下 上')


class VisualChordEditor(VisualTabEditor):
    document_type=ChordDocument
    chord_mode=True

    def __init__(self,parent,score,on_apply):
        super().__init__(parent,score,on_apply)
        self.title(f'和弦伴奏 v{__version__} · 拨弦与扫弦')
        self.minsize(820,min(660,self.winfo_screenheight()-90))
        self.preview_button.configure(text='▶ 本小节预演 Space')
        self.head_preview_button.configure(text='从头预演')
        self.feedback.set('上方选择和弦 → 点击弦线写入拨弦 → 点音符选时值；预演只显示节奏与动作')

    def build_chord_controls(self):
        palette=ttk.Frame(self,padding=(16,0,16,6));palette.pack(fill='x')
        ttk.Label(palette,text='游戏和弦',foreground=CHORD_COLOR).pack(side='left',padx=(0,8))
        self.palette_page=tk.StringVar(value='1 · 基础')
        self.page_labels=('1 · 基础','2 · 七与九','3 · 五与扩展','4 · 低音与其他')
        box=ttk.Combobox(palette,textvariable=self.palette_page,values=self.page_labels,width=14,state='readonly')
        box.pack(side='left',padx=(0,8));box.bind('<<ComboboxSelected>>',lambda event:self.update_palette())
        self.chord_buttons=[]
        for slot in range(6):
            button=tk.Button(palette,width=7,relief='flat',bd=1,bg=PAPER,fg=INK,
                             font=('Segoe UI',11,'bold'),cursor='hand2',
                             command=lambda s=slot:self.choose_chord(CHORD_PAGES[self.page_labels.index(self.palette_page.get())][s]))
            button.pack(side='left',padx=3,fill='x',expand=True);self.chord_buttons.append(button)
        ttk.Button(palette,text='移除标记',command=self.remove_chord).pack(side='left',padx=(6,0))
        tools=ttk.Frame(self,padding=(16,0,16,6));tools.pack(fill='x')
        ttk.Button(tools,text='上扫1→6 U',command=lambda:self.put_sweep(UP)).pack(side='left',padx=(0,4))
        ttk.Button(tools,text='下扫6→1 D',command=lambda:self.put_sweep(DOWN)).pack(side='left',padx=(0,8))
        self.multiple=tk.BooleanVar(value=False)
        ttk.Checkbutton(tools,text='同拍多弦',variable=self.multiple).pack(side='left',padx=(0,8))
        self.pattern=tk.StringVar(value=PATTERNS[0]);self.bass=tk.StringVar(value='6')
        ttk.Combobox(tools,textvariable=self.pattern,values=PATTERNS,state='readonly',width=26).pack(side='left')
        ttk.Label(tools,text='低音弦').pack(side='left',padx=(8,3))
        ttk.Combobox(tools,textvariable=self.bass,values=(6,5,4,3,2,1),state='readonly',width=2).pack(side='left')
        self.pattern_button=ttk.Button(tools,text='填入本小节',command=self.fill_pattern)
        self.pattern_button.pack(side='left',padx=8)
        self.update_palette()

    def update_palette(self):
        names=CHORD_PAGES[self.page_labels.index(self.palette_page.get())]
        active=self.doc.chord_at(self.at)
        for button,name in zip(self.chord_buttons,names):
            button.configure(text=name,bg=CHORD_COLOR if name==active else PAPER,fg='white' if name==active else INK)

    def choose_chord(self,name):
        self.edit(lambda:self.doc.set_chord(self.at,name));self.canvas.focus_set()

    def select_cursor(self,at,string=None):
        super().select_cursor(at,string)
        self.palette_page.set(self.page_labels[CHORD_PAGE_INDEX[self.doc.chord_at(self.at)]])
        self.update_palette()

    def remove_chord(self):
        self.edit(lambda:self.doc.remove_chord(self.at));self.canvas.focus_set()

    def put_sweep(self,direction):
        self.edit(lambda:self.doc.write_action(self.at,(direction,),self.beats));self.canvas.focus_set()

    def fill_pattern(self):
        self.edit(lambda:self.doc.fill_pattern(self.doc.bar_index(self.at),self.pattern.get(),int(self.bass.get())))
        self.canvas.focus_set()

    def click(self,event):
        super().click(event)
        y=self.canvas.canvasy(event.y)
        for left,top,width in self.bar_boxes:
            if left+43<=event.x<=left+width-8 and top+44<=y<=top+176:
                cell=self.doc.at(self.at)
                if self.multiple.get():self.edit(lambda:self.doc.toggle_string(self.at,self.string,self.beats))
                elif not cell or cell.notes!=(self.string,):
                    self.edit(lambda:self.doc.write_action(self.at,(self.string,),cell.beats if cell else self.beats))
                break

    def context_menu(self,event):
        # Right click selects without creating or toggling a pluck.
        VisualTabEditor.click(self,event)
        menu=self.bar_menu;menu.delete(0,'end')
        menu.add_command(label='从本小节预演',command=self.preview)
        menu.add_command(label='小节设置',command=self.measure_dialog)
        menu.add_command(label='在本小节前插入',command=lambda:self.insert_bar(False))
        menu.add_command(label='在本小节后插入',command=lambda:self.insert_bar(True))
        try:menu.tk_popup(event.x_root,event.y_root)
        finally:menu.grab_release()

    def key(self,event):
        char=event.char.lower()
        if char in '123456' and char:
            string=int(char);self.string=string
            if self.multiple.get():self.edit(lambda:self.doc.toggle_string(self.at,string,self.beats))
            else:
                cell=self.doc.at(self.at)
                self.edit(lambda:self.doc.write_action(self.at,(string,),cell.beats if cell else self.beats))
            return 'break'
        if char in ('u','d'):self.put_sweep(UP if char=='u' else DOWN);return 'break'
        if char=='0':self.put_rest();return 'break'
        return super().key(event) if char not in '789' else 'break'

    def clear(self):
        def change():
            self.doc.cells=[];self.doc.bars=4;self.doc.tempo_map=();self.doc.bar_changes={}
            self.doc.chord_changes={F(0):'G'};self.doc.default_timbre='clean'
        self.timbre.set('清音');self.edit(change);self.select_cursor(0,1)

    def refresh(self):
        super().refresh()
        if not hasattr(self,'palette_page'):return
        name=self.doc.chord_at(self.at)
        self.update_palette()
        cell=self.doc.at(self.at)
        action='空位：点击弦线或选扫弦' if not cell else '休止' if not cell.notes else '上扫（1→6弦）' if cell.notes==(UP,) else '下扫（6→1弦）' if cell.notes==(DOWN,) else '拨 '+ '、'.join(map(str,cell.notes))+' 弦'
        self.feedback.set(f'第 {self.doc.bar_index(self.at)+1} 小节 · {name} · {action}  |  ← → 移音位；U/D 扫弦，R 休止；预演无声音')

    def draw_bar(self,index,left,top,width):
        super().draw_bar(index,left,top,width)
        measure=self.doc.bar(index);c=self.canvas
        def px(at):return left+58+float(at-measure.start)/float(measure.beats)*(width-78)
        marks={at:name for at,name in self.doc.chord_changes.items() if measure.start<=at<measure.end}
        marks.setdefault(measure.start,self.doc.chord_at(measure.start))
        for at,name in sorted(marks.items()):
            c.create_text(px(at),top+34,text=name,fill=CHORD_COLOR,anchor='w',font=('Segoe UI',12,'bold'),tags='chord_mark')
        for string in range(1,7):
            c.create_text(left+32,top+55+(string-1)*22,text=str(string),fill=MUTED,font=('Consolas',8))
        for cell in self.doc.cells:
            if not measure.start<=cell.start<measure.end:continue
            x=px(cell.start)
            if cell.notes in ((UP,),(DOWN,)):
                c.create_rectangle(x-9,top+76,x+9,top+141,fill=PAPER,outline='')
                # TAB string 1 is at the top. Follow sounding string order.
                y1,y2=(top+55,top+165) if cell.notes==(UP,) else (top+165,top+55)
                c.create_line(x,y1,x,y2,arrow='last',arrowshape=(10,12,5),width=2,fill=INK,tags='strum')
                c.create_text(x+9,top+111,text='上' if cell.notes==(UP,) else '下',anchor='w',
                              fill=MUTED,font=('Microsoft YaHei UI',8),tags='strum_label')
            else:
                for string in cell.notes:
                    y=top+55+(string-1)*22
                    c.create_rectangle(x-9,y-10,x+9,y+10,fill=PAPER,outline='')
                    c.create_oval(x-4,y-4,x+4,y+4,fill=INK,outline='',tags='pluck')

    def preview(self,from_start=False):
        if self.player.running:self.stop_preview();return
        try:
            from .measures import crop_from_measure
            index=0 if from_start else self.doc.bar_index(self.at)
            score,self.preview_offset=crop_from_measure(self.score(),index);self.preview_score=score
            self.player.start(score,GameProfile(),PreviewBackend())
            self.preview_button.configure(text='■ 停止预演 Space')
            self.feedback.set('正在预演节奏与动作；实际和弦音色请进入游戏试听')
        except ValueError as error:self.feedback.set(str(error))

    def stop_preview(self):
        super().stop_preview()
        if hasattr(self,'preview_button') and not self.closed:self.preview_button.configure(text='▶ 本小节预演 Space')

    def _poll(self):
        super()._poll()
        if self.closed:return
        if self.feedback.get()=='试听结束，可继续编辑':self.feedback.set('预演结束，可继续编辑；实际音色请在游戏中试听')
        if not self.player.running:self.preview_button.configure(text='▶ 本小节预演 Space')
