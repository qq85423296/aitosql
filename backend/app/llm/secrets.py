"""Backward-compatible public credential API; implementation is shared infrastructure."""

from app.credentials import decrypt_text, encrypt_text, ensure_key_ready, get_fernet, new_key_b64

__all__ = ["decrypt_text", "encrypt_text", "ensure_key_ready", "get_fernet", "new_key_b64"]
