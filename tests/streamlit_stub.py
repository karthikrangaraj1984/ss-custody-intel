"""A stand-in for streamlit and altair, so app.py's own logic can run in tests without a browser.

Every Streamlit call is accepted and recorded; widgets return scripted values from `answers`
(keyed by label) or a sensible default. Charts are accepted but not drawn."""
from __future__ import annotations

import sys
import types


class _Any:
    """Absorbs any attribute access, call, context manager or chaining."""
    def __init__(self, rec=None, name="st"):
        self._rec, self._name = rec, name

    def __getattr__(self, item):
        return _Any(self._rec, f"{self._name}.{item}")

    def __call__(self, *a, **k):
        if self._rec is not None:
            self._rec.append((self._name, a, k))
        return _Any(self._rec, self._name + "()")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __add__(self, other):
        return self

    def __iter__(self):
        return iter([])


class SessionState(dict):
    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError as e:
            raise AttributeError(k) from e

    def __setattr__(self, k, v):
        self[k] = v


class StopApp(Exception):
    pass


def make_streamlit(answers: dict | None = None):
    answers = answers or {}
    rec: list = []
    st = types.ModuleType("streamlit")
    st.calls = rec
    st.session_state = SessionState()
    st.secrets = {}

    def record(name):
        def f(*a, **k):
            rec.append((name, a, k))
            return _Any(rec, name)
        return f

    for name in ("set_page_config", "markdown", "caption", "title", "subheader", "metric", "error",
                 "warning", "info", "write", "json", "divider", "altair_chart", "dataframe", "empty",
                 "status", "chat_message", "expander", "sidebar", "spinner"):
        setattr(st, name, _Any(rec, name) if name in ("sidebar",) else record(name))

    def chat_message(*a, **k):
        rec.append(("chat_message", a, k))
        return _Any(rec, "chat_message")
    st.chat_message = chat_message

    def status(*a, **k):
        rec.append(("status", a, k))
        return _Any(rec, "status")
    st.status = status
    st.empty = lambda: _Any(rec, "empty")
    st.expander = lambda *a, **k: _Any(rec, "expander")

    def columns(spec, **k):
        n = spec if isinstance(spec, int) else len(spec)
        return [_ColumnStub(st) for _ in range(n)]
    st.columns = columns

    def radio(label, options, key=None, **k):
        if key and key not in st.session_state:
            st.session_state[key] = options[0]
        return st.session_state.get(key, options[0])
    st.radio = radio

    def selectbox(label, options, index=0, **k):
        return answers.get(label, options[index])
    st.selectbox = selectbox
    st.multiselect = lambda label, options, default=None, **k: answers.get(label, default or [])
    st.text_input = lambda label, **k: answers.get(label, "")
    st.chat_input = lambda placeholder="", **k: answers.get("chat_input")

    def button(label, key=None, on_click=None, args=(), **k):
        rec.append(("button", (label,), k))
        pressed = answers.get("button") == label
        if pressed and on_click:
            on_click(*args)
        return pressed
    st.button = button

    def stop():
        raise StopApp()
    st.stop = stop

    def rerun():
        raise StopApp()
    st.rerun = rerun

    def cache_data(fn=None, **k):
        if fn is None:
            return lambda f: f
        return fn
    st.cache_data = cache_data
    st.cache_resource = cache_data
    st.column_config = _Any(rec, "column_config")
    return st


class _ColumnStub:
    def __init__(self, st):
        self._st = st

    def __getattr__(self, item):
        return getattr(self._st, item)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def make_altair():
    alt = types.ModuleType("altair")
    for name in ("Chart", "X", "Y", "Color", "Tooltip", "Axis", "Scale", "value", "condition",
                 "FieldOneOfPredicate", "OverlayMarkDef", "EncodingSortField", "Text"):
        setattr(alt, name, _Any(None, f"alt.{name}"))
    return alt


def install(answers=None):
    st = make_streamlit(answers)
    sys.modules["streamlit"] = st
    sys.modules["altair"] = make_altair()
    return st
