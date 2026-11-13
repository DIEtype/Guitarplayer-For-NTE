"""Open editable Guitar Pro tracks without blocking Tk."""
from pathlib import Path
from queue import Empty, Queue
from threading import Thread
import tkinter as tk
from tkinter import messagebox, ttk

from .composer_guidance import file_guidance
from .duet_composition import ROLES, TRACK_NAMES
from .guitar_pro import inspect_guitar_pro, import_guitar_pro_duet, import_guitar_pro_track
from .measures import TONE_NAMES
from .navigation import present, suppress_return
from .score import pitch_name


class GuitarProImportDialog(tk.Toplevel):
    def __init__(self, app, path, *, source_editor=None):
        super().__init__(app)
        self.app = app
        self.path = Path(path)
        self.source_editor = source_editor
        self.source_role = getattr(source_editor, 'role', None)
        self.source = None
        self.eligible_tracks = []
        self.pending = True
        self.results = Queue()
        self.title('导入 Guitar Pro')
        self.transient(app)
        width = min(760, self.winfo_screenwidth() - 40)
        height = min(510, self.winfo_screenheight() - 90)
        self.geometry(f'{width}x{height}')
        self.minsize(min(600, width), min(400, height))
        panel = ttk.Frame(self, padding=16)
        panel.place(relwidth=1, relheight=1)
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(1, weight=1)
        self.heading = ttk.Label(panel, text=self.path.name, font=('Microsoft YaHei UI', 13, 'bold'), wraplength=540)
        self.heading.grid(row=0, column=0, sticky='w', pady=(0, 10))
        table = ttk.Frame(panel)
        table.grid(row=1, column=0, sticky='nsew')
        self.tracks = ttk.Treeview(table, columns=('track', 'tuning', 'notes'), show='headings', selectmode='browse', height=4)
        for key, label, width in [('track', '轨道', 220), ('tuning', '定弦（1 → 6 弦）', 220), ('notes', '音符', 80)]:
            self.tracks.heading(key, text=label)
            self.tracks.column(key, width=width, minwidth=55, stretch=key != 'notes')
        scrollbar = ttk.Scrollbar(table, orient='vertical', command=self.tracks.yview)
        self.tracks.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y')
        self.tracks.pack(fill='both', expand=True)
        self.feedback = tk.StringVar(value='正在读取 Guitar Pro 曲谱…')
        self.approximate_bends = tk.BooleanVar(value=True)
        ttk.Checkbutton(panel, text='长推弦用换品重拨近似', variable=self.approximate_bends).grid(row=2, column=0, sticky='w', pady=(8, 0))
        self.feedback_label = ttk.Label(panel, textvariable=self.feedback, wraplength=530, justify='left')
        self.feedback_label.grid(row=4, column=0, sticky='ew', pady=8)
        self.duet_controls = ttk.LabelFrame(panel, text='双轨导入', padding=(8, 4, 8, 8))
        self.duet_choices = {}
        for column, role in enumerate(ROLES):
            self.duet_controls.columnconfigure(column, weight=1, uniform='track')
            ttk.Label(self.duet_controls, text=TRACK_NAMES[role]).grid(row=0, column=column, sticky='w')
            choice = ttk.Combobox(self.duet_controls, state='disabled', width=16)
            choice.grid(row=1, column=column, sticky='ew', padx=(0, 8))
            choice.bind('<<ComboboxSelected>>', self.duet_selection_changed)
            self.duet_choices[role] = choice
        controls = ttk.Frame(panel)
        controls.grid(row=5, column=0, sticky='ew')
        ttk.Button(controls, text='关闭', command=self.destroy).pack(side='right')
        single_label = f'替换{TRACK_NAMES[self.source_role]}' if self.source_role else '导入所选轨道'
        self.open_button = ttk.Button(controls, text=single_label, command=self.open_selected, state='disabled')
        self.open_button.pack(side='left')
        self.duet_button = ttk.Button(controls, text='导入两轨并打开双轨编谱',
                                      style='Workspace.Compose.TButton', command=self.open_duet, state='disabled')
        self.tracks.bind('<<TreeviewSelect>>', self.selection_changed)
        self.tracks.bind('<Double-1>', self.open_selected)
        self.tracks.bind('<Return>', self.open_selected)
        self.bind('<Escape>', lambda _event: self.destroy())
        self.protocol('WM_DELETE_WINDOW', self.destroy)
        self.timer = None
        self.run('read', inspect_guitar_pro, self.path)

    def run(self, stage, operation, *arguments):
        self.pending = True
        self.selection_changed()
        results = self.results

        def work():
            try:
                results.put((stage, operation(*arguments)))
            except (OSError, ValueError) as error:
                results.put(('error', file_guidance(error, '导入 Guitar Pro')))
            except Exception:
                results.put(('error', 'Guitar Pro 导入失败。请用原软件重新保存该曲谱后重试。'))

        Thread(target=work, daemon=True, name='guitar-pro-import').start()
        self.timer = self.after(35, self.poll)

    def poll(self):
        self.timer = None
        try:
            stage, result = self.results.get_nowait()
        except Empty:
            self.timer = self.after(35, self.poll)
            return
        self.pending = False
        if stage == 'error':
            self.feedback.set(result)
            self.selection_changed()
        elif stage == 'read':
            self.source = result
            self.heading.configure(text=result.title)
            for track in result.tracks:
                tuning = ' '.join(pitch_name(pitch) for pitch in track.tuning)
                if track.capo:
                    tuning += f' · Capo {track.capo}'
                name = track.name if track.supported else track.name + '（非六弦轨）'
                self.tracks.insert('', 'end', iid=str(track.index), values=(name, tuning, track.note_count if track.supported else '—'))
                if track.supported and track.note_count:
                    self.eligible_tracks.append(track.index)
            first = str(self.eligible_tracks[0])
            self.tracks.selection_set(first)
            self.tracks.focus(first)
            if len(self.eligible_tracks) >= 2:
                names = [f'{index + 1}. {result.tracks[index].name}' for index in self.eligible_tracks]
                for choice in self.duet_choices.values():
                    choice.configure(values=names)
                self.duet_controls.grid(row=3, column=0, sticky='ew', pady=(8, 0))
                self.duet_button.pack(side='right', padx=8)
            self.feedback.set(self.ready_message())
            self.selection_changed()
        else:
            if stage == 'document' and self.source_role and self.source_editor is self.app.tab_editor:
                self.replace_duet_track(*result)
                return
            if not self.prepare_editor():
                self.selection_changed()
                return
            if stage == 'duet':
                editor, summary = self.open_duet_editor(*result)
            else:
                document, notices = result
                editor = self.app.open_fingerstyle_editor(document)
                title = f'{self.path.stem} · {self.source.tracks[self.selected_track].name}'
                if editor is not None:
                    editor.title(f'{title} · 吉他编曲')
                summary = '已导入 ' + title + '。保存时使用 .gscore。'
                if notices:
                    summary += ' ' + '。'.join(notices) + '。'
            if editor is None:
                self.feedback.set(summary if stage == 'duet' else self.app.status.get())
                self.selection_changed()
                return
            if editor.refresh_id is not None:
                editor.after_cancel(editor.refresh_id)
                editor.refresh_id = None
            editor.refresh()
            editor.feedback.set(summary)
            self.app.status.set(summary)
            suppress_return(self)
            self.destroy()
            present(editor, editor.canvas)

    def replace_duet_track(self, document, notices):
        editor = self.source_editor
        if self.app.busy:
            self.feedback.set('请先停止演奏，再替换轨道。')
        elif editor.switch_track(self.source_role) and editor.settings_changed() and editor.replace_current_track(document,keep_clock=True):
            title = self.source.tracks[self.selected_track].name
            summary = f'已将「{title}」导入{TRACK_NAMES[self.source_role]}，沿用当前双轨的速度和拍号。可用 Ctrl+Z 撤销。'
            if notices:
                summary += ' ' + '。'.join(notices) + '。'
            editor.feedback.set(summary)
            self.destroy()
            return
        else:
            self.feedback.set(editor.feedback.get())
        self.selection_changed()

    def prepare_editor(self):
        if self.app.busy:
            self.feedback.set('请先停止演奏，再导入所选轨道。')
            return False
        editor = self.app.tab_editor
        if editor is None or editor is not self.source_editor:
            if self.app.editor_allows_replacement():
                return True
            self.feedback.set('请先保存并关闭当前编谱，再重试导入。')
            return False

        if not editor.settings_changed():
            self.feedback.set(editor.feedback.get())
            return False
        if editor.changed or (getattr(editor, 'staged', False) and editor.file_path is None):
            save = messagebox.askyesnocancel('导入 Guitar Pro', '是否先保存当前曲谱，再打开导入的曲谱？', parent=self)
            if save is None:
                self.feedback.set('已取消换谱。可以继续选择轨道，或关闭本窗口返回编谱。')
                return False
            if save and editor.save_file() is None:
                self.feedback.set('当前曲谱尚未保存。请保存后重试，或关闭本窗口返回编谱。')
                return False
            if not save:
                suppress_return(editor)
                editor.destroy()
                return True

        editor.close()
        if editor.winfo_exists():
            self.feedback.set(editor.feedback.get())
            return False
        return True

    def open_duet_editor(self, project, notices):
        workspace = self.app.open_recording_sync()
        if workspace is None:
            return None, self.app.status.get()
        editor = workspace.open_duet_editor(project=project)
        if editor is None:
            return None, workspace.feedback.get()
        editor.title(f'{self.source.title} · 双轨吉他编曲')
        summary = '已导入双轨。' + '，'.join(
            f'{TRACK_NAMES[role]}为「{self.source.tracks[index].name}」({TONE_NAMES[project.documents[role].default_timbre]})'
            for role, index in zip(ROLES, self.selected_duet)
        ) + '。'
        instructions = '可选轨并点“5 秒后演奏当前轨”。编配两轨时，在“自动编配双轨…”选择“保留原轨与音色”。'
        import_notices = ' '.join(notice + '。' for role in ROLES for notice in notices[role])
        return editor, ' '.join(part for part in (summary, instructions, import_notices) if part)

    def ready_message(self):
        prompt = '选择有音符的六弦轨道，导入后可编辑、试听或转译。'
        if len(self.eligible_tracks) >= 2:
            prompt = '可选一轨单独导入，或指定录音伴奏和现场声部进入双轨编谱。'
        return f'{len(self.source.order)} 小节 · {prompt}'

    def duet_selection_changed(self, _event=None):
        self.selection_changed()
        if not self.pending:
            recorded, live = (self.duet_choices[role].current() for role in ROLES)
            if recorded == live and recorded >= 0:
                self.feedback.set('录音伴奏和现场声部请选择不同的轨道。')
            else:
                self.feedback.set(self.ready_message())

    def selection_changed(self, _event=None):
        selection = self.tracks.selection()
        single = not self.pending and selection and int(selection[0]) in self.eligible_tracks
        self.open_button.configure(state='normal' if single else 'disabled')
        duet = not self.pending and len(self.eligible_tracks) >= 2
        for choice in self.duet_choices.values():
            choice.configure(state='readonly' if duet else 'disabled')
        recorded, live = (self.duet_choices[role].current() for role in ROLES)
        self.duet_button.configure(state='normal' if duet and min(recorded, live) >= 0 and recorded != live else 'disabled')

    def open_selected(self, _event=None):
        if self.pending or self.source is None or not self.tracks.selection():
            return
        self.selected_track = int(self.tracks.selection()[0])
        track = self.source.tracks[self.selected_track]
        if not track.supported or not track.note_count:
            self.feedback.set('请选择有音符的六弦吉他轨道。')
            return
        self.feedback.set('正在准备六线谱…')
        self.run('document', import_guitar_pro_track, self.source, self.selected_track, self.approximate_bends.get())

    def open_duet(self):
        if self.pending or len(self.eligible_tracks) < 2:
            return
        recorded, live = (self.duet_choices[role].current() for role in ROLES)
        if min(recorded, live) < 0 or recorded == live:
            self.feedback.set('请分别为录音伴奏和现场声部选择不同的六弦吉他轨道。')
            return
        self.selected_duet = (self.eligible_tracks[recorded], self.eligible_tracks[live])
        self.feedback.set('正在准备双轨六线谱…')
        self.run('duet', import_guitar_pro_duet, self.source, *self.selected_duet, self.approximate_bends.get())

    def destroy(self):
        if self.timer is not None:
            self.after_cancel(self.timer)
            self.timer = None
        if getattr(self.app, 'guitar_pro_window', None) is self:
            self.app.guitar_pro_window = None
        super().destroy()
