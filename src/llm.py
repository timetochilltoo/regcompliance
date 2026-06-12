"""
LLM chat client — same swap-able provider pattern as embeddings.

Supported providers for chat (LLM):
  - minimax   — uses the openai SDK with base_url=https://api.minimaxi.com/v1
  - openai    — standard OpenAI API
  - deepseek  — uses openai SDK with base_url=https://api.deepseek.com
  - anthropic — uses the anthropic SDK (different payload format)

Note: Unlike embeddings, the chat endpoint IS OpenAI-compatible across
providers (including MiniMax M-series). So most providers reuse the openai SDK.

Every call returns a dict:
    {
        "text": str,            # the assistant's reply
        "model": str,           # model name actually used
        "provider": str,        # provider name
        "input_tokens": int,    # if reported by the API
        "output_tokens": int,
        "latency_ms": int,
    }
"""
from __future__ import annotations

import logging
import time
from typing import List, Optional

log = logging.getLogger(__name__)


def _get_openai_compatible_client(api_key: str, base_url: Optional[str] = None):
    from openai import OpenAI
    kwargs = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    return OpenAI(**kwargs)


def chat_minimax(messages: List[dict], model: str, temperature: float = 0.2) -> dict:
    from .config import MINIMAX_API_KEY, MINIMAX_BASE_URL
    if not MINIMAX_API_KEY:
        raise RuntimeError("MINIMAX_API_KEY is not set. Add it to your .env file.")
    client = _get_openai_compatible_client(MINIMAX_API_KEY, MINIMAX_BASE_URL)
    t0 = time.time()
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
    )
    latency = int((time.time() - t0) * 1000)
    return {
        "text": resp.choices[0].message.content or "",
        "model": resp.model or model,
        "provider": "minimax",
        "input_tokens": getattr(resp.usage, "prompt_tokens", None),
        "output_tokens": getattr(resp.usage, "completion_tokens", None),
        "latency_ms": latency,
    }


def chat_openai(messages: List[dict], model: str, temperature: float = 0.2) -> dict:
    from .config import OPENAI_API_KEY, OPENAI_BASE_URL
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set. Add it to your .env file.")
    client = _get_openai_compatible_client(OPENAI_API_KEY, OPENAI_BASE_URL or None)
    t0 = time.time()
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
    )
    latency = int((time.time() - t0) * 1000)
    return {
        "text": resp.choices[0].message.content or "",
        "model": resp.model or model,
        "provider": "openai",
        "input_tokens": getattr(resp.usage, "prompt_tokens", None),
        "output_tokens": getattr(resp.usage, "completion_tokens", None),
        "latency_ms": latency,
    }


def chat_anthropic(messages: List[dict], model: str, temperature: float = 0.2) -> dict:
    """Anthropic Claude — different SDK, slightly different message format."""
    from .config import ANTHROPIC_API_KEY
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY is not set. Add it to your .env file.")
    import anthropic
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    # Anthropic expects system message separate from the rest
    system_text = ""
    user_assistant = []
    for m in messages:
        if m["role"] == "system":
            system_text += m["content"] + "\n"
        else:
            user_assistant.append(m)
    t0 = time.time()
    resp = client.messages.create(
        model=model,
        max_tokens=4096,
        system=system_text.strip(),
        messages=user_assistant,
        temperature=temperature,
    )
    latency = int((time.time() - t0) * 1000)
    text = "".join(block.text for block in resp.content if block.type == "text")
    return {
        "text": text,
        "model": resp.model or model,
        "provider": "anthropic",
        "input_tokens": getattr(resp.usage, "input_tokens", None),
        "output_tokens": getattr(resp.usage, "output_tokens", None),
        "latency_ms": latency,
    }


# Public dispatcher
def chat(messages: List[dict], temperature: float = 0.2) -> dict:
    """
    Send messages to the configured LLM provider and return the response.
    """
    from .config import (
        LLM_PROVIDER, MINIMAX_MODEL, OPENAI_MODEL, ANTHROPIC_MODEL,
    )

    provider = (LLM_PROVIDER or "").lower().strip()
    if provider == "minimax":
        return chat_minimax(messages, model=MINIMAX_MODEL, temperature=temperature)
    if provider == "openai":
        return chat_openai(messages, model=OPENAI_MODEL, temperature=temperature)
    if provider == "anthropic":
        return chat_anthropic(messages, model=ANTHROPIC_MODEL, temperature=temperature)
    if provider == "deepseek":
        # DeepSeek uses the openai SDK with a custom base_url
        from .config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL
        if not DEEPSEEK_API_KEY:
            raise RuntimeError("DEEPSEEK_API_KEY is not set.")
        client = _get_openai_compatible_client(DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL)
        t0 = time.time()
        resp = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=messages,
            temperature=temperature,
        )
        latency = int((time.time() - t0) * 1000)
        return {
            "text": resp.choices[0].message.content or "",
            "model": resp.model or DEEPSEEK_MODEL,
            "provider": "deepseek",
            "input_tokens": getattr(resp.usage, "prompt_tokens", None),
            "output_tokens": getattr(resp.usage, "completion_tokens", None),
            "latency_ms": latency,
        }
    raise NotImplementedError(
        f"LLM provider '{provider}' is not wired up. "
        "Supported: openai, minimax, anthropic, deepseek."
    )
