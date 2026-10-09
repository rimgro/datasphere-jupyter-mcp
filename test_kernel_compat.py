"""Offline regression tests; never contact or start remote compute."""
import datetime
import json
import logging
import queue
from types import SimpleNamespace
import unittest

from jupyter_kernel_client.wsclient import KernelWebSocketClient, WSChannel
from kernel_compat import install
from compute_config import _SELECTIONS, kernel_key

BASE = 'https://example.invalid/lab/v2/test'
install(BASE)


class KernelCompatTests(unittest.TestCase):
    def tearDown(self):
        _SELECTIONS.clear()

    def test_selected_compute_is_added_without_mutating_caller(self):
        captured = []
        kid = '00000000-0000-0000-0000-000000000001'
        endpoint = kernel_key(BASE, kid)
        _SELECTIONS[endpoint] = 'g1.1'
        session = SimpleNamespace(send=lambda socket, channel, msg: captured.append(msg))
        socket = SimpleNamespace(url=endpoint.replace('https:', 'wss:'))
        channel = WSChannel('shell', socket, session, queue.Queue(), logging.getLogger('test'))
        msg = {'msg_id': 'req', 'msg_type': 'execute_request',
               'header': {'msg_type': 'execute_request'}, 'content': {'code': '2+2'},
               'metadata': {}}
        channel.send(msg)
        self.assertEqual(captured[0]['metadata']['spec_config'], 'g1.1')
        self.assertEqual(msg['metadata'], {})
        channel.send({'msg_id': 'info', 'msg_type': 'kernel_info_request',
                      'header': {'msg_type': 'kernel_info_request'}, 'metadata': {}})
        self.assertNotIn('spec_config', captured[1]['metadata'])

    def test_header_only_reply_is_received(self):
        client = KernelWebSocketClient('wss://example.invalid/lab/v2/test/api/kernels/test/channels')
        msg = {'channel':'shell', 'header':{'msg_id':'reply', 'msg_type':'kernel_info_reply'},
               'parent_header':{}, 'metadata':{}, 'content':{'status':'ok'}}
        client._on_message(None, json.dumps(msg))
        received = client._shell_msg_queue.get_nowait()
        self.assertEqual(received['msg_id'], 'reply')
        self.assertEqual(received['msg_type'], 'kernel_info_reply')

    def test_other_endpoint_is_not_normalized(self):
        client = KernelWebSocketClient('wss://other.invalid/lab/v2/test/api/kernels/test/channels')
        msg = {'channel':'shell', 'header':{'msg_id':'reply', 'msg_type':'kernel_info_reply'},
               'parent_header':{}, 'metadata':{}, 'content':{}}
        # This is the unpatched library failure for a header-only reply.
        with self.assertRaises(KeyError):
            client._on_message(None, json.dumps(msg))

    def test_execution_metadata_and_iso_date_without_mutating_caller(self):
        captured = []
        session = SimpleNamespace(send=lambda socket, channel, msg: captured.append(msg))
        socket = SimpleNamespace(url='wss://example.invalid/lab/v2/test/api/kernels/test/channels')
        channel = WSChannel('shell', socket, session, queue.Queue(), logging.getLogger('test'))
        date = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
        msg = {'msg_id':'req', 'msg_type':'execute_request',
               'header':{'msg_id':'req', 'msg_type':'execute_request', 'date':date},
               'parent_header':{}, 'content':{'code':'2+2'}, 'metadata':{'cellId':'real-cell'}}
        channel.send(msg)
        self.assertEqual(captured[0]['header']['date'], '2026-01-01T00:00:00+00:00')
        self.assertEqual(captured[0]['metadata']['cellId'], 'real-cell')
        self.assertEqual(captured[0]['metadata']['deletedCells'], [])
        self.assertIs(msg['header']['date'], date)
        self.assertNotIn('deletedCells', msg['metadata'])


if __name__ == '__main__':
    unittest.main()
