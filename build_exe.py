"""Build a portable Windows EXE and ZIP without including local user data."""
from pathlib import Path
import hashlib
from importlib.metadata import distribution
import os
import subprocess
import shutil
import sys
import zipfile

from guitar_player import __version__


def build():
    root=Path(__file__).resolve().parent
    output=root/'dist'/f'v{__version__}'
    output.mkdir(parents=True,exist_ok=True)
    work=root/'build'/'pyinstaller'
    work.mkdir(parents=True,exist_ok=True)
    icon=root/'assets'/'app.ico'
    if not icon.is_file() or not (icon.parent/'app-icon.png').is_file():raise RuntimeError('缺少程序头像图标')
    version=tuple(map(int,__version__.split('.')))+(0,)
    version_file=work/'version_info.txt'
    version_file.write_text(f'''VSVersionInfo(
  ffi=FixedFileInfo(filevers={version}, prodvers={version}, mask=0x3f,
    flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0,0)),
  kids=[StringFileInfo([StringTable('080404b0', [
    StringStruct('CompanyName', 'Akari'),
    StringStruct('FileDescription', '异环吉他编曲器'),
    StringStruct('FileVersion', '{__version__}.0'),
    StringStruct('InternalName', 'NTEGuitarPlayer'),
    StringStruct('OriginalFilename', 'NTEGuitarPlayer.exe'),
    StringStruct('ProductName', 'NTE Guitar Player'),
    StringStruct('ProductVersion', '{__version__}')])]),
    VarFileInfo([VarStruct('Translation', [2052,1200])])])
''',encoding='utf-8')
    subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onefile','--windowed',
                    '--name','NTEGuitarPlayer','--paths',str(root/'vendor'),
                    '--icon',str(icon),'--version-file',str(version_file),
                    '--add-data',str(root/'assets')+os.pathsep+'assets',
                    '--add-data',str(root/'examples')+os.pathsep+'examples',
                    '--add-data',str(root/'vendor/mido-1.3.3.dist-info')+os.pathsep+'mido-1.3.3.dist-info',
                    '--distpath',str(output),'--workpath',str(work/'cache'),'--specpath',str(work),
                    str(root/'main.py')],cwd=root,check=True)
    licenses=output/'licenses';licenses.mkdir(exist_ok=True)
    shutil.copyfile(Path(sys.base_prefix)/'LICENSE.txt',licenses/'Python-LICENSE.txt')
    for source in (Path(sys.base_prefix)/'tcl').rglob('license.terms'):
        if 'demos' not in source.parts:shutil.copyfile(source,licenses/(source.parent.name+'-license.terms'))
    dist=distribution('pyinstaller')
    for item in dist.files:
        if item.name in ('COPYING.txt','COPYING','LICENSE'):
            shutil.copyfile(dist.locate_file(item),licenses/('PyInstaller-'+item.name))
    package=root/'dist'/f'NTE-Guitar-Player-v{__version__}-Windows.zip'
    with zipfile.ZipFile(package,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        for name in ('NTEGuitarPlayer.exe',):archive.write(output/name,name)
        for name in ('README.md','THIRD_PARTY.md'):archive.write(root/name,name)
        archive.write(root/'assets'/'icon-source.md','assets/icon-source.md')
        gamepad_license=root/'assets/gamepad/ViGEmClient-LICENSE.txt'
        if gamepad_license.is_file():archive.write(gamepad_license,'assets/gamepad/ViGEmClient-LICENSE.txt')
        for path in sorted(licenses.iterdir()):archive.write(path,Path('licenses')/path.name)
        for folder in ('examples','vendor/mido-1.3.3.dist-info','vendor/packaging-26.3.dist-info/licenses'):
            for path in sorted((root/folder).rglob('*')):
                if path.is_file() and '__pycache__' not in path.parts:archive.write(path,path.relative_to(root))
    with zipfile.ZipFile(package) as archive:
        if archive.testzip():raise RuntimeError('EXE 压缩包检查失败')
    digest=hashlib.sha256(package.read_bytes()).hexdigest()
    package.with_suffix('.zip.sha256').write_text(digest+'  '+package.name+'\n',encoding='ascii')
    print(package)
    print(output/'NTEGuitarPlayer.exe')


if __name__=='__main__':build()
