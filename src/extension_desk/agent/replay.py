"""Replay a recorded run's model turns against the live system.

`extension-desk demo --replay <run>/<hh>.json` swaps the model for its own
recorded answers, in order. Everything else runs live: both MCP servers, the
data lookups, the citation check, the server's check, the review queue and
the officer gate. So the server's refusal of a bad draft is computed again on
the spot, and the officer decides for real.

Before each replayed turn, the live conversation so far is compared with the
recording. If a tool result or a check's reply differs, the recorded answer
no longer fits what the model would have seen, so the run stops with
ReplayDiverged instead of carrying on as if nothing changed.
"""

import asyncio
import json
import time
from pathlib import Path

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, messages_from_dict
from langchain_core.outputs import ChatGeneration, ChatResult

from extension_desk.agent.graph import _text


class ReplayDiverged(RuntimeError):
    pass


def load_recording(path: Path | str) -> dict:
    """A household transcript (`<hh>.json` from a run directory), plus the
    model named in the run's `run.json` when there is one."""
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "household_id" not in data or not data.get("messages"):
        raise ValueError(f"{path} is not a household transcript (needs household_id and messages)")
    run = path.parent / "run.json"
    model = None
    if run.exists():
        model = json.loads(run.read_text(encoding="utf-8")).get("model")
    return {"household_id": data["household_id"], "messages": messages_from_dict(data["messages"]),
            "model": model, "path": path}


# Human messages the graph writes are either the review queue's verdict on a
# draft (evidence: compared) or instructions to the model, like the opening
# message and the nudge after plain text (not compared, like the system prompt).
VERDICTS = ("The review queue refused the draft",)


def _same(live: BaseMessage, recorded: BaseMessage) -> bool:
    if live.type != recorded.type:
        return False
    if live.type == "ai":
        strip = lambda m: [(tc["name"], tc["args"]) for tc in getattr(m, "tool_calls", [])]  # noqa: E731
        return strip(live) == strip(recorded)
    if live.type == "human" and not any(_text(m).startswith(VERDICTS) for m in (live, recorded)):
        return True
    return " ".join(_text(live).split()) == " ".join(_text(recorded).split())


def first_difference(live: list[BaseMessage], recorded: list[BaseMessage]) -> str | None:
    """Where the live conversation stops matching the recording, or None.
    Every tool result and every review-queue verdict must match exactly, and
    every message must be of the recorded kind. The graph's instructions to
    the model (system prompt, opening message with today's date, nudges) are
    not compared: they are instructions, not evidence."""
    if len(live) >= len(recorded):
        return f"the live run reached turn {len(live)}, past the end of the recording"
    for i, (a, b) in enumerate(zip(live, recorded)):
        if i < 2:
            continue
        if not _same(a, b):
            return (f"message {i} differs: live {a.type} {_text(a)[:120]!r} "
                    f"vs recorded {b.type} {_text(b)[:120]!r}")
    if recorded[len(live)].type != "ai":
        return f"the recording expects a {recorded[len(live)].type} message at {len(live)}, not a model turn"
    return None


class ReplayModel(BaseChatModel):
    """Answers with the recorded model turns, after checking the live
    conversation still matches the recording."""
    recorded: list
    delay: float = 0.0

    @property
    def _llm_type(self) -> str:
        return "replay"

    def bind_tools(self, tools, **kwargs):
        return self

    def _next(self, messages: list[BaseMessage]) -> ChatResult:
        problem = first_difference(messages, self.recorded)
        if problem:
            raise ReplayDiverged(f"replay stopped: {problem}")
        rec = self.recorded[len(messages)]
        msg = AIMessage(content=rec.content, tool_calls=rec.tool_calls,
                        usage_metadata=rec.usage_metadata, response_metadata=rec.response_metadata)
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        time.sleep(self.delay)
        return self._next(messages)

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        await asyncio.sleep(self.delay)
        return self._next(messages)


def token_usage(messages: list[BaseMessage]) -> dict:
    """Model calls and tokens for one household, from the usage each model
    reply carries (Ollama's prompt_eval_count and eval_count)."""
    calls = inp = out = 0
    for m in messages:
        usage = getattr(m, "usage_metadata", None) if getattr(m, "type", "") == "ai" else None
        if usage:
            calls += 1
            inp += usage.get("input_tokens", 0)
            out += usage.get("output_tokens", 0)
    return {"model_calls": calls, "input_tokens": inp, "output_tokens": out}
