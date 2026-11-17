"""Claude-powered custody market analyst over the local N-CEN database.

    set ANTHROPIC_API_KEY=...            (or put it in a .env file in this folder)
    python -m custody_intel.agent                                   # interactive chat
    python -m custody_intel.agent -q "Who did State Street lose custody to since 2022?"
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import date

from . import analytics as a
from . import config

DEFAULT_MODEL = os.environ.get("CUSTODY_AGENT_MODEL", "claude-sonnet-5-5")

SYSTEM_PROMPT = """You are the Custody Market Analyst. You answer questions about which banks and service \
companies service US-registered funds (custodian, fund administrator, transfer agent, securities lending \
agent, shareholder servicing agent): market share, trends, which fund families switched provider, \
State Street's wins and losses against BNY, JPMorgan, Northern Trust, Citi and others, and sales prospects. \
Use ONLY the tools provided. They read SEC Form N-CEN filings (DERA data sets) loaded into a local database.

Rules:
1. Every number you state must come from a tool result in this conversation. Never use remembered market \
share, AUM or deal figures.
2. Always say which fiscal year(s) a figure is for. A fund's fiscal year is the year its reporting period ends.
3. N-CEN covers US-registered '40 Act funds only (mutual funds, ETFs, money market, closed-end), not private \
funds, UCITS, CITs or separate accounts. Say "share of US registered-fund assets", never "total custody share".
4. Provider changes are reported annually, so a move can show up to a year after it happened. When you \
describe a switch, give its first_seen_fy, and say whether it was confirmed by the fund's own \
"hired or terminated" flag (flagged_in_filing=1). Phased moves (change_type 'phased') add the new provider \
one year and drop the old one later; give both years.
5. For very large moves, suggest checking the underlying filings before relying on the figure.
6. The latest fiscal year can be incomplete because filings are still arriving; tools report the latest \
complete year. Mention this if the user asks about the newest year.
7. Factual analysis only. You can rank prospects and describe incumbents, but don't speculate about \
contract terms or fees, which N-CEN doesn't contain.
8. Be concise: lead with the answer, then a small table if it helps, then a one-line "Source" note: \
"SEC Form N-CEN (DERA data sets), fiscal year(s) ...".

Today's date: {today}."""

_ROLE = {"type": "string", "enum": config.ROLES,
         "description": "Service role. Default custodian."}
_PROVIDER = {"type": "string", "description": "Provider group, e.g. 'State Street', 'BNY', 'JPMorgan', "
                                              "'Northern Trust', 'Citi', 'Brown Brothers Harriman', 'U.S. Bank'"}
_CATEGORY = {"type": "string", "description": "Optional fund category filter: 'ETF', 'Mutual fund', "
                                              "'Money market', 'Closed-end'"}
_YEAR = {"type": "integer", "description": "Fiscal year. Omit for the latest complete year."}

