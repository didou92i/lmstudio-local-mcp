"""Start an ephemeral loopback MCP HTTP server; optionally test LM Studio HTTP restart."""
import argparse
import asyncio
import os
import socket
from datetime import timedelta

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from lmstudio_mcp.client import now
from lmstudio_mcp.config import ROOT
from lmstudio_mcp.storage import atomic_json


async def main(restart=False):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    proc = await asyncio.create_subprocess_exec(str(ROOT / '.venv/bin/python'), '-m', 'lmstudio_mcp.server',
        '--http', '--port', str(port), cwd=ROOT, env=dict(os.environ),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    report = {'tested_at': now(), 'transport': 'streamable-http', 'checks': []}
    url = f'http://127.0.0.1:{port}/mcp'
    try:
        async with httpx.AsyncClient(timeout=1, trust_env=False) as http:
            for _ in range(80):
                if proc.returncode is not None:
                    raise AssertionError('MCP HTTP server exited during startup')
                try:
                    await http.get(url)
                    break
                except httpx.HTTPError:
                    await asyncio.sleep(0.25)
            else:
                raise AssertionError('MCP HTTP startup timed out')
        async with streamable_http_client(url) as (read, write, _), ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert len(tools.tools) == 28
            report['checks'].append('HTTP initialize and discovery: 28 tools')
            async def call(name, args=None):
                result = await session.call_tool(name, args or {}, read_timeout_seconds=timedelta(seconds=120))
                assert not result.isError, result
                return result.structuredContent
            for _ in range(3):
                await call('lm_models')
            report['checks'].append('Repeated independent HTTP tool requests')
            if restart:
                status = await call('lm_server_control', {'action': 'status'})
                assert 'true' in status['data']['stdout'].lower(), 'Start the LM Studio server before the restart test'
                try:
                    result = await call('lm_server_control', {'action': 'restart'})
                    assert result['data']['verified'] and result['data']['status']['running']
                finally:
                    await call('lm_server_control', {'action': 'start'})
                await call('lm_models')
                report['checks'].append('Real LM Studio server stop/start, verified running and API recovered')
            report['completed'] = True
            atomic_json(ROOT / '.state/server-smoke.json', report)
            print(report)
    finally:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), 10)
        except TimeoutError:
            proc.kill()
            await proc.wait()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--restart', action='store_true')
    asyncio.run(main(parser.parse_args().restart))
