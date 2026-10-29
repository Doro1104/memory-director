"""Explicit, scoped deletion plans. Development tests use disposable files only."""
import ctypes
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path


def hardlinks(path):
    path=Path(path)
    if os.name!='nt' or not path.exists() or path.stat().st_nlink<2:
        return [path]
    from ctypes import wintypes
    api=ctypes.WinDLL('kernel32',use_last_error=True)
    first=api.FindFirstFileNameW;first.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,ctypes.POINTER(wintypes.DWORD),wintypes.LPWSTR];first.restype=wintypes.HANDLE
    next_name=api.FindNextFileNameW;next_name.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD),wintypes.LPWSTR];next_name.restype=wintypes.BOOL
    close=api.FindClose;close.argtypes=[wintypes.HANDLE];close.restype=wintypes.BOOL
    length=wintypes.DWORD(32768);buffer=ctypes.create_unicode_buffer(length.value)
    handle=first(str(path),0,ctypes.byref(length),buffer)
    if handle==wintypes.HANDLE(-1).value:
        raise OSError('无法核对原片的硬链接，请暂时只从图库移除')
    paths=[]
    try:
        paths.append(Path(path.drive+buffer.value))
        while True:
            length.value=32768
            if not next_name(handle,ctypes.byref(length),buffer):
                if ctypes.get_last_error()!=38:  # ERROR_HANDLE_EOF
                    raise OSError('无法完整核对原片入口')
                break
            paths.append(Path(path.drive+buffer.value))
    finally:
        close(handle)
    return paths


def allowed_source(path,data,legacy):
    path=Path(path).resolve()
    if path.is_relative_to((Path(data)/'uploads').resolve()):
        return True
    root=Path(legacy).parent.resolve()
    if not path.is_relative_to(root):
        return False
    relative=path.relative_to(root)
    return bool(relative.parts and ('照片' in relative.parts[0]))


def select_assets(conn,owner,body,purge=False):
    if body.get('all'):
        if not purge:
            raise ValueError('全选永久移除仅适用于回收站')
        rows=conn.execute('SELECT * FROM assets WHERE owner=? AND deleted_at IS NOT NULL AND purged_at IS NULL AND live_parent IS NULL ORDER BY id',(owner,)).fetchall()
        excluded=body.get('excluded',[])
        if not isinstance(excluded,list) or len(excluded)>1000 or any(not isinstance(a,str) for a in excluded):
            raise ValueError('全选排除列表无效')
        excluded=set(excluded);rows=[r for r in rows if r['id'] not in excluded]
    else:
        ids=body.get('ids',[])
        if not isinstance(ids,list) or not 1<=len(ids)<=100 or any(not isinstance(a,str) for a in ids):
            raise ValueError('请一次选择 1–100 个素材，或使用清空回收站')
        rows=[]
        for aid in sorted(set(ids)):
            row=conn.execute('SELECT * FROM assets WHERE id=? AND owner=? AND purged_at IS NULL AND live_parent IS NULL',(aid,owner)).fetchone()
            if not row:
                raise PermissionError('找不到这份素材')
            if purge and not row['deleted_at']:
                raise ValueError('素材不在回收站')
            rows.append(row)
    return rows


def build_plan(conn,data,legacy,owner,rows):
    other_paths=[Path(r[0]) for r in conn.execute('SELECT path FROM assets WHERE owner!=? AND purged_at IS NULL',(owner,))]
    other_inodes=set()
    for path in other_paths:
        if path.exists():
            stat=path.stat();other_inodes.add((stat.st_dev,stat.st_ino))
    entries=[];outside=0
    for row in rows:
        primary=Path(row['path']).resolve()
        if not allowed_source(primary,data,legacy) or Path(row['path']).is_symlink():
            raise ValueError('原文件不在已接入的素材目录内，未执行原文件删除')
        if not primary.is_file():
            raise ValueError('原片当前不可用，请先连接素材硬盘；也可以不勾选原文件删除，只移除应用记录')
        files=[]
        if primary.exists():
            stat=primary.stat()
            if (stat.st_dev,stat.st_ino) in other_inodes:
                raise ValueError('这份原片还有其他家庭账号在使用，不能删除原文件')
            for alias in hardlinks(primary):
                resolved=alias.resolve()
                if not allowed_source(resolved,data,legacy) or alias.is_symlink():
                    outside+=1;continue
                if resolved.exists() and os.path.samefile(primary,resolved):
                    files.append(str(resolved))
            files=list(dict.fromkeys(files+[str(primary)]))
            files.sort(key=lambda p:p==str(primary))  # primary last, so failed deletion can be retried
        entries.append({'id':row['id'],'name':row['name'],'files':files})
    signature=hashlib.sha256(json.dumps([(r['id'],r['path']) for r in rows],ensure_ascii=False).encode()).hexdigest()
    return {'entries':entries,'count':len(rows),'links':sum(len(e['files']) for e in entries),'outside_links':outside,'signature':signature}


def purge_assets(conn,data,plan,delete_originals):
    removed=0;errors=[];now=datetime.now().isoformat()
    for entry in plan['entries']:
        try:
            if delete_originals:
                for filename in entry['files']:
                    Path(filename).unlink(missing_ok=True)
            # Keep tombstones to prevent the legacy importer from resurrecting an erased entry.
            conn.execute('UPDATE assets SET deleted_at=?,purged_at=?,shared=0,hash=NULL WHERE id=?',(now,now,entry['id']))
            conn.execute('DELETE FROM favorites WHERE asset=?',(entry['id'],))
            crops=conn.execute('SELECT crop FROM faces WHERE asset=?',(entry['id'],)).fetchall()
            conn.execute('DELETE FROM faces WHERE asset=?',(entry['id'],))
            conn.commit()
            playback=(Path(data)/'playback'/(entry['id']+'.mp4')).resolve()
            if playback.parent==(Path(data)/'playback').resolve():
                try:playback.unlink(missing_ok=True)
                except OSError:pass
            for crop in crops:
                path=Path(crop[0]).resolve()
                if path.is_relative_to((Path(data)/'faces').resolve()):
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        pass  # An inaccessible cache never changes the result of original-file deletion.
            removed+=1
        except OSError:
            conn.rollback();errors.append(entry['name'])
    with (Path(data)/'deletions.jsonl').open('a',encoding='utf-8') as log:
        log.write(json.dumps({'time':now,'assets':[e['id'] for e in plan['entries']],'delete_originals':delete_originals,'removed':removed,'errors':errors},ensure_ascii=False)+'\n')
    return {'removed':removed,'errors':errors}
