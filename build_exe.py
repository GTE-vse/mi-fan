from pathlib import Path
import subprocess
import sys
root=Path(__file__).resolve().parent
target=Path(sys.argv[1]).resolve() if len(sys.argv)>1 else root/'dist'
subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onefile','--windowed',
 '--name','mi_fan','--manifest',str(root/'AirLink.manifest'),
 '--icon',str(root/'mi_fan.ico'),
 '--distpath',str(target),'--workpath',str(root/'build'),'--specpath',str(root/'build'),
 '--paths',str(root/'backend'),'--collect-submodules','miio','--copy-metadata','python-miio',
 '--copy-metadata','netifaces-plus','--collect-all','webview','--collect-all','clr_loader',
 '--hidden-import','pythonnet',
 '--hidden-import','pystray','--hidden-import','pystray._win32',
 '--hidden-import','PIL.Image','--hidden-import','PIL.ImageDraw',
 '--collect-submodules','PIL','--collect-submodules','pystray',
 '--add-data',str(root/'frontend/dist')+';frontend/dist',
 '--add-data',str(root/'mi_fan.ico')+';.',
 str(root/'backend/main.py')],check=True)
