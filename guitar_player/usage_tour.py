"""A first-use walkthrough anchored to the actual workspace controls."""
from dataclasses import dataclass
import json
from pathlib import Path
import tkinter as tk
from tkinter import ttk

INK='#20394e';MUTED='#62798d';TEAL='#0e7380';PAPER='#ffffff';LINE='#d8e2eb'


@dataclass(frozen=True)
class TourStep:
    target: str
    title: str
    body: str


STEPS=(
    TourStep('import_button','先选择曲谱来源',
        '已有 MIDI，点蓝色“导入 MIDI”选择轨道；已有收藏，点“我的曲库”。\n\n想自己写谱，使用旁边青色的“手动编谱”。引导只介绍按钮，不会自动导入或演奏。'),
    TourStep('compose_button','在六线谱上写音符',
        '进入“手动编谱”后，点击弦线并键入品位，再用音符图形选择时值；顶部可设置 BPM 和拍号。\n\n“直接载入原谱”保留原有内容；“近似转译…”另行生成适合游戏输入的演奏谱。'),
    TourStep('audition_button','先在本地听一遍',
        '“本地试听”用合成参考音核对曲子，不发送游戏按键。原谱会在编谱窗口中试听。\n\n核对旋律、节奏和速度后，再准备游戏连接；试听效果不代表游戏已经收到输入。'),
    TourStep('window_box','选择要演奏的游戏窗口',
        '打开游戏的吉他界面，在这里选择游戏窗口。没有找到时，点旁边的“刷新”。\n\n右侧“游戏当前”的模式、音色与页码，需要按游戏实际状态填写。混合演奏另到“游戏设置 → 分模式起始页”填写两种模式的页码。'),
    TourStep('calibrate_button','校准位置，再测试输入',
        '鼠标选择圆盘前，点“校准布局”对齐游戏中的区域。窗口大小或界面位置改变后重新校准。\n\n随后点“输入测试”，先验证单弦拨弦，再测试选区、翻页与扫弦。'),
    TourStep('game_play_button','准备好后再自动演奏',
        '点击粉色按钮后有 5 秒倒计时，请切回游戏吉他界面。F8 暂停／继续，F9 停止；快捷键是否可用以主界面提示为准。\n\n完成或跳过后，引导不再自动弹出。需要重看时，点击右上角“使用引导”。'),
)


def read_seen(directory):
    if directory is None:return False
    try:
        data=json.loads((Path(directory)/'usage-tour.json').read_text(encoding='utf-8'))
        return isinstance(data,dict) and data.get('seen') is True
    except (OSError,ValueError):return False


def card_position(anchor,size,bounds,gap=12):
    """Prefer an unobstructed side of the target, then clamp to its monitor."""
    left,top,right,bottom=anchor;width,height=size
    xmin,ymin,xmax,ymax=bounds;margin=10
    candidates=((left,bottom+gap),(left,top-height-gap),
                (right+gap,top),(left-width-gap,top))
    def clamp(x,y):
        return (int(max(xmin+margin,min(x,xmax-width-margin))),
                int(max(ymin+margin,min(y,ymax-height-margin))))
    for x,y in candidates:
        x,y=clamp(x,y)
        if x+width<=left or x>=right or y+height<=top or y>=bottom:return x,y
    return clamp(*candidates[0])


def work_area(widget):
    # Monitor work area excludes the taskbar, including on secondary monitors.
    try:
        import ctypes
        from ctypes import wintypes
        class MonitorInfo(ctypes.Structure):
            _fields_=[('cbSize',wintypes.DWORD),('rcMonitor',wintypes.RECT),('rcWork',wintypes.RECT),('dwFlags',wintypes.DWORD)]
        user=ctypes.windll.user32
        monitor_from=user.MonitorFromRect;monitor_from.argtypes=[ctypes.POINTER(wintypes.RECT),wintypes.DWORD];monitor_from.restype=wintypes.HANDLE
        get_info=user.GetMonitorInfoW;get_info.argtypes=[wintypes.HANDLE,ctypes.POINTER(MonitorInfo)];get_info.restype=wintypes.BOOL
        x,y=widget.winfo_rootx(),widget.winfo_rooty()
        rect=wintypes.RECT(x,y,x+widget.winfo_width(),y+widget.winfo_height())
        info=MonitorInfo();info.cbSize=ctypes.sizeof(info)
        if get_info(monitor_from(ctypes.byref(rect),2),ctypes.byref(info)):
            r=info.rcWork;return r.left,r.top,r.right,r.bottom
    except (AttributeError,OSError):pass
    x,y=widget.winfo_vrootx(),widget.winfo_vrooty()
    return x,y,x+widget.winfo_vrootwidth(),y+widget.winfo_vrootheight()


