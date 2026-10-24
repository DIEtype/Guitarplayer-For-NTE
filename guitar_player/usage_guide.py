"""Offline task-oriented guide using the same action names as the workspace."""
import tkinter as tk
from tkinter import ttk

from .updates import TUTORIAL_URL

TOPICS=(
('start','快速开始','先选一条开始方式',
 '蓝色“导入 MIDI”：选择文件、轨道和转换方式。\n蓝色“我的曲库”：导入、收藏、试听并载入已有曲谱。\n青色“手动编谱”：在六线谱上点击弦线、键入品位。\n\n先点“本地试听”核对乐曲，再选游戏窗口、校准布局。粉色“5 秒后自动演奏”会向游戏发输入；倒计时内切回游戏吉他界面。F9 停止，F8 暂停或继续。\n\n第一次使用可以先载入试弹曲，再跟着视频核对操作。',
 ('载入试弹曲','load_demo')),
('compose','手动编谱','在六线谱上填写音符与扫弦',
 '点击上方青色“手动编谱”，进入吉他编曲窗口。\n\n从上到下为吉他 1～6 弦。点击某根弦，键入 0～24 品；0 是空弦。用音符图形选择全音符、二分、四分、八分、十六分等时值。\n\n在“音符与奏法”中标记和弦；X／● 表示拨该和弦对应弦，数字表示具体品位。U／D 填写上扫／下扫；游戏上扫为 1→6 弦，下扫为 6→1 弦。\n\n“直接载入原谱”保留原来的音符和节奏；橙色“近似转译…”会另行生成游戏可执行的近似谱。\n\nDel 删除选中弦的音符，保留节拍与其他弦；Ctrl+Del 删除整列节拍，当前小节内的后续格前移。Insert 插入空节拍，Ctrl+Z 撤销。',
 ('打开手动编谱','open_visual_editor')),
('midi','MIDI 与曲库','先选声部，再决定怎样转译',
 '“导入 MIDI”可选择文件中的轨道。先找主旋律所在轨；整曲混音自动转出的 MIDI 可能混有泛音、噪声和其他乐器，应先试听原 MIDI。\n\n混合转译会优先保留指定主旋律，再尝试用和弦或品格补入伴奏。可在“声部与去留”核对推断结果，手动指定保留或伴奏。自动移调会比较整曲升降调；受保护的主旋律不会局部降八度。\n\n转译失败时可在“移调比较”定位失败音，再到声部列表核对；高低音跨度过大时，整体移调也可能无解。\n\n“我的曲库”保存曲谱、MIDI 与生成的 WAV 参考音频。导入文件后选中它，再点“载入编谱”；原文件保持独立。',
 ('导入 MIDI','import_midi')),
('game','游戏演奏','确认连接，再把输入送进游戏',
 '1. 打开游戏吉他界面，在主窗口“游戏窗口”下拉框选择游戏；没有时点“刷新”。\n\n2. 在右侧“游戏准备”填写游戏当前的品格／和弦模式、清音／失真音色与页码。它们是你填写的起始状态，并非程序自动识别的结果。\n\n3. 点“校准布局”设置圆盘区域；窗口大小或界面位置变化后重新校准。点“输入测试”先确认能拨弦，再测试选区、翻页和扫弦。\n\n4. 点“本地试听”听参考效果。准备好后点粉色“5 秒后自动演奏”，倒计时内切回游戏；F9 停止。\n\n主窗口圆盘按演奏谱的起音事件标出和弦／品格、页码、拨弦与扫弦方向；提前准备的选区另行提示。高亮表示本次起音，不表示该弦一直按住。它没有回读游戏画面或声音，无法确认游戏是否成功收到输入。原谱在编谱窗口试听时，以编谱光标为准；近似转译后圆盘显示的是生成的演奏谱。\n\n本地试听是合成参考音，不代表游戏已收到输入。鼠标选圆盘时请尽量避免手动移动鼠标。游戏按键 1～6 对应吉他 6～1 弦。',
 ('打开输入测试','open_input_tests')),
('pages','混合与起始页','什么时候要填写两种模式的页码？',
 '游戏通过 Z 切换和弦与品格模式，而且两种模式各自保留页码。主界面右侧的页码，只描述游戏当前显示的模式。\n\n混合曲谱会在两种模式间切换，因此还需要知道另一种模式停在哪一页。到“游戏设置 → 分模式起始页”填写：和弦 1～4 页，品格 1～5 页；品格第 5 页为空弦。\n\n若游戏两种模式都停在第一页，保持 1／1 即可。若把其中一种停在其他页而没有修改设置，切换后的选区就可能错位。\n\n程序按已填写状态规划翻页；不会从日志自动发现游戏当前页码。手动翻页或重新打开吉他界面后，请再次核对。',
 ('设置分模式起始页','pages')),
('timing','输入与日志','日志调优有用，但只补偿本机耗时',
 '“游戏设置 → 输入时序与日志”集中管理档位、最短按键、按谱保持时值和输入衔接。一般先保留默认“稳拍自适应”。\n\n最短按键是一次触发至少按住多久；音符的音乐时值仍由谱面决定。“按谱面时值保持”开启后，拨弦与扫弦按谱面安排松开。\n\n自适应输入衔接用于提前准备选区、给松开动作留余量，可在所有档位使用。\n\n日志调优统计本机输入调用和调度耗时，样本足够时据此预留余量；样本不足时仍用默认值。它不会改 BPM、移动谱面起拍，也不能知道游戏到底有没有发声，更不会仅凭日志自动学会游戏响应速度。\n\n切换后漏音时，先用“输入测试”确认是否选到了正确区域，再检查起始页和输入档位；不建议只把最短按键不断调小。',
 ('打开输入设置','timing')),
('trouble','常见问题','按现象排查',
 '倒计时正常，游戏无变化\n先核对目标窗口、切回游戏前台，再做单键拨弦测试。权限提示出现时重新使用程序的管理员启动；选区不动时重新校准。\n\n弹到了旧和弦或错误品位\n核对游戏当前模式、当前页和分模式起始页，再测试鼠标或手柄选择。\n\n提示触发间隔不足\n看提示中的事件位置：重复同键、换区、翻页和模式切换都有耗时。可以修改对应段落、尝试其他指法或输入档位；单纯减小最短按键不保证游戏能收到。\n\n原谱能试听但不能原样演奏\n原谱仍可编辑和保存；同拍跨品等内容可能超出游戏输入能力。按需选择“近似转译…”，并先试听结果。\n\n检测更新失败\n不影响编谱和本地试听。点底部“版本与下载”重试，或使用其中的百度网盘备用入口。',
 ('游戏准备教程','game')),
('text','文本谱语法','字符网格、逐音时值与指令谱',
 '文本六线谱\n从上到下是 1～6 弦，数字表示品位，0 表示空弦。没有“节奏:”行时，字符间距按左侧“字符网格”计时；这只影响文本输入，不限制图形编谱精度。\n\n逐音节奏\n每段六线谱前写：\n节奏: 1 1/2 1/3 3/2\n依次对应一拍、半拍、三分之一拍、一拍半。r:1/2 插入半拍休止；有节奏行时字符间距不计时。\n\n指令谱\n{II} 6 5:1/2 [3 2 1]:2 | {Zero} 6 | ↓ ↑ 0\n花括号为选区，数字表示弦号；冒号后为拍数。↑ 上扫 1→6 弦，↓ 下扫 6→1 弦，0 为休止。\n\n字符谱适合已有文本；新手可优先使用上方“手动编谱”。',
 ('打开手动编谱','open_visual_editor')),
)


