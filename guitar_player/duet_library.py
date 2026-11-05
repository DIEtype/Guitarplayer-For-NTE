"""Per-song duet snapshots, recording codes and alignment; never replace source."""
import copy
from dataclasses import dataclass
from datetime import datetime,timezone,timedelta
import hashlib,json,re,shutil
import threading
import uuid
from functools import wraps
from pathlib import Path

from .duet_project import DuetProject,DuetPart


def atomic(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(path)
    finally:temp.unlink(missing_ok=True)


def pair_id(project):
    raw='|'.join(project.parts[r].fingerprint if project.parts[r] else '' for r in ('recorded','live'))
    return hashlib.sha256(raw.encode('ascii')).hexdigest()[:32]


def project_name(project):
    for role in ('recorded','live'):
        part=project.parts[role]
        if part:
            name=part.name.split(' · ')[0].strip()
            if name not in ('录音伴奏','现场声部','主旋律／现场声部'):return name[:200]
    return '未命名合奏'


@dataclass(frozen=True)
class SavedDuet:
    identity:str
    name:str
    updated:str
    has_code:bool
    path:Path


def locked(method):
    @wraps(method)
    def call(self,*args,**kwargs):
        with self.lock:return method(self,*args,**kwargs)
    return call


class DuetLibrary:
    def __init__(self,root):self.root=Path(root) if root is not None else None;self.lock=threading.RLock()

    def folder(self,identity):
        if self.root is None:raise ValueError('当前未启用本机合奏曲库')
        if not isinstance(identity,str) or not re.fullmatch('[0-9a-f]{32}',identity):raise ValueError('合奏曲库编号无效')
        return self.root/identity

    def items(self):
        if self.root is None or not self.root.exists():return []
        result=[]
        for path in self.root.glob('*/session.json'):
            try:
                if path.stat().st_size>4096:continue
                d=json.loads(path.read_text(encoding='utf-8'));identity=path.parent.name
                if self.folder(identity)!=path.parent or not (path.parent/'合奏项目.gscore').exists():continue
                if not isinstance(d.get('name'),str) or not 1<=len(d['name'])<=200:continue
                result.append(SavedDuet(identity,d['name'],str(d.get('updated','')),bool(d.get('has_code')),path.parent))
            except (OSError,ValueError,TypeError,KeyError):continue
        return sorted(result,key=lambda entry:entry.updated,reverse=True)

    @locked
    def load(self,identity):
        path=self.folder(identity)/'合奏项目.gscore'
        if path.stat().st_size>20_000_000:raise ValueError('保存的合奏超过 20 MB，请先分段')
        data=json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(data,dict) or data.get('kind')!='duet_session':raise ValueError('保存的合奏项目格式无效')
        project=DuetProject.from_dict(data.get('project'))
        if pair_id(project)!=identity:raise ValueError('合奏声部与曲库编号不匹配，请重新保存该项目')
        project.archive_id=identity
        return project

    @locked
    def save(self,project,name=None,settings=None,report=None,source_path=None):
        if self.root is None or not any(project.parts.values()):return None
        identity=pair_id(project);folder=self.folder(identity);folder.mkdir(parents=True,exist_ok=True)
        saved=copy.deepcopy(project);old=None
        try:old=self.load(identity)
        except FileNotFoundError:pass
        recorded=saved.parts['recorded']
        bound_ok=not recorded or saved.bound_part_id==recorded.fingerprint
        if saved.bound_code and (not re.fullmatch('[0-9a-fA-F]{32}',saved.bound_code) or not bound_ok):
            saved.bound_code=old.bound_code if old else '';saved.bound_part_id=old.bound_part_id if old else '';saved.bound_name=old.bound_name if old else ''
        # Do not archive unapplied editor drafts over a ready-to-play version.
        # The complete current draft is saved separately in settings.
        for role,doc in saved.documents.items():
            if doc is not None and not doc.matches_part(saved.parts[role]):saved.documents[role]=old.documents[role] if old else None
        if any(doc is not None and not doc.matches_part(project.parts[r]) for r,doc in project.documents.items()):
            from .duet_composition import write_composition
            write_composition(folder/'未应用双轨草稿.gscore',project)
        saved.archive_id=identity;saved.archive_name=(name or project.archive_name or (old.archive_name if old else '') or project_name(project)).strip()[:200] or '未命名合奏'
        if settings is not None:saved.remember_offset(saved.bound_code,settings)
        for role,label in (('recorded','录音伴奏'),('live','现场声部')):
            part=saved.parts[role]
            if part:atomic(folder/(label+'.gscore'),{'version':1,'kind':'duet_part','role':role,'part':part.to_dict()})
        if report is not None:atomic(folder/'拆分报告.json',report)
        if source_path is not None:
            source=Path(source_path)
            if source.is_file() and source.stat().st_size<=20_000_000:
                target=folder/('导入原谱'+source.suffix.lower())
                if source.resolve()!=target.resolve() and not target.exists():shutil.copy2(source,target)
        atomic(folder/'合奏项目.gscore',{'version':1,'kind':'duet_session','project':saved.to_dict()})
        atomic(folder/'session.json',{'name':saved.archive_name,'updated':datetime.now(timezone(timedelta(hours=8))).isoformat(),
                                    'has_code':bool(re.fullmatch('[0-9a-fA-F]{32}',saved.bound_code))})
        project.archive_id=identity;project.archive_name=saved.archive_name
        return folder

    @locked
    def save_split(self,result,name,source_path=None):
        project=DuetProject()
        for role,score in result.scores.items():
            if score is not None:project.set_part(role,DuetPart((name+' · '+('录音伴奏' if role=='recorded' else '现场声部'))[:200],score,str(source_path or '自动拆分原谱')))
        project.split_summary=result.report['summary'][:2048]
        if self.root is None:return project
        identity=pair_id(project)
        try:
            old=self.load(identity)
            # Identical scores can reuse their verified recording association.
            project.bound_code=old.bound_code;project.bound_part_id=old.bound_part_id;project.bound_name=old.bound_name
            project.offsets=old.offsets;project.take_id=old.take_id;project.take_name=old.take_name;project.take_state=old.take_state
        except FileNotFoundError:pass
        self.save(project,name=name,report=result.report,source_path=source_path)
        return project


def library_for_app(app):
    if app.settings_dir is None:return DuetLibrary(None)
    if app.enable_native:
        from .paths import app_root
        return DuetLibrary(app_root()/'library'/'ensembles')
    return DuetLibrary(Path(app.settings_dir)/'duet-library')
