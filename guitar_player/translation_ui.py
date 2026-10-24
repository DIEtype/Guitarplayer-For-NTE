"""Review a fingerstyle arrangement against reference or measured game voicings."""
from dataclasses import replace
from fractions import Fraction as F
import copy
import json
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import ttk,filedialog,messagebox

from .fingerstyle_translation import from_text,read_source,translate,translate_best_key,transpose_name,KeySearchError,AUTO_RANGE_SHIFTS
from .game_profile import CHORD_NAMES,GameProfile
from .guitar import parse_guitar
from .measures import TONE_NAMES,apply_changes
from .score import pitch_name
from .voicings import Voicing,reference_voicings,read_voicings,write_voicings,parse_pitch,format_pitch,available_voicings
from .widgets import ScrollableStatusBanner
from .voice_roles import POLICIES,ROLE_NAMES,analyze_roles
from .import_guidance import translation_error
from .translation_failures import summarize_failures

AUTO_KEY='自动降调（0～24半音）'
AUTO_RANGE='自动升降调（±24半音）'
KEY_OPTIONS=('保持原调',AUTO_RANGE,AUTO_KEY,*(f'降{n}半音' for n in range(1,25)),*(f'升{n}半音' for n in range(1,25)))


def key_shift(label):
    if label in (AUTO_KEY,AUTO_RANGE,KEY_OPTIONS[0]):return 0
    return int(label[1:-2])*(-1 if label.startswith('降') else 1)


def names(pitches):return ' '.join(pitch_name(n) for n in pitches) or '—'


def omitted_count(report):
    return report.get('total_omitted_notes',report['omitted_notes'])+report.get('range_filtered_notes',0)


