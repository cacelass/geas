"""Tests de geas.llm — clientes multi-proveedor (§43), sin red real.

Se levanta un servidor HTTP local (stdlib http.server, tests/fake_llm_server.py)
que imita las respuestas de OpenAI /chat/completions y Anthropic /messages, y
se apunta el cliente a él con `api_base`. Cero llamadas externas.
"""

from __future__ import annotations

import pytest

from geas.llm import AnthropicClient, LLMError, OpenAIClient, get_client

from tests.fake_llm_server import FakeLLMServer


@pytest.fixture
def fake_server():
    with FakeLLMServer() as server:
        yield server


def test_openai_client_completes(fake_server):
    client = OpenAIClient(api_key="test-key", api_base=fake_server.base_url)
    result = client.complete("sistema", "usuario")

    assert result.provider == "openai"
    assert result.text == "Respuesta OpenAI"
    assert result.model == "gpt-4o-mini"
    assert result.tokens_input == 12
    assert result.tokens_output == 7
    # gpt-4o-mini: 0.15 USD / M input, 0.60 USD / M output
    assert result.cost == pytest.approx(12 / 1_000_000 * 0.15 + 7 / 1_000_000 * 0.60)

    # la petición llegó con auth y el modelo pedido
    req = fake_server.requests[0]
    assert req["path"] == "/chat/completions"
    assert req["headers"].get("authorization") == "Bearer test-key"
    assert req["body"]["model"] == "gpt-4o-mini"
    assert req["body"]["messages"][0]["role"] == "system"


def test_openai_client_requires_api_key(fake_server):
    with pytest.raises(LLMError, match="GEAS_OPENAI_API_KEY"):
        OpenAIClient(api_key="", api_base=fake_server.base_url).complete("s", "u")


def test_anthropic_client_completes(fake_server):
    client = AnthropicClient(api_key="test-key", api_base=fake_server.base_url)
    result = client.complete("sistema", "usuario", model="claude-3-5-sonnet")

    assert result.provider == "anthropic"
    assert result.text == "Respuesta Anthropic"
    assert result.tokens_input == 9
    assert result.tokens_output == 4
    assert result.cost == pytest.approx(9 / 1_000_000 * 3.0 + 4 / 1_000_000 * 15.0)

    req = fake_server.requests[0]
    assert req["path"] == "/messages"
    assert req["headers"].get("x-api-key") == "test-key"
    assert req["headers"].get("anthropic-version") == "2023-06-01"
    assert req["body"]["model"] == "claude-3-5-sonnet"


def test_get_client_unknown_provider():
    with pytest.raises(LLMError, match=r"openai.*anthropic|anthropic.*openai"):
        get_client("gemini")


def test_unknown_model_has_zero_cost(fake_server):
    client = OpenAIClient(
        api_key="k", api_base=fake_server.base_url, model="modelo-futuro"
    )
    result = client.complete("s", "u")
    assert result.cost == 0.0