"""Pairing a phone with a running desktop session.

A companion device gets to see the workspace and change settings, so letting
anything on the network attach would be worse than having no companion at
all. Pairing is deliberately conservative:

* The **desktop** initiates. A code only exists because someone sitting at
  the machine asked for one — a phone cannot request pairing out of nowhere.
* Codes are **short-lived** (two minutes), **single-use**, and generated
  with :mod:`secrets`, not :mod:`random`.
* A redeemed code becomes a **long random token** bound to that one session.
  The code never travels again.
* Wrong codes are **rate-limited**. Six digits is only a million
  possibilities, which is nothing to a script; the lockout is what makes the
  short code safe enough to read aloud.
* Every comparison uses :func:`hmac.compare_digest`, so a timing side
  channel cannot leak a code or token character by character.

What this is *not*: a replacement for transport security. It authenticates
the pairing, it does not encrypt the link. Over an untrusted network the
server should be behind TLS — see the README.
"""

from __future__ import annotations

import hmac
import secrets
from dataclasses import dataclass, field

#: Digits in a pairing code. Short enough to read aloud, and safe only
#: because of the attempt limit below.
CODE_DIGITS = 6
#: How long a code stays valid.
CODE_TTL_SECONDS = 120.0
#: Failed redemptions before pairing locks out.
MAX_ATTEMPTS = 5
#: How long the lockout lasts.
LOCKOUT_SECONDS = 300.0


class PairingError(Exception):
    """Pairing was refused. The message is safe to show the user."""


@dataclass(frozen=True, slots=True)
class PairingCode:
    code: str
    session_id: str
    expires_at: float

    def expired(self, now: float) -> bool:
        return now >= self.expires_at


@dataclass(frozen=True, slots=True)
class CompanionToken:
    token: str
    session_id: str
    issued_at: float
    #: Human-readable label for the paired device, shown on the desktop so
    #: the user can see what is attached and revoke it.
    label: str = "companion"


@dataclass(slots=True)
class PairingService:
    """Issues pairing codes and exchanges them for session tokens."""

    code_ttl: float = CODE_TTL_SECONDS
    max_attempts: int = MAX_ATTEMPTS
    lockout_seconds: float = LOCKOUT_SECONDS

    _codes: dict[str, PairingCode] = field(default_factory=dict)
    _tokens: dict[str, CompanionToken] = field(default_factory=dict)
    _failures: int = 0
    _locked_until: float = 0.0

    # --- Codes ------------------------------------------------------------

    def create_code(self, session_id: str, now: float) -> PairingCode:
        """Issue a fresh pairing code for a desktop session.

        Any previous code for the same session is dropped: two live codes
        for one session would mean a code the user has forgotten about is
        still redeemable.
        """
        self._expire(now)
        self._codes = {
            code: entry
            for code, entry in self._codes.items()
            if entry.session_id != session_id
        }

        digits = "".join(secrets.choice("0123456789") for _ in range(CODE_DIGITS))
        entry = PairingCode(digits, session_id, now + self.code_ttl)
        self._codes[digits] = entry
        return entry

    def cancel_code(self, session_id: str) -> bool:
        """Withdraw any outstanding code for a session."""
        before = len(self._codes)
        self._codes = {
            code: entry
            for code, entry in self._codes.items()
            if entry.session_id != session_id
        }
        return len(self._codes) != before

    def pending_code(self, session_id: str, now: float) -> PairingCode | None:
        self._expire(now)
        for entry in self._codes.values():
            if entry.session_id == session_id:
                return entry
        return None

    # --- Redemption -------------------------------------------------------

    def locked_out(self, now: float) -> bool:
        return now < self._locked_until

    def redeem(self, code: str, now: float, *, label: str = "companion") -> CompanionToken:
        """Exchange a code for a token. Raises :class:`PairingError` if refused."""
        if self.locked_out(now):
            remaining = int(self._locked_until - now)
            raise PairingError(f"too many attempts, try again in {remaining}s")

        self._expire(now)

        # Constant-time scan over every live code. Comparing only against a
        # dict hit would leak, through timing, whether a code exists at all.
        matched: PairingCode | None = None
        for candidate, entry in self._codes.items():
            if hmac.compare_digest(candidate, str(code)):
                matched = entry

        if matched is None:
            self._failures += 1
            if self._failures >= self.max_attempts:
                self._locked_until = now + self.lockout_seconds
                self._failures = 0
                raise PairingError("too many attempts, pairing locked")
            raise PairingError("invalid or expired code")

        # Single use: burn it whether or not anything later goes wrong.
        del self._codes[matched.code]
        self._failures = 0

        token = CompanionToken(
            token=secrets.token_urlsafe(32),
            session_id=matched.session_id,
            issued_at=now,
            label=label[:40] or "companion",
        )
        self._tokens[token.token] = token
        return token

    # --- Tokens -----------------------------------------------------------

    def verify(self, token: str | None) -> CompanionToken | None:
        """Look up a token in constant time. ``None`` means not authorised."""
        if not token:
            return None
        found: CompanionToken | None = None
        for candidate, entry in self._tokens.items():
            if hmac.compare_digest(candidate, str(token)):
                found = entry
        return found

    def revoke(self, token: str) -> bool:
        entry = self.verify(token)
        if entry is None:
            return False
        del self._tokens[entry.token]
        return True

    def revoke_session(self, session_id: str) -> int:
        """Drop every companion attached to a session, e.g. when it ends."""
        doomed = [t for t, e in self._tokens.items() if e.session_id == session_id]
        for token in doomed:
            del self._tokens[token]
        return len(doomed)

    def companions(self, session_id: str) -> list[CompanionToken]:
        return [e for e in self._tokens.values() if e.session_id == session_id]

    # --- Housekeeping -----------------------------------------------------

    def _expire(self, now: float) -> None:
        self._codes = {c: e for c, e in self._codes.items() if not e.expired(now)}
