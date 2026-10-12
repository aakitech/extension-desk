"""Model provider, chosen by config so the same graph runs on an
open-weights model (the default) or a hosted one.

    EXTENSION_DESK_MODEL=ollama:qwen2.5:7b          # default, local, open weights
    EXTENSION_DESK_MODEL=anthropic:claude-sonnet-5-5  # needs `uv sync --extra hosted`
"""

import json
import os
import urllib.request

DEFAULT_MODEL = "ollama:qwen2.5:7b"
OLLAMA_URL = os.environ.get("OLLAMA_HOST", "http://localhost:11434")


class ModelUnavailable(RuntimeError):
    pass


def model_spec(spec: str | None = None) -> str:
    return spec or os.environ.get("EXTENSION_DESK_MODEL", DEFAULT_MODEL)


def make_model(spec: str | None = None):
    provider, _, name = model_spec(spec).partition(":")
    if provider == "ollama":
        _check_ollama(name)
        from langchain_ollama import ChatOllama
        return ChatOllama(model=name, base_url=OLLAMA_URL, temperature=0)
    if provider == "anthropic":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise ModelUnavailable("ANTHROPIC_API_KEY is not set")
        try:
            from langchain_anthropic import ChatAnthropic
        except ImportError:
            raise ModelUnavailable("run `uv sync --extra hosted` for the anthropic provider") from None
        return ChatAnthropic(model=name, temperature=0)
    raise ModelUnavailable(f"unknown model provider {provider!r}; use ollama:<model> or anthropic:<model>")


def _check_ollama(name: str) -> None:
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=5) as resp:
            models = {m["name"] for m in json.load(resp).get("models", [])}
    except OSError:
        raise ModelUnavailable(
            f"Ollama isn't reachable at {OLLAMA_URL}. Install it from https://ollama.com, "
            f"then run `ollama pull {name}`.") from None
    if name not in models and f"{name}:latest" not in models:
        raise ModelUnavailable(f"Ollama is running but {name!r} isn't pulled. Run `ollama pull {name}`.")