class TranslationWindow(tk.Toplevel):
    def __init__(self,parent,table_path=None):
        super().__init__(parent);self.parent=parent
        self.title('指弹转译 · 和弦与品格');self.geometry('1120x760');self.minsize(900,620)
        self.transient(parent)
        self.table_path=Path(table_path) if table_path is not None else (parent.settings_dir/'voicings.json' if parent.settings_dir else None)
        self.rows={};self.source=None;self.result=None;self.report=None
        self.search_cancel=threading.Event();self.search_queue=queue.Queue();self.searching=False
        self.search_poll=None;self.key_comparison=None;self.key_settings=None
        self.failure_current=False
        self.table_timer=None
        self.voice_timer=None;self.voice_rows=[];self.voice_overrides={}
        self.audio_cancel=threading.Event();self.audio_queue=queue.Queue();self.audio_lock=threading.Lock()
        self.audio_poll=None;self.audio_end=None;self.audio_backend=None
        self.feedback=tk.StringVar(value='先选择指弹原谱，再预览转译。参考指法按标准定弦、无 Capo 建立，尚未在游戏实测。')
        try:
            if self.table_path:self.rows=read_voicings(self.table_path)
        except (OSError,ValueError,TypeError,KeyError) as error:self.feedback.set('本机音高表无法读取：'+str(error))
        ttk.Label(self,text='指弹原谱 → 匹配和弦弦音 → 品格补充',font=('Microsoft YaHei UI',17,'bold'),padding=14).pack(anchor='w')
        self.tabs=ttk.Notebook(self);self.tabs.pack(fill='both',expand=True,padx=14)
        convert=ttk.Frame(self.tabs,padding=10);table=ttk.Frame(self.tabs,padding=10)
        self.tabs.add(convert,text='原谱转译与对照');self.tabs.add(table,text='和弦音高表')
        self.build_conversion(convert);self.build_table(table)
        self.feedback_banner=ScrollableStatusBanner(self,self.feedback)
        self.feedback_banner.pack(side='bottom',before=self.tabs,fill='x',padx=14,pady=12)
        self.use_current()

    def build_conversion(self,frame):
        row=ttk.Frame(frame);row.pack(fill='x')
        ttk.Button(row,text='选择指弹原谱 .gscore / TAB',command=self.load_source).pack(side='left')
        ttk.Button(row,text='使用当前编辑内容',command=self.use_current).pack(side='left',padx=8)
        ttk.Button(row,text='编辑此原谱',command=self.edit_source).pack(side='left')
        ttk.Button(row,text='编辑和弦音高表',command=lambda:self.tabs.select(1)).pack(side='right')
        self.source_label=tk.StringVar(value='尚未选择原谱')
        ttk.Label(frame,textvariable=self.source_label,padding=(0,10),wraplength=1000).pack(anchor='w')
        options=ttk.Frame(frame);options.pack(fill='x',pady=(0,10))
        self.basis=tk.StringVar(value='无 Capo 参考指法（试译）')
        basis=ttk.Combobox(options,textvariable=self.basis,values=('无 Capo 参考指法（试译）','仅使用实测和弦'),width=27,state='readonly')
        basis.pack(side='left');basis.bind('<<ComboboxSelected>>',lambda event:self.invalidate())
        self.octaves=tk.BooleanVar(value=False)
        ttk.Checkbutton(options,text='伴奏可差一个八度；旋律遵循整体移调',variable=self.octaves,command=self.invalidate).pack(side='left',padx=10)
        self.convert_button=ttk.Button(options,text='生成转译预览',command=self.convert,style='Accent.TButton')
        self.convert_button.pack(side='right')
        key_row=ttk.Frame(frame);key_row.pack(fill='x',pady=(0,8))
        ttk.Label(key_row,text='整曲移调').pack(side='left')
        self.transpose=tk.StringVar(value=KEY_OPTIONS[0])
        key_box=ttk.Combobox(key_row,textvariable=self.transpose,values=KEY_OPTIONS,width=28,state='readonly')
        key_box.pack(side='left',padx=8);key_box.bind('<<ComboboxSelected>>',lambda event:self.invalidate())
        self.filter_low_bass=tk.BooleanVar(value=False)
        ttk.Checkbutton(key_row,text='过滤 E2 以下伴奏（主旋律不删）',variable=self.filter_low_bass,command=self.invalidate).pack(side='left')
        ornament_row=ttk.Frame(frame);ornament_row.pack(fill='x',pady=(0,5))
        self.ornaments=tk.BooleanVar(value=True)
        ttk.Checkbutton(ornament_row,text='装饰音适配：主音准拍，先提前，来不及合并／省略',
                        variable=self.ornaments,command=self.invalidate).pack(side='left')
        self.avoid_bm_second=tk.BooleanVar(value=True)
        ttk.Checkbutton(ornament_row,text='临时规避 Bm 2 弦异常',variable=self.avoid_bm_second,command=self.invalidate).pack(side='left',padx=8)
        voice_row=ttk.Frame(frame);voice_row.pack(fill='x',pady=(0,6))
        ttk.Label(voice_row,text='伴奏').pack(side='left')
        self.accompaniment=tk.StringVar(value=POLICIES['sparse']);self.distorted_only=tk.BooleanVar(value=True)
        policy=ttk.Combobox(voice_row,textvariable=self.accompaniment,values=tuple(POLICIES.values()),state='readonly',width=20)
        policy.pack(side='left',padx=6);policy.bind('<<ComboboxSelected>>',lambda event:self.invalidate())
        ttk.Checkbutton(voice_row,text='仅失真段简化',variable=self.distorted_only,command=self.invalidate).pack(side='left',padx=6)
        ttk.Label(voice_row,text='主旋律通道').pack(side='left',padx=(12,4))
        self.melody_channel=tk.StringVar(value='自动推断')
        self.channel_box=ttk.Combobox(voice_row,textvariable=self.melody_channel,values=('自动推断',),state='readonly',width=10)
        self.channel_box.pack(side='left');self.channel_box.bind('<<ComboboxSelected>>',lambda event:self.invalidate())
        self.key_summary=tk.StringVar(value='自动升降调比较 ±24 半音共 49 个方案；过滤按移调后音高判断，主旋律／待核对音／明确扫弦保留。')
        ttk.Label(frame,textvariable=self.key_summary,wraplength=1000,padding=(0,0,0,8)).pack(anchor='w')
        self.comparison_tabs=ttk.Notebook(frame);self.comparison_tabs.pack(fill='both',expand=True)
        notes=ttk.Frame(self.comparison_tabs);keys=ttk.Frame(self.comparison_tabs);ornaments=ttk.Frame(self.comparison_tabs)
        self.comparison_tabs.add(notes,text='逐音对照');self.comparison_tabs.add(keys,text='移调比较')
        self.comparison_tabs.add(ornaments,text='装饰音处理')
        voices=ttk.Frame(self.comparison_tabs);self.comparison_tabs.add(voices,text='声部与去留')
        self.voice_tree=ttk.Treeview(voices,columns=('beat','pitch','channel','role','basis','output'),show='headings')
        for key,label,width in zip(self.voice_tree['columns'],('拍位','原音','MIDI通道','声部','判断依据','实际去留'),(85,70,75,100,300,130)):
            self.voice_tree.heading(key,text=label);self.voice_tree.column(key,width=width,minwidth=55)
        self.voice_tree.pack(fill='both',expand=True)
        correction=ttk.Frame(voices);correction.pack(side='bottom',fill='x',pady=4,before=self.voice_tree)
        ttk.Label(correction,text='修正选中音符').pack(side='left')
        self.voice_role=tk.StringVar(value=ROLE_NAMES['melody'])
        ttk.Combobox(correction,textvariable=self.voice_role,values=tuple(ROLE_NAMES.values()),state='readonly',width=13).pack(side='left',padx=6)
        ttk.Button(correction,text='标记，重新转译生效',command=self.mark_voice).pack(side='left')
        ttk.Button(correction,text='清除手动标记',command=self.clear_voice_marks).pack(side='left',padx=6)
        self.ornament_tree=ttk.Treeview(ornaments,columns=('position','kind','pitches','action','target','shift'),show='headings')
        for key,label,width in zip(self.ornament_tree['columns'],('原位置','识别推断','原音高','处理','目标拍位','偏移 ms'),(180,180,170,80,110,90)):
            self.ornament_tree.heading(key,text=label);self.ornament_tree.column(key,width=width,minwidth=65)
        self.ornament_tree.pack(fill='both',expand=True)
        columns=('beat','source','target','main','marked','mode','selector','strings','output','omitted','basis','input')
        self.tree=ttk.Treeview(notes,columns=columns,show='headings',height=12)
        widths=(55,140,140,65,90,55,70,85,140,100,75,115)
        for key,label,width in zip(columns,('拍位','原谱音高','移调目标','主音','和弦标记','模式','实际选择','吉他弦号','表中音高','省略音','依据','按键／手柄'),widths):
            self.tree.heading(key,text=label);self.tree.column(key,width=width,minwidth=45)
        self.tree.pack(fill='both',expand=True)
        bar=ttk.Scrollbar(notes,orient='horizontal',command=self.tree.xview);bar.pack(fill='x');self.tree.configure(xscrollcommand=bar.set)
        self.keys=ttk.Treeview(keys,columns=('key','chord','omitted','switches','pages','timing','state'),show='headings',height=12)
        for key,label,width in zip(self.keys['columns'],('方案','和弦／起拍','省略音','模式切换','翻页','检查结果','状态'),(120,95,65,80,65,320,90)):
            self.keys.heading(key,text=label);self.keys.column(key,width=width,minwidth=45)
        self.keys.pack(fill='both',expand=True)
        self.keys.bind('<Double-1>',lambda event:self.use_key_candidate())
        key_actions=ttk.Frame(keys);key_actions.pack(fill='x',pady=5)
        ttk.Button(key_actions,text='定位失败音 → 声部核对',command=self.locate_failure).pack(side='left')
        ttk.Button(key_actions,text='查看／试用选中移调方案',command=self.use_key_candidate).pack(side='right')
        actions=ttk.Frame(frame);actions.pack(side='bottom',fill='x',pady=(10,0),before=self.comparison_tabs)
        self.apply_button=ttk.Button(actions,text='应用为演奏谱，再打开编曲',command=self.apply,state='disabled')
        self.apply_button.pack(side='right')
        ttk.Button(actions,text='保存转换／失败报告',command=self.save_report).pack(side='right',padx=8)
        ttk.Button(actions,text='试听原谱',command=lambda:self.audition(False)).pack(side='left')
        ttk.Button(actions,text='试听转译',command=lambda:self.audition(True)).pack(side='left',padx=6)
        ttk.Button(actions,text='停止试听',command=self.stop_audio).pack(side='left')

    def cancel_search(self):
        self.search_cancel.set();self.searching=False
        if self.search_poll is not None:self.after_cancel(self.search_poll);self.search_poll=None
        self.convert_button.configure(text='生成转译预览')

    def invalidate(self):
        self.cancel_search();self.stop_audio();self.result=None;self.report=None
        self.failure_current=False
        if self.table_timer is not None:self.after_cancel(self.table_timer);self.table_timer=None
        if self.voice_timer is not None:self.after_cancel(self.voice_timer);self.voice_timer=None
        self.apply_button.configure(state='disabled')
        self.tree.delete(*self.tree.get_children())
        self.ornament_tree.delete(*self.ornament_tree.get_children())
        self.feedback.set('设置已更改，请重新生成转译预览。')
        self.key_summary.set('设置已更改，请重新生成预览；原谱保持原调。')

    def use_current(self):
        try:
            if self.parent.busy:raise ValueError('请先停止演奏')
            if self.parent.unarranged_document is not None:
                self.set_source(self.parent.unarranged_document.pitch_source(self.rows,self.basis.get().startswith('无 Capo'),'已载入原谱'));return
            current=self.parent.analyze()
            if current is not None and self.parent.source_game_score==current and self.parent.arrangement_source is not None:
                self.set_source(self.parent.arrangement_source.pitch_source(self.rows,self.basis.get().startswith('无 Capo'),'当前编曲原谱'));return
            if current is not None and self.parent.mode.get()!='品格模式':
                from .fingerstyle_document import FingerstyleDocument
                self.set_source(FingerstyleDocument.from_score(current).pitch_source(self.rows,self.basis.get().startswith('无 Capo'),'当前混合／和弦谱'));return
            source=from_text(self.parent.editor.get('1.0','end-1c'),float(self.parent.bpm.get()),self.parent.tab_step.get(),self.parent.capo)
            score=replace(source.score,time_signature=self.parent.time_signature,measures=self.parent.measure_count)
            base=()
            if self.parent.tempo_source:
                from .midi_import import scale_tempos
                base=scale_tempos(self.parent.tempo_source,self.parent.tempo_bpm,score.bpm)
            self.set_source(replace(source,score=apply_changes(score,self.parent.bar_changes,self.parent.default_timbre,base)))
        except Exception as error:self.feedback.set('当前内容无法作为指弹原谱：'+str(error)+'；可另选原谱文件。')

    def set_source(self,source):
        self.invalidate();self.key_comparison=None;self.keys.delete(*self.keys.get_children())
        self.key_summary.set('自动升降调比较 ±24 半音共 49 个方案；过滤按移调后音高判断，主旋律／待核对音／明确扫弦保留。')
        self.source_path=None
        self.source=source;self.result=None;self.report=None;self.apply_button.configure(state='disabled')
        self.voice_overrides={};self.voice_rows=[];self.voice_tree.delete(*self.voice_tree.get_children())
        channels=sorted({h.channel+1 for h in source.note_hints if h.channel is not None})
        self.channel_box.configure(values=('自动推断',*(str(channel) for channel in channels)))
        self.melody_channel.set('自动推断')
        self.tree.delete(*self.tree.get_children())
        self.source_label.set(f'{source.name} · {source.score.bpm:g} BPM · {len([e for e in source.score.events if e.notes])} 个起拍 · {source.score.seconds:.1f} 秒')
        self.feedback.set('原谱已读取；同拍跨品位的音符会完整进入转译，原文件保留。')

    def load_source(self,path=None):
        path=path or filedialog.askopenfilename(parent=self,title='选择指弹原谱',filetypes=[('六线谱／曲谱','*.gscore *.txt *.tab')])
        if not path:return
        try:
            self.set_source(read_source(path,rows=self.rows,reference=self.basis.get().startswith('无 Capo')))
            self.source_path=path
        except Exception as error:self.feedback.set('原谱读取失败：'+str(error))

    def edit_source(self):
        try:
            document=self.source.document if self.source is not None else None
            if document is None and getattr(self,'source_path',None):
                from .fingerstyle_document import read_document
                document=read_document(self.source_path)
            if document is None:
                if self.source is not None and '.mid' in self.source.name.lower():
                    raise ValueError('MIDI 原谱请在外部编辑，或先应用转译结果再编辑演奏谱')
                score=self.parent.analyze()
                if score is not None:
                    from .fingerstyle_document import FingerstyleDocument
                    document=FingerstyleDocument.from_score(score)
            self.parent.open_fingerstyle_editor(document)
        except (ValueError,OSError) as error:self.feedback.set('原谱编曲无法打开：'+str(error))

    def convert(self):
        if self.searching:
            self.cancel_search();self.feedback.set('已取消转译计算');return
        self.invalidate()
        try:
            if self.source is None:raise ValueError('先选择一份指弹原谱')
            if self.parent.busy:raise ValueError('请先停止演奏')
            if self.source.document is not None:
                self.source=self.source.document.pitch_source(self.rows,self.basis.get().startswith('无 Capo'),self.source.name)
            profile=copy.deepcopy(self.parent.profiles['混合模式'])
            profile.initial_mode='chord' if self.parent.game_mode.get()=='和弦' else 'fret'
            profile.initial_timbre=next(k for k,v in TONE_NAMES.items() if v==self.parent.game_timbre.get())
            if self.parent.profile.mode!='mixed':profile.initial_pages[profile.initial_mode]=int(self.parent.page.get())-1
            args=(self.source,copy.deepcopy(self.rows),profile,self.parent.input_timing,int(self.parent.tap.get())/1000)
            kwargs={'reference':self.basis.get().startswith('无 Capo'),'octaves':self.octaves.get(),
                    'avoid_bm_second':self.avoid_bm_second.get(),
                    'filter_low_bass':self.filter_low_bass.get(),
                    'sustain_notes':self.parent.sustain_notes.get(),'ornaments':self.ornaments.get(),
                    'analyze_voices':True,'accompaniment':next(k for k,v in POLICIES.items() if v==self.accompaniment.get()),
                    'distorted_only':self.distorted_only.get(),
                    'melody_channel':None if self.melody_channel.get()=='自动推断' else int(self.melody_channel.get())-1,
                    'voice_overrides':tuple((at,pitch,role) for (at,pitch),role in sorted(self.voice_overrides.items()))}
            settings=(self.source.score,self.source.chord_marks,self.source.sweeps,args[1:],kwargs)
            if settings!=self.key_settings:
                self.key_comparison=None;self.keys.delete(*self.keys.get_children())
            self.key_settings=settings
            auto=self.transpose.get() in (AUTO_KEY,AUTO_RANGE)
            shifts=AUTO_RANGE_SHIFTS if self.transpose.get()==AUTO_RANGE else range(0,-25,-1)
            semitones=key_shift(self.transpose.get())
            if auto or len(self.source.score.events)>=128:
                self.search_cancel=threading.Event();cancel=self.search_cancel
                self.search_queue=queue.Queue();messages=self.search_queue;self.searching=True
                self.convert_button.configure(text='取消计算');self.feedback.set('正在后台比较整体移调方案，可取消…' if auto else '正在后台转译长曲，可取消…')
                def work():
                    try:
                        voices=analyze_roles(args[0],kwargs['melody_channel'],kwargs['voice_overrides'],cancel)
                        messages.put(('voices',voices))
                        if auto:
                            result=translate_best_key(*args,**kwargs,shifts=shifts,cancel=cancel,progress=lambda done,total,shift:messages.put(('progress',(done,total,shift))))
                        else:
                            result=translate(*args,**kwargs,transpose=semitones,cancel=cancel,
                                             progress=lambda done,total:messages.put(('conversion_progress',(done,total))))
                        messages.put(('done',result))
                    except KeySearchError as error:
                        messages.put(('key_error',{'message':str(error),'candidates':error.candidates}))
                    except Exception as error:messages.put(('error',str(error)))
                threading.Thread(target=work,daemon=True).start()
                self.search_poll=self.after(50,self.poll_search);return
            self.show_voice_review(analyze_roles(args[0],kwargs['melody_channel'],kwargs['voice_overrides']))
            self.show_result(*translate(*args,**kwargs,transpose=semitones))
        except Exception as error:
            self.result=None;self.report=None;self.apply_button.configure(state='disabled');self.feedback.set(translation_error(error))

    def poll_search(self):
        self.search_poll=None
        if not self.searching:return
        try:
            while True:
                kind,data=self.search_queue.get_nowait()
                if kind=='voices':self.show_voice_review(data)
                elif kind=='progress':
                    done,total,shift=data;self.feedback.set(f'正在比较 {done}/{total}：{transpose_name(shift)}… 可取消')
                elif kind=='conversion_progress':
                    done,total=data;self.feedback.set(f'后台转译 {done}/{total} 个起拍… 装饰音可能分轮适配，可取消')
                elif kind=='done':
                    self.cancel_search();self.show_result(*data);return
                elif kind in ('error','key_error'):
                    self.cancel_search()
                    if kind=='key_error':
                        self.failure_current=True
                        candidates=data['candidates']
                        self.key_comparison={'candidates':candidates,'selected':None,
                            'original':next((row for row in candidates if row['transpose_semitones']==0),None),
                            'diagnostics':summarize_failures(candidates)}
                        self.fill_key_rows()
                        self.key_summary.set(f'已比较 {len(candidates)} 个方案，全部未通过；选中后点“定位失败音”核对原音，或保存失败报告。')
                        self.comparison_tabs.select(self.keys.master)
                        data=data['message']
                    self.feedback.set(translation_error(data,candidates=candidates if kind=='key_error' else None));return
        except queue.Empty:pass
        self.search_poll=self.after(50,self.poll_search)

    def show_result(self,result,report):
        self.result,self.report=result,report
        self.tree.delete(*self.tree.get_children())
        self.ornament_tree.delete(*self.ornament_tree.get_children())
        for row in report.get('ornaments',{}).get('changes',[]):
            self.ornament_tree.insert('','end',values=(row['position'],row['kind'],names(row['pitches']),
                row['action'],row['to'],f"{row['shift_ms']:+.1f}"))
        if self.table_timer is not None:self.after_cancel(self.table_timer);self.table_timer=None
        self.fill_note_rows(report)
        self.show_voice_review(report.get('voices',{}).get('rows',[]))
        if 'key_search' in report:self.key_comparison=report['key_search']
        self.fill_key_rows(report['transpose_semitones'])
        original=self.key_comparison.get('original') if self.key_comparison else None
        comparison=''
        if original and original['valid']:
            comparison=f'；原调→当前：省略 {omitted_count(original)}→{omitted_count(report)} 音，切模式 {original["mode_switches"]}→{report["mode_switches"]} 次，翻页 {original["page_moves"]}→{report["page_moves"]} 次'
        self.key_summary.set('当前：'+transpose_name(report['transpose_semitones'])+comparison)
        self.feedback.set(report['summary']);self.apply_button.configure(state='normal')

    def fill_key_rows(self,selected_shift=None):
        self.keys.delete(*self.keys.get_children())
        if self.key_comparison:
            for row in self.key_comparison['candidates']:
                shift=row['transpose_semitones'];selected=shift==selected_shift
                if row['valid']:
                    omitted=omitted_count(row)
                    check=row['timing_warning'] or '通过'
                    values=(transpose_name(shift),f'{row["chord_events"]}/{row["chord_events"]+row["fret_events"]}',omitted,row['mode_switches'],row['page_moves'],check,'当前' if selected else '可选')
                else:values=(transpose_name(shift),'—','—','—','—',row['error'],'不可用')
                self.keys.insert('','end',iid=str(shift),values=values)
                if selected:self.keys.selection_set(str(shift));self.keys.see(str(shift))

    def fill_note_rows(self,report,offset=0):
        self.table_timer=None
        if self.report is not report:return
        end=min(len(report['events']),offset+150)
        for index in range(offset,end):
            row=report['events'][index];marked=row['marked_chord'] or '—'
            if row['target_chord']!=row['marked_chord']:marked+='→'+(row['target_chord'] or '—')
            self.tree.insert('','end',iid=str(index),values=(row['beat'],names(row['source']),names(row['target']),names(row.get('melody_pitches',[row['main_pitch']])),marked,
                '和弦' if row['mode']=='chord' else '品格',row['selector'],
                '上扫1→6' if row['action']=='up' else '下扫6→1' if row['action']=='down' else '+'.join(map(str,row['strings'])),
                names(row['pitches']),names(sorted(set(row['omitted'])|set(row.get('range_filtered',())))),{'reference':'参考','measured':'实测','standard_tuning':'定弦'}[row['basis']],self.input_text(row)))
        if end<len(report['events']):self.table_timer=self.after(1,lambda:self.fill_note_rows(report,end))

    def input_text(self,row):
        profile=self.parent.profile
        if row['action'] in ('up','down'):
            up=row['action']=='up'
            return ('右摇杆↑' if up else '右摇杆↓') if profile.strum_input=='gamepad' else ('鼠标左键' if up else '鼠标右键')
        return '+'.join(str(profile.bindings[string].key) for string in row['strings'])

    def show_voice_review(self,rows):
        if self.voice_timer is not None:self.after_cancel(self.voice_timer);self.voice_timer=None
        self.voice_rows=rows;self.voice_tree.delete(*self.voice_tree.get_children())
        for row in rows:row.setdefault('output','待转译；此处仅为原谱声部推断')
        self.fill_voice_rows(rows)

    def put_voice_row(self,index,row):
        iid=str(index)
        if self.voice_tree.exists(iid):self.voice_tree.move(iid,'',index);return
        self.voice_tree.insert('',index,iid=iid,values=(row['beat'],pitch_name(row['pitch']),
            '+'.join(map(str,row['channels'])) or '—',ROLE_NAMES[row['role']],
            row['strength']+' · '+row['basis'],row['output']))

    def fill_voice_rows(self,rows,offset=0):
        if self.voice_rows is not rows:return
        self.voice_timer=None
        end=min(len(rows),offset+150)
        for index in range(offset,end):
            self.put_voice_row(index,rows[index])
        if end<len(rows):self.voice_timer=self.after(1,lambda:self.fill_voice_rows(rows,end))

    def mark_voice(self):
        selected=self.voice_tree.selection()
        if not selected:self.feedback.set('先在“声部与去留”中选择音符，可按 Ctrl 多选');return
        role=next(k for k,v in ROLE_NAMES.items() if v==self.voice_role.get())
        for index in selected:
            row=self.voice_rows[int(index)];self.voice_overrides[F(row['beat']),row['pitch']]=role
            row.update(role=role,strength='指定',basis='手动指定',output='待重新生成')
            values=list(self.voice_tree.item(index,'values'));values[3]=ROLE_NAMES[role];values[4]='手动指定';values[5]='待重新生成'
            self.voice_tree.item(index,values=values)
        self.invalidate();self.feedback.set('已标记选中音符；重新生成转译预览后生效。主旋律／指定保留不会被伴奏简化或装饰音省略。')

    def clear_voice_marks(self):
        self.voice_overrides={};self.invalidate();self.feedback.set('已清除本窗口的手动声部标记，请重新生成预览')

    def use_key_candidate(self):
        selected=self.keys.selection()
        if not selected:return
        shift=int(selected[0])
        row=next((row for row in (self.key_comparison or {}).get('candidates',()) if row['transpose_semitones']==shift),None)
        if row and not row['valid']:
            self.locate_failure();return
        self.transpose.set(KEY_OPTIONS[0] if shift==0 else f'{"降" if shift<0 else "升"}{abs(shift)}半音');self.convert()

    def locate_failure(self):
        if not self.failure_current:
            self.feedback.set('请先重新生成移调比较，再定位当前设置下的失败音。');return
        selected=self.keys.selection()
        if not selected:self.feedback.set('先在“移调比较”选中一个失败方案，再点“定位失败音”。');return
        shift=int(selected[0])
        candidate=next((r for r in (self.key_comparison or {}).get('candidates',()) if r['transpose_semitones']==shift),None)
        if candidate is None or candidate.get('valid'):
            self.feedback.set('所选方案没有失败音；可点“查看／试用选中移调方案”预览。');return
        detail=candidate.get('failure',{});beat=detail.get('beat')
        pitches={p-shift for p in detail.get('pitches',())}
        matches=[i for i,r in enumerate(self.voice_rows) if r['beat']==beat and r['pitch'] in pitches]
        self.feedback.set(transpose_name(shift)+'：'+candidate['error'])
        if not matches:
            self.feedback.set(self.feedback.get()+'；此限制未对应到单个原音，请查看失败报告中的具体原因。');return
        for index in matches:self.put_voice_row(index,self.voice_rows[index])
        ids=[str(index) for index in matches]
        self.comparison_tabs.select(self.voice_tree.master)
        self.voice_tree.selection_set(ids);self.voice_tree.focus(ids[0]);self.voice_tree.see(ids[0])
        originals=names(sorted(pitches))
        self.feedback.set(self.feedback.get()+f'；已定位原谱 {originals}。请先试听核对，再选择声部并点“标记，重新转译生效”。')

    def audition_score(self,converted):
        if self.source is None:raise ValueError('请先读取原谱')
        if not converted:return self.source.score,dict(self.source.sweeps)
        if self.report is None:raise ValueError('请先生成转译预览')
        rows={F(row['beat']):row for row in self.report['events']};events=[];sweeps={}
        from .guitar import UP,DOWN
        for event in self.result.events:
            row=rows.get(event.start)
            events.append(replace(event,notes=tuple(row['pitches']) if row else ()))
            if row and row['action']!='pick':sweeps[event.start]=UP if row['action']=='up' else DOWN
        return replace(self.result,events=tuple(events),capo=0),sweeps

    def audition(self,converted):
        self.stop_audio()
        try:
            if self.parent.busy:raise ValueError('请先停止其他演奏或试听')
            score,sweeps=self.audition_score(converted)
            self.audio_cancel=threading.Event();cancel=self.audio_cancel
            self.feedback.set('正在生成'+('转译结果' if converted else '原谱')+'参考试听…')
            def work():
                try:
                    from .audio import SoundPreviewBackend
                    from .paths import app_root
                    backend=SoundPreviewBackend(score,app_root()/'.preview',cancel,pitch_events=True,strums=sweeps)
                    with self.audio_lock:
                        if cancel.is_set():backend.close()
                        else:self.audio_queue.put(('ready',(backend,cancel,score.seconds)))
                except Exception as error:self.audio_queue.put(('error',(str(error),cancel)))
            threading.Thread(target=work,daemon=True).start();self.audio_poll=self.after(50,self.poll_audio)
        except ValueError as error:self.feedback.set(str(error))

    def poll_audio(self):
        self.audio_poll=None
        if self.parent.busy:self.stop_audio();return
        try:
            while True:
                kind,data=self.audio_queue.get_nowait()
                if kind=='ready':
                    backend,cancel,seconds=data
                    if cancel is not self.audio_cancel or cancel.is_set():backend.close();continue
                    try:backend.sync_playback(0,False)
                    except Exception as error:backend.close();self.feedback.set('试听失败：'+str(error));continue
                    self.audio_backend=backend
                    self.audio_end=self.after(round((seconds+.2)*1000),self.stop_audio)
                    self.feedback.set('正在播放本地参考试听；可对比原谱与转译，游戏和弦音高仍以实测表为准。')
                elif kind=='error':
                    message,cancel=data
                    if cancel is self.audio_cancel and not cancel.is_set():self.feedback.set('试听失败：'+message)
        except queue.Empty:pass
        self.audio_poll=self.after(50,self.poll_audio)

    def stop_audio(self):
        self.audio_cancel.set()
        for timer in (self.audio_poll,self.audio_end):
            if timer is not None:self.after_cancel(timer)
        self.audio_poll=None;self.audio_end=None
        if self.audio_backend is not None:self.audio_backend.close();self.audio_backend=None
        with self.audio_lock:
            try:
                while True:
                    kind,data=self.audio_queue.get_nowait()
                    if kind=='ready':data[0].close()
            except queue.Empty:pass

    def destroy(self):
        if self.table_timer is not None:self.after_cancel(self.table_timer);self.table_timer=None
        if self.voice_timer is not None:self.after_cancel(self.voice_timer);self.voice_timer=None
        self.cancel_search();self.stop_audio();super().destroy()

    def apply(self):
        if self.result is None:return
        self.stop_audio()
        if self.parent.busy:self.feedback.set('请先停止演奏');return
        editor=self.parent.tab_editor
        if editor is not None and editor.winfo_exists():
            self.feedback.set('请先应用并关闭现有图形编谱窗口，再载入转译结果');return
        if self.parent.dirty and not messagebox.askyesno('应用为新曲谱','主窗口有未保存内容。确认用转译结果替换编辑内容？原文件不会改写。',parent=self):return
        notice='初次转译 '+self.source.name+'：'+self.report['summary']+'。编辑后以当前谱为准。'
        self.parent.accept_visual_score(self.result,self.report['summary'],chord_mode='mixed',origin_notice=notice,source_document=self.source.document)
        self.parent.file_path=None
        self.parent.open_visual_editor();self.destroy()

    def save_report(self):
        report=self.report
        failed=report is None and self.failure_current and self.key_comparison is not None and self.key_comparison['selected'] is None
        if failed:
            report={'kind':'failed_translation','source':self.source.name,'key_search':self.key_comparison,
                    'options':{'transpose':self.transpose.get(),'filter_low_bass':self.filter_low_bass.get(),
                               'melody_pitch_policy':'uniform_transposition_only','melody_channel':self.melody_channel.get()}}
        if report is None:self.feedback.set('请先生成转译预览；即使所有移调方案失败，也可保存失败报告');return
        path=filedialog.asksaveasfilename(parent=self,title='保存转译失败诊断' if failed else '保存逐音转换报告',defaultextension='.json',initialfile='转译失败诊断.json' if failed else '指弹转译报告.json',filetypes=[('JSON','*.json')])
        if path:
            try:Path(path).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');self.feedback.set('已保存转译失败诊断；含各方案首个阻碍，可用于定位问题' if failed else '已保存转换报告')
            except OSError as error:self.feedback.set('保存失败：'+str(error))

    def build_table(self,frame):
        top=ttk.Frame(frame);top.pack(fill='x')
        self.tone=tk.StringVar(value='清音')
        box=ttk.Combobox(top,textvariable=self.tone,values=tuple(TONE_NAMES.values()),state='readonly',width=8)
        box.pack(side='left');box.bind('<<ComboboxSelected>>',lambda event:self.refresh_table())
        ttk.Label(top,text='表中从左到右为吉他 1→6 弦。? 未知，x 静音；参考值可以逐项修正。').pack(side='left',padx=12)
        ttk.Button(top,text='导入表',command=self.import_table).pack(side='right')
        ttk.Button(top,text='导出表',command=self.export_table).pack(side='right',padx=6)
        self.table=ttk.Treeview(frame,columns=(*map(str,range(1,7)),'state'),show='tree headings',height=11)
        self.table.heading('#0',text='和弦');self.table.column('#0',width=95)
        for s in range(1,7):self.table.heading(str(s),text=f'{s} 弦 / 键 {7-s}');self.table.column(str(s),width=100)
        self.table.heading('state',text='依据');self.table.column('state',width=190)
        self.table.pack(fill='both',expand=True,pady=10);self.table.bind('<<TreeviewSelect>>',lambda event:self.select_row())
        form=ttk.Frame(frame);form.pack(fill='x')
        self.values=[tk.StringVar() for _ in range(6)]
        for s,value in enumerate(self.values,1):
            ttk.Label(form,text=f'{s} 弦').pack(side='left',padx=(6,3))
            ttk.Entry(form,textvariable=value,width=7).pack(side='left')
        self.measured=tk.BooleanVar(value=False)
        ttk.Checkbutton(frame,text='此和弦的六弦音高已在游戏逐弦核对（包含静音弦）',variable=self.measured).pack(anchor='w',pady=8)
        buttons=ttk.Frame(frame);buttons.pack(fill='x')
        ttk.Button(buttons,text='保存此和弦',command=self.save_row,style='Accent.TButton').pack(side='left')
        ttk.Button(buttons,text='查看此和弦逐弦采样谱',command=self.sampling_score).pack(side='left',padx=8)
        ttk.Label(buttons,text='音名可写 G4、F#3、Bb2，也可填 MIDI 数字。').pack(side='left')
        self.refresh_table()

    @property
    def tone_key(self):return next(k for k,v in TONE_NAMES.items() if v==self.tone.get())

    def refresh_table(self):
        selected=self.table.selection();name=selected[0] if selected else 'G'
        self.table.delete(*self.table.get_children());tone=self.tone_key;refs=reference_voicings(tone)
        for key in CHORD_NAMES:
            item=self.rows.get((tone,key),refs[key])
            state='游戏实测' if item.measured and item.complete else '部分未知' if not item.complete else '参考指法 · 未实测'
            if key=='Bm':state+='；2弦可临时规避'
            self.table.insert('','end',iid=key,text=key,values=(*(format_pitch(p) for p in item.pitches),state))
        self.table.selection_set(name);self.select_row()

    def select_row(self):
        if not self.table.selection():return
        name=self.table.selection()[0];tone=self.tone_key
        item=self.rows.get((tone,name),reference_voicings(tone)[name])
        for value,pitch in zip(self.values,item.pitches):value.set(format_pitch(pitch))
        self.measured.set(item.measured)

    def save_row(self):
        if not self.table.selection():return
        try:
            name=self.table.selection()[0]
            item=Voicing(name,self.tone_key,tuple(parse_pitch(v.get()) for v in self.values),self.measured.get())
            if item.measured and not item.complete:raise ValueError('标记实测前请补全六根弦，静音写 x')
            updated={**self.rows,(item.tone,item.name):item}
            if self.table_path:write_voicings(self.table_path,updated)
            self.rows=updated;self.refresh_table()
            self.invalidate();self.key_comparison=None;self.keys.delete(*self.keys.get_children())
            self.feedback.set('已保存 '+name+' 的弦音；请重新生成转译预览')
        except (OSError,ValueError) as error:self.feedback.set('音高表保存失败：'+str(error))

    def import_table(self):
        path=filedialog.askopenfilename(parent=self,title='导入和弦音高表',filetypes=[('JSON','*.json')])
        if not path:return
        try:
            updated=read_voicings(path)
            if self.table_path:write_voicings(self.table_path,updated)
            self.rows=updated;self.refresh_table()
            self.invalidate();self.key_comparison=None;self.keys.delete(*self.keys.get_children())
            self.feedback.set('已导入音高表；请重新生成转译预览')
        except Exception as error:self.feedback.set('音高表导入失败：'+str(error))

    def export_table(self):
        path=filedialog.asksaveasfilename(parent=self,title='导出和弦音高表',initialfile='和弦音高表.json',defaultextension='.json',filetypes=[('JSON','*.json')])
        if not path:return
        rows={(tone,name):item for tone in ('clean','distorted') for name,item in reference_voicings(tone).items()}
        try:write_voicings(path,{**rows,**self.rows});self.feedback.set('已导出音高表，参考与实测状态一并保留')
        except OSError as error:self.feedback.set('导出失败：'+str(error))

    def sampling_score(self):
        if not self.table.selection():return
        editor=self.parent.tab_editor
        if editor is not None and editor.winfo_exists():self.feedback.set('请先关闭现有图形编谱窗口');return
        name=self.table.selection()[0]
        score=replace(parse_guitar('{'+name+'} '+' '.join(f'{s}:2' for s in range(1,7))+' 0:2',100),default_timbre=self.tone_key)
        from .chord_editor import VisualChordEditor
        VisualChordEditor(self.parent,score,lambda score:self.parent.accept_visual_score(score,chord_mode=True))
        self.feedback.set('采样谱已打开：依次拨吉他 1→6 弦（游戏键 6→1），每弦 1.2 秒；应用后可进入游戏演奏并核对音高')
