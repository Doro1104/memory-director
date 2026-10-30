"""Execute a validated, frame-based edit locally; no AI, network or source writes."""
import json
from pathlib import Path
import random
import re
import subprocess

import imageio_ffmpeg
from PIL import Image,ImageOps
from curation import TEMPLATES,music_catalog,filter_selected
from director import FPS,validate


def render(conn,data,job,film,options):
    from processing import ffmpeg,progress,title_image,check_film_active,FilmCancelled
    draft=options['director'];plan=validate(draft['plan'],draft['candidates'],draft['target_frames'])
    fid=film['id'];folder=Path(data)/'jobs'/job['id'];folder.mkdir(exist_ok=True)
    dest=Path(data)/'films'/(fid+'.mp4');poster=Path(data)/'films'/(fid+'.jpg')
    width,height=(1920,1080) if options.get('resolution')=='1080' else (1280,720)
    rows=[]
    def asset(aid):
        row=conn.execute('SELECT * FROM assets WHERE id=? AND owner=? AND deleted_at IS NULL AND purged_at IS NULL AND live_parent IS NULL',(aid,job['owner'])).fetchone()
        if not row or not Path(row['path']).is_file():raise ValueError('确认的素材已不可用；没有跳过或替换镜头，请重新选片')
        stamp=Path(row['path']).stat()
        if draft.get('source_stamps',{}).get(aid)!=[stamp.st_size,stamp.st_mtime_ns]:raise ValueError('素材文件已改变，请重新选片确认')
        return row
    success=False
    try:
        check_film_active(conn,fid)
        rows=[asset(clip['asset_id']) for clip in plan['clips']]
        _,removed=filter_selected(conn,rows)
        if any(removed.values()):raise ValueError('选片筛查已改变，请重新预览，不会自动跳过镜头')
        music=music_catalog(data,job['owner']);choice=options.get('music','none')
        track=random.choice(music) if choice=='random' and music else next((r for r in music if r['id']==choice),None)
        if choice!='none' and not track:raise ValueError('配乐已不可用，请重新选择配乐')
        clips=[]
        for i,clip in enumerate(plan['clips']):
            check_film_active(conn,fid);row=asset(clip['asset_id']);seconds=clip['frames']/FPS;image=row['kind']=='image'
            color=TEMPLATES[options.get('template','warm')]['color']
            fit=f'scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=0x18251f,setsar=1,fps={FPS},'+color
            vf=fit+f'trim=duration={seconds},setpts=PTS-STARTPTS'
            if i==0:vf+=',fade=t=in:st=0:d=0.3'
            if i==len(rows)-1:vf+=f',fade=t=out:st={max(0,seconds-.5)}:d=0.5'
            if image:
                source=folder/f'{i:03}.png'
                with Image.open(row['path']) as im:
                    im=ImageOps.exif_transpose(im).convert('RGB');im.thumbnail((3840,3840));im.save(source)
                args=['-loop','1','-framerate',str(FPS),'-i',source];has_audio=False
            else:
                probe=subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-hide_banner','-i',row['path']],capture_output=True,timeout=20,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                match=re.search(rb'Duration: (\d+):(\d+):([\d.]+)',probe.stderr)
                available=int((int(match[1])*3600+int(match[2])*60+float(match[3]))*FPS)-1 if match else 0
                if available<clip['in_frame']+clip['frames']:raise ValueError('视频当前时长不足以执行确认的出点')
                has_audio=b'Audio:' in probe.stderr and clip['audio']=='original'
                args=['-ss',f"{clip['in_frame']/FPS:.9f}",'-i',row['path']]
            if not has_audio:args+=['-f','lavfi','-i','anullsrc=r=48000:cl=stereo']
            af=f'aresample=48000,apad,atrim=duration={seconds},asetpts=PTS-STARTPTS,afade=t=in:st=0:d=0.015,afade=t=out:st={max(0,seconds-.02)}:d=0.02'
            output=folder/f'{i:03}.mkv'
            ffmpeg([*args,'-map','0:v:0','-map','0:a:0' if has_audio else '1:a:0','-vf',vf,'-af',af,
                    '-t',str(seconds),'-frames:v',str(clip['frames']),'-c:v','libx264','-preset','fast','-crf','18','-threads','2','-pix_fmt','yuv420p','-c:a','pcm_s16le','-ar','48000','-ac','2',output])
            clips.append(output)
            progress(conn,job['id'],i+1,len(rows),f'正在执行已确认剪辑方案 {i+1} / {len(rows)}；不跳过失败镜头')
        listing=folder/'clips.txt';listing.write_text('\n'.join("file '"+path.name+"'" for path in clips),encoding='utf-8')
        total=draft['target_frames']/FPS
        args=['-f','concat','-safe','0','-i',listing]
        if track:
            args+=['-stream_loop','-1','-i',Path(data)/'music'/track['file']]
            mix=f'[0:a]volume=0.9,asplit[original][guide];[1:a]volume=0.2,afade=t=in:st=0:d=1,afade=t=out:st={max(0,total-1)}:d=1[bed];[bed][guide]sidechaincompress=threshold=0.025:ratio=8:attack=20:release=350[duck];[original][duck]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95:level=false[a]'
        else:mix='[0:a]alimiter=limit=0.95:level=false[a]'
        ffmpeg([*args,'-filter_complex',mix,'-map','0:v:0','-map','[a]','-r',str(FPS),'-frames:v',str(draft['target_frames']),'-t',str(total),
                '-c:v','libx264','-preset','fast','-crf','18','-threads','2','-pix_fmt','yuv420p','-c:a','aac','-b:a','192k','-ar','48000','-ac','2','-movflags','+faststart',dest],timeout=max(120,int(total)*12))
        # Decode every video frame before marking done: header/progress alone is not success.
        import cv2
        cap=cv2.VideoCapture(str(dest));decoded=0
        try:
            while cap.read()[0]:decoded+=1
        finally:cap.release()
        if decoded!=draft['target_frames']:raise ValueError('输出解码帧数不符合确认时间线；未作为成片交付')
        check_film_active(conn,fid)
        for row in rows:asset(row['id'])
        title_image(poster,film['title'],f'{total:g} 秒 · 剪辑规则 {plan["rules_version"]}',(width,height))
        with Image.open(poster) as im:im.thumbnail((640,360));im.save(poster)
        conn.execute('DELETE FROM film_assets WHERE film=?',(fid,))
        conn.executemany('INSERT INTO film_assets VALUES(?,?)',[(fid,r['id']) for r in rows])
        notes=f'{width}×{height} · 30fps · {total:g} 秒 · {len(rows)} 镜头 · 方案 v{options["draft_revision"]} / 规则 {plan["rules_version"]} · 配乐：'+(track['title'] if track else '无')
        changed=conn.execute("UPDATE films SET status='done',path=?,thumb=?,notes=? WHERE id=? AND deleted_at IS NULL",(str(dest),str(poster),notes,fid))
        if not changed.rowcount:conn.rollback();raise FilmCancelled('影片已取消')
        conn.commit();success=True;progress(conn,job['id'],len(rows),len(rows),notes)
    finally:
        if not success:dest.unlink(missing_ok=True);poster.unlink(missing_ok=True)
        for file in folder.iterdir():
            if file.is_file():file.unlink()
        folder.rmdir()
