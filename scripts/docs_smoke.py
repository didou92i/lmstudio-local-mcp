"""Verify the whole official reference snapshot and actual MCP discovery/retrieval.

Downloads documentation only. No model inference, app settings, accounts or external tool execution.
Local report contains counts and hashes, never personal configuration.
"""
import asyncio
import hashlib
import json
import os
import time
from datetime import timedelta

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from lmstudio_mcp.client import now
from lmstudio_mcp.config import ROOT, Settings
from lmstudio_mcp.knowledge import Knowledge
from lmstudio_mcp.storage import atomic_json


async def main():
    settings = Settings.from_env()
    knowledge = Knowledge(settings)
    target = settings.state_dir / 'docs-smoke.json'
    report = {'tested_at': now(), 'ok': False, 'checks': []}
    atomic_json(target, report)
    source = await knowledge.sync()
    snapshot = knowledge.index()
    report['source'] = source
    for path, record in snapshot['files'].items():
        if not record['readable']:
            continue
        assert hashlib.sha256((knowledge.root/path).read_bytes()).hexdigest() == record['sha256']
        start, offset, parts = 1, 0, []
        while True:
            page = await knowledge.run('read', path=path, scope='all', start_line=start, offset=offset,
                                       limit=200, prepared=True)
            parts.append(page['text'])
            if page['next_offset'] is not None:
                offset = page['next_offset']
            elif page['next_start_line'] is not None:
                parts.append('\n')
                start, offset = page['next_start_line'], 0
            else:
                break
        assert ''.join(parts) == '\n'.join(record['content'].splitlines()), path
    report['checks'].append('All readable Git files reconstructed without missing text; SHA-256 matches sources')
    coverage = await knowledge.run('coverage', scope='all', limit=200)
    report['coverage_counts'] = coverage['coverage_counts']
    if source['commit'] == knowledge.baseline['commit']:
        assert set(snapshot['files']) == set(knowledge.baseline['files'])
        assert 'needs_review' not in report['coverage_counts']
    report['checks'].append('Complete per-file coverage baseline or explicitly flagged upstream drift')
    cases = {
        'réglages mémoire chargement': '1_python/5_manage-models/loading.mdx',
        'connecteurs MCP authentification': '1_developer/0_core/authentication.mdx',
        'diagnostic serveur': '1_developer/0_core/0_server/settings.md',
        'RAG documents': '0_app/1_basics/rag.md',
        'tokenisation': '1_python/4_tokenization/index.md',
        'préréglages': '0_app/3_presets/index.md',
        'Bionic': '0_bionic/0_root/index.mdx',
    }
    report['searches'] = []
    for query, expected in cases.items():
        before = time.perf_counter()
        result = await knowledge.run('search', query=query, limit=5)
        assert expected in [r['path'] for r in result['results']], query
        report['searches'].append({'query': query, 'seconds': round(time.perf_counter()-before, 3)})
    params = StdioServerParameters(command=str(ROOT/'.venv/bin/python'), args=['-m', 'lmstudio_mcp.server'],
                                  cwd=str(ROOT), env=dict(os.environ))
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        init = await session.initialize()
        assert "action='brief'" in init.instructions
        tools = await session.list_tools()
        docs_tool = next(t for t in tools.tools if t.name == 'lm_docs')
        assert 'coverage' in docs_tool.inputSchema['properties']['action']['enum']
        async def call(arguments):
            result = await session.call_tool('lm_docs', arguments, read_timeout_seconds=timedelta(seconds=60))
            assert not result.isError, result
            return result.structuredContent['data']
        status = await call({'action': 'status'})
        assert status['counts'] == source['counts']
        brief = await call({'action': 'brief', 'query': 'réglages mémoire chargement', 'limit': 5})
        assert brief['references'] and brief['procedures']
        for topic in ('reglages', 'mcp', 'rag', 'diagnostic', 'authentification', 'bionic'):
            guide = await call({'action': 'guide', 'query': topic})
            assert guide['procedures'][topic]['sources']
            assert not guide['procedures'][topic]['missing_sources']
        resources = await session.list_resources()
        assert any(str(r.uri) == 'lmstudio://docs/overview' for r in resources.resources)
        resource = await session.read_resource(brief['references'][0]['resource_uri'])
        assert json.loads(resource.contents[0].text)['data']['text']
        prompt = await session.get_prompt('lmstudio_workflow', {'task': 'connecteurs MCP authentification'})
        assert 'references' in prompt.messages[0].content.text
    report['checks'].append('Real stdio: initialize instructions, tools, procedures, resources, page template and prompt')
    report['ok'] = True
    atomic_json(target, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
