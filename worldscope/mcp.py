"""Convenience launcher: `python -m worldscope.mcp` runs the MCP server.

The actual implementation lives at mcp-server/worldscope_mcp.py (kept
under that directory name so that its README and packaging stay
self-contained and visible at the repo root). This thin module just
locates that script and execs it so users don't have to type absolute
paths in their Claude Desktop / Claude Code config.

  Claude Desktop config (~/.config/Claude/claude_desktop_config.json):

    {
      "mcpServers": {
        "worldscope": {
          "command": "python3",
          "args": ["-m", "worldscope.mcp"],
          "cwd": "/path/to/worldscope"
        }
      }
    }

Equivalent and shorter than the README's previous absolute-path snippet,
and resilient to clone-location changes.
"""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO / "mcp-server" / "worldscope_mcp.py"


def main() -> None:
    if not _SCRIPT.exists():
        sys.stderr.write(f"FATAL: MCP entry script missing at {_SCRIPT}\n")
        sys.exit(2)
    runpy.run_path(str(_SCRIPT), run_name="__main__")


if __name__ == "__main__":
    main()