class UsageGuide(tk.Toplevel):
    def __init__(self,app,topic='start'):
        super().__init__(app);self.app=app;self.title('使用教程 · 异环吉他编曲器');self.transient(app)
        width=min(880,self.winfo_screenwidth()-60);height=min(650,self.winfo_screenheight()-90)
        self.geometry(f'{width}x{height}');self.minsize(min(720,width),min(510,height));self.configure(background='#edf2f6')
        ttk.Label(self,text='使用教程',font=('Microsoft YaHei UI',19,'bold'),padding=(18,14)).pack(anchor='w')
        footer=ttk.Frame(self,padding=14);footer.pack(side='bottom',fill='x')
        self.action_button=ttk.Button(footer,style='Workspace.Compose.TButton');self.action_button.pack(side='left')
        ttk.Button(footer,text='看视频教程',command=lambda:app.welcome.open_link(TUTORIAL_URL)).pack(side='left',padx=9)
        ttk.Button(footer,text='版本与下载',command=app.open_welcome).pack(side='right')
        ttk.Button(footer,text='关闭',command=self.destroy).pack(side='right',padx=7)
        body=ttk.Frame(self);body.pack(fill='both',expand=True,padx=18,pady=(0,10))
        self.navigation=ttk.Treeview(body,show='tree',selectmode='browse',height=9)
        self.navigation.column('#0',width=140,stretch=False);self.navigation.pack(side='left',fill='y',padx=(0,12))
        textframe=ttk.Frame(body);textframe.pack(fill='both',expand=True)
        scrollbar=ttk.Scrollbar(textframe);scrollbar.pack(side='right',fill='y')
        self.text=tk.Text(textframe,wrap='word',font=('Microsoft YaHei UI',11),background='white',foreground='#20394e',
                          relief='flat',padx=20,pady=18,spacing3=5,yscrollcommand=scrollbar.set)
        self.text.pack(fill='both',expand=True);scrollbar.configure(command=self.text.yview)
        self.text.tag_configure('title',font=('Microsoft YaHei UI',15,'bold'),foreground='#0e7380',spacing3=14)
        for key,label,*_ in TOPICS:self.navigation.insert('', 'end',iid=key,text=label)
        self.navigation.bind('<<TreeviewSelect>>',self.selected);self.select(topic)

    def select(self,topic):
        if not self.navigation.exists(topic):topic='start'
        self.navigation.selection_set(topic);self.navigation.see(topic);self.show_topic(topic)

    def selected(self,event=None):
        selected=self.navigation.selection()
        if selected:self.show_topic(selected[0])

    def show_topic(self,key):
        row=next(row for row in TOPICS if row[0]==key);self.topic=key
        self.text.configure(state='normal');self.text.delete('1.0','end')
        self.text.insert('end',row[2]+'\n','title');self.text.insert('end',row[3]);self.text.configure(state='disabled');self.text.yview_moveto(0)
        title,action=row[4];self.action_button.configure(text=title,command=lambda:self.run_action(action))

    def run_action(self,action):
        if action in ('pages','timing'):
            self.destroy();self.app.open_game_settings(action)
        elif action=='game':self.select('game')
        else:
            self.destroy();getattr(self.app,action)()
