"""Offline compute selection regressions: no network or paid compute."""
import json
import unittest
from unittest.mock import Mock, patch

import compute_config as config

BASE = 'https://example.invalid/lab/v2/test'
KID = '00000000-0000-0000-0000-000000000001'


class ComputeConfigTests(unittest.TestCase):
    def setUp(self):
        config._SELECTIONS.clear()
        self.client = config.ComputeConfig(BASE, 'not-a-secret')

    def tearDown(self):
        config._SELECTIONS.clear()

    def test_query_only_sends_readonly_message_and_closes(self):
        socket = Mock()
        def reply():
            request = json.loads(socket.send.call_args.args[0])
            return json.dumps({'header': {'msg_type': 'yds_get_current_spec_reply'},
                               'parent_header': {'msg_id': request['header']['msg_id']},
                               'content': {'name': 'c1.4'}})
        socket.recv.side_effect = reply
        with patch.object(config.websocket, 'create_connection', return_value=socket):
            self.assertEqual(self.client.current(KID)['current_spec'], 'c1.4')
        self.assertEqual(json.loads(socket.send.call_args.args[0])['header']['msg_type'],
                         'yds_get_current_spec_request')
        socket.send.assert_called_once()
        socket.close.assert_called_once()

    def test_query_failure_closes(self):
        socket = Mock()
        socket.recv.side_effect = TimeoutError()
        with patch.object(config.websocket, 'create_connection', return_value=socket):
            with self.assertRaises(TimeoutError):
                self.client.current(KID)
        socket.close.assert_called_once()

    def test_selection_is_scoped_and_not_a_vm_verification(self):
        response = Mock()
        response.json.return_value = {'execution_state': 'idle'}
        with patch.object(self.client, 'current', return_value={'current_spec': 'NOT_SPECIFIED'}), \
             patch.object(config.requests, 'get', return_value=response):
            result = self.client.select(KID, 'g1.1')
        self.assertFalse(result['compute_started'])
        self.assertFalse(result['verified_on_vm'])
        metadata = {}
        config.apply_selection(BASE.replace('https:', 'wss:') + '/api/kernels/' + KID + '/channels?session_id=test', metadata)
        self.assertEqual(metadata, {'spec_config': 'g1.1'})
        unrelated = {}
        config.apply_selection('wss://other.invalid/api/kernels/' + KID + '/channels', unrelated)
        self.assertEqual(unrelated, {})
        with self.assertRaises(ValueError):
            config.apply_selection(BASE + '/api/kernels/' + KID + '/channels', {'spec_config': 'c1.4'})

    def test_refuses_existing_different_compute(self):
        with patch.object(self.client, 'current', return_value={'current_spec': 'c1.4'}), \
             patch.object(config.requests, 'get') as get:
            with self.assertRaisesRegex(RuntimeError, 'already uses c1.4'):
                self.client.select(KID, 'g1.1')
        get.assert_not_called()
        self.assertEqual(config._SELECTIONS, {})

    def test_refuses_busy_compute(self):
        response = Mock()
        response.json.return_value = {'execution_state': 'busy'}
        with patch.object(self.client, 'current', return_value={'current_spec': 'NOT_SPECIFIED'}), \
             patch.object(config.requests, 'get', return_value=response):
            with self.assertRaisesRegex(RuntimeError, 'not idle'):
                self.client.select(KID, 'g1.1')
        self.assertEqual(config._SELECTIONS, {})

    def test_validates_identifiers_before_network(self):
        with patch.object(config.websocket, 'create_connection') as connect:
            with self.assertRaises(ValueError):
                self.client.current('../other')
            with self.assertRaises(ValueError):
                self.client.select(KID, 'garbage')
        connect.assert_not_called()


if __name__ == '__main__':
    unittest.main()
