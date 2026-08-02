"""Log redaction helpers.

Operational logs need to be correlatable, not readable. An email address in a
log line is personal data sitting in a file with a different retention policy,
a different backup path and a much wider read audience than the users table —
and under DPDP it is the same personal data either way.

The tag below is stable for a given address, so "same person, three failed
logins, then a reset" is still a question the logs can answer. It just isn't an
address any more, and it can't be turned back into one.
"""

import hashlib


def redact_email(email: str | None) -> str:
    """A short, stable, non-reversible tag for an email address.

    Truncated to 12 hex chars: enough to correlate lines within a log, short
    enough to stay readable, and — because the input space is real addresses
    rather than a keyspace — deliberately not offered as a security boundary.
    Its job is to keep plaintext PII out of the log file.
    """
    if not email:
        return "email:none"
    digest = hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()
    return f"email:{digest[:12]}"
