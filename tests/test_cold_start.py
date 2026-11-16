"""A cold start on a small CPU: the API answers before it loads what only a
question needs (Render's free plan sleeps when idle and wakes on a request)."""

from __future__ import annotations

import subprocess
import sys


def test_starting_the_api_loads_neither_the_llm_sdk_nor_langgraph():
    code = (
        "import sys, app.api.main;"
        "print('openai' in sys.modules, 'langgraph.graph' in sys.modules)"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.split()[-2:] == ["False", "False"], out.stdout + out.stderr


def test_langgraph_still_loads_when_a_graph_is_built():
    from app.agent.orchestration import _langgraph

    end, start, state_graph = _langgraph()
    assert end and start and callable(state_graph)


def test_no_warm_up_when_both_models_are_remote(monkeypatch):
    from app.api import main

    monkeypatch.setenv("MODEL_WARMUP_ON_STARTUP", "true")
    monkeypatch.setenv("EMBEDDING_BACKEND", "remote")
    monkeypatch.setenv("RERANKER_BACKEND", "remote")
    assert main._start_model_warmup() is None


def test_a_local_model_is_still_warmed(monkeypatch):
    from app.api import main

    started = []
    monkeypatch.setenv("MODEL_WARMUP_ON_STARTUP", "true")
    monkeypatch.setenv("EMBEDDING_BACKEND", "local")
    monkeypatch.setenv("RERANKER_BACKEND", "remote")

    class _Thread:
        def __init__(self, *a, **k):
            pass

        def start(self):
            started.append(True)

    monkeypatch.setattr(main.threading, "Thread", _Thread)
    main._start_model_warmup()
    assert started == [True]
