"""First-use dispatch, download links and a nonblocking release notification."""
import json
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import ttk
import webbrowser

from . import __version__
from .paths import resource_root
from .updates import (CREDIT,REPOSITORY,RELEASES_URL,TUTORIAL_URL,MIRROR_URL,
                      MIRROR_CODE,UpdateCheckError,check_latest)

BG='#edf2f6';PAPER='#ffffff';INK='#20394e';MUTED='#62798d'
ACCENT='#b93270';TEAL='#0e7380';LINE='#d8e2eb'


def read_preferences(directory):
    result={'show_welcome':True,'auto_check':True}
    if directory is None:return result
    try:
        data=json.loads((Path(directory)/'welcome.json').read_text(encoding='utf-8'))
        if isinstance(data,dict):
            for key in result:
                if type(data.get(key)) is bool:result[key]=data[key]
    except (OSError,ValueError):pass
    return result


class WelcomeController:
    def __init__(self,app):
        self.app=app;self.dialog=None;self.closed=False;self.running=False;self.checked=False
        self.results=queue.Queue();self.release=None;self.poll_id=None;self.start_id=None
        saved=read_preferences(app.settings_dir)
        self.hide_welcome=tk.BooleanVar(app,value=not saved['show_welcome'])
        self.auto_check=tk.BooleanVar(app,value=saved['auto_check'])
        self.message=tk.StringVar(app,value='启动时自动检查 GitHub 正式版。' if saved['auto_check'] else '自动检查已关闭；可点击“检查更新”。')
        self.notice=app.help_notice
        self.kind='idle'

    def start(self):
        self.start_id=self.app.after(450,self._startup)

    def _startup(self):
        self.start_id=None
        if self.closed:return
        if self.app.usage_tour is not None:
            self.app.usage_tour.maybe_start()
        elif not self.hide_welcome.get() and not self.app.busy and self.app.tab_editor is None and not self.app.grab_current():
            self.open(check=False)
        if self.auto_check.get():self.check()

    def save(self):
        if self.app.settings_dir is None:return True
        try:
            path=Path(self.app.settings_dir)/'welcome.json';path.parent.mkdir(parents=True,exist_ok=True)
            temporary=path.with_suffix('.json.tmp')
            temporary.write_text(json.dumps({'show_welcome':not self.hide_welcome.get(),
                'auto_check':self.auto_check.get()},ensure_ascii=False,indent=2),encoding='utf-8')
            temporary.replace(path)
        except OSError:
            self.kind='error';self.message.set('偏好未保存；请检查程序文件夹的写入权限，当前选择仍在本次运行中生效。')
            self._refresh();return False
        return True

    def toggle_auto(self):
        if not self.save():return
        if self.auto_check.get() and not self.checked and not self.running:self.check()
        elif not self.auto_check.get() and not self.checked and not self.running:
            self.message.set('自动检查已关闭；可点击“检查更新”。')

    def check(self):
        if self.closed or self.running:return
        self.running=True;self.kind='checking';self.message.set('正在检查 GitHub 正式版…');self.notice.set('正在检查更新…');self._refresh()
        results=self.results
        def worker():
            try:result=check_latest(__version__)
            except UpdateCheckError as error:results.put(('error',str(error)))
            except Exception:results.put(('error','检查更新未完成；请稍后重试，或直接打开下载入口。'))
            else:results.put(('release',result))
        threading.Thread(target=worker,name='release-check',daemon=True).start()
        self.poll_id=self.app.after(60,self._poll)

    def _poll(self):
        self.poll_id=None
        if self.closed:return
        try:kind,value=self.results.get_nowait()
        except queue.Empty:
            self.poll_id=self.app.after(60,self._poll);return
        self.running=False;self.checked=True
        if kind=='release':
            self.release=value;self.kind='new' if value.newer else 'current';self.message.set(value.message)
            self.notice.set(f'发现新版 v{value.version}' if value.newer else '版本与下载')
        else:
            self.kind='error';self.release=None;self.message.set(value);self.notice.set('版本与下载 · 检查未完成')
        self._refresh()

    def _refresh(self):
        if self.dialog is not None and self.dialog.winfo_exists():self.dialog.refresh()

    def open(self,check=True):
        if self.closed:return
        if self.dialog is not None and self.dialog.winfo_exists():
            self.dialog.deiconify();self.dialog.lift();return self.dialog
        self.dialog=WelcomeDialog(self)
        if check and self.auto_check.get() and not self.checked and not self.running:self.check()
        return self.dialog

    def open_link(self,url):
        if url not in (TUTORIAL_URL,MIRROR_URL,RELEASES_URL,REPOSITORY) and not (self.release and url==self.release.url):return
        try:opened=webbrowser.open(url,new=2)
        except (OSError,webbrowser.Error):opened=False
        if not opened:
            self.message.set('未能打开浏览器；请点击“复制链接”，粘贴到浏览器访问。');self.kind='error';self._refresh()

    def copy(self,text):
        self.app.clipboard_clear();self.app.clipboard_append(text);self.app.update_idletasks()
        if self.dialog is not None:self.dialog.link_feedback.set('已复制，可粘贴到浏览器或网盘提取码输入框。')

    def close(self):
        self.closed=True
        for timer in (self.start_id,self.poll_id):
            if timer is not None:
                try:self.app.after_cancel(timer)
                except tk.TclError:pass
        if self.dialog is not None:self.dialog.destroy();self.dialog=None