class UsageTour:
    def __init__(self,app):
        self.app=app;self.seen=read_seen(app.settings_dir);self.window=None;self.index=0
        self.highlights=[];self.position_id=None;self.closed=False;self.previous_focus=None
        self.anchor=None;self.anchor_binding=None
        self.bindings=[]
        for sequence,handler in (('<Configure>',self.moved),('<Unmap>',self.unmapped),
                                 ('<Map>',self.mapped),('<ButtonPress-1>',self.clicked_workspace)):
            self.bindings.append((sequence,app.bind(sequence,handler,add='+')))

    @property
    def active(self):return self.window is not None

    def maybe_start(self):
        if self.seen or self.active or self.closed:return False
        if self.app.busy or self.app.grab_current() or not self.app.winfo_viewable():return False
        if any(isinstance(child,tk.Toplevel) and child.winfo_viewable() for child in self.app.winfo_children()):return False
        return self.start()

    def start(self):
        if self.closed:return False
        if self.app.busy:
            self.app.status.set('请先停止演奏、试听或输入测试，再打开使用引导。');return False
        if self.active:
            self.index=0;self.show_step();return True
        self.previous_focus=self.app.focus_get()
        window=tk.Toplevel(self.app);self.window=window;window.withdraw()
        window.title('使用引导');window.transient(self.app);window.overrideredirect(True)
        window.attributes('-topmost',self.app.attributes('-topmost'))
        window.protocol('WM_DELETE_WINDOW',self.finish)
        window.configure(background=TEAL,padx=2,pady=2)
        window.bind('<Escape>',lambda event:self.finish())
        body=tk.Frame(window,bg=PAPER,padx=18,pady=14);body.pack(fill='both',expand=True)
        self.progress_label=tk.Label(body,bg=PAPER,fg=TEAL,font=('Microsoft YaHei UI',9,'bold'),anchor='w')
        self.progress_label.pack(fill='x')
        self.title_label=tk.Label(body,bg=PAPER,fg=INK,font=('Microsoft YaHei UI',14,'bold'),anchor='w',justify='left')
        self.title_label.pack(fill='x',pady=(5,9))
        self.body_label=tk.Label(body,bg=PAPER,fg=INK,font=('Microsoft YaHei UI',10),anchor='w',justify='left')
        self.body_label.pack(fill='x')
        self.progress=tk.Canvas(body,height=4,bg=PAPER,highlightthickness=0);self.progress.pack(fill='x',pady=(14,10))
        footer=tk.Frame(body,bg=PAPER);footer.pack(fill='x')
        self.skip_button=ttk.Button(footer,text='跳过引导',command=self.finish,padding=(8,5))
        self.skip_button.pack(side='left')
        self.next_button=ttk.Button(footer,text='下一步',command=self.next,style='Workspace.Compose.TButton',padding=(10,5))
        self.next_button.pack(side='right')
        self.previous_button=ttk.Button(footer,text='上一步',command=self.previous,padding=(8,5))
        self.previous_button.pack(side='right',padx=6)
        self.index=0;self.show_step();return True

    def show_step(self):
        step=STEPS[self.index]
        anchor=getattr(self.app,step.target)
        if self.anchor_binding is not None:self.anchor.unbind('<Configure>',self.anchor_binding)
        self.anchor=anchor;self.anchor_binding=anchor.bind('<Configure>',self.moved,add='+')
        bounds=self.bounds(anchor);self.card_width=min(390,max(290,bounds[2]-bounds[0]-24))
        self.title_label.configure(text=step.title,wraplength=self.card_width-40)
        self.body_label.configure(text=step.body,wraplength=self.card_width-40)
        self.progress_label.configure(text=f'使用引导  {self.index+1} / {len(STEPS)}')
        self.previous_button.configure(state='disabled' if self.index==0 else 'normal')
        self.next_button.configure(text='完成' if self.index==len(STEPS)-1 else '下一步')
        self.progress.delete('all');span=(self.card_width-40)/len(STEPS)
        for i in range(len(STEPS)):
            self.progress.create_rectangle(i*span,0,(i+1)*span-5,4,fill=TEAL if i<=self.index else LINE,outline='')
        for frame in self.highlights:frame.destroy()
        self.highlights=[tk.Frame(anchor.master,bg=TEAL) for _ in range(4)]
        self.window.update_idletasks();self.position();self.window.deiconify();self.window.lift()
        self.next_button.focus_set()

    def bounds(self,anchor):
        area=work_area(anchor);app=self.app
        left,top=app.winfo_rootx(),app.winfo_rooty()
        inside=(max(area[0],left),max(area[1],top),min(area[2],left+app.winfo_width()),min(area[3],top+app.winfo_height()))
        return inside if inside[2]-inside[0]>=320 and inside[3]-inside[1]>=400 else area

    def position(self):
        self.position_id=None
        if not self.active or self.closed:return
        anchor=getattr(self.app,STEPS[self.index].target)
        if not self.app.winfo_viewable():return
        x,y=anchor.winfo_x(),anchor.winfo_y();width,height=anchor.winfo_width(),anchor.winfo_height();edge=3
        for frame,geometry in zip(self.highlights,((x-edge,y-edge,width+edge*2,edge),
                (x-edge,y+height,width+edge*2,edge),(x-edge,y,edge,height),(x+width,y,edge,height))):
            a,b,w,h=geometry;frame.place(x=a,y=b,width=w,height=h,bordermode='outside');frame.lift()
        left,top=anchor.winfo_rootx(),anchor.winfo_rooty()
        height=self.window.winfo_reqheight()
        px,py=card_position((left,top,left+width,top+anchor.winfo_height()),(self.card_width,height),self.bounds(anchor))
        # '+-100' is an absolute negative coordinate; '-100' means an offset
        # from the screen's right edge in Tk geometry syntax.
        self.window.geometry(f'{self.card_width}x{height}+{px}+{py}')

    def moved(self,event):
        if event.widget in (self.app,self.anchor) and self.active and self.position_id is None:
            self.position_id=self.app.after_idle(self.position)

    def unmapped(self,event):
        if event.widget is self.app and self.active:
            self.window.withdraw()
            for frame in self.highlights:frame.place_forget()

    def mapped(self,event):
        if event.widget is self.app and self.active:
            self.position();self.window.deiconify()

    def clicked_workspace(self,event):
        if self.active and event.widget.winfo_toplevel() is self.app:self.finish(restore_focus=False)

    def next(self):
        if self.index==len(STEPS)-1:self.finish()
        else:self.index+=1;self.show_step()

    def previous(self):
        if self.index>0:self.index-=1;self.show_step()

    def finish(self,restore_focus=True):
        if not self.active:return
        if self.position_id is not None:self.app.after_cancel(self.position_id);self.position_id=None
        if self.anchor_binding is not None:self.anchor.unbind('<Configure>',self.anchor_binding)
        self.anchor=None;self.anchor_binding=None
        window=self.window;self.window=None;window.destroy()
        for frame in self.highlights:frame.destroy()
        self.highlights=[];self.seen=True
        if self.app.settings_dir is not None:
            try:
                path=Path(self.app.settings_dir)/'usage-tour.json';path.parent.mkdir(parents=True,exist_ok=True)
                temporary=path.with_suffix('.json.tmp')
                temporary.write_text(json.dumps({'seen':True},indent=2),encoding='utf-8');temporary.replace(path)
            except OSError:
                self.app.status.set('引导已关闭，但使用记录未保存；下次可能再次出现。请检查程序文件夹的写入权限。')
        if restore_focus and self.previous_focus is not None:
            try:
                if self.previous_focus.winfo_exists():self.previous_focus.focus_set()
            except tk.TclError:pass

    def close(self):
        self.finish(restore_focus=False);self.closed=True
        for sequence,identifier in self.bindings:self.app.unbind(sequence,identifier)
        self.bindings=[]
