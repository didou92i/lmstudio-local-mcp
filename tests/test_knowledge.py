import asyncio
import json
import os
import subprocess

import httpx
import pytest

from lmstudio_mcp.client import Client, ConnectorError
from lmstudio_mcp.config import Settings
from lmstudio_mcp.doc_guides import GUIDES
from lmstudio_mcp.knowledge import BASELINE, REPOSITORY, Knowledge
from lmstudio_mcp.server import create_server
from lmstudio_mcp.service import Service


def git(root, *args):
    env = {**os.environ, 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1',
           'GIT_AUTHOR_NAME': 'Test', 'GIT_COMMITTER_NAME': 'Test',
           'GIT_AUTHOR_EMAIL': 'test@example.invalid', 'GIT_COMMITTER_EMAIL': 'test@example.invalid'}
    return subprocess.check_output(['git', '-c', 'commit.gpgsign=false', '-C', str(root), *args], env=env, text=True).strip()


def commit(root):
    git(root, 'add', '.')
    git(root, 'commit', '-qm', 'test: update documentation')
    return git(root, 'rev-parse', 'HEAD')


@pytest.fixture
async def docs(tmp_path):
    settings = Settings(state_dir=tmp_path / 'state', docs_auto_sync=False)
    knowledge = Knowledge(settings)
    knowledge.root.mkdir(parents=True)
    git(knowledge.root, 'init', '-q', '-b', 'main')
    git(knowledge.root, 'remote', 'add', 'origin', REPOSITORY)
    files = {
        '1_developer/2_rest/load.md': '---\ntitle: Load a Model\n---\n# Loading\ncontextLength sets the load context.\nGPU memory parameters.\n',
        '1_developer/0_core/0_server/settings.md': '# Server Settings\nAuthentication token required.\n<img src="/assets/server.png" />\n',
        '_draft.mdx': '# Draft\ncontextLength unstable field.\n',
        'meta.json': '{"sections": ["developer"]}\n',
        '_assets/example.py': 'raise RuntimeError("NEVER EXECUTE THIS FILE")\n',
    }
    for name, text in files.items():
        path = knowledge.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    commit(knowledge.root)
    await knowledge.ensure()
    knowledge.baseline = {'files': {p: {'sha256': r['sha256'], 'status': 'reference_only', 'tools': [],
                                      'tests': [], 'limitation': 'fixture'} for p, r in knowledge.index()['files'].items()}}
    return knowledge


async def test_complete_inventory_includes_drafts_and_support_without_execution(docs):
    status = await docs.run('status')
    assert status['counts'] == {'repository_files': 5, 'readable_files': 5, 'published_documents': 2,
                               'unpublished_documents': 1, 'support_files': 2}
    assert status['freshness'] == 'stale'
    first = await docs.run('catalog', scope='all', limit=2)
    second = await docs.run('catalog', scope='all', limit=2, offset=first['next_offset'])
    third = await docs.run('catalog', scope='all', limit=2, offset=second['next_offset'])
    assert len({f['path'] for p in [first, second, third] for f in p['files']}) == 5
    assert third['next_offset'] is None
    support = await docs.run('read', scope='all', path='_assets/example.py')
    assert 'NEVER EXECUTE' in support['text']
    with pytest.raises(ConnectorError, match='published'):
        await docs.run('read', path='_draft.mdx')
    draft = await docs.run('read', scope='all', path='_draft.mdx')
    assert not draft['published']


async def test_french_search_citations_and_complete_pagination(docs):
    result = await docs.run('search', query='réglages mémoire chargement')
    top = result['results'][0]
    assert top['path'] == '1_developer/2_rest/load.md'
    assert docs.index()['commit'] in top['url'] and '#L' in top['url']
    assert all(r['published'] for r in result['results'])
    first = await docs.run('read', path=top['path'], limit=3)
    second = await docs.run('read', path=top['path'], limit=3, start_line=first['next_start_line'])
    assert first['text']+'\n'+second['text']+'\n' == (docs.root/top['path']).read_text()
    assert second['next_start_line'] is None
    assert (await docs.run('search', query='contextLength', scope='all'))['total_results'] == 2
    assert not (await docs.run('search', query='termthatdoesnotexist'))['results']


async def test_external_media_links_are_preserved_without_fetch(docs):
    page = await docs.run('read', path='1_developer/0_core/0_server/settings.md')
    assert page['links'] == [{'line': 3, 'url': 'https://lmstudio.ai/assets/server.png', 'fetched': False}]


