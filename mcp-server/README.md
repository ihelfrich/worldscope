# worldscope MCP server

A Model Context Protocol server that exposes the Worldscope lake as queryable tools to any Claude session.

## What this gives you

Once registered, any Claude session can call these tools natively:

| Tool | What it does |
|---|---|
| `worldscope.search_news(query, days_back, section_id, state, limit)` | Full-text search across ingested records |
| `worldscope.lookup_entity(name_or_id, include_records, include_relationships)` | Profile any entity in the graph (person, org, place, filing, bill, ...) |
| `worldscope.recent_state_bills(state, topic, days_back, limit)` | Query the OpenStates slice of the lake |
| `worldscope.get_paper_bets(status, days_back, limit)` | Paper-trading scorecard: open positions, resolved P&L, hit rate |
| `worldscope.get_anomalies(category, days_back, limit)` | Section-level anomaly flags (feed failures, statistical alerts, etc.) |
| `worldscope.get_source_health(stale_hours)` | Which feeds are fresh / stale / failing |
| `worldscope.graph_path(entity_a, entity_b, max_hops)` | Shortest connection path via the relationship graph |
| `worldscope.get_brief(date_iso)` | Fetch a past brief by date |
| `worldscope.get_section_summary(section_id, date_iso)` | Get a section's pre-synthesized markdown summary |
| `worldscope.query_relationships(entity_id, direction, type, limit)` | Relationship neighborhood for any entity |

And a single resource:

- `worldscope://lake/overview` — row counts, latest dates, source tier breakdown

## Setup

1. Install the MCP SDK:

   ```
   pip3 install 'mcp[cli]'
   ```

2. Register the server in your Claude Code config. The path depends on your install:

   - macOS Claude Code: `~/Library/Application Support/Claude/claude_desktop_config.json`
   - Linux: `~/.config/Claude/claude_desktop_config.json`

   Add this block (merging with any existing `mcpServers`):

   ```json
   {
     "mcpServers": {
       "worldscope": {
         "command": "python3",
         "args": ["/Users/ian/Projects/worldscope/mcp-server/worldscope_mcp.py"],
         "env": {}
       }
     }
   }
   ```

3. Restart Claude Code (or any other MCP-aware Claude client).

4. Verify it's loaded by asking Claude: "what worldscope tools do you have?"

## How it reads the lake

The server reads `lake/db/worldscope.sqlite` (the SQLite database the daily routines write to) in **read-only** mode. No write operations are exposed.

It also reads the markdown summaries under `lake/sections/<section-id>/<date>/summary.md` for the `get_section_summary` tool.

The lake DB itself is committed to the repo, so the server works on any machine that has the repo cloned. To use it on a different machine (e.g. for ad-hoc remote queries), `git pull` first.

## Performance

Each tool call opens its own SQLite connection (read-only via URI mode) and closes it on exit. Connection overhead is < 5ms. The lake is small enough (single-digit GB) that any query against an indexed column returns in milliseconds.

The indexes that matter (defined in `worldscope/lake/__init__.py`):

- `idx_records_section_date` — speeds up `search_news` filtered by section + recency
- `idx_records_source` — speeds up source-attributed lookups
- `idx_entities_type` + `idx_entities_name` — speeds up `lookup_entity`
- `idx_rel_from` + `idx_rel_to` — speeds up `graph_path` + `query_relationships`
- `idx_anom_section` + `idx_anom_time` — speeds up `get_anomalies`

## Adding new tools

Add a function decorated with `@mcp.tool()` in `worldscope_mcp.py`. The docstring becomes the tool's description in Claude; type hints become the parameter schema. Example:

```python
@mcp.tool()
def find_legislators_by_state(state: str, party: Optional[str] = None) -> dict:
    """Look up state legislators by jurisdiction.

    Args:
        state: full state name.
        party: optional filter ("D", "R", "I").
    """
    ...
```

Restart Claude Code to pick up changes.

## Read-only by design

The orchestrator routines are the only path that writes to the lake. The MCP server exposes query tools but never mutation tools, by design — this means an MCP-using Claude session can't accidentally corrupt the lake or contaminate paper-bet history. If you ever want to add a write tool (e.g. "manually mark a paper bet as invalid"), it should be a separate, explicitly-authorized server, not folded into this one.
