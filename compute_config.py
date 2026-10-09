"""Explicit per-kernel DataSphere compute selection; never starts/stops compute.

Uses the same shell query and execute metadata as dedicated_front_extension.
Selections live only in this MCP process and must be reselected after reload.
"""
import asyncio
import datetime
import json
import re
import threading
from urllib.parse import urlsplit
import uuid

import requests
import websocket

_SELECTIONS = {}
_LOCK = threading.Lock()
_SPEC = re.compile(r'[a-z][a-z0-9]*\.[1-9][0-9]*\Z')


def kernel_key(base_url, kernel_id):
    # UUID validation also prevents path/query injection.
    if str(uuid.UUID(kernel_id)) != kernel_id:
        raise ValueError('kernel_id must be a canonical UUID')
    return base_url.rstrip('/') + '/api/kernels/' + kernel_id + '/channels'


def apply_selection(socket_url, metadata):
    parsed = urlsplit(socket_url)
    key = 'https://' + parsed.netloc + parsed.path
    with _LOCK:
        spec = _SELECTIONS.get(key)
    if spec is not None:
        if metadata.get('spec_config', spec) != spec:
            raise ValueError('Execution spec_config conflicts with selected kernel compute')
        metadata['spec_config'] = spec


class ComputeConfig:
    def __init__(self, base_url, token):
        self.base_url = base_url.rstrip('/')
        self.headers = {'Authorization': 'token ' + token}

    def current(self, kernel_id):
        endpoint = kernel_key(self.base_url, kernel_id)
        ws = websocket.create_connection(endpoint.replace('https:', 'wss:', 1),
                                         header=self.headers, timeout=10)
        try:
            mid = uuid.uuid4().hex
            msg = {'header': {'msg_id': mid, 'msg_type': 'yds_get_current_spec_request',
                              'username': 'mcp', 'session': uuid.uuid4().hex,
                              'date': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                              'version': '5.3'},
                   'parent_header': {}, 'metadata': {}, 'content': {}, 'channel': 'shell'}
            ws.send(json.dumps(msg))
            # Bound traffic as well as elapsed time, including unsolicited messages.
            import time
            deadline = time.monotonic() + 10
            for _ in range(100):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                ws.settimeout(remaining)
                reply = json.loads(ws.recv())
                if reply.get('parent_header', {}).get('msg_id') != mid:
                    continue
                if reply.get('header', {}).get('msg_type') == 'yds_get_current_spec_reply':
                    name = reply.get('content', {}).get('name')
                    if name != 'NOT_SPECIFIED' and (not isinstance(name, str) or not _SPEC.fullmatch(name)):
                        raise RuntimeError('DataSphere returned an unexpected compute configuration')
                    with _LOCK:
                        selected = _SELECTIONS.get(endpoint)
                    return {'kernel_id': kernel_id, 'current_spec': name,
                            'selected_spec_for_next_execution': selected}
            raise RuntimeError('No DataSphere compute configuration reply received')
        finally:
            ws.close()

    def select(self, kernel_id, spec):
        if not _SPEC.fullmatch(spec):
            raise ValueError('Expected a configuration name such as g1.1, g2.1 or gt4.1')
        endpoint = kernel_key(self.base_url, kernel_id)
        state = self.current(kernel_id)
        if state['current_spec'] not in {'NOT_SPECIFIED', spec}:
            raise RuntimeError('Kernel already uses ' + state['current_spec'] +
                               '; refusing to change a running configuration. Save results and '
                               'use a new unassigned kernel, or stop compute through DataSphere first.')
        # Do not select while a kernel is executing. This is a best-effort check,
        # not a lock against other clients: use a dedicated MCP-owned kernel.
        response = requests.get(self.base_url + '/api/kernels/' + kernel_id,
                                headers=self.headers, timeout=10)
        response.raise_for_status()
        if response.json().get('execution_state') != 'idle':
            raise RuntimeError('Kernel is not idle; refusing compute selection')
        with _LOCK:
            _SELECTIONS[endpoint] = spec
        return {'kernel_id': kernel_id, 'selected_spec_for_next_execution': spec,
                'compute_started': False, 'verified_on_vm': False,
                'note': 'Process-local selection; lost on MCP reload. Next execution may incur '
                        'charges. Verify get_kernel_compute and nvidia-smi after execution. '
                        'Project defaults and other kernels are not modified.'}


def install(base_url, token):
    from jupyter_mcp_server.server import mcp
    from mcp.types import ToolAnnotations
    config = ComputeConfig(base_url, token)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False))
    async def get_kernel_compute(kernel_id: str) -> dict:
        """Read DataSphere kernel's actual assigned spec (NOT_SPECIFIED if unassigned). Does not execute Python or start/stop compute."""
        return await asyncio.to_thread(config.current, kernel_id)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
    async def select_kernel_compute(kernel_id: str, spec: str) -> dict:
        """Explicitly select a DataSphere VM spec for future MCP executions on an owned idle kernel. No compute starts now; future execution may be billed. Refuses a different already assigned spec. Selection is lost on MCP reload. Does not change project defaults or other kernels. Availability and actual GPU must be verified after execution."""
        return await asyncio.to_thread(config.select, kernel_id, spec)