@pytest.mark.parametrize('path', ['../private.txt', '/etc/passwd', '.git/config', 'untracked.md'])
async def test_read_rejects_paths_outside_index(docs, path):
    (docs.root/'untracked.md').write_text('PRIVATE DATA')
    with pytest.raises(ConnectorError, match='published'):
        await docs.run('read', path=path, scope='all')


async def test_symlink_is_catalogued_but_never_followed(docs, tmp_path):
    secret = tmp_path/'private.txt'
    secret.write_text('PRIVATE DATA')
    (docs.root/'linked.md').symlink_to(secret)
    commit(docs.root)
    await docs._build_index()
    result = await docs.run('read', path='linked.md')
    assert not result['readable'] and 'text' not in result
    assert 'PRIVATE DATA' not in docs.index_path.read_text()


async def test_tampered_checkout_never_replaces_committed_index(docs):
    before = docs.index_path.read_bytes()
    (docs.root/'1_developer/2_rest/load.md').write_text('PRIVATE UNCOMMITTED CONTENT')
    with pytest.raises(ConnectorError, match='modified'):
        await docs._build_index()
    assert docs.index_path.read_bytes() == before
    assert 'PRIVATE' not in (await docs.run('read', path='1_developer/2_rest/load.md'))['text']


async def test_auto_sync_ttl_retry_and_offline_fallback_across_instances(docs, monkeypatch):
    calls = []
    clock = [1000.0]
    monkeypatch.setattr('lmstudio_mcp.knowledge.time.time', lambda: clock[0])
    docs.settings.docs_auto_sync = True
    docs.settings.docs_ttl = 100
    async def fetch():
        calls.append(clock[0])
    monkeypatch.setattr(docs, '_fetch', fetch)
    assert (await docs.ensure())['freshness'] == 'fresh'
    await docs.ensure()
    assert len(calls) == 1
    clock[0] += 101
    async def offline():
        calls.append(clock[0])
        raise ConnectorError('offline')
    monkeypatch.setattr(docs, '_fetch', offline)
    result = await docs.ensure()
    assert result['freshness'] == 'stale' and result['available'] and result['sync_status'] == 'failed'
    other = Knowledge(docs.settings)
    monkeypatch.setattr(other, '_fetch', offline)
    await other.ensure()
    assert len(calls) == 2  # Persisted failure throttle works across stateless HTTP clients.
    clock[0] += 61
    await other.ensure()
    assert len(calls) == 3
    with pytest.raises(ConnectorError, match='verified'):
        await docs.sync()
    assert len(calls) == 4  # Explicit sync bypasses failure throttle.


async def test_changed_new_removed_pages_invalidate_mapping_and_delta_persists(docs, monkeypatch):
    async def fetch():
        pass
    monkeypatch.setattr(docs, '_fetch', fetch)
    path = '1_developer/2_rest/load.md'
    (docs.root/path).write_text('# New loading\nNew parameter.\n')
    (docs.root/'new.md').write_text('# New API\n')
    (docs.root/'_draft.mdx').unlink()
    commit(docs.root)
    await docs.sync()
    coverage = await docs.run('coverage', scope='all')
    changed = {r['path']: r['coverage'] for r in coverage['files']}
    assert changed[path]['status'] == changed['new.md']['status'] == 'needs_review'
    assert not changed[path]['tools'] and coverage['removed_since_baseline'] == ['_draft.mdx']
    changes = await docs.run('changes')
    assert {r['change'] for r in changes['changes']} == {'added', 'modified', 'removed'}
    await docs.sync()
    assert (await docs.run('changes'))['changes'] == changes['changes']
    assert (await docs.run('guide', query='reglages'))['procedures']['reglages']['requires_source_review']


