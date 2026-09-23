"""geas.llm — clientes de modelos de lenguaje multi-proveedor (§43).

Integra proveedores reales de LLM (OpenAI, Anthropic) usando solo stdlib
(urllib). Cero dependencias de runtime.

GEAS no guarda secretos: las claves se leen de variables de entorno:

    GEAS_OPENAI_API_KEY      → OpenAI  (chat completions)
    GEAS_ANTHROPIC_API_KEY   → Anthropic (messages API)

El coste estimado se calcula con la tabla interna (USD por millón de
tokens). Si el modelo no está en la tabla, cost queda en 0.0 — el dato
duro (tokens reales) vuelve en la respuesta de la API y es lo que se
registra en la Execution (§28 model traceability).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass


class LLMError(RuntimeError):
    """Error de red o de API con mensaje claro para el operador."""


@dataclass(frozen=True)
class LLMResult:
    """Respuesta normalizada de un proveedor."""

    text: str
    provider: str
    model: str
    tokens_input: int = 0
    tokens_output: int = 0
    cost: float = 0.0


# Precios aproximados USD por millón de tokens (input/output).
# Modelos sin precio conocido devuelven cost = 0.0.
PRICING: dict[str, tuple[float, float]] = {
    # OpenAI chat completions
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    "o3-mini": (1.10, 4.40),
    # Anthropic messages
    "claude-3-5-sonnet": (3.00, 15.00),
    "claude-3-5-haiku": (0.80, 4.00),
    "claude-3-7-sonnet": (3.00, 15.00),
    "claude-sonnet-4": (3.00, 15.00),
    "claude-haiku-4": (1.00, 5.00),
    "claude-opus-4": (15.00, 75.00),
}


def _price(model: str, tokens_input: int, tokens_output: int) -> float:
    pair = PRICING.get(model)
    if not pair:
        return 0.0
    return (tokens_input / 1_000_000 * pair[0]) + (
        tokens_output / 1_000_000 * pair[1]
    )


class _BaseClient:
    """Mínimo común: POST JSON con urllib y manejo de errores."""

    provider = ""

    def _post(self, url: str, headers: dict[str, str], payload: dict) -> dict:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise LLMError(
                f"{self.provider}: API {exc.code} en {url}\n{body[:400]}"
            ) from exc
        except urllib.error.URLError as exc:
            raise LLMError(
                f"{self.provider}: sin red hacia {url}: {exc.reason}"
            ) from exc


class OpenAIClient(_BaseClient):
    """Chat completions: POST /v1/chat/completions."""

    provider = "openai"
    api_base = "https://api.openai.com/v1"

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        model: str = "gpt-4o-mini",
    ):
        self.api_key = api_key or os.environ.get("GEAS_OPENAI_API_KEY", "")
        if api_base:
            self.api_base = api_base
        self.model = model

    def complete(
        self,
        system: str,
        user: str,
        model: str | None = None,
        max_tokens: int = 2048,
    ) -> LLMResult:
        if not self.api_key:
            raise LLMError(
                "openai: falta GEAS_OPENAI_API_KEY (el secret vive fuera de GEAS)"
            )
        model = model or self.model
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
        }
        data = self._post(
            f"{self.api_base}/chat/completions",
            {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            payload,
        )
        try:
            text = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})
            tokens_in = int(usage.get("prompt_tokens", 0))
            tokens_out = int(usage.get("completion_tokens", 0))
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(
                f"openai: respuesta inesperada de {model}: {str(data)[:400]}"
            ) from exc
        return LLMResult(
            text=text,
            provider=self.provider,
            model=model,
            tokens_input=tokens_in,
            tokens_output=tokens_out,
            cost=_price(model, tokens_in, tokens_out),
        )


class AnthropicClient(_BaseClient):
    """Messages API: POST /v1/messages."""

    provider = "anthropic"
    api_base = "https://api.anthropic.com/v1"

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        model: str = "claude-3-5-sonnet",
    ):
        self.api_key = api_key or os.environ.get("GEAS_ANTHROPIC_API_KEY", "")
        if api_base:
            self.api_base = api_base
        self.model = model

    def complete(
        self,
        system: str,
        user: str,
        model: str | None = None,
        max_tokens: int = 2048,
    ) -> LLMResult:
        if not self.api_key:
            raise LLMError(
                "anthropic: falta GEAS_ANTHROPIC_API_KEY (el secret vive fuera de GEAS)"
            )
        model = model or self.model
        payload = {
            "model": model,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "max_tokens": max_tokens,
        }
        data = self._post(
            f"{self.api_base}/messages",
            {
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            payload,
        )
        try:
            blocks = data.get("content", [])
            text = "".join(
                block.get("text", "") for block in blocks if block.get("type") == "text"
            )
            usage = data.get("usage", {})
            tokens_in = int(usage.get("input_tokens", 0))
            tokens_out = int(usage.get("output_tokens", 0))
        except (KeyError, TypeError) as exc:
            raise LLMError(
                f"anthropic: respuesta inesperada de {model}: {str(data)[:400]}"
            ) from exc
        return LLMResult(
            text=text,
            provider=self.provider,
            model=model,
            tokens_input=tokens_in,
            tokens_output=tokens_out,
            cost=_price(model, tokens_in, tokens_out),
        )


def get_client(
    provider: str,
    api_key: str | None = None,
    api_base: str | None = None,
    model: str = "",
) -> _BaseClient:
    """Devuelve el cliente del proveedor, o falla claro si no está soportado."""
    key = (provider or "").lower()
    if key == "openai":
        return OpenAIClient(api_key=api_key, api_base=api_base, model=model or "gpt-4o-mini")
    if key == "anthropic":
        return AnthropicClient(
            api_key=api_key, api_base=api_base, model=model or "claude-3-5-sonnet"
        )
    supported = ", ".join(sorted({"openai", "anthropic"}))
    raise LLMError(f"Proveedor LLM desconocido: {provider!r} (soportados: {supported})")