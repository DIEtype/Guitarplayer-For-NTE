"""A source TAB editor; game selectors are chosen by the translator afterwards."""
import copy
from fractions import Fraction as F
from bisect import bisect_right
from pathlib import Path
import tkinter as tk
from tkinter import ttk,filedialog

from .fingerstyle_document import FingerstyleDocument,HIT_X,HIT_DOT,read_document
from .game_profile import CHORD_NAMES
from .guitar import UP,DOWN
from .measures import crop_from_measure
from .visual_editor import VisualTabEditor,FOCUS,MUTED
from .tablature import identify_duration
from .voicings import read_voicings
from .composer_guidance import edit_guidance,file_guidance,measure_range,integer_field


class VisualFingerstyleEditor(VisualTabEditor):
    document_type=FingerstyleDocument
    chord_mode=True
    allow_capo=True
    heading_text='吉他编曲'
    apply_label='直接载入原谱'

    def __init__(self,parent,document,on_apply,path=None,on_direct=None):
        self.on_direct=on_direct
        self.bar_selection=None;self.measure_clipboard=None;self.preview_order=None
        self.file_path=Path(path) if path else None
        super().__init__(parent,document,on_apply)
        self.title('吉他编曲 · 原谱工作区 · by Akari');self.minsize(900,min(620,self.winfo_screenheight()-90))
        self.canvas.bind('<Control-c>',lambda event:self.copy_selection())
        self.canvas.bind('<Control-v>',lambda event:self.paste_selection(False))
        self.canvas.bind('<Control-Shift-V>',lambda event:self.paste_selection(True))
        self.canvas.bind('<Control-a>',lambda event:self.select_all_measures())

    def _build(self):
        from .composer_workspace import build_workspace
        build_workspace(self)

    def draw(self,width=None):
        super().draw(width)
        from .composer_workspace import update_context
        update_context(self)

    def edit(self,operation):
        result=super().edit(operation)
        if not result:self.feedback.set(edit_guidance(self.feedback.get()))
        return result

    def settings_changed(self,event=None):
        try:
            try:bpm=float(self.bpm.get())
            except ValueError:raise ValueError('顶部“BPM”请填写 20～360 的数字，例如 100 或 100.5，再重试。') from None
            if not 20<=bpm<=360:raise ValueError('顶部“BPM”超出范围；请填写 20～360，再重试。')
            integer_field(self.capo.get(),'Capo',0,24)
        except ValueError as error:self.feedback.set(str(error));return False
        return super().settings_changed(event)

    def select_measure_range(self):
        try:
            first,last=measure_range(self.range_first.get(),self.range_last.get(),self.doc.bars)
            self.bar_selection=(first,last);self.select_cursor(self.doc.bar(first).start)
            self.feedback.set(f'已选择第 {first+1}～{last+1} 小节，可复制、应用节奏预设或设置反复段')
            return True
        except ValueError as error:self.feedback.set(str(error));return False

    def clear_measure_selection(self):
        self.bar_selection=None;self.draw()

    def select_all_measures(self):
        self.bar_selection=(0,self.doc.bars-1);self.draw();return 'break'

    def sequence_dialog(self):
        dialog=tk.Toplevel(self);dialog.title('当前弦连续录入');dialog.transient(self);dialog.resizable(False,False)
        ttk.Label(dialog,text=f'第 {self.doc.bar_index(self.at)+1} 小节 · {self.string} 弦',padding=14).pack(anchor='w')
        entry=ttk.Entry(dialog,textvariable=self.sequence,width=38);entry.pack(padx=14)
        ttk.Label(dialog,text='020 → 三个连续音；多位品用空格分开，例如 0 12 0。',padding=14).pack()
        info=tk.StringVar();ttk.Label(dialog,textvariable=info,foreground='#a62d43',wraplength=430,padding=(14,0,14,12)).pack(anchor='w')
        def close():
            dialog.destroy()
            if self.winfo_viewable():self.grab_set();self.canvas.focus_set()
        def apply():
            if self.write_sequence():close()
            else:info.set('未写入：'+self.feedback.get())
        ttk.Button(dialog,text='写入并前进',command=apply,style='Accent.TButton').pack(anchor='e',padx=14,pady=(0,14))
        entry.bind('<Return>',lambda event:apply());dialog.protocol('WM_DELETE_WINDOW',close)
        if self.winfo_viewable():dialog.grab_set();entry.focus_set()
        dialog.write_sequence=apply;dialog.feedback=info
        return dialog

    def selected_measures(self):
        current=self.doc.bar_index(self.at)
        if self.bar_selection and self.bar_selection[0]<=current<=self.bar_selection[1]:return self.bar_selection
        return current,current

    def click(self,event):
        anchor=self.bar_selection[0] if self.bar_selection else self.doc.bar_index(self.at)
        super().click(event);current=self.doc.bar_index(self.at)
        self.bar_selection=(min(anchor,current),max(anchor,current)) if getattr(event,'state',0)&1 else None
        self.draw()

    def pattern_dialog(self):
        from .composition_ui import pattern_dialog
        return pattern_dialog(self)

    def copy_range_dialog(self):
        from .composition_ui import copy_dialog
        return copy_dialog(self)

    def repeat_dialog(self):
        from .composition_ui import repeat_dialog
        return repeat_dialog(self)

    def save_rhythm_dialog(self,on_saved=None):
        from .composition_ui import save_rhythm_dialog
        return save_rhythm_dialog(self,on_saved=on_saved)

    def copy_selection(self):
        from .composition import capture_measures
        try:
            first,last=self.selected_measures();self.measure_clipboard=capture_measures(self.doc,first,last)
            self.feedback.set(f'已复制第 {first+1}～{last+1} 小节；Ctrl+V 覆盖，Ctrl+Shift+V 插入')
            from .composer_workspace import update_context
            update_context(self)
        except ValueError as error:self.feedback.set(edit_guidance(str(error)))
        return 'break'

    def paste_selection(self,insert=False):
        from .composition import paste_measures
        if not self.measure_clipboard:self.feedback.set('尚未复制小节。请在“小节与反复 → 选择范围”选择小节，再点“复制”（Ctrl+C）；随后选中目标小节粘贴。');return 'break'
        target=self.doc.bar_index(self.at)
        if self.edit(lambda:paste_measures(self.doc,self.measure_clipboard,target,insert)):
            self.bar_selection=None;self.select_cursor(self.doc.bar(target).start)
        return 'break'

    def repeat_previous(self):
        from .composition import set_measure_repeat
        index=self.doc.bar_index(self.at)
        return self.edit(lambda:set_measure_repeat(self.doc,index))

    def unfold_percent(self):
        from .composition import materialize_percent
        return self.edit(lambda:materialize_percent(self.doc,self.doc.bar_index(self.at)))

    def clear_repeat_section(self):
        index=self.doc.bar_index(self.at)
        return self.edit(lambda:setattr(self.doc,'repeat_sections',[row for row in self.doc.repeat_sections if not row[0]<=index<=row[1]]))

    def extra_bar_commands(self):
        return (('节奏预设…',self.pattern_dialog),('复制选中小节 Ctrl+C',self.copy_selection),
                ('粘贴覆盖 Ctrl+V',lambda:self.paste_selection(False)),('粘贴为新小节 Ctrl+Shift+V',lambda:self.paste_selection(True)),
                ('指定范围复制…',self.copy_range_dialog),('% 重复前一小节',self.repeat_previous),
                ('展开 % 为可编辑音符',self.unfold_percent),('设置段落反复线…',self.repeat_dialog),
                ('清除当前段落反复线',self.clear_repeat_section))

    def context_menu(self,event):
        selected=self.bar_selection;self.click(event)
        if selected and selected[0]<=self.doc.bar_index(self.at)<=selected[1]:self.bar_selection=selected;self.draw()
        menu=self.bar_menu;menu.delete(0,'end')
        menu.add_command(label='从本小节试听',command=self.preview)
        menu.add_command(label='小节设置…',command=self.measure_dialog);menu.add_separator()
        for title,commands in (
            ('音符与节拍',(('删音（保留时值） Del',self.delete),('清空整列音符 Shift+Del',self.clear_column),
                       ('插入空节拍… Insert',self.insert_beat_dialog),('删除整列节拍 Ctrl+Del',self.delete_beat),
                       ('连接下一同音 T',self.tie_next),('断开延音 Shift+T',self.split_tie))),
            ('复制与粘贴',self.extra_bar_commands()[1:5]),
            ('插入小节',(('本小节之前',lambda:self.insert_bar(False)),('本小节之后',lambda:self.insert_bar(True)),('追加至末尾',self.add_bar))),
            ('重复记号',self.extra_bar_commands()[5:])):
            group=tk.Menu(menu,tearoff=False)
            for label,command in commands:group.add_command(label=label,command=command)
            menu.add_cascade(label=title,menu=group)
        menu.add_separator();menu.add_command(label='节奏预设…',command=self.pattern_dialog)
        menu.add_command(label='当前小节存为节奏预设…',command=self.save_rhythm_dialog)
        try:menu.tk_popup(event.x_root,event.y_root)
        finally:menu.grab_release()

    def cleanup_onsets(self,window_ms=30):
        self.stop_preview()
        if not self.settings_changed():return False
        from .onset_cleanup import cleanup_document,cleanup_summary
        try:
            cleaned,report=cleanup_document(self.doc,window_ms)
            if report['merged_groups']:
                if not self.edit(lambda:setattr(self.doc,'cells',cleaned.cells)):return False
            self.feedback.set(cleanup_summary(report)+'；可撤销，建议另存副本；大窗口会收拢快速琶音')
            return True
        except ValueError as error:self.feedback.set('无法整理：'+str(error));return False

    def put_sweep(self,direction):
        def change():
            if not self.doc.chord_at(self.at):self.doc.set_chord(self.at,self.chord.get().strip())
            self.doc.write_sweep(self.at,direction,self.beats)
        if self.edit(change):self.typed='';self.canvas.focus_set()

    def mark_chord(self):
        if self.edit(lambda:self.doc.set_chord(self.at,self.chord.get().strip())):self.canvas.focus_set()

    def put_hit(self,symbol):
        current=self.doc.at(self.at)
        if self.edit(lambda:self.doc.write_member(self.at,self.string,symbol,current.beats if current else self.beats)):
            self.typed='';self.canvas.focus_set()

    def key(self,event):
        if event.char=='%':self.repeat_previous();return 'break'
        if event.char.lower() in ('u','d'):
            self.put_sweep(UP if event.char.lower()=='u' else DOWN);return 'break'
        if event.char.lower()=='x':self.put_hit(HIT_X);return 'break'
        if event.char in ('.','·','●'):self.put_hit(HIT_DOT);return 'break'
        return super().key(event)

    def write_sequence(self):
        def change():self.at=self.doc.insert_sequence(self.at,self.string,self.sequence.get(),self.beats)
        if self.edit(change):
            self.typed='';self.select_cursor(self.at);self.canvas.focus_set();return True
        return False

    def clear_note(self):
        return super().clear_note()

    def clear(self):
        def change():
            self.doc.cells=[];self.doc.chord_changes={};self.doc.bars=4
            self.doc.measure_repeats=set();self.doc.repeat_sections=[];self.bar_selection=None
            self.doc.tempo_map=();self.doc.bar_changes={};self.doc.default_timbre='clean';self.doc.capo=0
        self.timbre.set('清音');self.capo.set('0');self.edit(change);self.select_cursor(0,1)

    def note_text(self,cell,string):
        value=cell.values[string-1]
        return 'X' if value==HIT_X else '●' if value==HIT_DOT else str(value)

    def refresh(self):
        super().refresh()
        active=self.doc.chord_at(self.at)
        if active:self.chord.set(active)
        current=self.doc.at(self.at);symbol=self.note_text(current,self.string) if current and current.values[self.string-1] is not None else '空位'
        if current and current.sweep:symbol='上扫 1→6' if current.sweep==UP else '下扫 6→1'
        if current and identify_duration(current.beats) is None:
            duration_ms=(self.draw_score.seconds_at(current.end)-self.draw_score.seconds_at(current.start))*1000
            symbol+=f' · 特殊时值 {current.beats} 拍 ≈ {duration_ms:.1f} ms'
        self.feedback.set(f'第 {self.doc.bar_index(self.at)+1} 小节 · {self.string} 弦 · {symbol} · 和弦 {active or "未标记"}  |  数字写品位，X／点拨和弦弦；U／D 扫弦；直接载入保留原谱')

    def draw_bar(self,index,left,top,width):
        super().draw_bar(index,left,top,width)
        if self.bar_selection and self.bar_selection[0]<=index<=self.bar_selection[1]:
            self.canvas.create_rectangle(left,top,left+width,top+254,outline=FOCUS,width=2,tags='measure_selection')
        measure=self.doc.bar(index);first=left+58;span=width-78
        if index in self.doc.measure_repeats:
            x=left+width/2;y=top+110
            self.canvas.create_line(x-14,y+24,x+14,y-24,fill=FOCUS,width=4,tags='measure_repeat')
            for dx,dy in ((-12,-14),(12,14)):
                self.canvas.create_oval(x+dx-4,y+dy-4,x+dx+4,y+dy+4,fill=FOCUS,outline=FOCUS,tags='measure_repeat')
            self.canvas.create_text(x,top+185,text='重复前一小节',fill=MUTED,tags='measure_repeat')
        for a,b,times in self.doc.repeat_sections:
            if index not in (a,b):continue
            for edge,direction in ((a,1),(b,-1)):
                if index!=edge:continue
                x=left+31 if direction==1 else left+width-17
                self.canvas.create_line(x,top+55,x,top+165,width=3,fill=FOCUS,tags='repeat_barline')
                self.canvas.create_line(x+direction*5,top+55,x+direction*5,top+165,width=1,fill=FOCUS,tags='repeat_barline')
                for y in (top+99,top+121):self.canvas.create_oval(x+direction*12-2,y-2,x+direction*12+2,y+2,fill=FOCUS,outline=FOCUS,tags='repeat_barline')
                if direction==-1:self.canvas.create_text(x-10,top+35,text=f'×{times}',anchor='e',fill=FOCUS,tags='repeat_barline')
        if index in self.doc.measure_repeats:return
        changes=[(at,name) for at,name in sorted(self.doc.chord_changes.items()) if measure.start<=at<measure.end]
        active=self.doc.chord_at(measure.start)
        if active and not any(at==measure.start for at,_ in changes):changes.insert(0,(measure.start,'('+active+')'))
        for at,name in changes:
            x=first+float(at-measure.start)/float(measure.beats)*span
            self.canvas.create_text(x,top+35,text=name,anchor='w',fill=FOCUS,font=('Consolas',12,'bold'),tags='source_chord')
        for cell in self.doc.cells:
            if not cell.sweep or not measure.start<=cell.start<measure.end:continue
            x=first+float(cell.start-measure.start)/float(measure.beats)*span
            y1,y2=(top+55,top+165) if cell.sweep==UP else (top+165,top+55)
            self.canvas.create_line(x,y1,x,y2,arrow='last',arrowshape=(10,12,5),width=2,fill=FOCUS,tags='strum')
            self.canvas.create_text(x+10,top+110,text='上' if cell.sweep==UP else '下',anchor='w',fill=MUTED,font=('Microsoft YaHei UI',8))

    def resolved_source(self,document=None):
        window=getattr(self.parent,'translation_window',None)
        if window is not None and window.winfo_exists():
            rows=window.rows;reference=window.basis.get().startswith('无 Capo')
        else:
            rows=read_voicings(self.parent.settings_dir/'voicings.json') if self.parent.settings_dir else {}
            reference=True
        return (document or self.doc).pitch_source(rows,reference,self.file_path.name if self.file_path else '原谱编写')

    def preview_audio(self,score,index):
        source=self.resolved_source()
        from .composition import playback_order
        order=playback_order(self.doc);expanded=order.index(index)
        audio,offset=crop_from_measure(source.score,expanded)
        options={'pitch_events':True}
        if source.sweeps:options['strums']={at-offset:direction for at,direction in source.sweeps if at>=offset}
        return audio,options

    def preview_segment(self,from_start=False):
        if not self.doc.measure_repeats and not self.doc.repeat_sections:
            self.preview_order=None
            return super().preview_segment(from_start)
        from .composition import materialize_repeats
        linear,order=materialize_repeats(self.doc)
        index=0 if from_start else self.doc.bar_index(self.at);expanded=order.index(index)
        score,self.preview_offset=crop_from_measure(linear.score(),expanded)
        self.preview_order=order;self.preview_layout=linear.layout;self.preview_starts=tuple(bar.start for bar in linear.layout)
        source=self.resolved_source(linear);audio,offset=crop_from_measure(source.score,expanded)
        options={'pitch_events':True,'strums':{at-offset:direction for at,direction in source.sweeps if at>=offset}}
        return score,audio,options,index

    def playback_position(self,beat):
        if not self.preview_order:return super().playback_position(beat)
        at=self.preview_offset+beat;index=max(0,min(len(self.preview_order)-1,bisect_right(self.preview_starts,at)-1))
        original=self.preview_order[index]
        return self.doc.bar(original).start+at-self.preview_layout[index].start

    def keep_draft(self):
        if self.parent.settings_dir:
            self.doc.save(self.parent.settings_dir/'fingerstyle_draft.gscore')
        self.parent.fingerstyle_document=copy.deepcopy(self.doc)
        self.parent.fingerstyle_path=self.file_path

    def apply(self,close=False):
        if self.on_direct is None:return self.send_to_translator()
        self.stop_preview()
        try:
            if not self.settings_changed():return False
            self.keep_draft();self.on_direct(copy.deepcopy(self.doc));self.applied_state=self.doc.snapshot()
            if close:self.destroy()
            else:self.feedback.set('原谱已直接载入；近似转译仍可单独选择')
            return True
        except (ValueError,OSError) as error:self.feedback.set(file_guidance(error,'载入'));return False

    def send_to_translator(self):
        self.stop_preview()
        try:
            if not self.settings_changed():return False
            source=self.resolved_source();self.keep_draft();self.applied_state=self.doc.snapshot()
            callback=self.on_apply
            # Release this editor before a converted game score can be opened.
            self.destroy();callback(source);return True
        except (ValueError,OSError) as error:self.feedback.set(file_guidance(error,'送入转译'));return False

    def save_file(self,path=None):
        try:
            if not self.settings_changed():return
            path=path or self.file_path or filedialog.asksaveasfilename(parent=self,title='保存指弹原谱',defaultextension='.gscore',initialfile='指弹原谱.gscore',filetypes=[('指弹原谱','*.gscore')])
            if not path:return
            self.doc.save(path);self.file_path=Path(path);self.keep_draft();self.applied_state=self.doc.snapshot()
            self.feedback.set('原谱已保存：'+self.file_path.name+'；可继续修改或送入转译');return self.file_path
        except (ValueError,OSError) as error:self.feedback.set(file_guidance(error,'保存'))

    def open_file(self,path=None):
        path=path or filedialog.askopenfilename(parent=self,title='读取指弹原谱',filetypes=[('曲谱／六线谱','*.gscore *.tab *.txt')])
        if not path:return
        try:
            doc=read_document(path);self.keep_draft();self.undo_stack.append(self.snapshot());self.redo_stack=[]
            self.doc=doc;self.file_path=Path(path);self.applied_state=doc.snapshot()
            self.bpm.set(f'{doc.bpm:g}');self.numerator.set(str(doc.meter[0]));self.denominator.set(str(doc.meter[1]))
            from .measures import TONE_NAMES
            self.timbre.set(TONE_NAMES[doc.default_timbre]);self.capo.set(str(doc.capo));self.select_cursor(F(0),1)
        except (ValueError,OSError,UnicodeError,KeyError,TypeError,IndexError,OverflowError) as error:self.feedback.set(file_guidance(error,'读取'))

    def close(self):
        try:
            if not self.settings_changed():return
            self.keep_draft();self.destroy()
        except (ValueError,OSError) as error:self.feedback.set(file_guidance(error,'草稿保存'))
