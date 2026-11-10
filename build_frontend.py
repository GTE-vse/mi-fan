"""Build offline desktop assets; direct esbuild CLI also works in restricted shells."""
from pathlib import Path
import subprocess
root=Path(__file__).resolve().parent/'frontend'
subprocess.run(['node',str(root/'node_modules/typescript/bin/tsc'),'--noEmit','-p',str(root/'tsconfig.json')],check=True)
executable=root/'node_modules/@esbuild/win32-x64/esbuild.exe'
subprocess.run([str(executable),str(root/'src/main.tsx'),'--bundle','--minify','--format=esm',
    '--target=chrome100','--define:process.env.NODE_ENV="production"',
    '--outfile='+str(root/'dist/assets/app.js'),'--legal-comments=eof'],check=True)
html=(root/'index.html').read_text(encoding='utf-8').replace('/src/main.tsx','./assets/app.js')
html=html.replace('</head>','<link rel="stylesheet" href="./assets/app.css"/></head>')
(root/'dist/index.html').write_text(html,encoding='utf-8')
print('Frontend assets ready.')
