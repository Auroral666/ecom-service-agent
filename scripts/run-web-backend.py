"""Web launcher with UTF-8 streams; service lifetime is managed by project-services.ps1."""
import os
from pathlib import Path
import sys

os.environ['PYTHONUTF8'] = '1'
os.environ['PYTHONIOENCODING'] = 'utf-8'
for stream in (sys.stdout, sys.stderr):
    if stream is not None:
        stream.reconfigure(encoding='utf-8')

project_root = Path(__file__).resolve().parents[1]
os.chdir(project_root)
sys.path.insert(0, str(project_root))

if __name__ == '__main__':
    import uvicorn
    uvicorn.run('app.api.main:app', host='127.0.0.1', port=18080)