TOOL_SPECS = [
    {"name": "data_status",
     "description": "What is loaded: quarters, funds and assets per fiscal year, latest complete year, known gaps. "
                    "Call first if unsure which years are available.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "market_share",
     "description": "Ranked provider market share for one role in one fiscal year, by assets and fund count.",
     "input_schema": {"type": "object", "properties": {
         "role": _ROLE, "year": _YEAR, "category": _CATEGORY,
         "weight": {"type": "string", "enum": ["assets", "funds"], "description": "Rank by assets (default) or fund count"},
         "exclude_affiliated": {"type": "boolean", "description": "Drop funds served by an affiliate of their sponsor"},
         "top_n": {"type": "integer", "description": "Default 10"}}}},
    {"name": "share_trend",
     "description": "Share % by fiscal year for chosen providers in one role.",
     "input_schema": {"type": "object", "properties": {
         "providers": {"type": "array", "items": {"type": "string"},
                       "description": "Provider groups; default the 7 largest custodians"},
         "role": _ROLE, "category": _CATEGORY,
         "weight": {"type": "string", "enum": ["assets", "funds"]},
         "start_year": {"type": "integer"}, "end_year": {"type": "integer"}}}},
    {"name": "provider_switches",
     "description": "Fund families (or funds) that changed provider for a role, biggest first. Filter by provider and "
                    "direction ('won' = provider gained the business, 'lost' = provider lost it).",
     "input_schema": {"type": "object", "properties": {
         "role": _ROLE, "provider": _PROVIDER,
         "direction": {"type": "string", "enum": ["any", "won", "lost"]},
         "since_year": {"type": "integer", "description": "Earliest first_seen fiscal year. Default: 2 years before latest."},
         "category": _CATEGORY,
         "min_net_assets_usd_m": {"type": "number"},
         "level": {"type": "string", "enum": ["family", "fund"]},
         "include_partial": {"type": "boolean"},
         "limit": {"type": "integer"}}}},
    {"name": "win_loss_summary",
     "description": "One provider's wins and losses per fiscal year (funds, assets), who it won from and lost to, "
                    "and its biggest family-level wins and losses.",
     "input_schema": {"type": "object", "properties": {
         "provider": _PROVIDER, "role": _ROLE, "since_year": {"type": "integer"}, "category": _CATEGORY},
         "required": ["provider"]}},
    {"name": "fund_family_profile",
     "description": "Profile one fund family (search by family, trust, fund name or ticker): size, fund mix, every "
                    "service provider by role with its share of the family's assets, and its provider changes.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "year": _YEAR},
                      "required": ["name"]}},
    {"name": "find_prospects",
     "description": "Sales targets: families where the provider has under 50% of the target role, ranked by assets, "
                    "with incumbents and the roles the provider already serves for that family (cross-sell).",
     "input_schema": {"type": "object", "properties": {
         "provider": _PROVIDER, "target_role": _ROLE, "year": _YEAR, "category": _CATEGORY,
         "min_family_assets_usd_bn": {"type": "number", "description": "Default 1"},
         "cross_sell_only": {"type": "boolean"}, "limit": {"type": "integer"}}}},
    {"name": "search_funds",
     "description": "Find funds by name or ticker and show their current providers by role.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
                      "required": ["query"]}},
]
TOOL_NAMES = {t["name"] for t in TOOL_SPECS}


