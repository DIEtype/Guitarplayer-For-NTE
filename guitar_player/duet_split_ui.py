"""Review and audition automatic two-pass arrangements before applying them."""
import copy
from dataclasses import replace
from fractions import Fraction as F
import json
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import ttk,filedialog

from .duet_split import STRATEGIES,split_source,midi_source,read_split_source
from .duet_project import DuetPart
from .measures import TONE_NAMES,crop_from_measure
from .score import pitch_name
from .voicings import read_voicings
from .widgets import ScrollableStatusBanner


class DuetSplitDialog(tk.Toplevel):
    def __init__(self,workspace):
        super().__init__(workspace);self.workspace=workspace;self.app=workspace.app
        self.title('自动拆分 · MIDI／指弹原谱 → 录音伴奏＋现场声部');self.transient(workspace)
        width=min(980,self.winfo_screenwidth()-70);height=min(760,self.winfo_screenheight()-90)
        self.geometry(f'{width}x{height}');self.minsize(min(780,width),min(600,height))
        self.rows={};self.source=None;self.midi=None;self.path=None;self.result=None;self.result_source=None;self.saved_project=None
        self.messages=queue.Queue();self.cancel=threading.Event();self.running=False;self.token=0;self.closed=False
        self.audio_cancel=threading.Event();self.audio_backend=None;self.audio_end=None;self.poll_timer=None
        self.manual_keys=set();self.note_rows=[];self.result_options=None
        self.feedback=tk.StringVar(value='选择原始 MIDI 或指弹谱。默认按音乐角色拆分，原文件保留；结果可试听、逐音核对后再应用。')
        if self.app.settings_dir:
            try:self.rows=read_voicings(self.app.settings_dir/'voicings.json')
            except (OSError,ValueError,TypeError,KeyError) as error:self.feedback.set('本机音高表未读取，将使用参考指法：'+str(error))
        head=ttk.Frame(self,padding=(14,12,14,5));head.pack(fill='x')
        ttk.Label(head,text='一份原谱，两次演奏',font=('Microsoft YaHei UI',17,'bold')).pack(side='left')
        self.file_button=ttk.Button(head,text='选择 MIDI／指弹谱…',command=self.load_file,style='Workspace.Source.TButton');self.file_button.pack(side='right')
        self.current_button=ttk.Button(head,text='使用当前原谱',command=self.use_current);self.current_button.pack(side='right',padx=8)
        self.source_label=tk.StringVar(value='尚未选择原谱')
        ttk.Label(self,textvariable=self.source_label,padding=(14,0,14,5),wraplength=900).pack(anchor='w')
        self.game_state=tk.StringVar()
        ttk.Label(self,textvariable=self.game_state,padding=(14,0,14,5),foreground='#326da8',wraplength=900).pack(anchor='w')
        self.refresh_game_state()
        tracks=ttk.Frame(self,padding=(14,0,14,6));tracks.pack(fill='x')
        self.track_tree=ttk.Treeview(tracks,columns=('name','notes'),show='headings',selectmode='extended',height=3)
        self.track_tree.heading('name',text='MIDI 轨道（可多选，鼓轨不参与）');self.track_tree.column('name',width=350)
        self.track_tree.heading('notes',text='音符数');self.track_tree.column('notes',width=65,stretch=False)
        self.track_tree.pack(side='left',fill='x',expand=True)
        pick=ttk.Frame(tracks,padding=(12,0));pick.pack(side='right',fill='y')
        ttk.Label(pick,text='主旋律轨道').pack(anchor='w');self.lead=tk.StringVar(value='自动推断');self.lead_items={}
        self.lead_box=ttk.Combobox(pick,textvariable=self.lead,values=('自动推断',),state='readonly',width=22);self.lead_box.pack(anchor='w',pady=4)
        ttk.Label(pick,text='指定后保护该轨全部音符；\n不指定时按音区、持续音和连续性推断。',foreground='#62798d').pack(anchor='w')
        options=ttk.Frame(self,padding=(14,0,14,6));options.pack(fill='x')
        ttk.Label(options,text='拆分策略').pack(side='left');self.strategy=tk.StringVar(value=STRATEGIES['roles'])
        ttk.Combobox(options,textvariable=self.strategy,values=tuple(STRATEGIES.values()),state='readonly',width=18).pack(side='left',padx=6)
        ttk.Label(options,text='共同前奏').pack(side='left',padx=(10,4));self.preroll=tk.StringVar(value='2')
        ttk.Spinbox(options,textvariable=self.preroll,from_=1,to=8,width=3).pack(side='left');ttk.Label(options,text='小节').pack(side='left',padx=4)
        ttk.Label(options,text='整曲移调').pack(side='left',padx=(10,4));self.transpose=tk.StringVar(value='0')
        ttk.Spinbox(options,textvariable=self.transpose,from_=-24,to=24,width=4).pack(side='left');ttk.Label(options,text='半音').pack(side='left',padx=4)
        self.timing_label=ttk.Label(self,text='按主窗口当前输入档位与保持时值设置检查；共同前奏同时加入两份谱。',foreground='#62798d',padding=(14,0,14,5))
        self.timing_label.pack(anchor='w')
        footer=ttk.Frame(self,padding=12);footer.pack(side='bottom',fill='x')
        self.apply_button=ttk.Button(footer,text='应用为录音伴奏＋现场声部',command=self.apply,state='disabled',style='Accent.TButton');self.apply_button.pack(side='right')
        self.generate_button=ttk.Button(footer,text='开始自动拆分',command=self.generate,style='Workspace.Source.TButton');self.generate_button.pack(side='right',padx=8)
        self.report_button=ttk.Button(footer,text='保存报告',command=self.save_report,state='disabled');self.report_button.pack(side='left')
        self.export_button=ttk.Button(footer,text='保存两份曲谱',command=self.export_scores,state='disabled');self.export_button.pack(side='left',padx=5)
        ttk.Button(footer,text='关闭',command=self.destroy).pack(side='left',padx=7)
        self.banner=ScrollableStatusBanner(self,self.feedback,lines=3);self.banner.pack(side='bottom',fill='x',padx=14,pady=(0,4))
        preview=ttk.Frame(self,padding=(14,0,14,6));preview.pack(side='bottom',fill='x')
        self.original_button=ttk.Button(preview,text='试听原谱',command=lambda:self.audition(False),state='disabled');self.original_button.pack(side='left')
        self.mix_button=ttk.Button(preview,text='试听两声部合成',command=lambda:self.audition(True),state='disabled');self.mix_button.pack(side='left',padx=7)
        ttk.Button(preview,text='停止试听',command=self.stop_audio).pack(side='left')
        ttk.Label(preview,text='参考音色；试听略去新增共同前奏，演奏谱仍保留。',foreground='#62798d').pack(side='left',padx=8)
        scores=ttk.Frame(self,padding=(14,0,14,5));scores.pack(side='bottom',fill='x')
        self.view_buttons={}
        for role,label in (('recorded','查看录音伴奏六线谱'),('live','查看现场声部六线谱')):
            button=ttk.Button(scores,text=label,command=lambda r=role:self.view_score(r),state='disabled')
            button.pack(side='left',padx=(0,7));self.view_buttons[role]=button
        view=ttk.Frame(self,padding=(14,0,14,0));view.pack(fill='both',expand=True)
        columns=('beat','source','role','part','selector','reason')
        self.notes=ttk.Treeview(view,columns=columns,show='headings',height=6)
        for key,title,w in zip(columns,('原拍位','音高','判断声部','分配到','和弦／品格','保留／省略说明'),(75,65,100,100,90,300)):
            self.notes.heading(key,text=title);self.notes.column(key,width=w,minwidth=55)
        scrollbar=ttk.Scrollbar(view,command=self.notes.yview);scrollbar.pack(side='right',fill='y')
        self.notes.configure(yscrollcommand=scrollbar.set);self.notes.pack(fill='both',expand=True)
        self.note_menu=tk.Menu(self,tearoff=False)
        self.note_menu.add_command(label='选中音设为此拍主旋律，重新拆分生效',command=self.protect_selected)
        self.note_menu.add_command(label='清除手动旋律标记',command=self.clear_protected)
        self.notes.bind('<Button-3>',self.note_context)
        for variable in (self.strategy,self.preroll,self.transpose,self.lead):
            variable.trace_add('write',lambda *_:self.changed_options())
        self.track_tree.bind('<<TreeviewSelect>>',lambda _event:self.changed_options())
        self.poll_timer=self.after(50,self.poll)

    def options_key(self):
        return (self.strategy.get(),self.preroll.get(),self.transpose.get(),self.lead.get(),tuple(self.track_tree.selection()),
                frozenset(self.manual_keys),self.app.input_timing,self.app.tap.get(),self.app.sustain_notes.get(),
                self.app.game_mode.get(),self.app.game_timbre.get(),copy.deepcopy(self.app.profiles['混合模式']))

    def refresh_game_state(self):
        pages=dict(self.app.profiles['混合模式'].initial_pages);mode='chord' if self.app.game_mode.get()=='和弦' else 'fret'
        try:pages[mode]=int(self.app.page.get())-1
        except ValueError:pass
        text=f'游戏起始：{self.app.game_mode.get()} · {self.app.game_timbre.get()}；和弦第 {pages["chord"]+1} 页，品格第 {pages["fret"]+1} 页；输入：{self.app.input_speed.get()}'
        if self.game_state.get()!=text:self.game_state.set(text)

    def changed_options(self):
        if self.result is not None and self.result_options!=self.options_key():
            self.result=None;self.saved_project=None
            for button in (self.apply_button,self.mix_button,self.report_button,self.export_button):button.configure(state='disabled')
            for button in self.view_buttons.values():button.configure(state='disabled')
            self.feedback.set('拆分设置已改变，请重新计算；已配置的合奏声部保留。')

    def current_result(self):
        self.changed_options()
        return self.result is not None

    def work(self,function,kind):
        self.token+=1;token=self.token;self.cancel=threading.Event();cancel=self.cancel;self.running=True
        self.file_button.configure(state='disabled');self.current_button.configure(state='disabled');self.generate_button.configure(text='取消计算')
        def run():
            try:
                value=function(cancel,token)
                if not cancel.is_set():self.messages.put((token,kind,value))
            except Exception as error:
                if not cancel.is_set():self.messages.put((token,'error',str(error)))
        threading.Thread(target=run,daemon=True).start()

    def finish_work(self):
        self.running=False;self.file_button.configure(state='normal');self.current_button.configure(state='normal');self.generate_button.configure(text='开始自动拆分')

    def load_file(self,path=None):
        if self.running:return
        path=path or filedialog.askopenfilename(parent=self,title='选择原始 MIDI 或指弹谱',filetypes=[('原谱','*.mid *.midi *.gscore *.tab *.txt')])
        if not path:return
        self.stop_audio();self.path=Path(path);selected_path=self.path;self.result=None;self.source=None;self.midi=None;self.result_source=None;self.manual_keys.clear();self.saved_project=None
        for button in (self.apply_button,self.original_button,self.mix_button,self.report_button,self.export_button,*self.view_buttons.values()):button.configure(state='disabled')
        self.feedback.set('正在后台读取原谱…')
        def load(cancel,token):
            if selected_path.suffix.lower() in ('.mid','.midi'):
                from .midi_import import inspect_midi
                return 'midi',inspect_midi(selected_path)
            return 'source',read_split_source(selected_path,self.rows)
        self.work(load,'loaded')

    def use_current(self):
        if self.running or self.app.busy:self.feedback.set('请先停止当前计算或演奏。');return
        try:
            document=self.app.unarranged_document or (self.app.arrangement_source if self.app.source_game_score==self.app.score else None)
            if document is None:
                from .fingerstyle_document import FingerstyleDocument
                score=self.app.analyze()
                if score is None:raise ValueError(self.app.info.get())
                document=FingerstyleDocument.from_score(score)
            document=copy.deepcopy(document)
            self.path=None;self.result=None;self.result_source=None;self.source=None;self.midi=None;self.manual_keys.clear();self.saved_project=None;self.stop_audio()
            for button in (self.apply_button,self.original_button,self.mix_button,self.report_button,self.export_button,*self.view_buttons.values()):button.configure(state='disabled')
            self.work(lambda cancel,token:('source',document.pitch_source(self.rows,True,'当前指弹原谱')),'loaded')
        except (ValueError,TypeError) as error:self.feedback.set('原谱未读取：'+str(error))

    def show_source(self,kind,value):
        self.track_tree.delete(*self.track_tree.get_children());self.lead.set('自动推断');self.lead_items={}
        self.midi=value if kind=='midi' else None;self.source=value if kind=='source' else None
        if self.midi is not None:
            for track in self.midi.tracks:
                self.track_tree.insert('','end',iid=str(track.index),values=(track.name,len(track.notes)))
                self.lead_items[f'{track.index} · {track.name}']=track.index
            self.track_tree.selection_set(self.track_tree.get_children())
            self.source_label.set(f'{self.midi.path.name} · {len(self.midi.tracks)} 个非鼓轨道 · {self.midi.bpm:g} BPM')
        else:
            self.source_label.set(f'{self.source.name} · {len([e for e in self.source.score.events if e.notes])} 个起音 · {self.source.score.bpm:g} BPM')
        self.lead_box.configure(values=('自动推断',*self.lead_items))
        self.feedback.set('原谱已读取。MIDI 可多选轨道并指定主旋律；拆分不会改写原文件。')

    def generate(self):
        if self.running:
            self.cancel.set();self.token+=1;self.finish_work();self.feedback.set('已取消，原谱与已配置的合奏声部保留。');return
        self.stop_audio()
        try:
            if self.app.busy:raise ValueError('请先停止当前演奏，再拆分')
            if not self.workspace.game_controls.apply_pages():raise ValueError(self.workspace.feedback.get())
            if self.source is None and self.midi is None:raise ValueError('请先选择 MIDI／指弹原谱，或使用当前原谱')
            tracks=tuple(int(key) for key in self.track_tree.selection());lead=self.lead_items.get(self.lead.get())
            bars=int(self.preroll.get());shift=int(self.transpose.get())
            if not 1<=bars<=8 or not -24<=shift<=24:raise ValueError('共同前奏为 1～8 小节，整曲移调为 -24～24 半音')
            strategy=next(k for k,v in STRATEGIES.items() if v==self.strategy.get())
            profile=copy.deepcopy(self.app.profiles['混合模式'])
            profile.initial_mode='chord' if self.app.game_mode.get()=='和弦' else 'fret'
            profile.initial_timbre=next(k for k,v in TONE_NAMES.items() if v==self.app.game_timbre.get())
            timing=self.app.input_timing;tap=int(self.app.tap.get())/1000;sustain=self.app.sustain_notes.get()
            midi=self.midi;original_source=self.source
            manual=tuple(self.manual_keys)
            options=self.options_key()
            selected_path=self.path
            self.result=None;self.apply_button.configure(state='disabled');self.mix_button.configure(state='disabled')
            self.report_button.configure(state='disabled');self.export_button.configure(state='disabled')
            for button in self.view_buttons.values():button.configure(state='disabled')
            self.feedback.set('正在后台分析声部并联合分配两条演奏路线，可取消…')
            def split(cancel,token):
                source,marked=midi_source(midi,tracks,lead) if midi is not None else (original_source,())
                self.messages.put((token,'source_notes',source))
                result=split_source(source,self.rows,profile,timing,tap,strategy=strategy,melody_keys=marked,
                    protected_keys=manual,
                    transpose=shift,preroll_bars=bars,cancel=cancel,sustain_notes=sustain,
                    progress=lambda done,total:self.messages.put((token,'progress',(done,total))))
                saved=None;save_error=''
                if not cancel.is_set():
                    try:saved=self.workspace.library.save_split(result,selected_path.stem if selected_path else source.name,selected_path)
                    except (OSError,ValueError,TypeError) as error:save_error=str(error)
                return source,result,options,saved,save_error
            self.work(split,'result')
        except (ValueError,TypeError,StopIteration) as error:self.feedback.set('未开始拆分：'+str(error))

    def show_result(self,source,result,options=None,saved=None,save_error=''):
        self.workspace.refresh_saved()
        if options is not None and options!=self.options_key():
            self.feedback.set('计算期间设置已改变，请按当前设置重新拆分；原谱与合奏声部保留。');return
        self.result_options=options if options is not None else self.options_key()
        self.result_source=source;self.result=result;self.notes.delete(*self.notes.get_children())
        if saved is None and not save_error:
            try:saved=self.workspace.library.save_split(result,self.path.stem if self.path else source.name,self.path)
            except (OSError,ValueError,TypeError) as error:save_error=str(error)
        self.saved_project=saved;self.workspace.refresh_saved()
        from .voice_roles import ROLE_NAMES
        self.note_rows=result.report['notes'][:1200]
        for index,row in enumerate(self.note_rows):
            role=ROLE_NAMES.get(row['role'],row['role']);part={'recorded':'录音伴奏','live':'现场声部','omitted':'省略'}[row['part']]
            self.notes.insert('','end',iid=str(index),values=(row['beat'],pitch_name(row['output_pitch']),role,part,row['selector'],row['reason']))
        complete=all(result.scores.values());self.apply_button.configure(state='normal' if complete else 'disabled')
        self.original_button.configure(state='normal');self.mix_button.configure(state='normal');self.report_button.configure(state='normal')
        self.export_button.configure(state='normal' if complete else 'disabled')
        for role,button in self.view_buttons.items():button.configure(state='normal' if result.scores[role] is not None else 'disabled')
        note='' if complete else ' 当前仅需一个声部，无需录音叠加；可试听或更换“可演奏优先”检查密集起音。'
        if len(result.report['notes'])>1200:note+=' 列表显示前 1200 音，完整数据可保存报告。'
        archive_note=' 已自动保存到“已保存合奏”，以后直接选择即可。' if saved is not None and saved.archive_id else ''
        if save_error:archive_note=' 自动保存失败：'+save_error+'。结果仍保留，可点“保存两份曲谱”另存。'
        self.feedback.set('生成完成：'+result.report['summary']+note+archive_note+' 可右键选中需要保护的音，指定主旋律后重新拆分。')

    def show_source_notes(self,source):
        self.result_source=source;self.notes.delete(*self.notes.get_children());self.note_rows=[]
        for event in source.score.events:
            for pitch in sorted(set(event.notes)):
                if len(self.note_rows)>=1200:break
                row={'beat':str(event.start),'original_pitch':pitch,'output_pitch':pitch,'role':'uncertain'}
                index=len(self.note_rows);self.note_rows.append(row)
                self.notes.insert('','end',iid=str(index),values=(row['beat'],pitch_name(pitch),'待核对','待分配','','原谱音符'))
            if len(self.note_rows)>=1200:break
        self.original_button.configure(state='normal')

    def note_context(self,event):
        row=self.notes.identify_row(event.y)
        if row and row not in self.notes.selection():self.notes.selection_set(row)
        self.note_menu.tk_popup(event.x_root,event.y_root)

    def protect_selected(self):
        for key in self.notes.selection():
            row=self.note_rows[int(key)];self.manual_keys.add((F(row['beat']),row['original_pitch']))
        self.changed_options()
        self.feedback.set(f'已手动保护 {len(self.manual_keys)} 个旋律音，请点“开始自动拆分”重新计算。')

    def clear_protected(self):
        self.manual_keys.clear();self.changed_options();self.feedback.set('手动旋律标记已清除，重新拆分后生效。')

    def apply(self):
        if not self.current_result():return False
        if not all(self.result.scores.values()):self.feedback.set('请先生成两份有效的演奏谱。');return False
        if self.app.busy:self.feedback.set('请先停止播放，再应用拆分结果。');return False
        if self.saved_project is not None:
            try:
                if not self.workspace.activate_project(copy.deepcopy(self.saved_project)):
                    self.feedback.set(self.workspace.feedback.get());return False
                self.workspace.feedback.set(self.result.report['summary']+' 两轨已载入；'+('已恢复该曲录音码，可直接合奏。' if self.workspace.code.get().strip() else '先录制伴奏，再填写录音码；谱子已自动保存。'))
                self.destroy();self.workspace.show_overview();return True
            except (OSError,ValueError) as error:self.feedback.set('合奏未应用：'+str(error));return False
        try:settings=replace(self.workspace.settings(),code='')
        except ValueError as error:self.feedback.set('合奏设置需要调整：'+str(error));return False
        name=self.path.stem if self.path else self.result.report['source']
        for role,label in (('recorded','录音伴奏'),('live','主旋律／现场声部')):
            self.workspace.project.set_part(role,DuetPart((name+' · '+label)[:200],self.result.scores[role],str(self.path or '自动拆分当前原谱')))
        self.workspace.project.split_summary=self.result.report['summary'][:2048]
        self.workspace.code.set('');self.workspace.project.bind_code('')
        try:
            self.workspace.write_settings(settings);self.workspace.persist_project()
            if self.app.settings_dir:
                path=self.app.settings_dir/'duet-split-report.json';path.write_text(json.dumps(self.result.report,ensure_ascii=False,indent=2),encoding='utf-8')
        except OSError as error:self.feedback.set('拆分结果已载入，但本机保存失败：'+str(error));return False
        self.workspace.refresh_project();self.workspace.select_tab(self.workspace.overview_tab)
        self.workspace.feedback.set(self.result.report['summary']+' 两份谱已自动填入；请录制新的蓝色声部，再填写新录音码合奏。')
        self.destroy();self.workspace.show_overview();return True

    def view_score(self,role):
        if not self.current_result() or self.result.scores[role] is None:return
        project=copy.deepcopy(self.workspace.project)
        for r,score in self.result.scores.items():
            project.set_part(r,DuetPart(self.result.report['source']+' · '+('录音伴奏' if r=='recorded' else '现场声部'),score,'自动拆分结果') if score is not None else None)
        editor=self.workspace.open_duet_editor(role,project=project)
        if editor is not None:self.withdraw()
        return editor

    def export_scores(self,directory=None):
        if not self.current_result() or not all(self.result.scores.values()):return
        directory=directory or filedialog.askdirectory(parent=self,title='选择保存目录：两份曲谱将放入独立文件夹')
        if not directory:return
        try:
            from datetime import datetime,timezone,timedelta
            target=Path(directory)/datetime.now(timezone(timedelta(hours=8))).strftime('双声部拆分_%Y%m%d_%H%M%S')
            target.mkdir(exist_ok=False)
            source_name=self.path.stem if self.path else self.result.report['source']
            for role,label in (('recorded','录音伴奏'),('live','现场声部')):
                part=DuetPart((source_name+' · '+label)[:200],self.result.scores[role],str(self.path or '自动拆分'))
                (target/(label+'.gscore')).write_text(json.dumps({'version':1,'kind':'duet_part','role':role,'part':part.to_dict()},ensure_ascii=False,indent=2),encoding='utf-8')
            (target/'拆分报告.json').write_text(json.dumps(self.result.report,ensure_ascii=False,indent=2),encoding='utf-8')
            self.feedback.set('两份曲谱和报告已保存：'+str(target)+'。可直接打开，或在录音合奏中选择声部读取。')
            return target
        except (OSError,ValueError) as error:self.feedback.set('曲谱保存失败：'+str(error))

    def save_report(self,path=None):
        if self.result is None:return
        path=path or filedialog.asksaveasfilename(parent=self,title='保存逐音拆分报告',defaultextension='.json',filetypes=[('JSON 报告','*.json')])
        if not path:return
        try:Path(path).write_text(json.dumps(self.result.report,ensure_ascii=False,indent=2),encoding='utf-8');self.feedback.set('拆分报告已保存：'+Path(path).name)
        except OSError as error:self.feedback.set('报告保存失败：'+str(error))

    def audition(self,converted):
        if self.running:self.feedback.set('请先完成或取消拆分计算，再试听。');return
        if converted and not self.current_result():return
        if not converted and self.result_source is None:return
        if self.app.busy:self.feedback.set('请先停止其他演奏或试听。');return
        self.stop_audio();cancel=self.audio_cancel;token=self.token
        if converted:
            scores=[score for score in self.result.pitched.values() if score is not None]
            score=replace(scores[0],events=tuple(sorted((e for s in scores for e in s.events),key=lambda e:e.start)))
            score,offset=crop_from_measure(score,self.result.report['preroll_bars'])
            markers={(at-offset,label):direction for values in self.result.sweeps.values() for (at,label),direction in values.items() if at>=offset}
        else:score=self.result_source.score;markers=dict(self.result_source.sweeps)
        self.feedback.set('正在生成'+('两声部合成' if converted else '原谱')+'参考音频…')
        def render():
            try:
                from .audio import SoundPreviewBackend
                from .paths import app_root
                backend=SoundPreviewBackend(score,app_root()/'.preview',cancel,pitch_events=True,strums=markers)
                if cancel.is_set() or self.closed:backend.close()
                else:self.messages.put((token,'audio',(backend,cancel,score.seconds)))
            except Exception as error:
                if not cancel.is_set():self.messages.put((token,'audio_error',str(error)))
        threading.Thread(target=render,daemon=True).start()

    def stop_audio(self):
        self.audio_cancel.set();self.audio_cancel=threading.Event()
        if self.audio_backend is not None:self.audio_backend.close();self.audio_backend=None
        if self.audio_end is not None:self.after_cancel(self.audio_end);self.audio_end=None

    def poll(self):
        self.poll_timer=None
        self.refresh_game_state()
        try:
            while True:
                token,kind,value=self.messages.get_nowait()
                if token!=self.token:
                    if kind=='audio':value[0].close()
                    continue
                if kind=='loaded':self.finish_work();self.show_source(*value)
                elif kind=='source_notes':self.show_source_notes(value)
                elif kind=='result':self.finish_work();self.show_result(*value)
                elif kind=='progress':self.feedback.set(f'正在联合分配两条路线：{value[0]}/{value[1]} 个起音，可取消…')
                elif kind=='error':self.finish_work();self.feedback.set('自动拆分失败：'+value)
                elif kind=='audio_error':self.feedback.set('试听失败：'+value)
                elif kind=='audio':
                    backend,cancel,seconds=value
                    if cancel is not self.audio_cancel or cancel.is_set() or self.app.busy:backend.close()
                    else:
                        try:backend.sync_playback(0,False)
                        except Exception as error:backend.close();self.feedback.set('试听失败：'+str(error));continue
                        self.audio_backend=backend;self.audio_end=self.after(round((seconds+.2)*1000),self.stop_audio)
                        self.feedback.set('正在试听合成参考音色；游戏效果需实测，音长合组与截尾见报告。')
        except queue.Empty:pass
        if not self.closed:self.poll_timer=self.after(50,self.poll)

    def destroy(self):
        self.closed=True;self.cancel.set();self.stop_audio()
        if self.poll_timer is not None:self.after_cancel(self.poll_timer);self.poll_timer=None
        try:
            while True:
                token,kind,value=self.messages.get_nowait()
                if kind=='audio':value[0].close()
        except queue.Empty:pass
        super().destroy()
