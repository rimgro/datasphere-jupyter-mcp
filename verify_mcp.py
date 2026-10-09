"""End-to-end MCP smoke test; uses REST only to create/delete an isolated test kernel."""
import asyncio
import datetime
import json
import os
from pathlib import Path
import sys
import uuid
import requests
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = Path(__file__).parent
BASE = os.environ['JUPYTER_URL'].rstrip('/')
TOKEN = Path(os.environ.get('DATASPHERE_TOKEN_FILE', HERE / 'token')).expanduser().read_text().strip()
http = requests.Session()
http.headers['Authorization'] = 'token ' + TOKEN

async def main():
    evidence = {'timestamp': datetime.datetime.now(datetime.timezone.utc).isoformat()}
    kernel_id = None
    notebook_path = name = None
    try:
        params = StdioServerParameters(command=sys.executable, args=[str(HERE / 'start_mcp.py')])
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                init = await session.initialize()
                evidence['server'] = init.server_info.model_dump(mode='json')
                tools = await session.list_tools()
                evidence['tools'] = [t.name for t in tools.tools]
                print('MCP INITIALIZED; tools:', len(tools.tools), flush=True)
                files = await session.call_tool('list_files', {'path': '', 'max_depth': 0, 'limit': 30})
                evidence['list_files'] = files.model_dump(mode='json')
                assert not files.is_error, evidence['list_files']
                text = json.dumps(evidence['list_files'], ensure_ascii=False)
                assert 'project' in text, text
                print('MCP LIST_FILES VERIFIED:', text[:1800], flush=True)
                r = http.post(BASE + '/api/kernels', json={'name': 'python3', 'path': 'project'}, timeout=25)
                r.raise_for_status()
                kernel_id = r.json()['id']
                name = 'project/agent_mcp_test_' + uuid.uuid4().hex[:10] + '.txt'
                notebook_path = name.replace('.txt', '.ipynb')
                notebook = await session.call_tool('use_notebook', {'notebook_name': 'mcp-smoke-test', 'notebook_path': notebook_path, 'mode': 'create', 'kernel_id': kernel_id})
                evidence['use_notebook'] = notebook.model_dump(mode='json')
                evidence['test_notebook'] = notebook_path
                print('MCP USE_NOTEBOOK:', json.dumps(evidence['use_notebook'], ensure_ascii=False)[:2500], flush=True)
                assert not notebook.is_error, evidence['use_notebook']
                code = ('from pathlib import Path\n'
                        f'(Path.home() / {name!r}).write_text("Created through MCP execute_code.\\n2 + 2 = 4\\n")\n'
                        f'print("FILE:", {name!r})\n'
                        f'print("READBACK:", (Path.home() / {name!r}).read_text())\n'
                        'print("MCP_EXECUTION_OK", 2 + 2)\n2 + 2')
                result = await session.call_tool('execute_code', {'code': code, 'timeout': 90})
                evidence['execute_code'] = result.model_dump(mode='json')
                print('MCP EXECUTE_CODE:', json.dumps(evidence['execute_code'], ensure_ascii=False)[:6000], flush=True)
                assert not result.is_error, evidence['execute_code']
                text = json.dumps(evidence['execute_code'], ensure_ascii=False)
                assert 'MCP_EXECUTION_OK 4' in text, text
                r = http.get(BASE + '/api/contents/' + name, timeout=25)
                r.raise_for_status()
                assert r.json()['content'] == 'Created through MCP execute_code.\n2 + 2 = 4\n'
                evidence['test_file'] = name
                evidence['file_readback_verified'] = True
                state = await session.call_tool('execute_code', {'code': 'mcp_probe_value = 40\nprint("MCP_STATE_SET", mcp_probe_value + 2)', 'timeout': 30})
                assert not state.is_error and 'MCP_STATE_SET 42' in json.dumps(state.model_dump(mode='json'))
                inserted = await session.call_tool('insert_cell', {'cell_index': 1, 'cell_type': 'code', 'cell_source': 'print("MCP_CELL_OK", mcp_probe_value + 3)'})
                assert not inserted.is_error, inserted
                cell = await session.call_tool('execute_cell', {'cell_index': 1, 'timeout': 30})
                evidence['execute_cell'] = cell.model_dump(mode='json')
                assert not cell.is_error and 'MCP_CELL_OK 43' in json.dumps(evidence['execute_cell']), evidence['execute_cell']
                saved = http.get(BASE + '/api/contents/' + notebook_path, timeout=25)
                saved.raise_for_status()
                saved_cell = saved.json()['content']['cells'][1]
                assert 'MCP_CELL_OK 43' in json.dumps(saved_cell['outputs']), saved_cell
                assert isinstance(saved_cell['execution_count'], int), saved_cell
                evidence['state_and_cell_save_verified'] = True
                evidence['verified'] = True
    finally:
        if kernel_id:
            r = http.delete(BASE + '/api/kernels/' + kernel_id, timeout=25)
            evidence['test_kernel_cleanup_status'] = r.status_code
            print('TEST KERNEL CLEANUP:', r.status_code, flush=True)
            r.raise_for_status()
        for path in (notebook_path, name):
            if path:
                response = http.delete(BASE + '/api/contents/' + path, timeout=25)
                evidence.setdefault('artifact_cleanup', {})[path] = response.status_code
                if response.status_code != 404:
                    response.raise_for_status()
        (HERE / 'verification.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
    print('MCP END-TO-END VERIFIED; evidence:', HERE / 'verification.json', flush=True)

if __name__ == '__main__':
    asyncio.run(main())
