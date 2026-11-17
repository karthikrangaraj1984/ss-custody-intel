"""MCP server: Custody Market Intelligence on SEC Form N-CEN.

Run:  python -m custody_intel.server        (stdio transport, for Claude Desktop / Claude Code)
"""
from __future__ import annotations

import json
from typing import Optional

try:  # mcp >= 2
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server

from . import analytics as a

INSTRUCTIONS = """Custody Market Intelligence over SEC Form N-CEN (US-registered funds).
Roles: custodian, administrator, transfer_agent, securities_lending, shareholder_servicing.
Provider names are grouped (e.g. 'State Street', 'BNY', 'JPMorgan', 'Northern Trust', 'Citi',
'Brown Brothers Harriman', 'U.S. Bank'). Years are fund fiscal years. Start with data_status if unsure
what is loaded. Always pass on the caveats: '40 Act funds only, annual filing so changes show with a lag."""

mcp = _Server("custody-intel", instructions=INSTRUCTIONS)


def _j(x) -> str:
    return json.dumps(x, default=str, indent=1)


@mcp.tool()
def data_status() -> str:
    """What is loaded: quarters, fund counts and assets per fiscal year, latest complete year, known gaps."""
    return _j(a.data_status())


@mcp.tool()
def market_share(role: str = "custodian", year: Optional[int] = None, weight: str = "assets",
                 category: Optional[str] = None, exclude_affiliated: bool = False, top_n: int = 10) -> str:
    """Provider market share for a service role in one fiscal year.
    weight: 'assets' (net-asset weighted) or 'funds' (fund count).
    category: optional filter - 'ETF', 'Mutual fund', 'Money market', 'Closed-end'.
    exclude_affiliated: drop funds served by an affiliate of the sponsor (e.g. in-house custody) to see the contestable market."""
    return _j(a.market_share(role, year, weight, category, exclude_affiliated, top_n))


@mcp.tool()
def share_trend(providers: Optional[list[str]] = None, role: str = "custodian", weight: str = "assets",
                start_year: Optional[int] = None, end_year: Optional[int] = None,
                category: Optional[str] = None, exclude_affiliated: bool = False) -> str:
    """Market share by fiscal year for chosen providers (default: State Street, BNY, JPMorgan, Northern Trust,
    Citi, BBH, U.S. Bank). Returns one row per year with share % per provider."""
    return _j(a.share_trend(providers, role, weight, start_year, end_year, category, exclude_affiliated))


@mcp.tool()
def provider_switches(role: str = "custodian", provider: Optional[str] = None, direction: str = "any",
                      since_year: Optional[int] = None, category: Optional[str] = None,
                      min_net_assets_usd_m: float = 0, level: str = "family", include_partial: bool = True,
                      limit: int = 25) -> str:
    """Funds or fund families that changed provider for a role, biggest first.
    provider + direction ('won' | 'lost' | 'any') filters to moves involving that provider.
    level: 'family' (moves rolled up per fund family and year) or 'fund'.
    include_partial=False keeps only clean replacements (old provider fully out)."""
    return _j(a.provider_switches(role, provider, direction, since_year, category, min_net_assets_usd_m,
                                  level, include_partial, limit))


@mcp.tool()
def win_loss_summary(provider: str = "State Street", role: str = "custodian", since_year: Optional[int] = None,
                     category: Optional[str] = None) -> str:
    """Wins and losses for one provider per fiscal year (funds and assets), who it won from and lost to,
    and the biggest family-level wins and losses."""
    return _j(a.win_loss_summary(provider, role, since_year, category))


@mcp.tool()
def fund_family_profile(name: str, year: Optional[int] = None) -> str:
    """Profile a fund family (search by family, trust, fund name or ticker): size, fund mix, every service
    provider by role with share of the family's assets, and its provider changes over time."""
    return _j(a.fund_family_profile(name, year))


@mcp.tool()
def find_prospects(provider: str = "State Street", target_role: str = "custodian", year: Optional[int] = None,
                   category: Optional[str] = None, min_family_assets_usd_bn: float = 1.0,
                   cross_sell_only: bool = False, limit: int = 25) -> str:
    """Sales targets: fund families where the provider has under half of the target role, ranked by assets,
    with incumbents and the roles where the provider already serves the family (cross-sell)."""
    return _j(a.find_prospects(provider, target_role, year, category, min_family_assets_usd_bn,
                               cross_sell_only, limit))


@mcp.tool()
def search_funds(query: str, limit: int = 15) -> str:
    """Find funds by name or ticker and show their current providers by role."""
    return _j(a.search_funds(query, limit))


@mcp.tool()
def unmapped_providers(role: str = "custodian", year: Optional[int] = None, limit: int = 30) -> str:
    """Largest provider names not yet mapped to a group in config/providers.csv - for data quality upkeep."""
    return _j(a.unmapped_providers(role, year, limit))


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
