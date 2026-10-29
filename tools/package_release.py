"""Fail-closed, reproducible source ZIP: explicit file manifest, never a directory ZIP."""
import argparse,hashlib,json,re,zipfile
from pathlib import Path,PurePosixPath

ROOT=Path(__file__).resolve().parents[1]
BLOCKED={'data','work','.venv','.runtime','.git','__pycache__','xcuserdata','DerivedData'}
BAD_EXT={'.sqlite','.db','.key','.pem','.p12','.mobileprovision','.ipa','.heic','.jpg','.jpeg','.mov','.mp4','.wav','.ogg','.mp3'}
TOKEN=re.compile(rb'gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{24,}|-----BEGIN (?:RSA |EC )?PRIVATE KEY-----')
PROFILE=re.compile(rb'[A-Za-z]:[\\/](?:Users|Documents and Settings)[\\/]',re.I)


def checked_files():
    manifest=json.loads((ROOT/'PUBLIC-MANIFEST.json').read_text(encoding='utf-8'))
    names=[]
    for item in manifest['files']:
        name=item['path'];relative=PurePosixPath(name);path=ROOT/name
        if relative.is_absolute() or '..' in relative.parts or BLOCKED.intersection(relative.parts):raise ValueError('Forbidden package path')
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(ROOT):raise ValueError('Unsafe package source')
        if path.suffix.lower() in BAD_EXT or '.sqlite' in path.name.lower():raise ValueError('Private artifact extension')
        if path.suffix.lower()=='.png' and name!='ios/MemoryDirector/Assets.xcassets/AppIcon.appiconset/AppIcon.png':raise ValueError('Unapproved bitmap')
        data=path.read_bytes()
        if hashlib.sha256(data).hexdigest()!=item['sha256']:raise ValueError('Public manifest mismatch: '+name)
        if TOKEN.search(data) or PROFILE.search(data):raise ValueError('Potential secret or user-profile path: '+name)
        names.append(name)
    if len(names)!=len(set(names)):raise ValueError('Duplicate manifest path')
    return sorted(names)+['PUBLIC-MANIFEST.json']


def archive(destination,names,prefix,strip_prefix=''):
    destination.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(destination,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as output:
        for name in names:
            entry=name[len(strip_prefix):] if strip_prefix else name
            info=zipfile.ZipInfo(prefix+'/'+entry,date_time=(2026,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
            info.external_attr=(0o100644<<16);info.create_system=3
            output.writestr(info,(ROOT/name).read_bytes())


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=ROOT/'outputs');args=parser.parse_args()
    names=checked_files();version='0.1.0-preview'
    archive(args.output/f'memory-director-{version}-source.zip',names,'memory-director')
    ios=[n for n in names if n.startswith('ios/') or n in ('LICENSE','PRIVACY.md','RELEASE-CHECKLIST.md')]
    archive(args.output/f'memory-director-{version}-ios-source.zip',ios,'memory-director-ios')
    skill=[n for n in names if n.startswith('skills/memory-director-setup/')]
    archive(args.output/f'memory-director-{version}-skill.zip',skill,'memory-director-setup','skills/memory-director-setup/')
    sums=[]
    for path in sorted(args.output.glob(f'memory-director-{version}-*.zip')):
        sums.append(hashlib.sha256(path.read_bytes()).hexdigest()+'  '+path.name)
    (args.output/'SHA256SUMS.txt').write_text('\n'.join(sums)+'\n',encoding='ascii')
    print(json.dumps({'public_files_checked':len(names),'archives':3,'private_runtime_directories_included':False}))
