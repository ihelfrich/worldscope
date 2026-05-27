"""Wrappers around Ian's external Go crawlers (web-intel) used by
sections that need BFS-style ingest of editorial sites.

Each module here is a thin Python shim: it shells out to `web-intel`,
streams the JSONL output back into the worldscope lake, and respects
PULL_TIMEOUT_S deadlines so a wedged crawler cannot stall a brief.
"""
