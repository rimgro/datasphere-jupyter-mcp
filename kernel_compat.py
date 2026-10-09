"""Scoped DataSphere kernel compatibility; third-party sources stay untouched."""
from copy import deepcopy
import json
from urllib.parse import urlsplit
import uuid


def install(base_url):
    from jupyter_kernel_client import JupyterKernelClient
    from jupyter_kernel_client.wsclient import WSChannel, KernelWebSocketClient

    expected = urlsplit(base_url.rstrip('/') + '/')
    original_start = JupyterKernelClient.start
    original_send = WSChannel.send
    original_channels = KernelWebSocketClient.start_channels
    original_message = KernelWebSocketClient._on_message

    def matches(url):
        parsed = urlsplit(url)
        return (parsed.hostname == expected.hostname
                and parsed.path.startswith(expected.path)
                and parsed.scheme in {'https', 'wss'})

    def start_channels(self, *args, **kwargs):
        if matches(self.kernel_ws_endpoint) and self.token:
            self._headers = dict(self._headers or {})
            self._headers['Authorization'] = 'token ' + self.token
            token = self.token
            self.token = None  # Use header auth, exactly like the working raw probe.
            try:
                return original_channels(self, *args, **kwargs)
            finally:
                self.token = token
        return original_channels(self, *args, **kwargs)

    def on_message(self, socket, message):
        if matches(self.kernel_ws_endpoint) and isinstance(message, str):
            msg = json.loads(message)
            header = msg.get('header', {})
            msg.setdefault('msg_id', header.get('msg_id'))
            msg.setdefault('msg_type', header.get('msg_type'))
            message = json.dumps(msg)
        return original_message(self, socket, message)

    def start(self, *args, **kwargs):
        original_start(self, *args, **kwargs)
        if matches(self.server_url.rstrip('/') + '/'):
            # A WS handshake alone does not initialize the DataSphere transport.
            self._manager.client.kernel_info_interactive(timeout=15)

    def send(self, msg):
        if matches(self.socket.url):
            msg = deepcopy(msg)
            # json.dumps(default=str) renders datetime with a space, not RFC3339.
            # The successful raw DataSphere probe uses an ISO 'T' separator.
            for key in ('header', 'parent_header'):
                header = msg.get(key, {})
                date = header.get('date')
                if hasattr(date, 'isoformat'):
                    header['date'] = date.isoformat()
            if msg.get('header', {}).get('msg_type') == 'execute_request':
                metadata = msg.setdefault('metadata', {})
                metadata.setdefault('cellId', uuid.uuid4().hex)
                metadata.setdefault('deletedCells', [])
                metadata.setdefault('recordTiming', False)
        return original_send(self, msg)

    JupyterKernelClient.start = start
    KernelWebSocketClient.start_channels = start_channels
    KernelWebSocketClient._on_message = on_message
    WSChannel.send = send
