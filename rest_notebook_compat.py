"""REST Contents API fallback for DataSphere deployments without Jupyter RTC.

The Jupyter MCP package's remote notebook client requires
/api/collaboration/session, which DataSphere does not expose on this instance.
This adapter gives its cell CRUD tools a file-backed notebook model instead.
It intentionally refuses writes while a JupyterLab notebook session is open:
Contents PUT is not compare-and-swap and could otherwise overwrite unsaved edits.
"""
from __future__ import annotations

from copy import deepcopy
import threading
from urllib.parse import quote

import nbformat
import requests

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _document_lock(url: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(url, threading.Lock())


class RestNotebookDocument:
    def __init__(self, base_url: str, token: str, path: str):
        self.base_url = base_url.rstrip("/")
        self.path = path.lstrip("/")
        self.url = f"{self.base_url}/api/contents/{quote(self.path, safe='/')}"
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {token}"})
        self._load()

    def _load(self):
        response = self.session.get(self.url, timeout=30)
        response.raise_for_status()
        payload = response.json()
        if payload.get("type") != "notebook" or not isinstance(payload.get("content"), dict):
            raise ValueError(f"{self.path!r} is not a Jupyter notebook")
        self._content = payload["content"]
        self._etag = response.headers.get("ETag")
        self._last_modified = response.headers.get("Last-Modified") or payload.get("last_modified")

    def _check_no_active_lab_session(self):
        response = self.session.get(f"{self.base_url}/api/sessions", timeout=30)
        response.raise_for_status()
        for session in response.json():
            if session.get("type") == "notebook" and session.get("path", "").lstrip("/") == self.path:
                raise RuntimeError(
                    f"Refusing to save {self.path!r}: it is open in a JupyterLab session. "
                    "Close that notebook session before using REST-based cell editing, "
                    "to avoid overwriting unsaved edits."
                )

    def _save(self):
        self._check_no_active_lab_session()
        # Contents API exposes ETag/Last-Modified but this DataSphere endpoint
        # ignores If-Match, so re-read immediately before PUT and fail on a change.
        # The lock serializes callers in this MCP process; it cannot serialize
        # edits from external clients in the small GET-to-PUT window.
        with _document_lock(self.url):
            check = self.session.get(self.url, timeout=30)
            check.raise_for_status()
            current_etag = check.headers.get("ETag")
            current_modified = check.headers.get("Last-Modified") or check.json().get("last_modified")
            changed = (
                current_etag != self._etag
                if self._etag
                else bool(self._last_modified and current_modified != self._last_modified)
            )
            if changed:
                raise RuntimeError(
                    f"Refusing to overwrite {self.path!r}: it changed since it was read. "
                    "Re-read the notebook and retry the edit."
                )
            response = self.session.put(
                self.url,
                json={"type": "notebook", "format": "json", "content": self._content},
                headers={"If-Match": self._etag} if self._etag else None,
                timeout=30,
            )
            response.raise_for_status()
            self._etag = response.headers.get("ETag")
            self._last_modified = response.headers.get("Last-Modified") or response.json().get("last_modified")

    def __len__(self):
        return len(self._content["cells"])

    def __getitem__(self, index):
        return self._content["cells"][index]

    def __setitem__(self, index, cell):
        self._content["cells"][index] = deepcopy(cell)
        self._save()

    def __delitem__(self, index):
        del self._content["cells"][index]
        self._save()

    def as_dict(self):
        return deepcopy(self._content)

    def format_output(self, **kwargs):
        from jupyter_mcp_server.models import Notebook
        return Notebook(**self._content).format_output(**kwargs)

    def get_cell_source(self, index):
        source = self._content["cells"][index].get("source", "")
        return source if isinstance(source, str) else "".join(source)

    def set_cell_source(self, index, source):
        self._content["cells"][index]["source"] = source
        self._save()

    def insert_cell(self, index, source, cell_type="code"):
        constructors = {
            "code": nbformat.v4.new_code_cell,
            "markdown": nbformat.v4.new_markdown_cell,
            "raw": nbformat.v4.new_raw_cell,
        }
        try:
            cell = constructors[cell_type](source=source or "")
        except KeyError as exc:
            raise ValueError(f"Unsupported notebook cell type: {cell_type}") from exc
        if self._content.get("nbformat_minor", 0) < 5:
            cell.pop("id", None)
        self._content["cells"].insert(index, cell)
        self._save()

    def insert(self, index, cell):
        self._content["cells"].insert(index, deepcopy(cell))
        self._save()

    def delete_cell(self, index):
        cell = self._content["cells"].pop(index)
        self._save()
        return cell

    def add_code_cell(self, source):
        self.insert_cell(len(self), source, "code")

    def add_markdown_cell(self, source):
        self.insert_cell(len(self), source, "markdown")

    def execute_cell(self, index, kernel):
        cell = self._content["cells"][index]
        if cell.get("cell_type") != "code":
            raise ValueError(f"Cell {index} is not a code cell")
        result = kernel.execute(self.get_cell_source(index))
        cell["outputs"] = deepcopy(result.get("outputs", []))
        cell["execution_count"] = result.get("execution_count")
        self._save()
        return cell["outputs"]

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.session.close()


def install():
    """Install file-backed documents for MCP's remote notebook cell tools."""
    from jupyter_mcp_server.notebook_manager import NotebookConnection
    from jupyter_mcp_server.config import get_config

    async def rest_enter(self):
        config = get_config()
        base_url = self.notebook_info.get("server_url") or config.resolved_document_url()
        token = self.notebook_info.get("token") or config.resolved_document_token()
        path = self.notebook_info.get("path", config.document_id)
        self._notebook = RestNotebookDocument(base_url, token, path)
        return self._notebook

    NotebookConnection.__aenter__ = rest_enter
    # RTC is deliberately bypassed here. Its extension-version probe is not
    # applicable and otherwise burns ~50 seconds on an unsupported endpoint.
    from jupyter_mcp_server.tools.use_notebook_tool import _COLLABORATION_VERDICTS
    import os
    config = get_config()
    for url in (config.resolved_document_url(), os.environ.get('JUPYTER_URL')):
        if url:
            _COLLABORATION_VERDICTS[url.rstrip('/')] = None
            _COLLABORATION_VERDICTS[url.rstrip('/') + '/'] = None
    return True
