import pytest

from lmstudio_mcp.client import ConnectorError
from lmstudio_mcp.knowledge import Knowledge


@pytest.fixture(autouse=True)
def no_documentation_network(monkeypatch):
    """Unit tests must never clone GitHub; sync tests explicitly supply a local fetch."""
    async def offline(self):
        raise ConnectorError('Documentation network disabled in isolated tests')
    monkeypatch.setattr(Knowledge, '_fetch', offline)
