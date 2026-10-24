"""Rhythm-group previews and explicit measure-range operations."""
import tkinter as tk
from tkinter import ttk
from fractions import Fraction as F

from .game_profile import CHORD_NAMES
from .guitar import UP
from .rhythm_patterns import PATTERNS,PATTERN_MAP,pattern_cells,apply_pattern
from .composition import copy_measures,set_repeat_section
from .rhythm_library import read_patterns,save_pattern,capture_pattern,rename_pattern,delete_pattern
from .composer_guidance import edit_guidance,file_guidance,integer_field,measure_range


def pattern_path(editor):
    return editor.parent.settings_dir/'rhythm_patterns.json' if editor.parent.settings_dir else None


def save_rhythm_dialog(editor,on_saved=None,owner=None,existing=None):
    index=editor.doc.bar_index(editor.at);meter=editor.doc.bar(index).meter
    dialog=tk.Toplevel(owner or editor);dialog.title('保存节奏预设');dialog.transient(owner or editor);dialog.resizable(False,False)
    ttk.Label(dialog,text=f'用第 {index+1} 小节更新此预设' if existing else f'将第 {index+1} 小节存入“我的预设”',font=('Microsoft YaHei UI',13,'bold'),padding=14).pack(anchor='w')
    row=ttk.Frame(dialog,padding=(14,0));row.pack(fill='x');ttk.Label(row,text='预设名称').pack(side='left',padx=(0,8))
    name=tk.StringVar(value=existing.name if existing else f'我的节奏 {meter[0]}/{meter[1]}');entry=ttk.Entry(row,textvariable=name,width=34);entry.pack(side='left')
    adaptive=tk.BooleanVar(value=True)
    ttk.Checkbutton(dialog,text='低音弦随所选和弦自动适配',variable=adaptive).pack(anchor='w',padx=14,pady=(12,4))
    ttk.Label(dialog,text='保存时值、休止、拨弦位置与扫弦方向；数字品位转为弦上击奏，应用时采用目标和弦。',wraplength=420,padding=(14,6)).pack(anchor='w')
    info=tk.StringVar();ttk.Label(dialog,textvariable=info,foreground='#a62d43',wraplength=420,padding=(14,4)).pack(anchor='w')
    def close():
        dialog.destroy();target=owner or editor
        if target.winfo_viewable():target.grab_set()
        if owner is None:editor.canvas.focus_set()
    def save():
        try:
            if not editor.settings_changed():raise ValueError(editor.feedback.get())
            item=capture_pattern(editor.doc,index,name.get(),adaptive.get(),key=existing.key if existing else None);save_pattern(pattern_path(editor),item)
            if on_saved:on_saved(item)
            editor.feedback.set('已保存节奏预设：'+item.name);close()
        except (ValueError,OSError) as error:info.set('未保存：'+(file_guidance(error,'保存预设') if isinstance(error,OSError) else edit_guidance(str(error))))
    row=ttk.Frame(dialog,padding=14);row.pack(fill='x')
    ttk.Button(row,text='更新预设' if existing else '保存预设',command=save,style='Composer.Load.TButton').pack(side='right')
    ttk.Button(row,text='取消',command=close).pack(side='right',padx=8)
    entry.bind('<Return>',lambda event:save());dialog.protocol('WM_DELETE_WINDOW',close)
    if (owner or editor).winfo_viewable():dialog.grab_set();entry.focus_set();entry.select_range(0,'end')
    dialog.preset_name=name;dialog.save_preset=save;dialog.feedback=info
    return dialog


def _finish(dialog,editor):
    dialog.destroy()
    if editor.winfo_viewable():editor.grab_set();editor.canvas.focus_set()


def _range_fields(parent,first,last):
    row=ttk.Frame(parent,padding=10);row.pack(fill='x')
    a=tk.StringVar(value=str(first+1));b=tk.StringVar(value=str(last+1))
    for label,var in (('起始小节',a),('结束小节',b)):
        ttk.Label(row,text=label).pack(side='left',padx=(0,5))
        ttk.Spinbox(row,textvariable=var,from_=1,to=25000,width=6).pack(side='left',padx=(0,16))
    return a,b,row


