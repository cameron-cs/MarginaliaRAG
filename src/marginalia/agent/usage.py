"""Token accounting for the LLM calls behind one answer.

Every chat model made by `make_llm` carries a `UsageCallback` with its role (agent or grader).
After each call it adds the tokens Ollama reports to the `TokenUsage` of the answer in progress,
which `BookAgent.ask` sets in a context variable for its duration. Questions answered at the same
time (the eval runner shares one agent) each count only their own calls.

    {"agent":  {"calls": 3, "prompt": 9120, "completion": 1450, "total": 10570,
                "seconds": 21.4, "per_second": 67.8},
     "grader": {...},
     "total":  {...}}

`completion` includes the model's thinking (qwen3 thinks before it answers). `seconds` is Ollama's
own generation time (eval_duration), so `per_second` is generation speed, not wall-clock time.
"""

import contextvars
import threading
from dataclasses import dataclass

from langchain_core.callbacks import BaseCallbackHandler

_current = contextvars.ContextVar('marginalia_token_usage', default=None)


@dataclass
class Tally:
    calls: int = 0
    prompt: int = 0
    completion: int = 0
    seconds: float = 0.0
    prompt_seconds: float = 0.0

    def add(self, message):
        meta = getattr(message, 'response_metadata', None) or {}
        usage = getattr(message, 'usage_metadata', None) or {}
        self.calls += 1
        self.prompt += int(usage.get('input_tokens') or meta.get('prompt_eval_count') or 0)
        self.completion += int(usage.get('output_tokens') or meta.get('eval_count') or 0)
        self.seconds += (meta.get('eval_duration') or 0) / 1e9
        self.prompt_seconds += (meta.get('prompt_eval_duration') or 0) / 1e9

    def __add__(self, other):
        return Tally(self.calls + other.calls, self.prompt + other.prompt, self.completion + other.completion,
                     self.seconds + other.seconds, self.prompt_seconds + other.prompt_seconds)

    def as_dict(self):
        return {'calls': self.calls, 'prompt': self.prompt, 'completion': self.completion,
                'total': self.prompt + self.completion, 'seconds': round(self.seconds, 2),
                'per_second': round(self.completion / self.seconds, 1) if self.seconds else None}


class TokenUsage:
    def __init__(self):
        self.roles = {}
        self._lock = threading.Lock()

    def add(self, role, message):
        with self._lock:
            self.roles.setdefault(role, Tally()).add(message)

    def summary(self):
        with self._lock:
            out = {role: t.as_dict() for role, t in sorted(self.roles.items())}
            out['total'] = sum(self.roles.values(), Tally()).as_dict()
        return out

    def counting(self):
        """`with usage.counting():` attributes the calls made inside to this usage."""
        return _Counting(self)


class _Counting:
    def __init__(self, usage):
        self.usage, self.token = usage, None

    def __enter__(self):
        self.token = _current.set(self.usage)
        return self.usage

    def __exit__(self, *exc):
        _current.reset(self.token)


class UsageCallback(BaseCallbackHandler):
    """Adds each finished call's tokens to the usage being counted, under `role`."""

    run_inline = True          # in the caller's context, where the current usage is set

    def __init__(self, role):
        super().__init__()
        self.role = role

    def on_llm_end(self, response, **kwargs):
        usage = _current.get()
        if usage is None:
            return
        for generations in response.generations:
            for g in generations:
                message = getattr(g, 'message', None)
                if message is not None:
                    usage.add(self.role, message)
