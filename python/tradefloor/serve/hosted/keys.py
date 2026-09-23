"""API keys: generated once, shown once, stored only as a salted hash.

A key looks like `tfk_3f9c0a1b2d4e_<43 url-safe characters>`:

    tfk_          a fixed prefix, so secret scanners (GitHub's, gitleaks) can be
                  taught to recognise a leaked key by pattern
    3f9c0a1b2d4e  the key id: public, 48 random bits, used to look the key up,
                  shown in the admin CLI and written to the audit log
    <secret>      32 bytes from `secrets.token_urlsafe` (256 bits)

At rest the server keeps the key id, a 16-byte random salt, and
HMAC-SHA256(pepper, salt || secret). The pepper is a server-side secret
(`TRADEFLOOR_HOSTED_PEPPER`, from Secrets Manager in the AWS path) that is not
stored beside the accounts file, so a copy of that file alone does not even
allow an offline guess.

Why not bcrypt/scrypt/argon2: those slow a guess down because a password has
perhaps 40 bits of entropy. This secret has 256; nobody enumerates that at any
speed, and a slow hash on every request would cost more CPU than the
simulation. The salt still stops two stored hashes being compared, and the
comparison is constant-time.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass

PREFIX = "tfk"
_KEY_RE = re.compile(r"^tfk_([0-9a-f]{12})_([A-Za-z0-9_\-]{32,128})$")


@dataclass(frozen=True)
class ParsedKey:
    key_id: str
    secret: str


def new_key_id() -> str:
    return secrets.token_hex(6)


def new_secret() -> str:
    return secrets.token_urlsafe(32)


def format_key(key_id: str, secret: str) -> str:
    return f"{PREFIX}_{key_id}_{secret}"


def parse_key(text: str) -> ParsedKey | None:
    """The key id and secret, or None if `text` is not shaped like a key."""
    m = _KEY_RE.match(text.strip())
    if not m:
        return None
    return ParsedKey(key_id=m.group(1), secret=m.group(2))


def new_salt() -> str:
    return secrets.token_hex(16)


def hash_secret(secret: str, salt_hex: str, pepper: bytes) -> str:
    return hmac.new(pepper, bytes.fromhex(salt_hex) + secret.encode("utf-8"),
                    hashlib.sha256).hexdigest()


def verify_secret(secret: str, salt_hex: str, expected_hex: str, pepper: bytes) -> bool:
    return hmac.compare_digest(hash_secret(secret, salt_hex, pepper), expected_hex)
