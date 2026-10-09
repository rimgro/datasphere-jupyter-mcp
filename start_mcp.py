"""Local stdio launcher for DataSphere's Jupyter MCP. No public listener."""
import os
from pathlib import Path
import shutil
import sys

package = Path(__file__).resolve().parent
url = os.environ.get('JUPYTER_URL', '').strip().rstrip('/')
token_file = Path(os.environ.get('DATASPHERE_TOKEN_FILE', package / 'token')).expanduser()
if not url:
    raise SystemExit('Set JUPYTER_URL to your DataSphere Jupyter URL')
if not token_file.is_file():
    raise SystemExit(f'Token file not found: {token_file}')
token = token_file.read_text().strip()
if not token:
    raise SystemExit(f'Token file is empty: {token_file}')
os.environ['JUPYTER_TOKEN'] = token
os.environ['JUPYTER_URL'] = url
os.environ['JUPYTERLAB'] = 'False'
os.environ['START_NEW_CODE_SANDBOX'] = 'False'
os.environ['OPEN_NOTEBOOK_IN_UI'] = 'False'
server = shutil.which('jupyter-mcp-server')
if not server:
    raise SystemExit('Install jupyter-mcp-server==2.2.3 and ensure it is on PATH')
os.execv(sys.executable, [sys.executable, str(package / 'runtime.py')])
