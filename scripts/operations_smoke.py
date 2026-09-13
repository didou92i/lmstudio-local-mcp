"""Live v2 MCP checks; synthetic RAG, isolated MCP config, existing local models only."""
import asyncio
import json
import os
import sys
from datetime import timedelta

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from lmstudio_mcp.client import now
from lmstudio_mcp.config import ROOT, Settings
from lmstudio_mcp.storage import atomic_json


async def main():
    settings = Settings.from_env()
    work = settings.state_dir / 'operations-smoke'
    documents = work / 'documents'
    documents.mkdir(parents=True, exist_ok=True)
    document = documents / 'centre-test.txt'
    document.write_text('Fiche fictive de validation. Le centre Boréal ferme à 18 h 30 le mercredi. '
                        'Le code de la salle de réunion est ZEPHYR-842. La responsable est Mme Lenoir.')
    config = work / 'mcp.json'
    atomic_json(config, {'mcpServers': {}})
    fixture = work / 'fixture.py'
    fixture.write_text('from mcp.server.fastmcp import FastMCP\nm=FastMCP("validation")\n'
                       '@m.tool()\ndef ping(value: int) -> int:\n return value+1\nm.run()\n')
    report = {'tested_at': now(), 'checks': [], 'known_limitations': [], 'isolated_mcp_config': str(config)}
    target = settings.state_dir / 'operations-smoke.json'
    def record(name, evidence=None):
        report['checks'].append({'name': name, 'evidence': evidence})
        atomic_json(target, report)
        print('PASS ' + name, flush=True)
    params = StdioServerParameters(command=str(ROOT / '.venv/bin/python'), args=['-m', 'lmstudio_mcp.server'],
        cwd=str(ROOT), env={**os.environ, 'LM_MCP_CONFIG_PATH': str(config), 'LM_MCP_RAG_ROOTS': str(documents)})
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        async def call(name, args=None, allow_mismatch=False):
            result = await session.call_tool(name, args or {}, read_timeout_seconds=timedelta(seconds=950))
            data = result.structuredContent or json.loads(next(c.text for c in result.content if c.type == 'text'))
            mismatch = data.get('data', {}).get('configuration_mismatches') if isinstance(data.get('data'), dict) else None
            if not data['ok'] and not (allow_mismatch and mismatch):
                raise AssertionError(f"{name}: {data.get('error')}")
            return data['data']
        listing = await session.list_tools()
        assert len(listing.tools) == 28
        record('MCP exposes 28 tools')
        before_models = await call('lm_models')
        before = {i['id'] for m in before_models for i in m['loaded_instances']}
        owned = set()
        try:
            docs = await call('lm_docs', {'action': 'sync'})
            assert docs['available'] and docs['commit']
            search = await call('lm_docs', {'action': 'search', 'query': 'contextLength', 'limit': 2})
            assert search['results']
            excerpt = await call('lm_docs', {'action': 'read', 'path': search['results'][0]['path'], 'limit': 20})
            assert excerpt['text']
            record('Official docs sync, search and cited read', docs)
            connection = await call('lm_connections', {'action': 'test'})
            assert connection['compatible']
            record('Local server connection verified')
            profile_name = 'smoke-connection'
            await call('lm_connections', {'action': 'save', 'name': profile_name, 'url': settings.base_url})
            await call('lm_connections', {'action': 'select', 'name': profile_name})
            await call('lm_models')
            blocked = await session.call_tool('lm_server_control', {'action': 'stop'})
            assert blocked.isError
            await call('lm_connections', {'action': 'select', 'name': 'local'})
            await call('lm_connections', {'action': 'delete', 'name': profile_name})
            record('Server profile switch, API use and local CLI isolation')
            diagnostic = await call('lm_diagnose', {'include_hardware': True})
            assert diagnostic['hardware'].get('exit_code') == 0
            record('Diagnostics and hardware survey', {'server': diagnostic['server'], 'findings': diagnostic['findings']})
            await call('lm_runtime', {'action': 'check_updates'})
            record('Stable runtime update dry run')
            link = await call('lm_link', {'action': 'status'})
            record('LM Link status', link)
            mc = await call('lm_mcp_config', {'action': 'list'})
            assert not mc['servers']
            record('Isolated empty MCP configuration')
            conf = {'command': sys.executable, 'args': [str(fixture)]}
            preview = await call('lm_mcp_config', {'action': 'upsert', 'name': 'smoke-fixture', 'config': conf, 'allowed_tools': ['ping']})
            await call('lm_mcp_config', {'action': 'upsert', 'name': 'smoke-fixture', 'config': conf, 'allowed_tools': ['ping'],
                                       'apply': True, 'expected_digest': preview['expected_digest']})
            await call('lm_mcp_probe', {'name': 'smoke-fixture'})
            invoked = await call('lm_mcp_call', {'name': 'smoke-fixture', 'tool': 'ping', 'arguments': {'value': 41}})
            assert '42' in json.dumps(invoked['result'])
            record('MCP configuration, discovery and actual authorized tool call')
            sdk = await call('lm_model_config', {'action': 'schema'})
            assert sdk['config_schema']
            record('Installed official SDK config schema', {'api_token_supported': sdk['api_token_supported']})
            await call('lm_profiles', {'action': 'save', 'name': 'smoke-gemma', 'model': 'google/gemma-4-e4b', 'config': {'contextLength': 4096}})
            instance = 'lm-mcp-operations-gemma'
            assert instance not in before
            owned.add(instance)
            loaded = await call('lm_profiles', {'action': 'load', 'name': 'smoke-gemma', 'instance_id': instance}, True)
            if loaded['configuration_mismatches']:
                report['known_limitations'].append(loaded['configuration_mismatches'])
            inspected = await call('lm_model_config', {'action': 'inspect', 'instance_id': instance})
            assert inspected['interfaces_agree']
            record('Saved profile load and SDK/REST effective settings', inspected)
            embedding = await call('lm_load', {'model': 'text-embedding-nomic-embed-text-v1.5'})
            embedding_id = embedding['state']['instance_id']
            if embedding_id not in before:
                owned.add(embedding_id)
            indexed = await call('lm_rag_index', {'collection': 'smoke_rag', 'paths': [str(document)], 'embedding_model': embedding_id, 'replace': True})
            assert indexed['indexed_chunks'] == 1
            query = {'collection': 'smoke_rag', 'query': 'Quel est le code de la salle de réunion ?', 'embedding_model': embedding_id}
            found = await call('lm_rag_search', query)
            assert found['sources'] and 'ZEPHYR-842' in found['sources'][0]['text']
            answer = await call('lm_rag_ask', {**query, 'model': instance})
            assert answer['supported'] and 'ZEPHYR-842' in answer['answer'], answer
            record('Real local RAG: index, retrieval and answer with exact quote', answer)
            document.write_text('Fiche fictive modifiée depuis indexation.')
            stale = await call('lm_rag_search', query)
            assert not stale['sources'] and stale['excluded_stale_documents']
            record('Stale RAG sources excluded')
        finally:
            # Recover active profile first; unload exactly the instances this script created.
            await call('lm_connections', {'action': 'select', 'name': 'local'})
            await call('lm_connections', {'action': 'delete', 'name': 'smoke-connection'})
            models = await call('lm_models')
            loaded_ids = {i['id'] for m in models for i in m['loaded_instances']}
            for instance in owned & loaded_ids:
                await call('lm_unload', {'instance_id': instance})
            await call('lm_profiles', {'action': 'delete', 'name': 'smoke-gemma'})
            await call('lm_rag_manage', {'action': 'delete', 'collection': 'smoke_rag'})
            preview = await call('lm_mcp_config', {'action': 'remove', 'name': 'smoke-fixture'})
            await call('lm_mcp_config', {'action': 'remove', 'name': 'smoke-fixture', 'apply': True,
                                       'expected_digest': preview['expected_digest']})
            record('Test model instances, profiles, index and MCP fixture cleaned up')
        report['completed'] = True
        atomic_json(target, report)
    print(str(target))


if __name__ == '__main__':
    asyncio.run(main())