async def test_concurrent_sync_and_read_see_one_complete_snapshot(docs, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    async def fetch():
        entered.set()
        await release.wait()
    monkeypatch.setattr(docs, '_fetch', fetch)
    sync = asyncio.create_task(docs.sync())
    await entered.wait()
    read = asyncio.create_task(Knowledge(docs.settings).run('catalog', scope='all'))
    await asyncio.sleep(0.01)
    assert not read.done()
    release.set()
    await sync
    assert (await read)['total_files'] == 5


async def test_long_line_read_cursor_loses_no_content(docs):
    text = 'x' * 51000
    (docs.root/'long.md').write_text(text)
    commit(docs.root)
    await docs._build_index()
    offset, chunks = 0, []
    while True:
        page = await docs.run('read', path='long.md', offset=offset)
        chunks.append(page['text'])
        offset = page['next_offset']
        if offset is None:
            break
    assert ''.join(chunks) == text


async def test_corrupt_cache_rebuilds_offline_and_never_claims_freshness(docs):
    docs.index_path.write_text('{broken')
    docs.meta.write_text('[]')
    other = Knowledge(docs.settings)
    result = await other.run('status')
    assert result['counts']['repository_files'] == 5
    assert result['freshness'] == 'stale'
    assert result['warning']


async def test_missing_cache_does_not_report_deleted_upstream_files(tmp_path):
    knowledge = Knowledge(Settings(state_dir=tmp_path, docs_auto_sync=False))
    status = await knowledge.run('status')
    assert status['freshness'] == 'unavailable'
    assert not status['coverage']['assessed'] and status['coverage']['removed_files'] == 0


async def test_each_operation_checks_docs_and_zero_ttl_forces_refresh(docs, monkeypatch):
    docs.settings.docs_auto_sync = True
    docs.settings.docs_ttl = 0
    calls = []
    async def fetch(self):
        calls.append(True)
    monkeypatch.setattr(Knowledge, '_fetch', fetch)
    client = Client(docs.settings)
    async def preflight(**kwargs):
        client.current_health = {'api_v1': 'available'}
        return []
    monkeypatch.setattr(client, 'preflight', preflight)
    try:
        service = Service(client)
        for operation in ['models', 'docs']:
            result = await service.run(operation, **({'action': 'status'} if operation == 'docs' else {}))
            assert result['ok']
            assert result['diagnostics']['documentation']['freshness'] == 'fresh'
        assert len(calls) == 2  # No duplicate fetch inside the documentation tool.
    finally:
        await client.close()


def test_coverage_baseline_references_real_tools_tests_and_all_guide_sources():
    baseline = json.loads(BASELINE.read_text())
    server = (BASELINE.parents[1]/'server.py').read_text()
    project = BASELINE.parents[3]
    assert len(baseline['commit']) == 40
    for record in baseline['files'].values():
        assert len(record['sha256']) == 64
        for tool in record['tools']:
            assert f'async def {tool}(' in server
        for test in record['tests']:
            file, _, function = test.partition('::')
            assert (project/file).is_file(), file
            if function:
                assert f'def {function}(' in (project/file).read_text()
    for guide in GUIDES.values():
        assert set(guide['sources']) <= set(baseline['files'])


async def test_mcp_instructions_resources_prompt_and_docs_when_lm_is_down(docs, monkeypatch):
    class OfflineClient(Client):
        def __init__(self, settings):
            super().__init__(settings, transport=httpx.MockTransport(lambda r: httpx.Response(503)),
                             remote_transport=httpx.MockTransport(lambda r: httpx.Response(503)))
        async def cli(self, *args, **kwargs):
            raise ConnectorError('LM Studio unavailable')
    monkeypatch.setattr('lmstudio_mcp.server.Client', OfflineClient)
    server = create_server(docs.settings)
    app = server.streamable_http_app()
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:8765',
        headers={'Accept': 'application/json, text/event-stream'},
    ) as client:
        async def rpc(method, params=None):
            response = await client.post('/mcp', json={'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {}})
            assert response.status_code == 200, response.text
            assert 'error' not in response.json(), response.text
            return response.json()['result']
        init = await rpc('initialize', {'protocolVersion': '2025-11-25', 'capabilities': {},
                                       'clientInfo': {'name': 'test', 'version': '1'}})
        assert "action='brief'" in init['instructions']
        resources = await rpc('resources/list')
        assert any(r['uri'] == 'lmstudio://docs/overview' for r in resources['resources'])
        overview = await rpc('resources/read', {'uri': 'lmstudio://docs/overview'})
        assert json.loads(overview['contents'][0]['text'])['data']['counts']['repository_files'] == 5
        templates = await rpc('resources/templates/list')
        assert templates['resourceTemplates'][0]['uriTemplate'] == 'lmstudio://docs/page/{path}'
        uri = (await docs.run('search', query='contextLength'))['results'][0]['resource_uri']
        page = await rpc('resources/read', {'uri': uri})
        assert 'contextLength' in json.loads(page['contents'][0]['text'])['data']['text']
        prompt = await rpc('prompts/get', {'name': 'lmstudio_workflow', 'arguments': {'task': 'réglages mémoire'}})
        assert 'contextLength' in prompt['messages'][0]['content']['text']
        tool = await rpc('tools/call', {'name': 'lm_docs', 'arguments': {'action': 'brief', 'query': 'mémoire'}})
        assert not tool['isError']
        assert tool['structuredContent']['diagnostics']['api_v1'] != 'available'
