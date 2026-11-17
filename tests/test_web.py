"""Runs app.py's real code on every page with a Streamlit stand-in, plus the streaming chat engine.

    python -m pytest tests            (or: python tests/test_web.py)
Uses the synthetic N-CEN fixture, or the real database if CUSTODY_TEST_REAL_DB points at one."""
import json
import os
import runpy
import sys
import tempfile
import types
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import streamlit_stub  # noqa: E402
import make_fixture  # noqa: E402
from custody_intel import config, load, model  # noqa: E402
from custody_intel.agent import CustodyTools, TOOL_SPECS, stream_turn  # noqa: E402

APP = ROOT / "app.py"
PAGES = ["Overview", "Market share", "Switches", "Fund families", "Prospects", "Ask the analyst"]


def build_db() -> Path:
    real = os.environ.get("CUSTODY_TEST_REAL_DB")
    if real:
        return Path(real)
    tmp = Path(tempfile.mkdtemp())
    make_fixture.build(tmp / "zips")
    conn = load.connect(tmp / "t.sqlite")
    load.load_all(conn, tmp / "zips")
    model.build(conn)
    conn.close()
    return tmp / "t.sqlite"


# ------------------------------------------------------------------ fake Claude, streaming

class FakeStream:
    def __init__(self, texts, final):
        self._final = final
        self.text_stream = iter(texts)

    def __enter__(self):
        return self

    def __exit__(self, *e):
        return False

    def get_final_message(self):
        return self._final


def fake_client(script):
    calls = []

    def stream(**kw):
        calls.append(kw)
        texts, final = script[len(calls) - 1]
        return FakeStream(texts, final)
    return SimpleNamespace(messages=SimpleNamespace(stream=stream)), calls


def tool_use(name, args, id_="t1"):
    return SimpleNamespace(type="tool_use", name=name, input=args, id=id_)


def text_block(t):
    return SimpleNamespace(type="text", text=t)


def chat_script():
    return [
        ([], SimpleNamespace(stop_reason="tool_use", content=[tool_use("win_loss_summary", {"provider": "State Street"})])),
        (["State Street ", "won custody."], SimpleNamespace(stop_reason="end_turn", content=[text_block("State Street won custody.")])),
    ]


def install_anthropic(script):
    mod = types.ModuleType("anthropic")

    class APIError(Exception):
        pass
    mod.APIError = APIError
    mod.Anthropic = lambda api_key=None: fake_client(script)[0]
    sys.modules["anthropic"] = mod


def run_page(db: Path, page: str, answers=None, session=None):
    st = streamlit_stub.install(answers or {})
    st.session_state.update(session or {"page": page})
    config.DB_PATH = db
    try:
        runpy.run_path(str(APP), run_name="__main__")
    except streamlit_stub.StopApp:
        pass
    return st


def test_all():
    db = build_db()
    for page in PAGES[:-1]:
        st = run_page(db, page, answers={"Family size": 1.0})
        errors = [c for c in st.calls if c[0] == "error"]
        assert not errors, (page, errors)
        assert any(c[0] == "altair_chart" or c[0] == "dataframe" for c in st.calls), page
        print(f"page ok: {page} ({len(st.calls)} calls)")

    # switches page filtered to all providers, and fund family page for a chosen family
    run_page(db, "Switches", answers={"Provider": "All providers"})
    fam = "ALPHA FUNDS" if not os.environ.get("CUSTODY_TEST_REAL_DB") else "BLACKROCK-ADVISED FUNDS"
    st = run_page(db, "Fund families", session={"page": "Fund families", "family": fam})
    assert any(fam.title() in str(c[1]) for c in st.calls if c[0] == "markdown"), "family title missing"

    # chat page: no key -> warning; with key -> streams answer and records the tool call
    os.environ.pop("ANTHROPIC_API_KEY", None)
    st = run_page(db, "Ask the analyst")
    os.environ["ANTHROPIC_API_KEY"] = "sk-test"
    install_anthropic(chat_script())
    st = run_page(db, "Ask the analyst", answers={"chat_input": "Who did State Street win from?"})
    tr = st.session_state["transcript"]
    assert tr[-1]["role"] == "assistant" and "won custody" in tr[-1]["text"], tr
    assert tr[-1]["tools"][0]["name"] == "win_loss_summary"
    assert "by_year" in tr[-1]["tools"][0]["result"] and "source" in tr[-1]["tools"][0]["result"]
    print("chat ok")

    # every tool spec runs through CustodyTools without error
    import sqlite3
    conn = sqlite3.connect(db)
    t = CustodyTools(conn)
    samples = {"fund_family_profile": {"name": fam}, "search_funds": {"query": "fund"},
               "win_loss_summary": {"provider": "State Street"}}
    for spec in TOOL_SPECS:
        out = json.loads(t.call(spec["name"], samples.get(spec["name"], {})))
        assert "error" not in out, (spec["name"], out)
    assert "error" in json.loads(t.call("nope", {}))
    print("tools ok")


if __name__ == "__main__":
    test_all()
    print("all web checks passed")