def pattern_dialog(editor):
    from .fingerstyle_document import FingerstyleDocument
    from .tablature import display_duration
    from .visual_editor import music_symbol,FOCUS,MUTED
    first,last=editor.selected_measures();dialog=tk.Toplevel(editor)
    dialog.title('节奏预设 · 内置与我的预设');dialog.geometry('960x560');dialog.minsize(840,520);dialog.transient(editor)
    heading=ttk.Frame(dialog,padding=12);heading.pack(fill='x')
    ttk.Label(heading,text='节奏预设',font=('Microsoft YaHei UI',15,'bold')).pack(side='left')
    ttk.Label(heading,text='选预设 → 核对拍号与弦位 → 应用到选中小节',foreground=MUTED).pack(side='left',padx=18)
    body=ttk.Frame(dialog,padding=(12,0));body.pack(fill='both',expand=True)
    left=ttk.Frame(body,width=300);left.pack(side='left',fill='y')
    search=tk.StringVar();ttk.Entry(left,textvariable=search,width=30).pack(fill='x',pady=(0,6))
    library=ttk.Frame(left);library.pack(fill='both',expand=True)
    tree=ttk.Treeview(library,columns=('meter',),show='tree headings',height=10)
    tree.heading('#0',text='名称');tree.heading('meter',text='拍号');tree.column('#0',width=220);tree.column('meter',width=55,stretch=False)
    tree.pack(side='left',fill='both',expand=True)
    scroll=ttk.Scrollbar(library,orient='vertical',command=tree.yview);scroll.pack(side='left',fill='y')
    tree.configure(yscrollcommand=scroll.set)
    manage=ttk.Frame(left);manage.pack(fill='x',pady=(6,0))
    right=ttk.Frame(body);right.pack(side='left',fill='both',expand=True,padx=(14,0))
    title=tk.StringVar(value='选择一个节奏预设');ttk.Label(right,textvariable=title,font=('Microsoft YaHei UI',12,'bold')).pack(anchor='w',pady=(0,7))
    canvas=tk.Canvas(right,bg='white',height=225,highlightthickness=1,highlightbackground='#d8e0e8');canvas.pack(fill='both',expand=True)
    horizontal=ttk.Scrollbar(right,orient='horizontal',command=canvas.xview);horizontal.pack(fill='x');canvas.configure(xscrollcommand=horizontal.set)
    options=ttk.Frame(right);options.pack(fill='x',pady=8)
    chord=tk.StringVar(value='沿用各小节' if editor.doc.chord_at(editor.doc.bar(first).start) else editor.chord.get())
    bass=tk.StringVar(value='自动')
    ttk.Label(options,text='和弦').pack(side='left')
    combo=ttk.Combobox(options,textvariable=chord,values=('沿用各小节',*CHORD_NAMES),width=11);combo.pack(side='left',padx=6)
    ttk.Label(options,text='低音弦').pack(side='left')
    bass_box=ttk.Combobox(options,textvariable=bass,values=('自动','6','5','4','3','2','1'),state='readonly',width=5);bass_box.pack(side='left',padx=6)
    start,end,range_row=_range_fields(dialog,first,last)
    info=tk.StringVar(value='可在谱面写好一个小节，点“保存当前小节…”存入我的预设；应用会替换选定范围，可撤销。')
    notice=ttk.Label(dialog,textvariable=info,wraplength=780,padding=(12,2));notice.pack(anchor='w')
    library_problem=tk.StringVar();library_notice=ttk.Label(dialog,textvariable=library_problem,foreground='#a62d43',wraplength=780,padding=(12,0));library_notice.pack(anchor='w')
    def notify(message,error=False):
        info.set(message);notice.configure(foreground='#a62d43' if error else MUTED)
    patterns={};users={};deleted=[]
    def selected():
        selection=tree.selection();return patterns.get(selection[0]) if selection else None
    def reload(preferred=None):
        nonlocal users,patterns
        try:users=read_patterns(pattern_path(editor));library_problem.set('')
        except (ValueError,OSError) as error:users={};library_problem.set(str(error))
        patterns={**PATTERN_MAP,**users};tree.delete(*tree.get_children())
        query=search.get().strip().casefold()
        for root,label,items in (('builtin','内置预设',PATTERNS),('custom','我的预设',tuple(users.values()))):
            tree.insert('','end',iid=root,text=label,open=True)
            for item in items:
                if query and query not in item.name.casefold():continue
                tree.insert(root,'end',iid=item.key,text=item.name,values=(f'{item.meter[0]}/{item.meter[1]}',))
        match=preferred if preferred and tree.exists(preferred) else next((p.key for p in patterns.values() if tree.exists(p.key) and p.meter==editor.doc.bar(first).meter),None)
        if match:tree.selection_set(match);tree.see(match)
        preview()
    def preview(event=None,update_notice=True):
        if not dialog.winfo_exists():return
        item=selected();is_user=item is not None and item.key in users
        rename_button.configure(state='normal' if is_user else 'disabled');delete_button.configure(state='normal' if is_user else 'disabled')
        canvas.delete('all')
        if item is None:title.set('选择一个节奏预设');return
        title.set(item.name)
        name=chord.get() if chord.get()!='沿用各小节' else editor.doc.chord_at(editor.doc.bar(first).start) or 'G'
        width=max(420,canvas.winfo_width(),len(item.actions)*35+80);left=32;span=width-62
        canvas.configure(scrollregion=(0,0,width,225))
        for s,label in enumerate('eBGDAE',1):
            y=30+(s-1)*24;canvas.create_text(14,y,text=label,fill=MUTED)
            canvas.create_line(left,y,width-18,y,fill='#bdcbd5')
        try:
            document=FingerstyleDocument(meter=item.meter,bars=1,chord_changes=((F(0),name),))
            cells,_=pattern_cells(document,0,item,'auto' if bass.get()=='自动' else bass.get(),name)
            for cell in cells:
                x=left+12+float(cell.start/document.bar(0).beats)*(span-12)
                if cell.sweep:
                    y1,y2=(27,153) if cell.sweep==UP else (153,27)
                    canvas.create_line(x,y1,x,y2,arrow='last',fill=FOCUS,width=2)
                else:
                    for s in cell.members:
                        y=30+(s-1)*24;canvas.create_rectangle(x-7,y-10,x+7,y+10,fill='white',outline='white')
                        canvas.create_text(x,y,text='X',fill=FOCUS,font=('Consolas',12,'bold'))
                denominator,dotted,triplet=display_duration(cell.beats)
                music_symbol(canvas,x,190,denominator,rest=not cell.notes,scale=.6)
                if dotted:canvas.create_oval(x+9,188,x+12,191,fill=FOCUS,outline=FOCUS)
                if triplet:canvas.create_text(x,207,text='3',fill=MUTED)
            if update_notice:notify(f'{item.meter[0]}/{item.meter[1]} · 上扫 1→6，下扫 6→1。应用会替换选定范围，随后可逐音修改或 Ctrl+Z 撤销。')
        except ValueError as error:notify('预设预览受限：'+str(error),True)
    def apply():
        try:
            item=selected()
            if item is None:raise ValueError('请在左侧展开“内置预设”或“我的预设”，选中一个具体节奏；分组标题不能应用。')
            a,b=measure_range(start.get(),end.get(),editor.doc.bars)
            if editor.edit(lambda:apply_pattern(editor.doc,a,b,item,'auto' if bass.get()=='自动' else bass.get(),None if chord.get()=='沿用各小节' else chord.get().strip())):
                editor.select_cursor(editor.doc.bar(a).start);_finish(dialog,editor)
                editor.feedback.set(f'节奏预设已填入第 {a+1}～{b+1} 小节；可逐音修改或 Ctrl+Z 撤销')
            else:notify('未应用：'+editor.feedback.get(),True)
        except ValueError as error:notify('未应用：'+str(error),True)
    def save_current():return save_rhythm_dialog(editor,lambda item:reload(item.key),owner=dialog)
    def update_current():
        item=selected()
        if item is not None and item.key in users:
            return save_rhythm_dialog(editor,lambda saved:reload(saved.key),owner=dialog,existing=item)
    def rename():
        item=selected()
        if item is None or item.key not in users:return
        form=tk.Toplevel(dialog);form.title('重命名节奏预设');form.transient(dialog);form.resizable(False,False)
        name=tk.StringVar(value=item.name);entry=ttk.Entry(form,textvariable=name,width=35);entry.pack(padx=14,pady=14)
        error=tk.StringVar();ttk.Label(form,textvariable=error,foreground='#a62d43',wraplength=330).pack(padx=14)
        def finish():
            form.destroy()
            if dialog.winfo_viewable():dialog.grab_set()
        def commit():
            try:rename_pattern(pattern_path(editor),item.key,name.get());reload(item.key);finish()
            except (ValueError,OSError) as problem:error.set('未保存：'+str(problem))
        ttk.Button(form,text='保存名称',command=commit).pack(anchor='e',padx=14,pady=14)
        entry.bind('<Return>',lambda event:commit());form.protocol('WM_DELETE_WINDOW',finish)
        if dialog.winfo_viewable():form.grab_set();entry.focus_set()
        return form
    def remove():
        item=selected()
        if item is None or item.key not in users:return
        try:
            delete_pattern(pattern_path(editor),item.key);deleted[:]=[item];reload()
            restore_button.configure(state='normal');notify('已删除预设：'+item.name+'；关闭此窗口前，可点“撤销删除”恢复最近删除的一个预设')
        except (ValueError,OSError) as error:notify('删除失败：'+str(error),True)
    def restore():
        if not deleted:return
        try:save_pattern(pattern_path(editor),deleted[0]);reload(deleted[0].key);deleted.clear();restore_button.configure(state='disabled')
        except (ValueError,OSError) as error:notify('恢复失败：'+str(error),True)
    ttk.Button(manage,text='保存当前小节…',command=save_current,style='Composer.TButton').pack(side='left')
    rename_button=ttk.Button(manage,text='改名',command=rename,style='Composer.TButton');rename_button.pack(side='left',padx=4)
    delete_button=ttk.Button(manage,text='删除',command=remove,style='Composer.TButton');delete_button.pack(side='left')
    restore_button=ttk.Button(left,text='撤销删除',command=restore,state='disabled',style='Composer.TButton');restore_button.pack(anchor='w',pady=(5,0))
    def context(event):
        key=tree.identify_row(event.y)
        if key not in users:return
        tree.selection_set(key);preview();menu=tk.Menu(tree,tearoff=False)
        menu.add_command(label='用当前小节更新此预设…',command=update_current)
        menu.add_command(label='重命名…',command=rename);menu.add_command(label='删除预设',command=remove)
        try:menu.tk_popup(event.x_root,event.y_root)
        finally:menu.grab_release()
    tree.bind('<Button-3>',context)
    buttons=ttk.Frame(dialog,padding=12);buttons.pack(fill='x')
    apply_button=ttk.Button(buttons,text='应用到选定小节',command=apply,style='Composer.Load.TButton');apply_button.pack(side='right')
    ttk.Button(buttons,text='关闭',command=lambda:_finish(dialog,editor)).pack(side='right',padx=8)
    for widget in (buttons,library_notice,notice,range_row):widget.pack_configure(side='bottom',before=body)
    tree.bind('<<TreeviewSelect>>',preview);combo.bind('<<ComboboxSelected>>',preview);combo.bind('<Return>',preview)
    bass_box.bind('<<ComboboxSelected>>',preview);canvas.bind('<Configure>',lambda event:preview(event,update_notice=False))
    search.trace_add('write',lambda *args:reload());reload()
    dialog.protocol('WM_DELETE_WINDOW',lambda:_finish(dialog,editor))
    if editor.winfo_viewable():dialog.grab_set()
    dialog.pattern_tree=tree;dialog.apply_pattern=apply;dialog.save_current=save_current
    dialog.delete_preset=remove;dialog.restore_preset=restore;dialog.reload_patterns=reload
    dialog.update_current_preset=update_current
    dialog.feedback=info;dialog.library_problem=library_problem;dialog.range_start=start;dialog.range_end=end
    dialog.chord=chord;dialog.bass=bass;dialog.preview_pattern=preview
    dialog.apply_button=apply_button;dialog.notice_label=notice;dialog.library_notice=library_notice
    return dialog


