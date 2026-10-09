"""Real MCP upload/link/download smoke test. No kernel and no compute."""
import asyncio
import datetime
import hashlib
import json
import os
import sys
from pathlib import Path
import tempfile
import uuid

import requests
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = Path(__file__).parent
BASE = os.environ['JUPYTER_URL'].rstrip('/')
TOKEN = Path(os.environ.get('DATASPHERE_TOKEN_FILE', HERE / 'token')).expanduser().read_text().strip()


async def main():
    path = 'project/agent_transfer_' + uuid.uuid4().hex[:10] + ' проба +%.bin'
    payload = bytes(range(256)) * 16 + b'\x00MCP_TRANSFER_OK\xff'
    digest = hashlib.sha256(payload).hexdigest()
    evidence = {'timestamp':datetime.datetime.now(datetime.timezone.utc).isoformat(),
                'remote_path':path, 'size_bytes':len(payload), 'sha256':digest}
    try:
        with tempfile.TemporaryDirectory(prefix='datasphere-transfer-test-') as directory:
            source = Path(directory)/'source.bin'; source.write_bytes(payload)
            target = Path(directory)/'download.bin'
            params = StdioServerParameters(command=sys.executable, args=[str(HERE/'start_mcp.py')])
            async with stdio_client(params) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    tools = [t.name for t in (await session.list_tools()).tools]
                    assert all(t in tools for t in ['upload_file','download_file','get_file_download_link']), tools
                    evidence['tool_count'] = len(tools)
                    for name, args in [
                        ('upload_file', {'local_path':str(source), 'remote_path':path}),
                        ('get_file_download_link', {'remote_path':path}),
                        ('download_file', {'remote_path':path, 'local_path':str(target)}),
                    ]:
                        result = await asyncio.wait_for(session.call_tool(name, args), timeout=90)
                        dump = result.model_dump(mode='json')
                        assert not result.is_error, dump
                        assert TOKEN not in json.dumps(dump), 'Token leaked in tool result'
                        evidence[name] = dump
                        print(name, json.dumps(dump,ensure_ascii=False)[:1600], flush=True)
                    assert target.read_bytes() == payload
                    conflict = await session.call_tool('upload_file', {'local_path':str(source),'remote_path':path})
                    assert conflict.is_error, 'Default upload unexpectedly overwrote existing file'
                    evidence['overwrite_refused'] = True
                    evidence['verified'] = True
    finally:
        with requests.Session() as http:
            http.headers['Authorization'] = 'token ' + TOKEN
            from urllib.parse import quote
            response = http.delete(BASE+'/api/contents/'+quote(path,safe='/'),timeout=25,allow_redirects=False)
            evidence['remote_cleanup_status'] = response.status_code
            (HERE/'file_transfer_verification.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2))
            assert response.status_code in {204,404}, f'Cleanup failed: HTTP {response.status_code}'
    print('MCP FILE TRANSFER VERIFIED',flush=True)


if __name__ == '__main__':
    asyncio.run(main())
