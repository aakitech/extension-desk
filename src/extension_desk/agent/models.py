"""Model provider, chosen by config so the same graph runs on an
open-weights model (the default) or a hosted one.

    EXTENSION_DESK_MODEL=ollama:qwen2.5:7b          # default, local, open weights
    EXTENSION_DESK_MODEL=anthropic:claude-sonnet-5-5  # needs `uv sync --extra hosted`
"""

import json
import os
import urllib.request

DEFAULT_MODEL = "ollama:qwen2.5:7b"
# Ollama's default context is 4096 tokens; past that it truncates the oldest
# non-system messages (its prompt builder keeps system messages). Eval runs
# reach ~3k tokens of messages plus the tool schemas sent on every call, so
# 4096 is close to the edge.
NUM_CTX = int(os.environ.get("EXTENSION_DESK_NUM_CTX", "16384"))
OLLAMA_URL = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
if "://" not in OLLAMA_URL:  # Ollama itself accepts host:port
    OLLAMA_URL = "http://" + OLLAMA_URL


class ModelUnavailable(RuntimeError):
    pass


def model_spec(spec: str | None = None) -> str:
    return spec or os.environ.get("EXTENSION_DESK_MODEL", DEFAULT_MODEL)


def make_model(spec: str | None = None):
    provider, _, name = model_spec(spec).partition(":")
    if provider == "ollama":
        _check_ollama(name)
        from langchain_ollama import ChatOllama
        return ChatOllama(model=name, base_url=OLLAMA_URL, temperature=0, num_ctx=NUM_CTX)
    if provider == "anthropic":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise ModelUnavailable("ANTHROPIC_API_KEY is not set")
        try:
            from langchain_anthropic import ChatAnthropic
        except ImportError:
            raise ModelUnavailable("run `uv sync --extra hosted` for the anthropic provider") from None
        return ChatAnthropic(model=name, temperature=0)
    raise ModelUnavailable(f"unknown model provider {provider!r}; use ollama:<model> or anthropic:<model>")


def model_details(spec: str | None = None) -> dict:
    """The exact model build, for run records. Best effort: never raises."""
    provider, _, name = model_spec(spec).partition(":")
    details = {"provider": provider, "name": name}
    if provider != "ollama":
        return details
    details["num_ctx"] = NUM_CTX
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=5) as resp:
            for m in json.load(resp).get("models", []):
                if m["name"] in (name, f"{name}:latest"):
                    details.update(digest=m.get("digest"), modified_at=m.get("modified_at"), size=m.get("size"))
        req = urllib.request.Request(f"{OLLAMA_URL}/api/show", data=json.dumps({"model": name}).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            details.update(json.load(resp).get("details", {}))
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/version", timeout=5) as resp:
            details["ollama_version"] = json.load(resp).get("version")
    except (OSError, ValueError, KeyError) as e:
        details["error"] = str(e)
    return details


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
