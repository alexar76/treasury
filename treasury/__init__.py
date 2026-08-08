"""MOMUS Treasury — the payer role, deliberately separate from MOMUS.

The whole point of a distinct package and a distinct container is to make "someone else pays"
a *physical* fact, not a config convention. The Treasury holds the one Ed25519 key that can
authorize a bounty; MOMUS runs in a different process, mounts a different key volume, and can
therefore find and sign findings all day without ever being able to release a cent to itself.

The Treasury re-derives its decision independently: it receives a finding + verdicts over HTTP,
re-verifies every signature, re-checks the independence quorum and the external-verifier
requirement, re-checks the dedup ledger, and only then — with ITS key — signs a payout decision.
It trusts nothing MOMUS asserts except the signed documents, whose signatures it checks itself.
"""

__version__ = "0.1.0"
