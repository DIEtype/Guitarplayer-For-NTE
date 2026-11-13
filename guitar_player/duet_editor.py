"""One TAB work surface for accompaniment and melody; one shared time axis."""
import copy
from dataclasses import replace
from fractions import Fraction as F
import math
from pathlib import Path
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk,filedialog

from .duet_composition import ROLES,TRACK_NAMES,DuetTrackDocument,prepare_documents,sync_clock,clock_matches,combine_sources,write_composition,read_composition
from .duet_project import DuetPart
from .fingerstyle_editor import VisualFingerstyleEditor
from .fingerstyle_document import read_document
from .measures import TONE_NAMES,crop_from_measure

COLORS={'recorded':'#326da8','live':'#0e7380'}
SCALE=.78;TRACK_HEIGHT=224;PAIR_HEIGHT=TRACK_HEIGHT*2+16


class DuetEditor(VisualFingerstyleEditor):
    document_type=DuetTrackDocument
    def __init__(self,workspace,role='live',document=None,project=None):
        self.workspace=workspace;self.project=project or workspace.project;self.staged=project is not None
        self.documents=prepare_documents(self.project);self.role=role
        if document is not None:self.documents[role]=DuetTrackDocument.from_document(document)
        bars=max(doc.bars for doc in self.documents.values())
        for doc in self.documents.values():doc.bars=bars
        self.surfaces={};self.draw_scores={};self.visible_tracks=set()
        super().__init__(workspace.app,self.documents[role],lambda _source:None,on_direct=lambda _doc:None)
        # The single-track constructor copies its input; the duet surface must
        # edit the same document instances used by saving and undo snapshots.
        self.doc=self.documents[self.role]
        self.title('双轨吉他编曲 · 录音伴奏与主旋律 · by Akari')
        self.geometry(f'{min(1460,self.winfo_screenwidth()-60)}x{min(960,self.winfo_screenheight()-80)}')
        self.minsize(min(1020,self.winfo_screenwidth()-60),min(720,self.winfo_screenheight()-90))
        self.base_state=self.track_state();self.file_path=None
        self.feedback.set('点击弦线切换轨道。想在游戏里只弹这一轨，点“5 秒后演奏当前轨”。两轨各有音色时，可在“自动编配双轨…”选择“保留原轨与音色”。')

    def _build(self):
        super()._build()
        heading=self.credit_label.master
        self.track_caption=tk.StringVar(value='双轨工作区')
        ttk.Label(heading,textvariable=self.track_caption,foreground=COLORS[self.role]).pack(side='left',padx=12)
        self.apply_button.configure(text='应用两轨并返回合奏')
        self.translate_action_button.configure(text='自动编配双轨…',command=self.send_to_translator)
        tracks=ttk.Frame(self.workspace_shell,padding=(12,3));tracks.pack(fill='x',before=self.workspace_body)
        self.track_buttons={}
        for role in ROLES:
            button=ttk.Button(tracks,text=TRACK_NAMES[role],command=lambda r=role:self.switch_track(r),
                              style='Workspace.Source.TButton' if role=='recorded' else 'Workspace.Compose.TButton')
            button.pack(side='left',padx=(0,8));self.track_buttons[role]=button
        self.preview_choice=tk.StringVar(value='两轨合成')
        ttk.Combobox(tracks,textvariable=self.preview_choice,values=('两轨合成','仅当前轨'),state='readonly',width=10).pack(side='left',padx=8)
        self.solo_button=ttk.Button(tracks,text='5 秒后演奏当前轨',command=self.play_current_track,style='Workspace.Compose.TButton')
        self.solo_button.pack(side='left',padx=4)
        self.lead_button=ttk.Button(tracks,text='两轨前加空小节',command=self.add_lead_bar)
        self.lead_button.pack(side='right')
        for child in heading.winfo_children():
            if isinstance(child,ttk.Button):
                if child.cget('text')=='新建':child.configure(text='清空当前轨')
                elif child.cget('text')=='打开原谱…':child.configure(text='打开／替换轨道…')

    def initial_focus(self):
        if self.winfo_viewable() and not self.attributes('-disabled'):self.canvas.focus_set()

    def track_state(self):return tuple(self.documents[r].snapshot() for r in ROLES)

    def snapshot(self):return self.track_state(),self.role,self.at,self.string,self.value,self.dotted,self.triplet

    @property
    def changed(self):return self.track_state()!=self.base_state

    def restore(self,state):
        docs,self.role,self.at,self.string,self.value,self.dotted,self.triplet=state
        for role,data in zip(ROLES,docs):self.documents[role].restore(data)
        self.doc=self.documents[self.role];self.doc.focus_string=self.string;self.typed='';self.load_variables();self.refresh()

    def load_variables(self):
        self.bpm.set(f'{self.doc.bpm:g}');self.numerator.set(str(self.doc.meter[0]));self.denominator.set(str(self.doc.meter[1]))
        self.timbre.set(TONE_NAMES[self.doc.default_timbre]);self.capo.set(str(self.doc.capo))

    def edit(self,operation):
        before=self.snapshot();self.stop_preview()
        try:
            operation();sync_clock(self.documents,self.role)
            if self.track_state()!=before[0]:self.undo_stack.append(before);self.undo_stack=self.undo_stack[-150:];self.redo_stack.clear()
            self.refresh();return True
        except (ValueError,ZeroDivisionError) as error:
            self.restore(before);self.feedback.set(str(error));return False

    def switch_track(self,role):
        if role==self.role:return True
        if not self.settings_changed():return False
        self.stop_preview();self.role=role;self.doc=self.documents[role];self.doc.focus_string=self.string
        self.typed='';self.typed_location=None;self.bar_selection=None;self.load_variables();self.refresh();self.reveal_cursor();return True

    def draw(self,width=None):
        if not hasattr(self,'canvas') or self.closed:return
        from .score_view import score_view
        c=self.canvas;views={r:score_view(self,d,r) for r,d in self.documents.items()}
        width=max(420,width or c.winfo_width());columns=max(1,min(3,width//450))
        key=(tuple(views.values()),width,self.role,self.bar_selection)
        if getattr(self,'_render_key',None)==key:
            self.bar_boxes=self.surfaces[self.role]
            self.draw_cursor();return
        self._render_key=key;self.draw_views=views
        bars=max(d.bars for d in self.documents.values())
        geometry=(width,bars)
        if getattr(self,'_bar_geometry',None)!=geometry:
            self._bar_geometry=geometry;c.delete('all');self.visible_tracks=set();self._bar_render_keys={}
            bar_width=(width-40-(columns-1)*24)/columns;self.draw_columns=columns
            self.surfaces={role:[(20+(i%columns)*(bar_width+24),25+(i//columns)*PAIR_HEIGHT+j*TRACK_HEIGHT,bar_width)
                                 for i in range(bars)] for j,role in enumerate(ROLES)}
            c.configure(scrollregion=(0,0,width,40+math.ceil(bars/columns)*PAIR_HEIGHT))
        self.bar_boxes=self.surfaces[self.role];self.draw_scores={r:v.score for r,v in views.items()}
        self.draw_score=self.draw_scores[self.role];self.draw_tempos=views[self.role].tempos;self.draw_cells=views[self.role]
        self.draw_visible();self.draw_playhead()
        if hasattr(self,'track_caption'):self.track_caption.set('正在编辑：'+TRACK_NAMES[self.role])
        from .composer_workspace import update_context
        update_context(self)

    def draw_visible(self):
        self.visible_id=None
        if self.closed or not self.surfaces:return
        top=self.canvas.canvasy(0);height=max(300,self.canvas.winfo_height())
        first=max(0,int((top-25)//PAIR_HEIGHT)-1)*self.draw_columns
        last=min(len(self.surfaces[self.role]),(int((top+height-25)//PAIR_HEIGHT)+2)*self.draw_columns)
        wanted={(role,index) for role in ROLES for index in range(first,last)}
        keys={ (role,index):self.draw_views[role].bar_key(self.documents[role],index,self.bar_selection)
               for role,index in wanted if index<self.documents[role].bars }
        wanted=set(keys)
        dirty={key for key in wanted if self._bar_render_keys.get(key)!=keys[key]}
        for role,index in (self.visible_tracks-wanted)|(self.visible_tracks&dirty):self.canvas.delete(f'track_{role}_{index}')
        active=self.doc;at=self.at
        try:
            for role,index in sorted((wanted-self.visible_tracks)|dirty):
                self.doc=self.documents[role];self.draw_score=self.draw_scores[role]
                self.draw_tempos=self.draw_views[role].tempos;self.draw_cells=self.draw_views[role]
                left,y,width=self.surfaces[role][index];tag=f'track_{role}_{index}'
                self.canvas.create_rectangle(left,y-15,left+width,y+TRACK_HEIGHT-14,fill='white',outline='#d8e1e8',tags=tag)
                self.canvas.create_rectangle(left,y-15,left+4,y+TRACK_HEIGHT-14,fill=COLORS[role],outline='',tags=tag)
                self.canvas.create_text(left+12,y-6,text=TRACK_NAMES[role],anchor='w',fill=COLORS[role],font=('Microsoft YaHei UI',9,'bold'),tags=tag)
                staff_before=len(self.canvas.find_all())
                VisualFingerstyleEditor.draw_bar(self,index,left/SCALE,(y+8)/SCALE,width/SCALE)
                for item in self.canvas.find_all()[staff_before:]:
                    self.canvas.scale(item,0,0,SCALE,SCALE)
                    self.canvas.addtag_withtag(tag,item)
                    if self.canvas.type(item)=='text':
                        raw=self.canvas.itemcget(item,'font');cache=getattr(self,'_scaled_fonts',None)
                        if cache is None:cache=self._scaled_fonts={}
                        if raw not in cache:
                            options=tkfont.Font(root=self,font=raw).actual()
                            cache[raw]=(options['family'],max(7,round(options['size']*SCALE)),options['weight'],options['slant'])
                        self.canvas.itemconfigure(item,font=cache[raw])
        finally:
            self.doc=active;self.at=at;self.suppress_cursor=False
            self.draw_score=self.draw_scores[self.role];self.draw_tempos=self.draw_views[self.role].tempos;self.draw_cells=self.draw_views[self.role]
        self._bar_render_keys=keys;self.visible_tracks=wanted;self.canvas.tag_raise('playhead');self.draw_cursor()

    def draw_cursor(self):
        if self.closed or not self.surfaces:return
        index=self.doc.bar_index(self.at)
        if index>=len(self.surfaces[self.role]):return
        left,top,width=self.surfaces[self.role][index]
        self.draw_cursor_at(left,top+8,width,SCALE)

    def click(self,event):
        absolute=self.canvas.canvasy(event.y)
        for role,index in sorted(self.visible_tracks):
            left,y,width=self.surfaces[role][index]
            if left<=event.x<=left+width and y-15<=absolute<y+TRACK_HEIGHT-14:
                if not self.switch_track(role):return
                self.click_bar(index,(left/SCALE,(y+8)/SCALE,width/SCALE),event.x/SCALE,absolute/SCALE,getattr(event,'state',0))
                return

    def reveal_cursor(self):
        if not self.surfaces:return
        index=self.doc.bar_index(self.at);_,top,_=self.surfaces[self.role][index]
        y=top+8+(55+(self.string-1)*22)*SCALE;visible=self.canvas.canvasy(0);height=self.canvas.winfo_height()
        total=float(self.canvas.cget('scrollregion').split()[3])
        if y<visible+20:self.canvas.yview_moveto(max(0,(y-30)/total))
        elif y>visible+height-30:self.canvas.yview_moveto(max(0,(y-height+40)/total))

    def draw_playhead(self):
        self.canvas.delete('playhead')
        if self.play_at is None:return
        for role in ROLES:
            doc=self.documents[role];index=doc.bar_index(self.play_at)
            if not self.surfaces.get(role) or index>=len(self.surfaces[role]):continue
            left,top,width=self.surfaces[role][index];bar=doc.bar(index)
            x=left+58*SCALE+float(self.play_at-bar.start)/float(bar.beats)*(width-78*SCALE)
            self.canvas.create_line(x,top+36*SCALE,x,top+246*SCALE,fill='#b93270',width=2,tags='playhead')

    def insert_bar(self,after=False):
        index=self.doc.bar_index(self.at)+int(after)
        if self.edit(lambda:[d.insert_bar(index) for d in self.documents.values()]):self.select_cursor(self.doc.bar(index).start)

    def add_bar(self):
        index=max(d.bars for d in self.documents.values())
        if self.edit(lambda:[d.insert_bar(index) for d in self.documents.values()]):self.select_cursor(self.doc.bar(index).start)

    def add_lead_bar(self):
        previous=self.at;length=self.doc.bar(0).beats
        if self.edit(lambda:[d.insert_bar(0) for d in self.documents.values()]):
            self.select_cursor(previous+length)
            self.feedback.set('两轨前已同步加入一小节休止，用于开录／导入后的选区准备；原有两轨仍保持对齐。')

    def remove_bar(self):
        index=self.doc.bar_index(self.at)
        if self.edit(lambda:[d.remove_empty_bar(index) for d in self.documents.values()]):self.select_cursor(self.doc.bar(min(index,self.doc.bars-1)).start)

    def paste_selection(self,insert=False):
        if not insert:return super().paste_selection(False)
        from .composition import paste_measures
        if not self.measure_clipboard:self.feedback.set('请先复制小节，再插入粘贴。');return 'break'
        index=self.doc.bar_index(self.at);other=self.documents['live' if self.role=='recorded' else 'recorded']
        def change():
            paste_measures(self.doc,self.measure_clipboard,index,True)
            for _ in self.measure_clipboard:other.insert_bar(index)
        self.edit(change);return 'break'

    def preview_segment(self,from_start=False):
        if not self.settings_changed():raise ValueError(self.feedback.get())
        docs=self.documents if self.preview_choice.get()=='两轨合成' else {role:(doc if role==self.role else DuetTrackDocument.from_document(doc)) for role,doc in self.documents.items()}
        if self.preview_choice.get()!='两轨合成':docs['live' if self.role=='recorded' else 'recorded'].cells=[]
        source,options,_=combine_sources(docs,self.voice_rows())
        index=0 if from_start else self.doc.bar_index(self.at)
        from .composition import materialize_repeats
        linear,order=materialize_repeats(self.doc);expanded=order.index(index)
        self.preview_order=order if order!=list(range(self.doc.bars)) else None
        self.preview_layout=linear.layout;self.preview_starts=tuple(bar.start for bar in linear.layout)
        score,self.preview_offset=crop_from_measure(source.score,expanded)
        options['strums']={(at-self.preview_offset,role):v for (at,role),v in options['strums'].items() if at>=self.preview_offset}
        options['event_timbres']={(at-self.preview_offset,role):v for (at,role),v in options['event_timbres'].items() if at>=self.preview_offset}
        return score,score,options,index

    def voice_rows(self):
        from .voicings import read_voicings
        return read_voicings(self.parent.settings_dir/'voicings.json') if self.parent.settings_dir else {}

    def play_current_track(self):
        from .direct_score import compile_document
        from .guitar import make_plan

        app=self.parent
        if app.busy:
            self.feedback.set('请先停止当前演奏，再演奏当前轨。');return
        if not self.settings_changed():return
        if not any(cell.notes for cell in self.doc.cells):
            self.feedback.set('当前轨没有音符。请点击另一条轨道的弦线，或先写入音符。');return
        self.stop_preview()
        name=TRACK_NAMES[self.role]
        part=self.project.parts[self.role]
        try:
            rows=self.voice_rows()
        except (OSError,ValueError) as error:
            self.feedback.set('读取和弦音高表失败：'+str(error));return
        if not self.doc.matches_part(part):
            try:part=DuetPart(name,compile_document(self.doc,rows))
            except ValueError as error:
                self.offer_solo_translation(rows,name,error);return
        try:
            profile=app.part_playback_profile(part,solo=True)
            tap=int(app.tap.get())/1000
        except ValueError as error:
            self.feedback.set(str(error));return
        try:
            make_plan(part.score,profile,tap=tap,timing=app.input_timing,sustain_notes=app.sustain_notes.get())
        except ValueError as error:
            self.offer_solo_translation(rows,name,error);return
        app.start_solo(part,self.feedback)

    def offer_solo_translation(self,rows,name,error):
        try:source=self.doc.pitch_source(rows,True,name)
        except ValueError as source_error:
            self.feedback.set('当前轨无法转译：'+str(source_error));return
        window=self.parent.open_translator()
        if window is None:return
        window.set_source(source)
        window.feedback.set(f'{name}需要近似转译。点“生成转译预览”，再点“5 秒后演奏此轨”。\n{error}')
        from .navigation import navigate
        navigate(self,window)

    def keep_draft(self):
        for role,doc in self.documents.items():self.project.documents[role]=copy.deepcopy(doc)
        if not self.staged:self.workspace.persist_project()

    def apply(self,close=False):
        from .direct_score import compile_document
        if self.parent.busy:self.feedback.set('请先停止游戏演奏，再应用双轨修改。');return False
        if not self.settings_changed():return False
        try:
            if not clock_matches(*[self.documents[r] for r in ROLES]):raise ValueError('两轨 BPM 或拍号不一致，请先统一音乐时间轴')
            parts={};rows=self.voice_rows()
            for role,doc in self.documents.items():
                old=self.project.parts[role]
                if old is not None and doc.matches_part(old):parts[role]=old;continue
                if not any(c.notes for c in doc.cells):parts[role]=None;continue
                try:score=compile_document(doc,rows)
                except ValueError as error:raise ValueError(TRACK_NAMES[role]+'不能直接演奏：'+str(error)+'。原谱可保存；点右下角“自动编配双轨…”另行生成演奏谱。') from error
                parts[role]=DuetPart(old.name if old else TRACK_NAMES[role],score,old.source if old else '双轨编谱')
            previous=self.workspace.project.parts['recorded'];new=parts['recorded']
            changed=(previous.fingerprint if previous else '')!=(new.fingerprint if new else '')
            if changed:
                # Clear the persisted code too: reopening the workspace must not
                # associate a previous recording with the edited accompaniment.
                self.workspace.write_settings(replace(self.workspace.settings(),code=''))
                self.workspace.code.set('')
            for role,part in parts.items():
                self.project.set_part(role,part)
                if part:self.documents[role].mark_base(part)
            self.keep_draft()
            if self.staged:
                for role,part in parts.items():
                    self.workspace.project.set_part(role,part);self.workspace.project.documents[role]=copy.deepcopy(self.documents[role])
            self.workspace.persist_project();self.workspace.refresh_project();self.base_state=self.track_state()
            message='两轨已应用；'+('录音伴奏已改变，请重新录制并填写新录音码。' if changed else '可在右下角开始录制或合奏。')
            self.feedback.set(message)
            if close:
                self.destroy();self.workspace.show_overview(message)
            return True
        except (ValueError,OSError) as error:self.feedback.set(str(error));return False

    def save_file(self,path=None):
        if not self.settings_changed():return
        path=path or self.file_path or filedialog.asksaveasfilename(parent=self,title='保存双轨原谱',defaultextension='.gscore',initialfile='双轨原谱.gscore',filetypes=[('双轨原谱','*.gscore')])
        if not path:return
        try:
            self.keep_draft();write_composition(path,self.project);self.file_path=Path(path);self.base_state=self.track_state()
            self.feedback.set('双轨原谱已保存：'+self.file_path.name+'；两轨与独立时值一并保留。');return self.file_path
        except (ValueError,OSError) as error:self.feedback.set('双轨原谱未保存：'+str(error))

    def open_file(self,path=None):
        from .guitar_pro import EXTENSIONS, FILE_FILTER
        path=path or filedialog.askopenfilename(parent=self,title='打开曲谱',filetypes=[('曲谱',f'*.gscore *.tab *.txt {FILE_FILTER[1]}'),FILE_FILTER])
        if not path:return
        if Path(path).suffix.lower() in EXTENSIONS:return self.parent.import_guitar_pro(path,editor=self)
        try:
            import json
            data=json.loads(Path(path).read_text(encoding='utf-8-sig')) if Path(path).suffix.lower()=='.gscore' else None
            if isinstance(data,dict) and data.get('kind')=='duet_composition':
                project=read_composition(path);docs=prepare_documents(project)
                if not self.edit(lambda:self.documents.update(docs)):return
                self.project=project;self.staged=True
            else:
                return self.replace_current_track(read_document(path))
            self.doc=self.documents[self.role];self.file_path=Path(path)
            self.load_variables();self.select_cursor(0,1)
        except (OSError,ValueError,TypeError,KeyError) as error:self.feedback.set('原谱未打开：'+str(error))

    def replace_current_track(self,document,*,keep_clock=False):
        doc=DuetTrackDocument.from_document(document)
        def change():
            self.documents[self.role]=doc;self.doc=doc
            if keep_clock:sync_clock(self.documents,'live' if self.role=='recorded' else 'recorded')
        if not self.edit(change):return False
        self.file_path=None
        self.load_variables();self.select_cursor(0,1)
        return True

    def send_to_translator(self):
        if not self.settings_changed():return
        try:source,_,lead=combine_sources(self.documents,self.voice_rows());self.keep_draft()
        except (ValueError,OSError) as error:self.feedback.set('未开始编配：'+str(error));return
        try:
            dialog=self.workspace.open_splitter()
            if dialog is None:raise ValueError(self.workspace.feedback.get())
            dialog.use_duet_source(source,lead)
            from .navigation import suppress_return,present
            suppress_return(self);self.destroy();present(dialog)
        except (ValueError,OSError) as error:self.feedback.set('未进入自动编配：'+str(error))

    def close(self):
        if not self.settings_changed():return
        try:
            self.keep_draft();self.workspace.refresh_project();self.destroy();self.workspace.show_overview()
        except OSError as error:self.feedback.set('双轨草稿未保存：'+str(error))

    def destroy(self):
        super().destroy()
