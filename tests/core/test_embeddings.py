from __future__ import annotations

from app.core import embeddings
from app.core.config import get_settings


async def test_get_embedding_without_api_key_returns_none(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "OPENAI_API_KEY", None)
    assert await embeddings.get_embedding("Chef de projet") is None


async def test_get_embedding_with_blank_text_returns_none(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "OPENAI_API_KEY", "sk-test")
    assert await embeddings.get_embedding("   ") is None


async def test_get_embedding_returns_vector(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(embeddings, "_embed_sync", lambda **kwargs: [0.1, 0.2, 0.3])

    result = await embeddings.get_embedding("Chef de projet SI")

    assert result == [0.1, 0.2, 0.3]


async def test_get_embedding_swallows_failure_and_returns_none(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "OPENAI_API_KEY", "sk-test")

    def _raise(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(embeddings, "_embed_sync", _raise)

    assert await embeddings.get_embedding("Chef de projet SI") is None
