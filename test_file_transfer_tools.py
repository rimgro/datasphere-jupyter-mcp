"""Offline file transfer regression tests; no network or kernels."""
import hashlib
from pathlib import Path
import tempfile
import unittest

from file_transfer_tools import FileTransfer, remote_path


class Response:
    def __init__(self, status=200, data=None, chunks=()):
        self.status_code, self.data, self.chunks = status, data, chunks
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def json(self):
        return self.data
    def iter_content(self, chunk_size):
        yield from self.chunks


class HTTP:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def get(self, url, **kw):
        self.calls.append(('GET', url, kw))
        return self.responses.pop(0)
    def put(self, url, **kw):
        self.calls.append(('PUT', url, kw))
        return self.responses.pop(0)


class FileTransferTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.transfer = FileTransfer('https://example.invalid/lab/v2/test', 'SECRET')

    def http(self, *responses):
        client = HTTP(responses)
        self.transfer.session = lambda: client
        return client

    def test_invalid_paths(self):
        for value in ['', '/etc/passwd', '../x', 'project/../x', 'project//x', 'project/./x', 'https://other/x', 'a\\b', 'a\nx']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                remote_path(value)

    def test_encoded_private_link(self):
        http = self.http(Response(data={'type':'file','size':4}))
        result = self.transfer.link('project/тест +%2F.bin')
        self.assertTrue(result['requires_authentication'])
        self.assertIn('%D1%82', result['url'])
        self.assertIn('%20%2B%252F.bin', result['url'])
        self.assertNotIn('SECRET', str(result))
        self.assertNotIn('token=', result['url'])
        self.assertFalse(http.calls[0][2]['allow_redirects'])

    def test_download_binary_and_digest(self):
        payload = b'\x00\xffdata'
        self.http(Response(data={'type':'file','size':len(payload)}), Response(chunks=[payload[:2], payload[2:]]))
        target = self.directory/'result.bin'
        result = self.transfer.download('project/file.bin', str(target))
        self.assertEqual(target.read_bytes(), payload)
        self.assertEqual(result['sha256'], hashlib.sha256(payload).hexdigest())
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_never_overwrite_by_default(self):
        target = self.directory/'existing'
        target.write_bytes(b'original')
        with self.assertRaises(FileExistsError):
            self.transfer.download('project/file', str(target))
        self.assertEqual(target.read_bytes(), b'original')

    def test_failed_download_keeps_original_and_removes_temp(self):
        target = self.directory/'existing'
        target.write_bytes(b'original')
        self.http(Response(data={'type':'file','size':None}), Response(chunks=[b'12345']))
        with self.assertRaises(ValueError):
            self.transfer.download('project/file', str(target), overwrite=True, max_bytes=4)
        self.assertEqual(target.read_bytes(), b'original')
        self.assertEqual(list(self.directory.glob('.datasphere-download-*')), [])

    def test_size_mismatch_not_published(self):
        self.http(Response(data={'type':'file','size':10}), Response(chunks=[b'short']))
        target = self.directory/'result'
        with self.assertRaises(RuntimeError):
            self.transfer.download('project/file', str(target))
        self.assertFalse(target.exists())
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_directory_and_redirect_rejected(self):
        self.http(Response(data={'type':'directory'}))
        with self.assertRaises(ValueError):
            self.transfer.link('project/folder')
        http = self.http(Response(data={'type':'file','size':0}), Response(status=302))
        with self.assertRaises(RuntimeError):
            self.transfer.download('project/file', str(self.directory/'result'))
        self.assertFalse(http.calls[-1][2]['allow_redirects'])

    def test_upload_binary_verified(self):
        payload = b'\x00\xffupload'
        source = self.directory/'source.bin'; source.write_bytes(payload)
        http = self.http(Response(status=404), Response(data=[]), Response(status=201), Response(chunks=[payload]))
        result = self.transfer.upload(str(source), 'project/upload.bin')
        self.assertTrue(result['readback_verified'])
        self.assertEqual(result['sha256'], hashlib.sha256(payload).hexdigest())
        self.assertEqual(http.calls[2][2]['json']['format'], 'base64')

    def test_upload_conflict_and_open_session(self):
        source = self.directory/'source'; source.write_bytes(b'data')
        self.http(Response(data={'type':'file'}))
        with self.assertRaises(FileExistsError):
            self.transfer.upload(str(source), 'project/file')
        http = self.http(Response(status=404), Response(data=[{'path':'project/file'}]))
        with self.assertRaises(RuntimeError):
            self.transfer.upload(str(source), 'project/file')
        self.assertFalse(any(c[0] == 'PUT' for c in http.calls))

    def test_symlink_and_outside_project_rejected(self):
        source = self.directory/'source'; source.write_bytes(b'data')
        link = self.directory/'link'; link.symlink_to(source)
        with self.assertRaises(ValueError):
            self.transfer.upload(str(link), 'project/file')
        with self.assertRaises(ValueError):
            self.transfer.upload(str(source), '.jupyter/file')
        with self.assertRaises(ValueError):
            self.transfer.download('project/file', 'relative/local')


if __name__ == '__main__':
    unittest.main()
