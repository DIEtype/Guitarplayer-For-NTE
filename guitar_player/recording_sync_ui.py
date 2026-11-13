"""Two independent score lanes, one recording-code-driven primary action."""
from dataclasses import replace
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog
from .recording_sync import POINTS, RECORD_POINTS, ALL_POINTS
from .duet_project import DuetPart, write_project
from .widgets import ScrollableStatusBanner
from .duet_library import library_for_app

BLUE='#326da8';TEAL='#0e7380';BG='#edf2f6';INK='#20394e';MUTED='#62798d'


class RecordingSyncDialog(tk.Toplevel):
    def __init__(self,app):
        super().__init__(app);self.app=app;self.project=app.duet_project
        self.title('录音合奏 · 伴奏与现场声部');self.transient(app)
        width=min(1120,self.winfo_screenwidth()-60);height=min(940,self.winfo_screenheight()-80)
        self.geometry(f'{width}x{height}');self.minsize(min(900,width),min(700,height))
        self.working=app.recording_sync_settings;self.pending_pick=None;self.watch_timer=None;self.code_trace=None
        self.library=library_for_app(app);self.saved_items=[];self.metadata_timer=None;self.metadata_dirty=False
        self.was_busy=False;self.built=False;self.last_code=''
        initial_code=self.project.bound_code if self.project.archive_id else self.working.code
        self.code=tk.StringVar(value=initial_code);self.values={};self.part_names={};self.part_info={};self.part_buttons=[];self.time_controls=[]
        self.feedback=tk.StringVar(value=app.recording_sync_notice or app.duet_project_notice or
            '先指定两份声部；可直接查看、编辑分离谱。没有录音码时右下角自动录制伴奏，填码后自动合奏。')
        ttk.Label(self,text='录音伴奏 + 现场声部',font=('Microsoft YaHei UI',18,'bold'),padding=(16,10,16,6)).pack(anchor='w')
        sources=ttk.Frame(self,padding=(16,0,16,6));sources.pack(fill='x')
        self.split_button=ttk.Button(sources,text='自动拆分 MIDI／指弹原谱…',command=self.open_splitter,style='Workspace.Source.TButton')
        self.split_button.pack(side='left',padx=(0,8));self.split_window=None
        self.compose_button=ttk.Button(sources,text='双轨编谱 · 伴奏＋主旋律',command=self.open_duet_editor,style='Workspace.Compose.TButton')
        self.compose_button.pack(side='left',padx=(0,8))
        self.open_composition_button=ttk.Button(sources,text='打开双轨原谱…',command=self.open_composition);self.open_composition_button.pack(side='left')
        saved=ttk.Frame(self,padding=(16,0,16,6));saved.pack(fill='x')
        ttk.Label(saved,text='已保存合奏').pack(side='left')
        self.saved_choice=tk.StringVar();self.saved_box=ttk.Combobox(saved,textvariable=self.saved_choice,state='readonly',postcommand=self.refresh_saved,width=35)
        self.saved_box.pack(side='left',fill='x',expand=True,padx=8)
        self.saved_box.bind('<<ComboboxSelected>>',lambda _e:self.load_saved())
        self.save_song_button=ttk.Button(saved,text='保存当前合奏',command=self.save_current);self.save_song_button.pack(side='left')
        self.folder_button=ttk.Button(saved,text='打开保存目录',command=self.open_saved_folder);self.folder_button.pack(side='left',padx=(8,0))
        from .duet_controls import DuetGameControls
        self.game_controls=DuetGameControls(self,app,self.feedback);self.game_controls.pack(fill='x',padx=4,pady=(0,5))
        lanes=ttk.Frame(self,padding=(16,0,16,6));lanes.pack(fill='x');lanes.columnconfigure(0,weight=1,uniform='duet');lanes.columnconfigure(1,weight=1,uniform='duet')
        for column,(role,title,color) in enumerate((('recorded','录音伴奏 · 由游戏重播',BLUE),('live','现场声部 · 由程序演奏',TEAL))):
            panel=tk.Frame(lanes,bg='white',highlightthickness=1,highlightbackground=color)
            panel.grid(row=0,column=column,sticky='nsew',padx=(0,8) if not column else (0,0))
            tk.Label(panel,text=title,bg=color,fg='white',font=('Microsoft YaHei UI',10,'bold'),anchor='w',padx=10,pady=4).pack(fill='x')
            name=tk.StringVar(value='未选择曲谱');info=tk.StringVar();self.part_names[role]=name;self.part_info[role]=info
            label=tk.Label(panel,textvariable=name,bg='white',fg=INK,font=('Microsoft YaHei UI',11,'bold'),anchor='w',justify='left',padx=10,pady=3,width=1,height=1)
            label.pack(fill='x');label.bind('<Configure>',lambda e,w=label:w.configure(wraplength=max(150,e.width-20)))
            detail=tk.Label(panel,textvariable=info,bg='white',fg=MUTED,font=('Microsoft YaHei UI',9),anchor='w',justify='left',padx=10,width=1,height=2)
            detail.pack(fill='x');detail.bind('<Configure>',lambda e,w=detail:w.configure(wraplength=max(150,e.width-20)))
            row=ttk.Frame(panel);row.pack(fill='x',padx=8,pady=6)
            for text,command in (('选谱…',lambda r=role:self.choose_part(r)),('使用当前谱',lambda r=role:self.use_current(r)),
                                 ('查看／编辑',lambda r=role:self.open_duet_editor(r)),('试听',lambda r=role:self.preview_part(r))):
                button=ttk.Button(row,text=text,command=command,padding=(8,3),width=0);button.pack(side='left',padx=(0,6));self.part_buttons.append(button)
        row=ttk.Frame(self,padding=(16,0,16,4));row.pack(fill='x')
        ttk.Label(row,text='游戏录音码').pack(side='left')
        self.code_entry=ttk.Entry(row,textvariable=self.code,font=('Consolas',11));self.code_entry.pack(side='left',fill='x',expand=True,padx=8)
        self.associate_button=ttk.Button(row,text='关联所选伴奏',command=self.associate_code,padding=(8,3));self.associate_button.pack(side='right')
        self.recording_link=tk.StringVar();ttk.Label(self,textvariable=self.recording_link,foreground=BLUE,padding=(16,0,16,6)).pack(anchor='w')
        footer=ttk.Frame(self,padding=(14,8));footer.pack(side='bottom',fill='x')
        self.start_button=ttk.Button(footer,command=self.primary_action,style='Accent.TButton');self.start_button.pack(side='right')
        self.capture_button=self.start_button
        self.save_button=ttk.Button(footer,text='保存设置',command=self.save);self.save_button.pack(side='right',padx=8)
        ttk.Button(footer,text='关闭',command=self.destroy).pack(side='left')
        self.banner=ScrollableStatusBanner(self,self.feedback,lines=2);self.banner.pack(side='bottom',fill='x',padx=16,pady=(0,3))
        self.tabs=ttk.Notebook(self);self.tabs.pack(fill='both',expand=True,padx=16,pady=(0,5))
        overview=self.tab('合奏／听感对齐');positions=self.tab('位置校准')
        self.overview_tab=overview;self.capture_tab=positions;self.setup_tab=positions;self.demo_tab=overview
        ttk.Label(overview,text='听感对齐 · 调整现场声部的整体早晚',font=('Microsoft YaHei UI',12,'bold')).pack(anchor='w',pady=(0,6))
        self.split_info=tk.StringVar()
        self.point_labels={};self.pick_buttons={};self.record_pick_buttons={};self.size_label=tk.StringVar()
        ttk.Label(positions,text='自动录制 · 开始与停止',font=('Microsoft YaHei UI',11,'bold')).pack(anchor='w')
        self.build_points(positions,RECORD_POINTS,self.record_pick_buttons)
        ttk.Separator(positions).pack(fill='x',pady=8)
        ttk.Label(positions,text='重播合奏 · 播放、填码与导入',font=('Microsoft YaHei UI',11,'bold')).pack(anchor='w')
        self.build_points(positions,POINTS,self.pick_buttons)
        ttk.Label(positions,textvariable=self.size_label,foreground=MUTED).pack(anchor='w',pady=6)
        ttk.Label(positions,text='先显示游戏中的按钮，点“3 秒后拾取”，切回游戏并把鼠标停在按钮中心，等待即可。\n停止按钮可在临时录制时拾取，完成后手动结束该次录音。导入框内按钮拾取后关闭弹窗。\n坐标只与游戏窗口布局有关；尺寸或布局改变后重新拾取。',wraplength=960,foreground=MUTED).pack(anchor='w',pady=4)
        ttk.Separator(overview).pack(fill='x',pady=8)
        self.time_row(overview,'score_offset_ms','现场与录音的听感偏移',-60000,60000)
        self.offset_total=tk.StringVar();ttk.Label(overview,textvariable=self.offset_total,foreground=TEAL).pack(anchor='w')
        adjust=ttk.Frame(overview);adjust.pack(fill='x',pady=5);self.offset_buttons=[]
        for label,delta in (('现场早 50 ms',-50),('早 10 ms',-10),('晚 10 ms',10),('现场晚 50 ms',50)):
            button=ttk.Button(adjust,text=label,command=lambda n=delta:self.adjust_offset(n));button.pack(side='left',padx=(0,8));self.offset_buttons.append(button)
        ttk.Label(overview,text='现场旋律比录音早，就点“现场晚”；反之点“现场早”。调整自动记忆，下次合奏生效。\n固定偏移只修正整体早晚；若越弹越偏，应核对两轨速度／变速节点与游戏录音本身。',wraplength=960,foreground=MUTED).pack(anchor='w',pady=4)
        self.advanced_button=ttk.Button(overview,text='展开高级时间设置',command=self.toggle_advanced);self.advanced_button.pack(anchor='w',pady=5)
        self.advanced=ttk.Frame(overview)
        for key,label,low,high in (('launch_delay_ms','导入到录音起播',0,5000),('dialog_wait_ms','打开录音窗口后等待',0,5000),('paste_wait_ms','填码后等待',0,5000),('import_settle_ms','导入后界面恢复等待',0,5000),('record_start_delay_ms','开始按钮到实际录音',0,5000),('record_settle_ms','开启录音后界面等待',0,5000),('record_tail_ms','结束后留尾音',0,5000)):
            self.time_row(self.advanced,key,label,low,high)
        ttk.Label(self.advanced,text='通常保持默认即可；这些是界面等待与录音起点估计，不是游戏声音测量结果。',foreground=MUTED).pack(anchor='w',pady=6)
        help_row=ttk.Frame(overview);help_row.pack(fill='x',pady=(6,0))
        self.demo_button=ttk.Button(help_row,text='准备《小星星》对齐示范',command=self.prepare_demo);self.demo_button.pack(side='left')
        ttk.Label(help_row,text='曲谱、录音码与偏移按曲目自动记忆；换回旧曲可直接合奏。',foreground=MUTED).pack(side='left',padx=10)
        ttk.Label(self.advanced,textvariable=self.split_info,wraplength=960,foreground=TEAL).pack(anchor='w',pady=6)
        self.code_trace=self.code.trace_add('write',lambda *_:self.code_changed())
        self.code_changed();self.refresh_points();self.refresh_project();self.refresh_saved();self.built=True
        for variable in self.values.values():variable.trace_add('write',lambda *_:self.schedule_metadata())
        if all(self.project.parts.values()) and not self.project.archive_id:
            try:self.persist_project()
            except (OSError,ValueError) as error:self.feedback.set('最近合奏已读取，但曲库自动保存失败：'+str(error))
        self.watch()

    def tab(self,title):
        outer=ttk.Frame(self.tabs);self.tabs.add(outer,text=title)
        scroll=ttk.Scrollbar(outer,orient='vertical');scroll.pack(side='right',fill='y')
        canvas=tk.Canvas(outer,bg=BG,highlightthickness=0,yscrollcommand=scroll.set);canvas.pack(fill='both',expand=True);scroll.configure(command=canvas.yview)
        body=ttk.Frame(canvas,padding=12);item=canvas.create_window(0,0,window=body,anchor='nw')
        body.bind('<Configure>',lambda e:canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>',lambda e:canvas.itemconfigure(item,width=e.width))
        body._tab_frame=outer;body._canvas=canvas
        canvas.bind('<MouseWheel>',lambda e:canvas.yview_scroll(-int(e.delta/120),'units'))
        return body

    def select_tab(self,body):self.tabs.select(body._tab_frame)

    def build_points(self,parent,points,buttons):
        for key,label in points:
            row=ttk.Frame(parent);row.pack(fill='x',pady=3);ttk.Label(row,text=label,width=17).pack(side='left')
            variable=tk.StringVar();self.point_labels[key]=variable;ttk.Label(row,textvariable=variable).pack(side='left',padx=6)
            button=ttk.Button(row,text='3 秒后拾取',command=lambda k=key:self.pick(k),padding=(8,4));button.pack(side='right');buttons[key]=button

    def time_row(self,parent,key,label,low,high):
        row=ttk.Frame(parent);row.pack(fill='x',pady=3);ttk.Label(row,text=label,width=23).pack(side='left')
        value=tk.StringVar(value=str(getattr(self.working,key)));self.values[key]=value
        box=ttk.Spinbox(row,textvariable=value,from_=low,to=high,increment=10,width=7);box.pack(side='left');self.time_controls.append(box)
        ttk.Label(row,text='毫秒').pack(side='left',padx=8)

    def update_action(self):
        has_code=bool(self.code.get().strip())
        solo=self.project.parts['recorded'] is None and self.project.parts['live'] is not None
        self.start_button.configure(text='5 秒后合奏' if has_code else '5 秒后演奏现场声部' if solo else '5 秒后自动录制',style='Accent.TButton' if has_code else 'Workspace.Source.TButton')

    def code_changed(self):
        code=self.code.get().strip()
        if self.last_code and self.last_code!=code:
            try:self.project.remember_offset(self.last_code,self.settings())
            except ValueError:pass
        if code!=self.last_code and code in self.project.offsets:
            for key,value in self.project.offsets[code].items():self.values[key].set(str(value))
        self.last_code=code;self.project.bind_code(code);self.update_action();self.refresh_project()
        self.schedule_metadata()

    def toggle_advanced(self):
        if self.advanced.winfo_manager():self.advanced.pack_forget();self.advanced_button.configure(text='展开高级时间设置')
        else:self.advanced.pack(fill='x');self.advanced_button.configure(text='收起高级时间设置')

    def adjust_offset(self,delta):
        if self.app.busy:self.feedback.set('请先停止本次合奏，再调整下次合奏的听感偏移。');return
        try:value=int(self.values['score_offset_ms'].get())+delta
        except ValueError:self.feedback.set('听感偏移请填写整数毫秒，例如 -20、0 或 30。');return
        if not -60000<=value<=60000:self.feedback.set('听感偏移应在 -60000～60000 毫秒之间。');return
        self.values['score_offset_ms'].set(str(value));self.feedback.set(f'现场偏移已调整为 {value:+d} ms，下次合奏生效；将自动记住本曲设置。')

    def schedule_metadata(self):
        try:self.offset_total.set(f'合奏总偏移：{int(self.values["score_offset_ms"].get())+int(self.values["launch_delay_ms"].get()):+d} ms')
        except (ValueError,KeyError,AttributeError):pass
        if not self.built:return
        self.metadata_dirty=True
        if self.metadata_timer is not None:self.after_cancel(self.metadata_timer)
        self.metadata_timer=self.after(500,self.flush_metadata)

    def flush_metadata(self):
        self.metadata_timer=None
        if self.app.busy:return
        code=self.code.get().strip()
        if code and (len(code)!=32 or any(c not in '0123456789abcdefABCDEF' for c in code)):
            self.metadata_dirty=False;return
        try:
            settings=self.settings();self.project.bind_code(code);self.project.remember_offset(code,settings)
            self.write_settings(settings);self.persist_project();self.metadata_dirty=False
        except (OSError,ValueError) as error:
            self.metadata_dirty=False;self.feedback.set('录音码／偏移未自动保存：'+str(error)+'。修正后点“保存当前合奏”重试。')

    def refresh_saved(self):
        try:self.saved_items=self.library.items()
        except OSError as error:self.feedback.set('已保存合奏列表未读取：'+str(error));return
        labels=[entry.name+(' · 有录音码' if entry.has_code else ' · 待录音') for entry in self.saved_items]
        self.saved_box.configure(values=labels)
        index=next((i for i,entry in enumerate(self.saved_items) if entry.identity==self.project.archive_id),None)
        if index is not None:self.saved_box.current(index)
        elif not labels:self.saved_choice.set('拆分完成后自动保存到这里')
        else:self.saved_choice.set('当前尚未保存，或从列表选择已保存合奏')

    def load_saved(self,identity=None):
        if self.app.busy:self.feedback.set('请先停止演奏，再切换已保存合奏。');return False
        if identity is None:
            index=self.saved_box.current()
            if not 0<=index<len(self.saved_items):return False
            identity=self.saved_items[index].identity
        try:return self.activate_project(self.library.load(identity))
        except (OSError,ValueError,TypeError,KeyError) as error:self.feedback.set('合奏未载入：'+str(error));return False

    def activate_project(self,project,refresh_saved=True):
        if self.app.busy:self.feedback.set('请先停止演奏，再切换合奏。');return False
        editor=self.app.tab_editor
        if editor is not None and editor.winfo_exists():self.feedback.set('请先保存并关闭当前编谱窗口，再切换已保存合奏。');return False
        settings=self.settings()
        if any(self.project.parts.values()):self.persist_project()
        if refresh_saved and project.archive_id and self.library.root is not None:project=self.library.load(project.archive_id)
        code=project.bound_code;offset=project.offsets.get(code,{})
        settings=replace(settings,code=code,score_offset_ms=offset.get('score_offset_ms',0),launch_delay_ms=offset.get('launch_delay_ms',0)).validate()
        self.project.__dict__.clear();self.project.__dict__.update(project.__dict__)
        self.last_code='';self.working=settings
        for key in ('score_offset_ms','launch_delay_ms'):self.values[key].set(str(getattr(settings,key)))
        self.code.set(code);self.write_settings(settings);self.persist_project();self.refresh_project();self.refresh_saved()
        self.select_tab(self.overview_tab)
        self.feedback.set('已载入“'+(self.project.archive_name or '合奏')+'”：两轨、录音码和偏移已恢复；'+('可直接合奏。' if code else '先录制伴奏并保存录音码。'))
        return True

    def save_current(self):
        if not any(self.project.parts.values()):self.feedback.set('请先自动拆分或应用双轨演奏谱，再保存合奏。');return
        if self.save() is not None:self.feedback.set('当前合奏已保存；下次从上方“已保存合奏”直接选择即可。')

    def open_saved_folder(self):
        if self.library.root is None:self.feedback.set('当前未启用本机合奏保存目录。');return
        try:
            import os
            path=self.library.folder(self.project.archive_id) if self.project.archive_id else self.library.root
            path.mkdir(parents=True,exist_ok=True);os.startfile(str(path))
        except (OSError,ValueError) as error:self.feedback.set('保存目录未打开：'+str(error))

    def refresh_project(self):
        for role in ('recorded','live'):
            part=self.project.parts[role];doc=self.project.documents[role]
            self.part_names[role].set(part.name if part else '双轨原谱（待应用）' if doc and any(c.notes for c in doc.cells) else '未选择曲谱')
            info=part.summary if part else ('选择需要录入游戏的伴奏谱' if role=='recorded' else '选择与录音同时演奏的曲谱')
            if doc is not None and (part is not None or any(c.notes for c in doc.cells)) and not doc.matches_part(part):info+=' · 有未应用的双轨修改'
            if role=='recorded' and part and self.project.take_id==part.fingerprint:
                state={'recording':'录制中','complete':'录制流程完成，待保存录音码','interrupted':'上次录制未完成'}.get(self.project.take_state)
                if state:info+=' · '+state
            self.part_info[role].set(info)
        if self.code.get().strip():
            text='录音码关联：'+(self.project.bound_name or '外部游戏录音（未关联伴奏谱）');part=self.project.parts['recorded']
            if part and self.project.bound_part_id and part.fingerprint!=self.project.bound_part_id:text+=' · 伴奏已更换，请更新录音码'
        else:text='未填写录音码：先录制蓝色伴奏，再由青色声部补充演奏。'
        self.recording_link.set(text)
        if hasattr(self,'split_info'):self.split_info.set(self.project.split_summary)
        if hasattr(self,'start_button'):self.update_action()

    def open_splitter(self):
        if self.app.busy:self.feedback.set('请先停止当前播放，再拆分原谱。');return None
        if self.split_window is not None and self.split_window.winfo_exists():
            from .navigation import navigate
            return navigate(self,self.split_window)
        from .duet_split_ui import DuetSplitDialog
        self.split_window=DuetSplitDialog(self)
        from .navigation import navigate
        return navigate(self,self.split_window)

    def open_duet_editor(self,role='live',document=None,project=None):
        if self.app.busy:self.feedback.set('请先停止演奏，再查看或编辑曲谱。');return
        editor=self.app.tab_editor
        if editor is not None and editor.winfo_exists():
            from .navigation import present
            present(editor,editor.canvas)
            if getattr(editor,'workspace',None) is self and project is None:editor.switch_track(role)
            else:
                message='请先保存并关闭当前编谱窗口，再打开另一份双轨原谱。'
                self.feedback.set(message);editor.feedback.set(message);return None
            return editor
        try:
            from .duet_editor import DuetEditor
            editor=DuetEditor(self,role,document,project)
            from .navigation import navigate
            return navigate(self,editor,editor.canvas)
        except (ValueError,OSError) as error:self.feedback.set('曲谱未打开：'+str(error))

    def show_overview(self,message=None):
        """Return to the ensemble action page, without a transient covering it."""
        if self.split_window is not None and self.split_window.winfo_exists():self.split_window.withdraw()
        self.select_tab(self.overview_tab)
        if message:self.feedback.set(message)
        from .navigation import present
        return present(self,self.start_button)

    def preview_part(self,role):
        if self.project.parts[role] is None and self.project.documents[role] is None:
            self.feedback.set('请先选择该轨曲谱，或进入双轨编谱写入音符。');return
        editor=self.open_duet_editor(role)
        if editor is not None and getattr(editor,'workspace',None) is self:
            editor.preview_choice.set('仅当前轨');editor.preview(True)

    def open_composition(self,path=None):
        if self.app.busy:self.feedback.set('请先停止演奏，再打开双轨原谱。');return
        from .guitar_pro import EXTENSIONS, FILE_FILTER
        path=path or filedialog.askopenfilename(parent=self,title='打开双轨原谱',filetypes=[('曲谱',f'*.gscore {FILE_FILTER[1]}'),('双轨原谱','*.gscore'),FILE_FILTER])
        if not path:return
        candidate_path=Path(path)
        if candidate_path.suffix.lower() in EXTENSIONS:return self.app.import_guitar_pro(candidate_path,owner=self)
        try:
            from .duet_composition import read_composition
            import json
            if candidate_path.stat().st_size>20_000_000:raise ValueError('合奏原谱超过 20 MB，请先分段')
            data=json.loads(candidate_path.read_text(encoding='utf-8-sig'))
            if isinstance(data,dict) and data.get('kind')=='duet_session':
                from .duet_project import DuetProject
                self.activate_project(DuetProject.from_dict(data.get('project')),refresh_saved=False);return self
            project=read_composition(path)
            editor=self.open_duet_editor(project=project)
            if editor is not None and getattr(editor,'project',None) is project:editor.file_path=candidate_path
            return editor
        except (OSError,ValueError,TypeError,KeyError) as error:self.feedback.set('双轨原谱未打开：'+str(error))

    def persist_project(self):
        archive_error=None
        if all(self.project.parts.values()) or self.project.archive_id:
            try:self.library.save(self.project,settings=self.settings())
            except (OSError,ValueError) as error:archive_error=error
        write_project(self.app.settings_dir,self.project);self.app.duet_project_notice=''
        self.metadata_dirty=False
        if self.built:self.refresh_saved()
        if archive_error is not None:raise OSError('当前项目已保留，但合奏曲库未保存：'+str(archive_error))

    def choose_part(self,role,path=None):
        if self.app.busy:self.feedback.set('请先停止当前播放，再更换声部。');return
        if not self.app.editor_allows_replacement():self.feedback.set(self.app.status.get());return
        path=path or filedialog.askopenfilename(parent=self,title='选择录音伴奏' if role=='recorded' else '选择现场声部',filetypes=[('演奏谱','*.gscore *.txt *.tab'),('所有文件','*.*')])
        if not path:return
        try:
            part=DuetPart.from_path(path);self.project.set_part(role,part);self.persist_project();self.refresh_project()
            self.feedback.set(('录音伴奏' if role=='recorded' else '现场声部')+'已设为“'+part.name+'”；开始时会自动使用这份谱。')
        except (OSError,ValueError,TypeError,KeyError) as error:self.feedback.set('未选择声部：'+str(error))

    def use_current(self,role):
        if not self.app.editor_allows_replacement():self.feedback.set(self.app.status.get());return
        try:
            part=DuetPart.from_current(self.app);self.project.set_part(role,part);self.persist_project();self.refresh_project()
            self.feedback.set(('录音伴奏' if role=='recorded' else '现场声部')+'已保留当前谱快照；主窗口之后换谱不会改变它。')
        except (OSError,ValueError) as error:self.feedback.set('未设置声部：'+str(error))

    def prepare_demo(self):
        if self.app.busy:self.feedback.set('请先停止当前播放，再准备示范。');return
        if not self.app.editor_allows_replacement():self.feedback.set(self.app.status.get());return
        from .recording_demo import demo_parts
        parts=demo_parts();self.project.set_part('recorded',DuetPart('小星星 · 扫弦伴奏',parts['recorded'],'内置双声部示范'))
        self.project.set_part('live',DuetPart('小星星 · 现场旋律',parts['live'],'内置双声部示范'))
        try:self.persist_project()
        except OSError as error:self.feedback.set('示范已准备，但保存失败：'+str(error));return
        self.refresh_project();self.feedback.set('生成完成：两份示范均为 96 BPM，开头休止 5 秒。校准后可从右下角直接开始自动录制。')

    def associate_code(self):
        if not self.code.get().strip():self.feedback.set('请先填写游戏生成的录音码。');return
        if self.project.parts['recorded'] is None:self.feedback.set('请先在蓝色栏选择与该录音对应的伴奏谱。');return
        self.project.associate_code(self.code.get());self.refresh_project()
        try:self.persist_project();self.feedback.set('录音码已按所选伴奏关联；程序无法读取码内声音，请核对它确实是这一声部。')
        except OSError as error:self.feedback.set('关联未保存：'+str(error))

    def primary_action(self):
        if self.code.get().strip():self.start()
        elif self.project.parts['recorded'] is None and self.project.parts['live'] is not None:
            if not self.part_is_current('live') or not self.game_controls.apply_pages():return
            if self.save() is None:return
            self.app.start(True,duet_part=self.project.parts['live']);self.feedback.set(self.app.status.get())
        else:self.capture()

    def refresh_points(self):
        for key,_ in ALL_POINTS:
            point=getattr(self.working,key);self.point_labels[key].set('未校准' if point is None else f'{point[0]:.4f}, {point[1]:.4f}')
        w,h=self.working.reference_size;self.size_label.set(f'校准游戏客户区：{w} × {h}' if w and h else '尚未拾取游戏画面尺寸')

    def pick(self,key):
        if self.app.busy:self.feedback.set('请先停止当前播放，再拾取录音按钮。');return
        hwnd=self.app.window_items.get(self.app.window.get())
        if not hwnd:self.feedback.set('请先在主窗口选择游戏窗口，再拾取。');return
        if self.pending_pick is not None:self.feedback.set('请完成本次拾取后，再拾取下一个位置。');return
        label=dict(ALL_POINTS)[key];self.feedback.set('3 秒后拾取“'+label+'”，请切回游戏，将鼠标停在目标中心。')
        def finish():
            self.pending_pick=None
            if not self.winfo_exists():return
            try:
                if self.app.busy:raise ValueError('当前正在播放，已取消拾取')
                from .native import pick_relative_point
                point,size=pick_relative_point(hwnd)
                if min(self.working.reference_size) and tuple(size)!=tuple(self.working.reference_size):self.working=replace(self.working,**{k:None for k,_ in ALL_POINTS})
                self.working=replace(self.working,**{key:point,'reference_size':size});self.refresh_points();self.feedback.set('已拾取“'+label+'”；本页所需位置完成后保存。')
            except Exception as error:self.feedback.set('拾取失败：'+str(error))
            self.lift()
        self.pending_pick=self.after(3000,finish)
        from .native import activate_window
        try:activate_window(hwnd)
        except RuntimeError:pass

    def settings(self,launch=False):
        try:values={key:int(variable.get()) for key,variable in self.values.items()}
        except ValueError:raise ValueError('时间轴设置需要整数毫秒；现场偏移可为负数') from None
        return replace(self.working,code=self.code.get().strip(),**values).validate(launch=launch)

    def save(self,launch=False,capture=False):
        try:
            if self.app.busy:raise ValueError('请先停止当前播放，再保存设置')
            if self.pending_pick is not None:raise ValueError('请等待拾取完成后再操作')
            settings=self.settings(launch)
            if capture:settings.validate_capture()
            self.project.bind_code(settings.code);self.project.remember_offset(settings.code,settings)
            self.write_settings(settings);self.persist_project();self.feedback.set('声部、录音码和对齐设置已保存；重复合奏会沿用偏移。')
            return settings
        except (OSError,ValueError) as error:self.feedback.set('未保存：'+str(error));return None

    def write_settings(self,settings):
        if self.app.settings_dir:
            from .ui import atomic_json
            directory=Path(self.app.settings_dir);directory.mkdir(parents=True,exist_ok=True);atomic_json(directory/'recording-sync.json',settings.to_dict())
        self.working=settings;self.app.recording_sync_settings=settings;self.app.recording_sync_notice=''

    def start(self):
        if not self.game_controls.apply_pages():return
        settings=self.save(launch=True)
        if settings is None:self.select_tab(self.setup_tab);return
        try:part=self.project.validate_replay()
        except ValueError as error:self.feedback.set('未开始合奏：'+str(error));return
        if not self.part_is_current('live'):return
        self.app.start(True,recording_sync=settings,duet_part=part);self.feedback.set(self.app.status.get())

    def capture(self):
        part=self.project.parts['recorded']
        if part is None:
            self.feedback.set('请先在蓝色“录音伴奏”选择曲谱；如果已写双轨原谱，请在编谱窗口点“应用两轨并返回合奏”，再自动录制。');return
        if not self.part_is_current('recorded') or not self.game_controls.apply_pages():return
        settings=self.save(capture=True)
        if settings is None:self.select_tab(self.capture_tab);return
        self.app.start(True,recording_capture=settings,duet_part=part)
        if self.app.player.running:
            self.project.begin_take(part);self.code.set('')
            try:self.write_settings(replace(settings,code=''));self.persist_project()
            except OSError as error:self.feedback.set('录制已开始，但项目保存失败：'+str(error))
        else:self.feedback.set(self.app.status.get())
        self.refresh_project()

    def part_is_current(self,role):
        doc=self.project.documents[role];part=self.project.parts[role]
        editor=self.app.tab_editor
        if editor is not None and editor.winfo_exists() and getattr(editor,'workspace',None) is self:
            if getattr(editor,'staged',False):
                self.feedback.set('当前正在查看另一份双轨原谱，请先应用两轨或关闭该编谱窗口，再录制／合奏。');return False
            doc=editor.documents[role]
        if doc is not None and (part is not None or any(c.notes for c in doc.cells)) and not doc.matches_part(part):
            self.feedback.set(('录音伴奏' if role=='recorded' else '现场声部')+'有尚未应用的双轨修改。请打开“查看／编辑六线谱”，应用两轨；不能直接演奏时选择“自动编配双轨…”。');return False
        return True

    def watch(self):
        busy=self.app.busy
        if getattr(self,'_controls_busy',None)!=busy:
            for button in (self.start_button,self.save_button,self.save_song_button,self.demo_button,self.compose_button,self.open_composition_button,self.associate_button,self.split_button,*self.part_buttons,*self.offset_buttons,*self.time_controls):button.configure(state='disabled' if busy else 'normal')
            self.saved_box.configure(state='disabled' if busy else 'readonly')
            self.code_entry.configure(state='disabled' if busy else 'normal');self._controls_busy=busy
        self.game_controls.refresh()
        if self.was_busy and not busy:self.feedback.set(self.app.status.get());self.refresh_project();self.schedule_metadata()
        if not busy and self.metadata_dirty and self.metadata_timer is None:self.metadata_timer=self.after(500,self.flush_metadata)
        self.was_busy=busy;self.watch_timer=self.after(150 if self.winfo_viewable() else 750,self.watch)

    def destroy(self):
        if self.metadata_timer is not None:self.after_cancel(self.metadata_timer);self.metadata_timer=None
        if self.pending_pick is not None:self.after_cancel(self.pending_pick);self.pending_pick=None
        if self.watch_timer is not None:self.after_cancel(self.watch_timer);self.watch_timer=None
        if self.built and not self.app.busy:self.save()
        if self.code_trace is not None:self.code.trace_remove('write',self.code_trace);self.code_trace=None
        super().destroy()