class WelcomeDialog(tk.Toplevel):
    def __init__(self,controller):
        self.controller=controller;app=controller.app
        super().__init__(app);self.withdraw();self.title('版本与下载 · 异环吉他编曲器 · '+CREDIT)
        self.configure(bg=BG);self.transient(app)
        width=min(750,self.winfo_screenwidth()-60);height=min(600,self.winfo_screenheight()-100)
        self.geometry(f'{width}x{height}+{max(0,app.winfo_rootx()+(app.winfo_width()-width)//2)}+{max(0,app.winfo_rooty()+(app.winfo_height()-height)//2)}')
        self.minsize(min(650,width),min(460,height));self.protocol('WM_DELETE_WINDOW',self.close)
        style=ttk.Style(self)
        for name,color,active in (('Welcome.Primary',TEAL,'#095863'),('Welcome.Update',ACCENT,'#95285c')):
            style.configure(name+'.TButton',background=color,foreground='white',padding=(14,7),font=('Microsoft YaHei UI',10,'bold'))
            style.map(name+'.TButton',background=[('active',active)],foreground=[('disabled','#dce4eb')])
        style.configure('Welcome.TCheckbutton',background=BG,foreground=INK,font=('Microsoft YaHei UI',9))
        style.map('Welcome.TCheckbutton',background=[('active',BG)])
        hero=tk.Frame(self,bg=INK,padx=24,pady=12);hero.pack(fill='x')
        self.avatar=None
        try:
            photo=tk.PhotoImage(file=str(resource_root()/'assets/app-icon.png'))
            self.avatar=photo.subsample(max(1,(photo.width()+79)//80))
            tk.Label(hero,image=self.avatar,bg=INK).pack(side='right',padx=(12,0))
        except tk.TclError:pass
        tk.Label(hero,text='异环 · 吉他编曲器',font=('Microsoft YaHei UI',22,'bold'),fg='white',bg=INK).pack(anchor='w')
        signature=tk.Frame(hero,bg=INK);signature.pack(anchor='w',pady=(7,0))
        tk.Label(signature,text=CREDIT,font=('Bahnschrift',19),fg='#f6b9d2',bg=INK).pack(side='left')
        tk.Label(signature,text=f'v{__version__}',font=('Consolas',11),fg='#c5d6e4',bg=INK).pack(side='left',padx=18)
        footer=tk.Frame(self,bg=BG,padx=22,pady=12);footer.pack(side='bottom',fill='x')
        self.enter_button=ttk.Button(footer,text='进入编曲',style='Welcome.Primary.TButton',command=self.close)
        self.enter_button.pack(side='right',padx=(12,0))
        self.replay_button=ttk.Button(footer,text='重看操作引导',command=self.replay_tour,padding=(8,4))
        self.replay_button.pack(anchor='w')
        self.auto_box=ttk.Checkbutton(footer,text='启动时自动检查更新',style='Welcome.TCheckbutton',variable=controller.auto_check,command=controller.toggle_auto)
        self.auto_box.pack(anchor='w',pady=(4,0))
        surface=tk.Frame(self,bg=BG);surface.pack(fill='both',expand=True)
        bar=ttk.Scrollbar(surface,orient='vertical');bar.pack(side='right',fill='y')
        self.body_canvas=tk.Canvas(surface,bg=BG,highlightthickness=0,yscrollcommand=bar.set)
        self.body_canvas.pack(fill='both',expand=True);bar.configure(command=self.body_canvas.yview)
        content=tk.Frame(self.body_canvas,bg=BG,padx=22,pady=12)
        item=self.body_canvas.create_window(0,0,window=content,anchor='nw')
        content.bind('<Configure>',lambda e:self.body_canvas.configure(scrollregion=self.body_canvas.bbox('all')))
        self.body_canvas.bind('<Configure>',lambda e:self.body_canvas.itemconfigure(item,width=e.width))
        self.bind('<MouseWheel>',lambda e:self.body_canvas.yview_scroll(-int(e.delta/120),'units'))
        tutorial=tk.Frame(content,bg=PAPER,padx=18,pady=10,highlightbackground=LINE,highlightthickness=1);tutorial.pack(fill='x')
        tk.Label(tutorial,text='第一次使用？从教程开始',font=('Microsoft YaHei UI',12,'bold'),bg=PAPER,fg=INK).pack(anchor='w')
        tutorial_row=tk.Frame(tutorial,bg=PAPER);tutorial_row.pack(fill='x',pady=(6,0))
        self.tutorial_button=ttk.Button(tutorial_row,text='观看教程 · Bilibili',style='Welcome.Primary.TButton',command=lambda:controller.open_link(TUTORIAL_URL))
        self.tutorial_button.pack(side='left')
        ttk.Button(tutorial_row,text='复制链接',command=lambda:controller.copy(TUTORIAL_URL)).pack(side='left',padx=10)
        update=tk.Frame(content,bg=PAPER,padx=18,pady=10,highlightbackground=LINE,highlightthickness=1);update.pack(fill='x',pady=(12,0))
        update_heading=tk.Frame(update,bg=PAPER);update_heading.pack(fill='x')
        tk.Label(update_heading,text='版本与下载',font=('Microsoft YaHei UI',12,'bold'),bg=PAPER,fg=INK).pack(side='left')
        self.check_button=ttk.Button(update_heading,text='检查更新',command=controller.check);self.check_button.pack(side='right')
        self.update_label=tk.Label(update,textvariable=controller.message,bg=PAPER,fg=MUTED,font=('Microsoft YaHei UI',10),anchor='w',justify='left',wraplength=580)
        self.update_label.pack(fill='x',pady=(6,3));update.bind('<Configure>',lambda e:self.update_label.configure(wraplength=max(200,e.width-38)))
        row=tk.Frame(update,bg=PAPER);row.pack(fill='x',pady=(2,3))
        self.release_button=ttk.Button(row,text='GitHub 下载页',command=self.open_release);self.release_button.pack(side='left')
        self.mirror_button=ttk.Button(row,text='百度网盘 · 备用',command=lambda:controller.open_link(MIRROR_URL));self.mirror_button.pack(side='left',padx=9)
        ttk.Button(row,text='复制下载链接',command=lambda:controller.copy(MIRROR_URL)).pack(side='left')
        code=tk.Frame(update,bg=PAPER);code.pack(fill='x')
        tk.Label(code,text='网盘提取码  '+MIRROR_CODE,bg=PAPER,fg=INK,font=('Microsoft YaHei UI',10,'bold')).pack(side='left')
        self.copy_code_button=ttk.Button(code,text='复制提取码',command=lambda:controller.copy(MIRROR_CODE));self.copy_code_button.pack(side='left',padx=12)
        ttk.Button(code,text='复制 GitHub 链接',command=lambda:controller.copy(controller.release.url if controller.release and controller.release.newer else RELEASES_URL)).pack(side='left')
        tk.Label(update,text='网盘为备用下载入口，版本提示以 GitHub 正式版为准。',bg=PAPER,fg=MUTED,font=('Microsoft YaHei UI',9),anchor='w').pack(fill='x',pady=(8,0))
        self.link_feedback=tk.StringVar(self)
        feedback=tk.Label(content,textvariable=self.link_feedback,fg=TEAL,bg=BG,font=('Microsoft YaHei UI',9),anchor='w')
        self.link_feedback.trace_add('write',lambda *_:feedback.pack(fill='x',pady=(6,0)) if self.link_feedback.get() else feedback.pack_forget())
        self.refresh();self.bind('<Escape>',lambda e:self.close());self.deiconify()

    def open_release(self):
        c=self.controller;c.open_link(c.release.url if c.release and c.release.newer else RELEASES_URL)

    def refresh(self):
        c=self.controller
        self.check_button.configure(state='disabled' if c.running else 'normal')
        self.update_label.configure(fg=ACCENT if c.kind=='new' else '#9c4930' if c.kind=='error' else MUTED)
        self.release_button.configure(text='查看新版' if c.kind=='new' else 'GitHub 下载页',
            style='Welcome.Update.TButton' if c.kind=='new' else 'TButton')

    def close(self):
        if not self.controller.save():self.controller.app.status.set(self.controller.message.get())
        self.controller.dialog=None;self.destroy()

    def replay_tour(self):
        app=self.controller.app
        self.close();app.open_usage_tour()