def copy_dialog(editor):
    first,last=editor.selected_measures();dialog=tk.Toplevel(editor);dialog.title('指定小节复制');dialog.geometry('600x390');dialog.minsize(600,390);dialog.transient(editor)
    ttk.Label(dialog,text='复制一段小节到指定位置',font=('Microsoft YaHei UI',15,'bold'),padding=12).pack(anchor='w')
    start,end,_=_range_fields(dialog,first,last)
    row=ttk.Frame(dialog,padding=10);row.pack(fill='x');target=tk.StringVar(value=str(last+2));count=tk.StringVar(value='1')
    for label,var,limit in (('目标起始小节',target,editor.doc.bars+1),('复制次数',count,32)):
        ttk.Label(row,text=label).pack(side='left',padx=(0,5));ttk.Spinbox(row,textvariable=var,from_=1,to=limit,width=6).pack(side='left',padx=(0,15))
    mode=tk.StringVar(value='插入新小节')
    ttk.Combobox(dialog,textvariable=mode,values=('插入新小节','覆盖目标小节'),state='readonly',width=22).pack(anchor='w',padx=12,pady=6)
    info=tk.StringVar(value='复制音符、和弦、节奏、速度、拍号及音色。粘贴后恢复后续小节原有设置；跨边界的延音需先断开。')
    label=ttk.Label(dialog,textvariable=info,wraplength=570,padding=12);label.pack(anchor='w')
    def failed(message):info.set('未复制：'+message);label.configure(foreground='#a62d43')
    def apply():
        try:
            a,b=measure_range(start.get(),end.get(),editor.doc.bars)
            d=integer_field(target.get(),'目标起始小节',1,editor.doc.bars+1)-1
            n=integer_field(count.get(),'复制次数',1,32)
            if editor.edit(lambda:copy_measures(editor.doc,a,b,d,mode.get()=='插入新小节',n)):
                editor.bar_selection=None;editor.select_cursor(editor.doc.bar(d).start);_finish(dialog,editor)
                editor.feedback.set(f'已复制第 {a+1}～{b+1} 小节到第 {d+1} 小节起；Ctrl+Z 可撤销')
            else:failed(editor.feedback.get())
        except ValueError as error:failed(str(error))
    ttk.Button(dialog,text='执行复制',command=apply,style='Accent.TButton').pack(side='bottom',anchor='e',before=label,padx=12,pady=8)
    dialog.protocol('WM_DELETE_WINDOW',lambda:_finish(dialog,editor))
    if editor.winfo_viewable():dialog.grab_set()
    dialog.feedback=info;dialog.range_start=start;dialog.range_end=end;dialog.target=target;dialog.copies=count;dialog.apply_copy=apply
    return dialog


