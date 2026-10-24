"""A separate, offline music browser with persistent exports and local playback."""
from fractions import Fraction
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog,ttk

from .library import MusicLibrary,read_score,wav_info
from .game_profile import GameProfile
from .guitar import GuitarEvent,GuitarScore
from .player import GuitarPlayer
from .widgets import StatusBanner


class LibraryWindow(tk.Toplevel):
    def __init__(self,parent,root=None,sound=None):
        super().__init__(parent)
        self.parent=parent;self.library=MusicLibrary(root);self.sound=sound
        self.closed=False;self.preparing=False;self.poll_id=None;self.close_id=None;self.selected=None
        self.messages=queue.Queue();self.audio_lock=threading.Lock();self.cancel=threading.Event()
        self.player=GuitarPlayer(lambda kind,data:self.messages.put((kind,data)))
        self.title('我的曲库 · 收藏、试听与编谱')
        self.geometry('1030x690');self.minsize(850,580);self.configure(background='#edf2f6')
        self.search=tk.StringVar();self.filter=tk.StringVar(value='全部');self.name=tk.StringVar(value='我的乐曲')
        self.feedback=tk.StringVar(value='导入已有文件，或把主窗口的曲谱和参考音频保存到这里。')
        self.summary=tk.StringVar(value='选择左侧文件，查看曲谱、MIDI 声部或 WAV 信息')
        self.track=tk.StringVar();self.highest=tk.BooleanVar(value=True);self.time=tk.StringVar(value='尚未试听')
        self.track_items={};self.midi_source=None;self.preview_score=None
        self._build();self.search_trace=self.search.trace_add('write',lambda *_:self.refresh())
        self.protocol('WM_DELETE_WINDOW',self.close);self.bind('<F9>',lambda _:self.stop_preview())
        self.refresh();self._poll()

    @property
    def busy(self):return self.preparing or self.player.running

    def _build(self):
        heading=ttk.Frame(self,padding=(20,16,20,10));heading.pack(fill='x')
        ttk.Label(heading,text='我的曲库',font=('Microsoft YaHei UI',20,'bold')).pack(side='left')
        ttk.Label(heading,text='曲谱 / MIDI / 参考音频',foreground='#62798d').pack(side='left',padx=14)
        ttk.Button(heading,text='导入文件…',command=self.import_files).pack(side='right')
        ttk.Button(heading,text='刷新',command=self.refresh).pack(side='right',padx=6)
        ttk.Label(self,text='保存位置：'+str(self.library.root),foreground='#62798d',padding=(20,0,20,10),wraplength=980).pack(anchor='w')
        tools=ttk.Frame(self,padding=(20,0,20,10));tools.pack(fill='x')
        ttk.Label(tools,text='乐曲名称').pack(side='left')
        ttk.Entry(tools,textvariable=self.name,width=23).pack(side='left',padx=8)
        self.save_button=ttk.Button(tools,text='保存当前曲谱',command=self.save_current)
        self.save_button.pack(side='left',padx=(0,6))
        self.export_button=ttk.Button(tools,text='当前谱 → WAV',command=self.export_audio,style='Accent.TButton')
        self.export_button.pack(side='left')
        ttk.Label(tools,text='同名自动另存',foreground='#62798d').pack(side='right')
        # Reserve actions and feedback first so the list cannot push them out.
        footer=ttk.Frame(self,padding=(20,10,20,14));footer.pack(side='bottom',fill='x')
        self.banner=StatusBanner(footer,self.feedback);self.banner.pack(fill='x',pady=(0,10))
        row=ttk.Frame(footer);row.pack(fill='x')
        self.preview_button=ttk.Button(row,text='▶ 试听选中',command=self.preview,style='Accent.TButton')
        self.preview_button.pack(side='left')
        ttk.Button(row,textvariable=self.parent.stop_label,command=self.stop_preview).pack(side='left',padx=8)
        self.load_button=ttk.Button(row,text='载入编谱 →',command=self.load_selected)
        self.load_button.pack(side='left')
        ttk.Label(row,textvariable=self.time,foreground='#62798d').pack(side='right')
        panes=ttk.Panedwindow(self,orient='horizontal');panes.pack(fill='both',expand=True,padx=20)
        left=ttk.Frame(panes);right=ttk.Frame(panes);panes.add(left,weight=4);panes.add(right,weight=5)
        find=ttk.Frame(left);find.pack(fill='x',pady=(0,8))
        ttk.Entry(find,textvariable=self.search,width=18).pack(side='left',fill='x',expand=True)
        box=ttk.Combobox(find,textvariable=self.filter,values=('全部','曲谱','MIDI','WAV'),state='readonly',width=7)
        box.pack(side='right',padx=(6,10));box.bind('<<ComboboxSelected>>',lambda _:self.refresh())
        self.tree=ttk.Treeview(left,columns=('kind',),show='tree headings',selectmode='browse')
        self.tree.heading('#0',text='乐曲 / 文件名');self.tree.heading('kind',text='类型')
        self.tree.column('#0',width=250,minwidth=140);self.tree.column('kind',width=60,stretch=False)
        scrollbar=ttk.Scrollbar(left,orient='vertical',command=self.tree.yview)
        scrollbar.pack(side='right',fill='y',padx=(0,8));self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(fill='both',expand=True);self.tree.bind('<<TreeviewSelect>>',self.on_select)
        self.tree.bind('<Double-1>',lambda _:self.load_selected())
        ttk.Label(right,text='文件预览',font=('Microsoft YaHei UI',13,'bold')).pack(anchor='w')
        ttk.Label(right,textvariable=self.summary,foreground='#24616f',wraplength=430,padding=(0,6)).pack(anchor='w')
        self.midi_controls=ttk.Frame(right)
        ttk.Label(self.midi_controls,text='试听声部').pack(anchor='w')
        self.track_box=ttk.Combobox(self.midi_controls,textvariable=self.track,state='readonly',width=35)
        self.track_box.pack(fill='x',pady=4)
        ttk.Checkbutton(self.midi_controls,text='同拍取最高音（试听单音旋律）',variable=self.highest).pack(anchor='w')
        self.content=tk.Text(right,wrap='none',font=('Consolas',11),background='white',foreground='#20394e',relief='flat',padx=12,pady=12,state='disabled')
        scroll=ttk.Scrollbar(right,orient='vertical',command=self.content.yview);scroll.pack(side='right',fill='y')
        self.content.configure(yscrollcommand=scroll.set);self.content.pack(fill='both',expand=True)

    def refresh(self,select=None):
        if self.closed:return
        old=select or (self.selected.path if self.selected else None)
        self.tree.delete(*self.tree.get_children());self.entries={}
        for item in self.library.items():
            if self.search.get().casefold() not in item.path.name.casefold():continue
            if self.filter.get() not in ('全部',item.kind):continue
            key=str(len(self.entries));self.entries[key]=item
            self.tree.insert('','end',iid=key,text=item.path.name,values=(item.kind,))
            if old and item.path==Path(old):self.tree.selection_set(key);self.tree.see(key)
        children=self.tree.get_children()
        if not children:
            self.selected=None;self.midi_source=None;self.midi_controls.pack_forget()
            self.summary.set('曲库为空。点击“导入文件…”或“保存当前曲谱”。')
            self.content.configure(state='normal');self.content.delete('1.0','end');self.content.configure(state='disabled')
        elif not self.tree.selection():self.tree.selection_set(children[0])

    def on_select(self,*_):
        selection=self.tree.selection()
        if not selection:return
        self.selected=self.entries[selection[0]];path=self.selected.path;self.name.set(path.stem)
        self.midi_controls.pack_forget();self.midi_source=None;self.track_items={}
        self.load_button.configure(state='disabled' if self.selected.kind=='WAV' else 'normal')
        try:
            if self.selected.kind=='曲谱':
                score,text=read_score(path)
                self.summary.set(f'{score.bpm:g} BPM · {score.time_signature[0]}/{score.time_signature[1]} 拍 · {len(score.events)} 个事件 · {score.seconds:.1f} 秒')
            elif self.selected.kind=='MIDI':
                from .midi_import import inspect_midi
                self.midi_source=inspect_midi(path)
                self.track_items={f'{track.index+1}. {track.name} · {len(track.notes)} 音':track.index for track in self.midi_source.tracks}
                self.track_box.configure(values=tuple(self.track_items));self.track.set(next(iter(self.track_items)))
                self.midi_controls.pack(before=self.content,fill='x',pady=(0,8))
                self.summary.set(f'{self.midi_source.bpm:g} BPM · {len(self.track_items)} 个非鼓声部；选择声部后试听')
                text='MIDI 声部\n\n'+'\n'.join(self.track_items)+'\n\n试听使用标准定弦合成音色。\n载入编谱时可以选择声部与整轨移调。'
            else:
                duration,rate,channels=wav_info(path)
                self.summary.set(f'{duration:.1f} 秒 · {rate} Hz · {channels} 声道')
                text=f'{path.name}\n\n已保存的 WAV 试听音频\n\n试听结束后文件仍会保留。\n音频本身不能还原为可编辑曲谱。'
            self.content.configure(state='normal');self.content.delete('1.0','end');self.content.insert('1.0',text);self.content.configure(state='disabled')
        except Exception as error:
            self.summary.set('文件读取失败：'+str(error));self.feedback.set(self.summary.get())

    def available(self):
        editor=self.parent.tab_editor
        if self.parent.busy or (editor and (editor.player.running or editor.preparing)):
            self.feedback.set('请先停止当前演奏、试听或文件生成，再操作曲库。');return False
        return True

    def import_files(self):
        if not self.available():return
        paths=filedialog.askopenfilenames(parent=self,title='导入到我的曲库',filetypes=[('曲谱、MIDI 与 WAV','*.gscore *.txt *.mid *.midi *.wav')])
        imported=[];failures=[]
        for path in paths:
            try:imported.append(self.library.add_file(path))
            except (ValueError,OSError) as error:failures.append(Path(path).name+'：'+str(error))
        if imported:self.search.set('');self.filter.set('全部');self.refresh(imported[-1])
        if paths:self.feedback.set(f'已入库 {len(imported)} 个文件。'+(' 导入失败：'+'；'.join(failures) if failures else '原文件保留。'))

    def save_current(self):
        if not self.available():return
        path=self.library.new_path(self.name.get(),'曲谱','.gscore')
        if self.parent.save_score(path):
            self.search.set('');self.filter.set('全部');self.refresh(path)
            self.feedback.set('曲谱已保存：'+path.name)

    def export_audio(self):
        if not self.available():return
        score=self.parent.analyze()
        if score is None:self.feedback.set('无法生成音频：'+self.parent.info.get());return
        if self.parent.profile.mode!='fret':self.feedback.set('请先切到品格模式；游戏内置和弦音高尚未核对。');return
        self.start_job('export',score=score,name=self.name.get())

    def preview(self):
        if not self.available():return
        if not self.selected:self.feedback.set('请先选择左侧的文件。');return
        try:
            if self.selected.kind=='WAV':
                duration,_,_=wav_info(self.selected.path)
                score=GuitarScore((GuitarEvent((),Fraction(0),Fraction(str(duration)),'Zero','音频',1),),Fraction(str(duration)),60)
                self.start_job('wav',score=score,path=self.selected.path)
            else:
                if self.selected.kind=='MIDI':
                    from .midi_import import convert_melody
                    source=self.midi_source
                    if source is None:raise ValueError('MIDI 未成功读取，请重新选择文件')
                    score,report=convert_melody(source,self.track_items[self.track.get()],highest=self.highest.get(),timing=self.parent.input_timing)
                    self.summary.set(report)
                else:score,_=read_score(self.selected.path)
                self.start_job('preview',score=score)
        except Exception as error:self.feedback.set('试听失败：'+str(error))

    def start_job(self,kind,score,name=None,path=None):
        self.preparing=True;cancel=threading.Event();self.cancel=cancel;self.preview_score=score
        self.feedback.set('正在生成并保存 WAV…' if kind=='export' else '正在准备本地试听…')
        self.preview_button.configure(state='disabled');self.export_button.configure(state='disabled')
        def prepare():
            try:
                if kind=='export':
                    result=self.library.export_audio(score,name,cancel)
                    self.messages.put(('exported',(result,cancel)))
                else:
                    from .audio import SoundPreviewBackend,WavPreviewBackend
                    cache=self.library.root.parent/'.preview'
                    backend=WavPreviewBackend(path,cache,self.sound) if kind=='wav' else SoundPreviewBackend(score,cache,cancel,self.sound)
                    with self.audio_lock:
                        if cancel.is_set() or self.closed:backend.close()
                        else:self.messages.put(('ready',(backend,score,cancel)))
                    if cancel.is_set():self.messages.put(('job_error',('已取消试听',cancel)))
            except Exception as error:self.messages.put(('job_error',('已取消' if cancel.is_set() else str(error),cancel)))
        threading.Thread(target=prepare,daemon=True).start()

    def stop_preview(self):
        self.cancel.set();self.player.stop()
        if not self.closed:self.feedback.set('已停止试听或取消文件生成；已保存文件保留。')

    def load_selected(self):
        if not self.available():return
        if not self.selected:self.feedback.set('请先选择左侧文件。');return
        if self.selected.kind=='WAV':self.feedback.set('WAV 用于试听；请选择曲谱或 MIDI 载入编谱。');return
        if self.selected.kind=='MIDI':self.parent.import_midi(self.selected.path)
        else:self.parent.open_score(self.selected.path)
        self.feedback.set('已请求载入 '+self.selected.path.name+'，请查看主窗口。')

    def _poll(self):
        if self.closed:return
        try:
            while True:
                kind,data=self.messages.get_nowait()
                if kind in ('ready','exported','job_error'):
                    cancel=data[-1]
                    if cancel is not self.cancel:
                        if kind=='ready':data[0].close()
                        continue
                    self.preparing=False;self.preview_button.configure(state='normal');self.export_button.configure(state='normal')
                    if kind=='ready':
                        backend,score,_=data
                        if cancel.is_set():backend.close()
                        else:
                            try:self.player.start(score,GameProfile.frets(),backend)
                            except Exception as error:
                                backend.close();self.feedback.set('试听失败：'+str(error));continue
                            self.feedback.set(self.parent.control_hint('正在试听 · 标准定弦参考音频；可用 F9 停止'))
                    elif kind=='exported':
                        self.search.set('');self.filter.set('全部');self.refresh(data[0])
                        self.feedback.set('WAV 已保存：'+data[0].name+'。可选中文件再次试听。')
                    else:self.feedback.set('操作已取消' if cancel.is_set() else '操作失败：'+data[0])
                elif kind=='progress':
                    self.time.set(f'{float(data):.1f} / {self.preview_score.seconds:.1f} 秒')
                elif kind in ('done','error'):
                    self.feedback.set('试听结束，文件仍保留在曲库。' if kind=='done' else '试听失败：'+str(data))
        except queue.Empty:pass
        self.poll_id=self.after(40,self._poll)

    def close(self):
        if self.closed:return
        self.stop_preview()
        if self.busy:self.close_id=self.after(30,self.close)
        else:self.destroy()

    def destroy(self):
        if self.closed:return
        self.stop_preview();self.closed=True
        for timer in (self.poll_id,self.close_id):
            if timer:self.after_cancel(timer)
        self.search.trace_remove('write',self.search_trace)
        with self.audio_lock:
            try:
                while True:
                    kind,data=self.messages.get_nowait()
                    if kind=='ready':data[0].close()
            except queue.Empty:pass
        if getattr(self.parent,'library_window',None) is self:self.parent.library_window=None
        super().destroy()
