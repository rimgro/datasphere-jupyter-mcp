"""Authenticated file transfer over Jupyter HTTP; no kernel or public listener."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import os
from pathlib import Path
import tempfile
import time
from urllib.parse import quote, urlsplit

import requests

DEFAULT_DOWNLOAD_LIMIT = 2 * 1024**3
UPLOAD_LIMIT = 32 * 1024**2


def remote_path(value: str) -> str:
    # Paths, not URLs. Preserve literal percent signs; quote exactly once below.
    if (not value or value.startswith('/') or '\\' in value
            or any(ord(c) < 32 for c in value)
            or any(p in {'', '.', '..'} for p in value.split('/'))):
        raise ValueError('Use a relative Jupyter file path without empty, . or .. segments')
    return value


def local_path(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError('local_path must be absolute (or start with ~/), on the MCP host')
    if path.is_symlink():
        raise ValueError('Refusing a symbolic-link file')
    return path


class FileTransfer:
    def __init__(self, base_url: str, token: str):
        parsed = urlsplit(base_url)
        if parsed.scheme != 'https' or parsed.query or parsed.fragment or parsed.username:
            raise ValueError('Expected an HTTPS Jupyter base URL without credentials/query')
        self.base_url = base_url.rstrip('/')
        self.token = token

    def session(self):
        http = requests.Session()
        http.headers['Authorization'] = 'token ' + self.token
        return http

    def contents_url(self, path):
        return self.base_url + '/api/contents/' + quote(remote_path(path), safe='/')

    def metadata(self, http, path):
        with http.get(self.contents_url(path), params={'content': 0},
                      timeout=(10, 30), allow_redirects=False) as response:
            if response.status_code != 200:
                raise RuntimeError(f'Jupyter file lookup failed: HTTP {response.status_code}')
            data = response.json()
        if data.get('type') not in {'file', 'notebook'}:
            raise ValueError('The remote path must name a file, not a directory')
        return data

    def link(self, path: str):
        path = remote_path(path)
        with self.session() as http:
            meta = self.metadata(http, path)
        return {'remote_path': path, 'size_bytes': meta.get('size'),
                'url': self.base_url + '/files/' + quote(path, safe='/') + '?download=1',
                'requires_authentication': True,
                'note': 'Not a public or signed link. Requires an authorized Jupyter browser session; no token is included.'}

    def download(self, path: str, destination: str, overwrite=False,
                 max_bytes=DEFAULT_DOWNLOAD_LIMIT):
        path = remote_path(path)
        target = local_path(destination)
        if not 1 <= max_bytes <= 16 * 1024**3:
            raise ValueError('max_bytes must be between 1 byte and 16 GiB')
        if not target.parent.is_dir():
            raise ValueError('The local parent directory must already exist')
        if target.exists() and (not overwrite or not target.is_file()):
            raise FileExistsError('Local destination exists; use overwrite=true only for an intentional file replacement')
        temporary = None
        digest = hashlib.sha256()
        total = 0
        deadline = time.monotonic() + 300
        try:
            with self.session() as http:
                meta = self.metadata(http, path)
                expected_size = meta.get('size')
                if isinstance(expected_size, int) and expected_size > max_bytes:
                    raise ValueError('Remote file exceeds max_bytes')
                url = self.base_url + '/files/' + quote(path, safe='/')
                with http.get(url, stream=True, timeout=(10, 30), allow_redirects=False) as response:
                    if response.status_code != 200:
                        raise RuntimeError(f'Jupyter download failed: HTTP {response.status_code}')
                    with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.datasphere-download-',
                                                     delete=False) as out:
                        temporary = Path(out.name)  # mode 0600
                        for chunk in response.iter_content(chunk_size=1024 * 1024):
                            if time.monotonic() > deadline:
                                raise TimeoutError('Download exceeded the 300-second total deadline')
                            if not chunk:
                                continue
                            total += len(chunk)
                            if total > max_bytes:
                                raise ValueError('Download exceeds max_bytes')
                            out.write(chunk)
                            digest.update(chunk)
                        out.flush()
                        os.fsync(out.fileno())
                if isinstance(expected_size, int) and total != expected_size:
                    raise RuntimeError('Downloaded size differs from metadata; file may have changed. Destination was not replaced.')
            # Publish only a completed file. Hard-link creation is exclusive and
            # prevents a concurrent creator from being overwritten by default.
            if overwrite:
                if target.is_symlink() or (target.exists() and not target.is_file()):
                    raise ValueError('Destination became a symlink or non-file')
                os.replace(temporary, target)
            else:
                os.link(temporary, target)
                temporary.unlink()
            temporary = None
            return {'remote_path': path, 'local_path': str(target), 'size_bytes': total,
                    'sha256': digest.hexdigest()}
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def upload(self, source: str, path: str, overwrite=False):
        path = remote_path(path)
        if not path.startswith('project/'):
            raise ValueError('Uploads are restricted to project/ to protect Jupyter configuration')
        source = local_path(source)
        if not source.is_file():
            raise ValueError('local_path must name a regular local file')
        # Refuse accidental credential uploads from agent/cloud/SSH directories.
        resolved = source.resolve()
        for private in (Path.home()/'.pi', Path.home()/'.ssh', Path.home()/'.aws', Path.home()/'.config/gcloud'):
            if resolved.is_relative_to(private.resolve()):
                raise ValueError('Refusing upload from a private credential/configuration directory')
        with source.open('rb') as handle:
            payload = handle.read(UPLOAD_LIMIT + 1)
        if len(payload) > UPLOAD_LIMIT:
            raise ValueError('Upload limit is 32 MiB (Contents API base64 transfer)')
        if path.lower().endswith('.ipynb'):
            import nbformat
            nbformat.validate(nbformat.reads(payload.decode('utf-8'), as_version=4))
        with self.session() as http:
            url = self.contents_url(path)
            with http.get(url, params={'content': 0}, timeout=(10, 30), allow_redirects=False) as response:
                if response.status_code == 200:
                    if not overwrite:
                        raise FileExistsError('Remote destination exists; overwrite is false')
                    if response.json().get('type') not in {'file', 'notebook'}:
                        raise ValueError('Cannot overwrite a directory')
                elif response.status_code != 404:
                    raise RuntimeError(f'Jupyter destination lookup failed: HTTP {response.status_code}')
            with http.get(self.base_url+'/api/sessions', timeout=(10, 30), allow_redirects=False) as response:
                if response.status_code != 200:
                    raise RuntimeError(f'Jupyter session lookup failed: HTTP {response.status_code}')
                if any(s.get('path', '').lstrip('/') == path for s in response.json()):
                    raise RuntimeError('Refusing upload: destination is open in a Jupyter session')
            # DataSphere ignores If-Match: the existence check is best-effort,
            # not atomic with PUT. Do not edit the same remote file concurrently.
            with http.put(url, json={'type': 'file', 'format': 'base64',
                                    'content': base64.b64encode(payload).decode('ascii')},
                          timeout=(10, 60), allow_redirects=False) as response:
                if response.status_code not in {200, 201}:
                    raise RuntimeError(f'Jupyter upload failed: HTTP {response.status_code}')
            # Verify actual binary contents, not just a successful PUT status.
            digest = hashlib.sha256()
            count = 0
            with http.get(self.base_url+'/files/'+quote(path, safe='/'), stream=True,
                          timeout=(10, 30), allow_redirects=False) as response:
                if response.status_code != 200:
                    raise RuntimeError(f'Upload readback failed: HTTP {response.status_code}; uploaded file may remain')
                for chunk in response.iter_content(chunk_size=1024*1024):
                    count += len(chunk)
                    if count > UPLOAD_LIMIT:
                        raise RuntimeError('Upload readback exceeds limit; remote file may have changed')
                    digest.update(chunk)
            sha256 = hashlib.sha256(payload).hexdigest()
            if count != len(payload) or digest.hexdigest() != sha256:
                raise RuntimeError('Upload readback differs; remote file may have changed')
        return {'local_path': str(source), 'remote_path': path, 'size_bytes': len(payload),
                'sha256': sha256, 'readback_verified': True}


def install(base_url: str, token: str):
    from jupyter_mcp_server.server import mcp
    from mcp.types import ToolAnnotations
    transfer = FileTransfer(base_url, token)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False))
    async def get_file_download_link(remote_path: str) -> dict:
        """Get a token-free Jupyter download URL for an existing file. Requires browser Jupyter authentication; not a public link. Does not run Python."""
        return await asyncio.to_thread(transfer.link, remote_path)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True))
    async def download_file(remote_path: str, local_path: str, overwrite: bool = False,
                            max_bytes: int = DEFAULT_DOWNLOAD_LIMIT) -> dict:
        """Download a remote Jupyter file to an explicit absolute path on the local MCP host. Streams binary data (default limit 2 GiB), returns SHA256; no kernel. Parent directory must exist. Never overwrite unless explicitly requested."""
        return await asyncio.to_thread(transfer.download, remote_path, local_path, overwrite, max_bytes)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True))
    async def upload_file(local_path: str, remote_path: str, overwrite: bool = False) -> dict:
        """Upload an explicitly selected local file to DataSphere project/ (up to 32 MiB). Verify SHA256 by readback. Refuses open Jupyter sessions and private credential directories. Remote overwrite protection is best-effort: do not edit concurrently. Does not run Python."""
        return await asyncio.to_thread(transfer.upload, local_path, remote_path, overwrite)