def repeat_dialog(editor):
    first,last=editor.selected_measures();dialog=tk.Toplevel(editor);dialog.title('反复段 · 起止小节与次数');dialog.geometry('570x350');dialog.minsize(570,350);dialog.transient(editor)
    ttk.Label(dialog,text='用反复线标记一段乐谱',font=('Microsoft YaHei UI',15,'bold'),padding=12).pack(anchor='w')
    start,end,_=_range_fields(dialog,first,last)
    row=ttk.Frame(dialog,padding=10);row.pack(fill='x');times=tk.StringVar(value='2')
    ttk.Label(row,text='总演奏次数（包含首遍）').pack(side='left',padx=(0,10));ttk.Spinbox(row,textvariable=times,from_=2,to=16,width=6).pack(side='left')
    info=tk.StringVar(value='谱面显示反复线与次数；试听和转译按次数展开，编辑原谱保持紧凑。')
    label=ttk.Label(dialog,textvariable=info,wraplength=540,padding=12);label.pack(anchor='w')
    def failed(message):info.set('未添加反复线：'+message);label.configure(foreground='#a62d43')
    def apply():
        try:
            a,b=measure_range(start.get(),end.get(),editor.doc.bars);n=integer_field(times.get(),'总演奏次数',2,16)
            if editor.edit(lambda:set_repeat_section(editor.doc,a,b,n)):_finish(dialog,editor)
            else:failed(editor.feedback.get())
        except ValueError as error:failed(str(error))
    ttk.Button(dialog,text='添加反复线',command=apply,style='Accent.TButton').pack(side='bottom',anchor='e',before=label,padx=12,pady=8)
    dialog.protocol('WM_DELETE_WINDOW',lambda:_finish(dialog,editor))
    if editor.winfo_viewable():dialog.grab_set()
    dialog.feedback=info;dialog.range_start=start;dialog.range_end=end;dialog.times=times;dialog.apply_repeat=apply
    return dialog
