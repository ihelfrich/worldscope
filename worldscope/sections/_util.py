"""Shared helpers for section modules.

Centralizes small utilities that were previously copy-pasted identically across
~14 section files, so there is a single source of truth.
"""
from __future__ import annotations


def slug(s: str) -> str:
    """URL-safe slug for entity IDs: lowercase alphanumerics, every other
    character collapsed to a hyphen, with leading/trailing hyphens trimmed."""
    return "".join(c.lower() if c.isalnum() else "-" for c in (s or "")).strip("-")
