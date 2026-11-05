"""Compatibility exports; normalization lives in the shared app.text module."""

from app.text import DEFAULT_MAX_LEN, sanitize_row, sanitize_text

__all__ = ["DEFAULT_MAX_LEN", "sanitize_row", "sanitize_text"]
