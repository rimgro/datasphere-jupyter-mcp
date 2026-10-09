"""Offline regression tests; never contact or start remote compute."""
import datetime
import json
import logging
import queue
from types import SimpleNamespace
import unittest

from jupyter_kernel_client.wsclient import KernelWebSocketClient, WSChannel
from kernel_compat import install

BASE = 'https://example.invalid/lab/v2/test'
install(BASE)


class KernelCompatTests(unittest.TestCase):
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
