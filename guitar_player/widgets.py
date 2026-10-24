"""Readable performance cues shared by the editor and the music library."""
import tkinter as tk


def notice_style(message):
    if any(word in message for word in ('失败','错误','有误','未写入','未应用','未保存','未开始演奏','停止以免','Windows 只接受','权限级别高于')):
        return '操作未完成','#a62d43','#fff0f2'
    if any(word in message for word in ('需要调整','需检查','间隔不足','先在','请先','未就绪','不在前台','尚未','超出','无法','无效','应为')):
        return '请调整','#8a5709','#fff1d5'
    if any(word in message for word in ('秒后','正在','将在')):
        return '正在进行','#a92e65','#fbe7f0'
    if any(word in message for word in ('已停止','已取消')):
        return '已停止','#41536b','#e5ebf2'
    if any(word in message for word in ('已保存','已入库','保存完成','生成完成','发送完成','演奏完成','试听结束')):
        return '已完成','#17664f','#e2f3eb'
    return '提示','#24616f','#e2f1f3'


class StatusBanner(tk.Frame):
    def __init__(self,parent,variable):
        super().__init__(parent,background='#e2f1f3')
        self.variable=variable
        self.stripe=tk.Frame(self,width=5);self.stripe.pack(side='left',fill='y')
        self.badge=tk.Label(self,font=('Microsoft YaHei UI',12,'bold'),padx=12,pady=10)
        self.badge.pack(side='left',anchor='n')
        self.message=tk.Label(self,font=('Microsoft YaHei UI',11,'bold'),justify='left',anchor='w',padx=8,pady=10)
        self.message.pack(side='left',fill='both',expand=True)
        self.bind('<Configure>',lambda event:self.message.configure(wraplength=max(160,event.width-self.badge.winfo_width()-30)))
        self.trace=variable.trace_add('write',self.refresh);self.refresh()

    def refresh(self,*_):
        title,color,tint=notice_style(self.variable.get())
        self.configure(background=tint);self.stripe.configure(background=color)
        self.badge.configure(text=title,foreground=color,background=tint)
        self.message.configure(text=self.variable.get(),foreground=color,background=tint)

    def destroy(self):
        self.variable.trace_remove('write',self.trace)
        super().destroy()


class ScrollableStatusBanner(tk.Frame):
    """Keep a long diagnostic readable without hiding conversion controls."""
    def __init__(self,parent,variable,lines=4):
        super().__init__(parent)
        self.variable=variable
        self.stripe=tk.Frame(self,width=5);self.stripe.pack(side='left',fill='y')
        self.badge=tk.Label(self,font=('Microsoft YaHei UI',11,'bold'),padx=10,pady=8)
        self.badge.pack(side='left',anchor='n')
        self.message=tk.Text(self,height=lines,width=1,wrap='word',font=('Microsoft YaHei UI',10,'bold'),
                             relief='flat',padx=8,pady=8,exportselection=False)
        scroll=tk.Scrollbar(self,command=self.message.yview);scroll.pack(side='right',fill='y')
        self.message.configure(yscrollcommand=scroll.set);self.message.pack(side='left',fill='both',expand=True)
        self.trace=variable.trace_add('write',self.refresh);self.refresh()

    def refresh(self,*_):
        title,color,tint=notice_style(self.variable.get())
        self.configure(background=tint);self.stripe.configure(background=color)
        self.badge.configure(text=title,foreground=color,background=tint)
        self.message.configure(state='normal',foreground=color,background=tint)
        self.message.delete('1.0','end');self.message.insert('1.0',self.variable.get())
        self.message.configure(state='disabled');self.message.yview_moveto(0)

    def destroy(self):
        self.variable.trace_remove('write',self.trace)
        super().destroy()