class CustodyTools:
    """Runs the analytics functions on one connection and returns JSON strings for the model."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._source = None

    def source(self) -> dict:
        if self._source is None:
            q = [r[0] for r in self.conn.execute("SELECT quarter FROM load_log ORDER BY quarter")]
            self._source = {"dataset": "SEC Form N-CEN data sets (DERA)", "url": config.DATASET_PAGE,
                            "quarters_loaded": f"{q[0]}–{q[-1]}" if q else None}
        return self._source

    def call(self, name: str, args: dict) -> str:
        fn = getattr(a, name, None)
        if name not in TOOL_NAMES or fn is None:
            return json.dumps({"error": f"Unknown tool {name}"})
        try:
            out = fn(**(args or {}), conn=self.conn)
            if isinstance(out, dict):
                out = {**out, "source": self.source()}
            return json.dumps(out, default=str)
        except (LookupError, ValueError, TypeError) as exc:
            return json.dumps({"error": str(exc)})
        except Exception as exc:  # report, don't crash: the model can then explain the failure
            return json.dumps({"error": f"{type(exc).__name__}: {exc}"})


def _request_parts():
    system = [{"type": "text", "text": SYSTEM_PROMPT.format(today=date.today().isoformat()),
               "cache_control": {"type": "ephemeral"}}]
    tool_defs = [dict(t) for t in TOOL_SPECS]
    tool_defs[-1] = {**tool_defs[-1], "cache_control": {"type": "ephemeral"}}
    return system, tool_defs


def stream_turn(client, tools: CustodyTools, messages: list, model: str, max_steps: int = 12):
    """Answer the last user message, yielding events as they happen:

    {"type": "text", "text": ...}                     a piece of the answer, as Claude writes it
    {"type": "tool", "name", "input", "result"}       a tool call and its parsed JSON result
    {"type": "stopped", "text": ...}                  gave up after max_steps

    `messages` is updated in place, so the conversation continues on the next call."""
    system, tool_defs = _request_parts()
    for _ in range(max_steps):
        with client.messages.stream(model=model, max_tokens=4096, system=system,
                                    tools=tool_defs, messages=messages) as stream:
            for text in stream.text_stream:
                yield {"type": "text", "text": text}
            final = stream.get_final_message()
        messages.append({"role": "assistant", "content": final.content})
        if final.stop_reason != "tool_use":
            return
        results = []
        for block in final.content:
            if block.type != "tool_use":
                continue
            out = tools.call(block.name, block.input)
            try:
                parsed = json.loads(out)
            except ValueError:
                parsed = {"raw": out}
            yield {"type": "tool", "name": block.name, "input": block.input, "result": parsed}
            results.append({"type": "tool_result", "tool_use_id": block.id, "content": out})
        messages.append({"role": "user", "content": results})
    yield {"type": "stopped", "text": "Stopped after too many tool steps; try a narrower question."}


def run_turn(client, tools: CustodyTools, messages: list, model: str, max_steps: int = 12,
             verbose: bool = False) -> str:
    system, tool_defs = _request_parts()
    for _ in range(max_steps):
        resp = client.messages.create(model=model, max_tokens=4096, system=system,
                                      tools=tool_defs, messages=messages)
        messages.append({"role": "assistant", "content": resp.content})
        if resp.stop_reason != "tool_use":
            return "".join(b.text for b in resp.content if b.type == "text")
        results = []
        for block in resp.content:
            if block.type != "tool_use":
                continue
            if verbose:
                print(f"  → {block.name}({json.dumps(block.input)})", file=sys.stderr)
            results.append({"type": "tool_result", "tool_use_id": block.id,
                            "content": tools.call(block.name, block.input)})
        messages.append({"role": "user", "content": results})
    return "Stopped after too many tool steps; try a narrower question."


def find_api_key() -> str | None:
    """ANTHROPIC_API_KEY from the environment, else from a .env file in the current or project folder."""
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip().strip('"').strip("'")
    if key:
        return key
    for folder in (os.getcwd(), str(config.PROJECT_ROOT)):
        env_file = os.path.join(folder, ".env")
        if os.path.exists(env_file):
            with open(env_file, encoding="utf-8-sig") as fh:
                for line in fh:
                    name, _, value = line.partition("=")
                    if name.strip() == "ANTHROPIC_API_KEY":
                        key = value.strip().strip('"').strip("'")
                        if key:
                            return key
    return None


def connect_ro() -> sqlite3.Connection:
    if not config.DB_PATH.exists():
        sys.exit(f"No database at {config.DB_PATH}. Run refresh.ps1 first.")
    return sqlite3.connect(f"file:{config.DB_PATH.as_posix()}?mode=ro", uri=True, check_same_thread=False)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("-q", "--question")
    ap.add_argument("-v", "--verbose", action="store_true", help="Print tool calls")
    args = ap.parse_args(argv)
    try:
        import anthropic
    except ImportError:
        sys.exit("Install the Anthropic SDK first: pip install anthropic")
    key = find_api_key()
    if not key:
        sys.exit("No API key found. Set ANTHROPIC_API_KEY, or create a .env file in this folder "
                 "containing: ANTHROPIC_API_KEY=sk-ant-...")
    client = anthropic.Anthropic(api_key=key)
    tools = CustodyTools(connect_ro())
    messages: list = []
    if args.question:
        messages.append({"role": "user", "content": args.question})
        print(run_turn(client, tools, messages, args.model, verbose=args.verbose))
        return 0
    print("Custody Market Analyst — SEC Form N-CEN. Ctrl-C to quit.\n")
    while True:
        try:
            q = input("you › ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if q:
            messages.append({"role": "user", "content": q})
            print("\nagent › " + run_turn(client, tools, messages, args.model, verbose=args.verbose) + "\n")


if __name__ == "__main__":
    sys.exit(main())
