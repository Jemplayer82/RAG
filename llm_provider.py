"""
LLM Provider Abstraction Layer for RAG v2.0.

Routes LLM calls to the configured provider (OpenAI, Anthropic, Ollama, or generic HTTP).
Admin sets the active provider via /admin/llm-settings dashboard.
Falls back to environment variables if no DB config exists.
"""

import os
import logging
from typing import Dict, Optional

import httpx

logger = logging.getLogger(__name__)


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

async def query_llm_async(prompt: str, config: Optional[Dict] = None) -> str:
    """
    Route to the appropriate LLM provider based on config dict.
    If config is None, falls back to environment variables.
    """
    if config is None:
        config = _config_from_env()

    provider = config.get("provider", "ollama")
    logger.info(f"[LLM] Using provider={provider} model={config.get('model')}")

    if provider == "openai":
        return await _call_openai(prompt, config)
    elif provider == "anthropic":
        return await _call_anthropic(prompt, config)
    elif provider == "ollama":
        return await _call_ollama(prompt, config)
    elif provider == "generic":
        return await _call_generic(prompt, config)
    else:
        raise ValueError(f"Unknown LLM provider: {provider!r}")


# ============================================================================
# PROVIDER IMPLEMENTATIONS
# ============================================================================

async def _call_openai(prompt: str, config: Dict) -> str:
    from openai import AsyncOpenAI
    from models import decrypt_api_key

    raw_key = config.get("api_key", "")
    api_key = decrypt_api_key(raw_key) if raw_key else os.getenv("OPENAI_API_KEY", "")

    client = AsyncOpenAI(api_key=api_key)
    response = await client.chat.completions.create(
        model=config.get("model", "gpt-4"),
        messages=[{"role": "user", "content": prompt}],
        temperature=config.get("temperature", 0.3),
        top_p=config.get("top_p", 0.9),
        max_tokens=config.get("max_tokens", 2048),
    )
    return response.choices[0].message.content or ""


async def _call_anthropic(prompt: str, config: Dict) -> str:
    import anthropic as anthropic_sdk
    from models import decrypt_api_key

    raw_key = config.get("api_key", "")
    api_key = decrypt_api_key(raw_key) if raw_key else os.getenv("ANTHROPIC_API_KEY", "")

    client = anthropic_sdk.AsyncAnthropic(api_key=api_key)
    response = await client.messages.create(
        model=config.get("model", "claude-3-5-sonnet-20241022"),
        max_tokens=config.get("max_tokens", 2048),
        messages=[{"role": "user", "content": prompt}],
        temperature=config.get("temperature", 0.3),
    )
    return response.content[0].text


async def _call_ollama(prompt: str, config: Dict) -> str:
    from models import decrypt_api_key

    base_url = (config.get("base_url") or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")).rstrip("/")
    model = config.get("model") or os.getenv("LLM_MODEL", "mistral-small3.1")
    raw_key = config.get("api_key", "")
    api_key = decrypt_api_key(raw_key) if raw_key else ""
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    async with httpx.AsyncClient(timeout=120) as client:
        try:
            response = await client.post(
                f"{base_url}/api/generate",
                headers=headers,
                json={
                    "model": model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": config.get("temperature", 0.3),
                        "top_p": config.get("top_p", 0.9),
                    },
                },
            )
        except httpx.ConnectError as e:
            raise RuntimeError(f"Cannot reach Ollama at {base_url}: {e}") from e
        if response.status_code >= 400:
            err_body = ""
            try:
                err_body = response.json().get("error", "") or response.text
            except Exception:
                err_body = response.text
            if "not found" in err_body.lower() or "no such" in err_body.lower() or response.status_code == 404:
                raise RuntimeError(
                    f"Ollama model '{model}' not found at {base_url}. "
                    f"Pull it with: docker exec rag-ollama-1 ollama pull {model}"
                )
            raise RuntimeError(f"Ollama error ({response.status_code}) at {base_url}: {err_body}")
        return response.json().get("response", "")


async def _call_generic(prompt: str, config: Dict) -> str:
    """OpenAI-compatible endpoints: Groq, Together, Fireworks, etc."""
    from models import decrypt_api_key

    raw_key = config.get("api_key", "")
    api_key = decrypt_api_key(raw_key) if raw_key else ""
    base_url = config.get("base_url", "")

    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": config.get("model", ""),
                "messages": [{"role": "user", "content": prompt}],
                "temperature": config.get("temperature", 0.3),
                "max_tokens": config.get("max_tokens", 2048),
            },
        )
        response.raise_for_status()
        data = response.json()
        return data.get("choices", [{}])[0].get("message", {}).get("content", "")


# ============================================================================
# ENVIRONMENT VARIABLE FALLBACK
# ============================================================================

def _config_from_env() -> Dict:
    return {
        "provider": os.getenv("LLM_PROVIDER", "ollama"),
        "model": os.getenv("LLM_MODEL", "mistral-small3.1"),
        "base_url": os.getenv("LLM_BASE_URL", os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")),
        "api_key": "",
        "temperature": float(os.getenv("LLM_TEMPERATURE", "0.3")),
        "top_p": float(os.getenv("LLM_TOP_P", "0.9")),
        "max_tokens": int(os.getenv("LLM_MAX_TOKENS", "2048")),
    }
