"""Portable local project snapshots and explicit v1.2 -> v1.3 migration.

Only local files and the Python standard library are used. Archive hashes detect
corruption, not authorship. No extraction is committed until all entries verify.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import uuid
import zipfile

MANIFEST='archive-manifest.json'
EXCLUDED={'.venv','venv','__pycache__','.pytest_cache','.cache','cache','caches','node_modules','pending','.pending'}


def _digest(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''): value.update(chunk)
    return value.hexdigest()


def _json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def _write_json(path,value):
    Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')


def _relative(name):
    if not isinstance(name,str) or not name or '\\' in name or '\x00' in name: raise ValueError('归档路径不合法')
    parts=PurePosixPath(name).parts
    if name.startswith('/') or ':' in parts[0] or any(p in {'..','.'} for p in name.split('/')) or '//' in name:
        raise ValueError('归档或stored_path存在路径穿越')
    return Path(*parts)


def _is_excluded(relative):
    return any(p in EXCLUDED or p.endswith('.lock') or p.startswith(('.pending-','.preflight-','.runtime.lock')) for p in relative.parts)


def _files(root):
    """Do not descend into environment/cache directories or follow symlinks."""
    rows=[]
    for directory,dirs,files in os.walk(root,followlinks=False):
        parent=Path(directory)
        keep=[]
        for name in dirs:
            path=parent/name; relative=path.relative_to(root)
            if _is_excluded(relative): continue
            if path.is_symlink(): raise ValueError(f'项目包含符号链接，不能归档：{relative}')
            keep.append(name)
        dirs[:]=keep
        for name in files:
            path=parent/name; relative=path.relative_to(root)
            if _is_excluded(relative) or relative.as_posix()==MANIFEST: continue
            if path.is_symlink(): raise ValueError(f'项目包含符号链接，不能归档：{relative}')
            if not path.is_file(): raise ValueError('项目包含不支持的文件类型')
            _relative(relative.as_posix()); rows.append((path,relative))
    return rows


def _inside_path(root,relative):
    path=root/_relative(relative)
    current=root
    for piece in path.relative_to(root).parts:
        current=current/piece
        if current.is_symlink(): raise ValueError('stored_path不能指向符号链接')
    if not path.resolve().is_relative_to(root.resolve()): raise ValueError('stored_path不能离开项目目录')
    return path


def _source(root,doc):
    if doc.get('stored_path'):
        path=_inside_path(root,doc['stored_path'])
    else:
        original=doc.get('path')
        if not isinstance(original,str) or not original: raise ValueError('旧项目没有可读取的源文件路径')
        path=Path(original).expanduser()
        if not path.is_absolute(): path=root/path
        if path.is_symlink(): raise ValueError('源文件不能是符号链接')
    if not path.is_file(): raise ValueError(f"源文件缺失：{doc.get('file','未知')}；请恢复原文件后再迁移或归档")
    if not isinstance(doc.get('sha256'),str) or _digest(path)!=doc['sha256']:
        raise ValueError(f"源文件哈希变化：{doc.get('file','未知')}")
    return path


def _read_state(root):
    try: state=_json(root/'state.json')
    except (OSError,ValueError) as exc: raise ValueError('不是有效项目：无法读取state.json') from exc
    if not isinstance(state,dict) or not isinstance(state.get('takeoff'),dict) or not isinstance(state.get('documents'),list) or not state['documents']:
        raise ValueError('项目state缺少takeoff或documents')
    return state


def _source_fingerprint(documents):
    source=[(d['file'],d['sha256'],d['page_count']) for d in documents]
    return hashlib.sha256(json.dumps(source,ensure_ascii=False,sort_keys=True,allow_nan=False).encode()).hexdigest()


def _copy_snapshot(root,stage):
    state=_read_state(root)
    # Preflight all sources before copying the project; never fabricate missing data.
    sources=[]; targets=set()
    for i,doc in enumerate(state['documents'],1):
        name=doc.get('file'); doc_id=doc.get('id',f'D{i:03}')
        if not isinstance(name,str) or not name or name in {'.','..'} or '/' in name or '\\' in name or ':' in name:
            raise ValueError('源文件file必须是单一文件名')
        if not isinstance(doc_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]+',doc_id): raise ValueError('源文件ID不合法')
        if not isinstance(doc.get('page_count'),int) or isinstance(doc['page_count'],bool) or doc['page_count']<1:
            raise ValueError('源文件page_count不合法')
        target=Path('sources')/f'{doc_id}-{name}'
        if target in targets: raise ValueError('源文件快照路径重复')
        targets.add(target); sources.append((_source(root,doc),target,doc))
    for source,relative in _files(root):
        dest=stage/relative; dest.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(source,dest)
    for source,relative,doc in sources:
        dest=stage/relative; dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source,dest)
        if _digest(dest)!=doc['sha256']: raise ValueError('复制时源文件发生变化，哈希校验失败')
        doc['stored_path']=relative.as_posix()
    state['takeoff'].pop('_base_dir',None)
    _write_json(stage/'state.json',state)
    return state


def _destination(dest):
    raw=Path(dest).expanduser()
    if raw.is_symlink(): raise ValueError('目标不能是符号链接')
    path=raw.resolve()
    if path.exists() and (not path.is_dir() or any(path.iterdir())): raise ValueError('目标目录必须不存在或为空，不能覆盖现有项目')
    path.parent.mkdir(parents=True,exist_ok=True)
    return path


def _commit(stage,dest):
    # Recheck at commit; never replace a populated directory.
    if dest.exists():
        if not dest.is_dir() or any(dest.iterdir()): raise ValueError('目标目录已被占用，未覆盖')
        dest.rmdir()
    os.rename(stage,dest)


def migrate_project(old_root,new_root):
    """Copy v1.2.0 into a fresh directory; preserve policy 2 and all observations."""
    old=Path(old_root).expanduser().resolve(); dest=_destination(new_root)
    if dest==old or dest.is_relative_to(old): raise ValueError('迁移目标必须在旧项目目录之外')
    original=_read_state(old)
    if original.get('runtime_version')!='1.2.0': raise ValueError('显式迁移仅支持v1.2.0；原目录不会改动')
    stage=Path(tempfile.mkdtemp(prefix='.migration-',dir=dest.parent))
    try:
        state=_copy_snapshot(old,stage)
        revision=state.get('revision',0)
        if not isinstance(revision,int) or isinstance(revision,bool) or revision<0: raise ValueError('旧项目revision不合法')
        project_id=state.get('project_id') or state['takeoff'].get('project_id') or str(uuid.uuid4())
        if not isinstance(project_id,str): raise ValueError('project_id必须为字符串')
        fingerprint=_source_fingerprint(state['documents'])
        state.update(runtime_version='1.3.0',project_id=project_id,source_fingerprint=fingerprint,revision=revision)
        state['takeoff'].update(project_id=project_id,source_fingerprint=fingerprint,revision=revision)
        # Legacy evidence remains legacy; no role/reading/coverage/confirmation is invented.
        state['takeoff'].setdefault('evidence_policy',1)
        state['migration']=dict(from_version='1.2.0',to_version='1.3.0',at=datetime.now(timezone.utc).isoformat(),
                                note='保留旧版证据策略，未补造v3测量、覆盖或人工确认记录')
        _write_json(stage/'state.json',state); _commit(stage,dest)
        return state
    finally:
        if stage.exists(): shutil.rmtree(stage)


def archive_project(root,dest):
    """Return archive Path. Source snapshots are bundled without mutating root."""
    root=Path(root).expanduser().resolve(); destination=Path(dest).expanduser()
    if destination.is_symlink() or destination.exists(): raise ValueError('归档目标已存在，不能覆盖')
    destination=destination.resolve()
    if destination.is_relative_to(root): raise ValueError('归档文件必须存放在项目目录之外')
    destination.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.takeoff-archive-',dir=destination.parent) as temp:
        temp=Path(temp); stage=temp/'snapshot'; stage.mkdir(); state=_copy_snapshot(root,stage)
        rows=[]
        for file,relative in sorted(_files(stage),key=lambda pair:pair[1].as_posix()):
            rows.append(dict(path=relative.as_posix(),sha256=_digest(file),size=file.stat().st_size))
        manifest=dict(schema_version=1,kind='takeoff_project_archive',runtime_version=state.get('runtime_version'),
                      project_id=state.get('project_id'),source_fingerprint=_source_fingerprint(state['documents']),files=rows)
        archive=temp/'project.zip'
        with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
            for row in rows: z.write(stage/row['path'],row['path'])
            z.writestr(MANIFEST,json.dumps(manifest,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
        # Same-filesystem exclusive link avoids replacing a destination created concurrently.
        try: os.link(archive,destination)
        except FileExistsError as exc: raise ValueError('归档目标已存在，未覆盖') from exc
    return destination


def restore_archive(archive_path,dest):
    """Validate all ZIP names, hashes, and source snapshots, then commit to fresh dest."""
    destination=_destination(dest)
    stage=Path(tempfile.mkdtemp(prefix='.takeoff-restore-',dir=destination.parent))
    try:
        with zipfile.ZipFile(archive_path) as z:
            entries={}
            for info in z.infolist():
                name=info.filename
                _relative(name.rstrip('/') if info.is_dir() else name)
                mode=info.external_attr>>16
                if stat.S_ISLNK(mode): raise ValueError('归档包含符号链接，不能恢复')
                if mode and stat.S_IFMT(mode) not in (0,stat.S_IFREG,stat.S_IFDIR): raise ValueError('归档包含非普通文件')
                if name in entries: raise ValueError('归档包含重复路径')
                entries[name]=info
            if MANIFEST not in entries: raise ValueError('归档缺少哈希清单')
            try: manifest=json.loads(z.read(MANIFEST))
            except (ValueError,UnicodeError) as exc: raise ValueError('归档清单无法解析') from exc
            if not isinstance(manifest,dict) or manifest.get('kind')!='takeoff_project_archive' or manifest.get('schema_version')!=1 or not isinstance(manifest.get('files'),list):
                raise ValueError('归档清单格式不兼容')
            expected={}
            for row in manifest['files']:
                if not isinstance(row,dict) or not isinstance(row.get('path'),str): raise ValueError('归档文件清单不合法')
                name=row['path']; _relative(name)
                if name in expected or name==MANIFEST: raise ValueError('归档清单包含重复路径')
                if not isinstance(row.get('sha256'),str) or not re.fullmatch(r'[0-9a-f]{64}',row['sha256']): raise ValueError('归档哈希不合法')
                if not isinstance(row.get('size'),int) or isinstance(row['size'],bool) or row['size']<0: raise ValueError('归档长度不合法')
                expected[name]=row
            actual={name for name,info in entries.items() if not info.is_dir() and name!=MANIFEST}
            if actual!=set(expected) or 'state.json' not in actual: raise ValueError('归档文件与哈希清单不一致')
            for name,row in expected.items():
                info=entries[name]
                if info.file_size!=row['size']: raise ValueError(f'归档长度校验失败：{name}')
                target=stage/_relative(name); target.parent.mkdir(parents=True,exist_ok=True)
                with z.open(info) as source,target.open('xb') as output: shutil.copyfileobj(source,output)
                if _digest(target)!=row['sha256']: raise ValueError(f'归档哈希校验失败：{name}')
        state=_read_state(stage)
        for doc in state['documents']:
            if not doc.get('stored_path'): raise ValueError('归档缺少源文件快照，不能依赖原电脑路径恢复')
            _source(stage,doc)
        fingerprint=_source_fingerprint(state['documents'])
        if fingerprint!=manifest.get('source_fingerprint'): raise ValueError('归档源文件指纹不匹配')
        _commit(stage,destination)
        return state
    except (zipfile.BadZipFile,KeyError,OSError) as exc:
        raise ValueError(f'归档恢复失败：{exc}') from exc
    finally:
        if stage.exists(): shutil.rmtree(stage)
