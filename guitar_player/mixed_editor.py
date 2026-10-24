"""Chord accompaniment and exact-fret passages on one measured timeline."""
import tkinter as tk
from tkinter import ttk

from .chord_editor import VisualChordEditor,CHORD_COLOR
from .chord_score import MixedDocument
from .game_profile import ROMAN,CHORD_PAGE_INDEX
from .visual_editor import VisualTabEditor,PAPER,INK


class VisualMixedEditor(VisualChordEditor):
    document_type=MixedDocument

    def __init__(self,parent,score,on_apply):
        super().__init__(parent,score,on_apply)
        self.title('和弦＋品格 · 混合编谱')

    def build_chord_controls(self):
        super().build_chord_controls()
        row=ttk.Frame(self,padding=(16,0,16,6));row.pack(fill='x')
        ttk.Label(row,text='品格段落',foreground=CHORD_COLOR).pack(side='left',padx=(0,8))
        self.fret_value=tk.StringVar(value='0')
        ttk.Spinbox(row,textvariable=self.fret_value,from_=0,to=24,width=4).pack(side='left')
        ttk.Button(row,text='在当前位置标记品位',command=self.choose_fret).pack(side='left',padx=8)
        ttk.Label(row,text='和弦按钮切回和弦段；品格段内数字表示品位，齐奏各弦共用品位').pack(side='left')

    def choose_fret(self):
        try:
            fret=int(self.fret_value.get())
            if not 0<=fret<=24:raise ValueError('品位应为 0～24')
            self.choose_chord(ROMAN[fret-1] if fret else 'Zero')
        except ValueError as error:self.feedback.set(str(error))

    def select_cursor(self,at,string=None):
        VisualTabEditor.select_cursor(self,at,string)
        name=self.doc.chord_at(self.at)
        if name in CHORD_PAGE_INDEX:self.palette_page.set(self.page_labels[CHORD_PAGE_INDEX[name]])
        elif hasattr(self,'fret_value'):self.fret_value.set(str(0 if name=='Zero' else ROMAN.index(name)+1))
        self.update_palette()

    def key(self,event):
        name=self.doc.chord_at(self.at)
        if name in (*ROMAN,'Zero') and event.char and event.char.isascii() and event.char.isdigit():
            # Use the standard TAB number-entry logic, but store the fret in a mark.
            import time
            now=time.monotonic();location=(self.at,self.string)
            typed=self.typed+event.char if now-self.typed_at<.9 and self.typed_location==location else event.char
            if len(typed)>2 or int(typed)>24:typed=event.char
            fret=int(typed)
            if fret>24:self.feedback.set('品位范围 0～24');return 'break'
            def write():
                self.doc.set_chord(self.at,ROMAN[fret-1] if fret else 'Zero')
                cell=self.doc.at(self.at)
                notes=tuple(sorted(set(cell.notes)|{self.string})) if self.multiple.get() and cell and all(n in range(1,7) for n in cell.notes) else (self.string,)
                self.doc.write_action(self.at,notes,cell.beats if cell else self.beats)
            if self.edit(write):self.typed=typed;self.typed_at=now;self.typed_location=location
            return 'break'
        return super().key(event)

    def refresh(self):
        super().refresh()
        if hasattr(self,'palette_page'):
            name=self.doc.chord_at(self.at)
            mode='品格' if name in (*ROMAN,'Zero') else '和弦'
            self.feedback.set(f'第 {self.doc.bar_index(self.at)+1} 小节 · {mode} {name}；切段自动发送 Z，需保留切换间隔；预演显示动作')

    def draw_bar(self,index,left,top,width):
        super().draw_bar(index,left,top,width)
        measure=self.doc.bar(index)
        for cell in self.doc.cells:
            if not measure.start<=cell.start<measure.end:continue
            name=self.doc.chord_at(cell.start)
            if name not in (*ROMAN,'Zero'):continue
            fret=0 if name=='Zero' else ROMAN.index(name)+1
            x=left+58+float(cell.start-measure.start)/float(measure.beats)*(width-78)
            for string in cell.notes:
                if string not in range(1,7):continue
                y=top+55+(string-1)*22
                self.canvas.create_rectangle(x-12,y-10,x+12,y+10,fill=PAPER,outline='')
                self.canvas.create_text(x,y,text=str(fret),fill=INK,font=('Consolas',14,'bold'),tags='mixed_fret')
