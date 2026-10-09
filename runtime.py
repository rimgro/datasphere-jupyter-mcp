"""Compatibility shim: DataSphere Jupyter requires token auth, not Bearer.

Only rewrites requests to the explicitly configured Jupyter base URL.
Installed third-party packages remain unmodified.
"""
import os
from pathlib import Path
import runpy
import sys
import shutil
import requests

original_prepare = requests.Session.prepare_request
base = os.environ['JUPYTER_URL'].rstrip('/') + '/'

def prepare_request(self, request):
    prepared = original_prepare(self, request)
    authorization = prepared.headers.get('Authorization', '')
    if prepared.url.startswith(base) and authorization.startswith('Bearer '):
        prepared.headers['Authorization'] = 'token ' + authorization[len('Bearer '):]
    return prepared

requests.Session.prepare_request = prepare_request

# DataSphere's Jupyter endpoint exposes Contents REST but not the collaboration
# session endpoint expected by jupyter-mcp-server's remote RTC notebook client.
from rest_notebook_compat import install as install_rest_notebook_compat
install_rest_notebook_compat()

from kernel_compat import install as install_kernel_compat
install_kernel_compat(os.environ['JUPYTER_URL'])

from compute_config import install as install_compute_config
install_compute_config(os.environ['JUPYTER_URL'], os.environ['JUPYTER_TOKEN'])

from file_transfer_tools import install as install_file_transfer_tools
install_file_transfer_tools(os.environ['JUPYTER_URL'], os.environ['JUPYTER_TOKEN'])

entrypoint = shutil.which('jupyter-mcp-server')
if not entrypoint:
    raise SystemExit('jupyter-mcp-server executable not found on PATH')
sys.argv = [entrypoint, 'start']
runpy.run_path(entrypoint, run_name='__main__')
