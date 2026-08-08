"""Treasury FastAPI service — the only process that can release a bounty.

Endpoints:
    GET  /health                       — liveness + treasury pubkey (never the private key)
    POST /authorize {finding, verdicts, deposit_posted_usd}
                                        — re-verify everything and return a signed Decision
    POST /deposit    {finding, verdicts, deposit_posted_usd}
                                        — rule on a claim's deposit (refund vs forfeit)
    GET  /ledger?limit=                — recent decisions/claims (read-only audit tail)

The treasury key is loaded from ``TREASURY_KEY_PATH`` and stays inside this process. The service
reconstructs a :class:`momus.economics.PayoutGate` with a KeyRing whose ``scanner`` slot is set to
the finding's declared scanner pubkey (read-only, for the independence check) — but authorization
is signed with the treasury key alone.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from oracle_core.app import client_key
from oracle_core.ratelimit import RateLimiter
from pydantic import BaseModel, Field

from momus.config import crypto_enabled, is_prod
from momus.economics import BountyLedger, KeyRing, PayoutGate
from momus.findings import Evidence, Finding, Verdict

from treasury import __version__


class AuthorizeRequest(BaseModel):
    finding: dict[str, Any]
    verdicts: list[dict[str, Any]] = Field(default_factory=list)
    deposit_posted_usd: float = 0.0


def _finding_from_dict(d: dict[str, Any]) -> Finding:
    ev = d.get("evidence") or {}
    if isinstance(ev, dict):
        evidence = Evidence(**{k: ev.get(k) for k in Evidence.__dataclass_fields__ if k in ev})
    else:
        evidence = Evidence(request_digest="", response_digest="")
    fields = {k: d.get(k) for k in Finding.__dataclass_fields__ if k in d and k != "evidence"}
    return Finding(evidence=evidence, **fields)


def _verdict_from_dict(d: dict[str, Any]) -> Verdict:
    fields = {k: d.get(k) for k in Verdict.__dataclass_fields__ if k in d}
    return Verdict(**fields)


def build_app() -> FastAPI:
    scanner_ref = os.environ.get("TREASURY_SCANNER_KEY_PATH", "").strip()
    treasury_key = os.environ.get("TREASURY_KEY_PATH", "data/treasury_signing_key")
    ledger_path = os.environ.get("TREASURY_LEDGER_PATH", "data/bounty_ledger.jsonl")
    data_dir = os.path.dirname(ledger_path) or "."
    os.makedirs(data_dir, exist_ok=True)

    # The treasury does NOT hold the scanner key. For the independence check it only needs the
    # scanner's PUBLIC key, which travels inside each finding. We build the KeyRing with the
    # treasury key as authoritative; the scanner slot is a throwaway used only if a finding omits
    # its scanner_pubkey (it never should). If an operator co-locates the scanner key for a demo,
    # the KeyRing's own guard still refuses scanner == treasury.
    scanner_path = scanner_ref or os.path.join(data_dir, ".treasury_scanner_ref_key")
    keyring = KeyRing(scanner_path, treasury_key)
    ledger = BountyLedger(ledger_path)
    external = PayoutGate.external_verifiers_from_env()

    # ── The UNI vault lives HERE, with the money ───────────────────────────────
    # A simulated treasury balance that is funded, reserved, drawn down, and CAN run out. It belongs
    # to the Treasury and nowhere else: a scanner holding the purse would defeat the separation the
    # whole design rests on. When the settlement tier is UNI, every share must actually come out of
    # this balance, so an underfunded treasury HOLDS the decision instead of inventing money.
    from momus.budget import SecurityBudget
    from momus.settlement import SettlementBackend, SettlementMode
    from momus.vault import UniVault

    vault = UniVault(os.environ.get("TREASURY_VAULT_PATH", os.path.join(data_dir, "uni_vault.jsonl")))
    budget = SecurityBudget(vault)
    settlement = SettlementBackend.from_env(
        uni_ledger_path=os.path.join(data_dir, "uni_settlements.jsonl"),
        crypto_enabled=crypto_enabled())
    settlement.vault = vault if settlement.mode is SettlementMode.UNI else None
    gate = PayoutGate(keyring, ledger, crypto_enabled=crypto_enabled(), prod=is_prod(),
                      external_verifiers=external, settlement=settlement)

    app = FastAPI(title="MOMUS Treasury", version=__version__,
                  description="The separate payer role for MOMUS red-team bounties.")
    origins = [o.strip() for o in os.environ.get("TREASURY_CORS_ORIGINS", "*").split(",") if o.strip()] or ["*"]
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["*"], allow_headers=["*"])

    # ── Caller authentication for the WRITE routes ────────────────────────────
    # The Treasury holds the only key that can release a bounty, and it sits on the shared
    # `ecosystem` Docker network. Without this, any process on that network (or on host loopback)
    # could POST a self-signed finding plus two self-minted verdicts and walk away with a
    # treasury-SIGNED "paid" decision. Signature checks alone are not enough: the gate proves the
    # documents are internally consistent, not that the caller is entitled to ask.
    #
    # So: /authorize, /deposit and /explain require a client token, fail-closed in production, and
    # are rate-limited. The read-only /health and /ledger stay open — they are the audit surface.
    # Additionally the finding's scanner_pubkey must be on an allowlist when one is configured, so
    # a stranger's key cannot claim a bounty even with a valid token.
    write_limiter = RateLimiter(int(os.environ.get("TREASURY_WRITE_RATE_LIMIT", "30")))
    client_token = os.environ.get("TREASURY_CLIENT_TOKEN", "").strip()
    allowed_scanners = {k.strip() for k in os.environ.get("TREASURY_SCANNER_PUBKEYS", "").split(",") if k.strip()}

    def _require_client(request: Request) -> None:
        if not write_limiter.allow(client_key(request)):
            raise HTTPException(status_code=429, detail="rate limited")
        if not client_token:
            if is_prod():
                raise HTTPException(
                    status_code=503,
                    detail="TREASURY_CLIENT_TOKEN is unset — refusing payout requests in production "
                           "(fail-closed). Configure it on both the Treasury and MOMUS.")
            return  # dev convenience only
        supplied = (request.headers.get("x-treasury-client") or "").strip()
        if supplied != client_token:
            raise HTTPException(status_code=403, detail="treasury client token required")

    def _require_known_scanner(finding: dict[str, Any]) -> None:
        """A valid token authenticates the CALLER; this checks the CLAIMANT identity too."""
        if not allowed_scanners:
            return
        pub = str((finding or {}).get("scanner_pubkey") or "")
        if pub not in allowed_scanners:
            raise HTTPException(
                status_code=403,
                detail="finding's scanner_pubkey is not a registered claimant "
                       "(set TREASURY_SCANNER_PUBKEYS)")

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "service": "treasury", "version": __version__,
                "treasury_pubkey": keyring.treasury_pubkey,
                # Whether the payout routes require a caller token (armed in prod).
                "write_gated": bool(client_token) or is_prod(),
                "registered_scanners": len(allowed_scanners),
                "crypto_enabled": crypto_enabled(), "prod": is_prod(),
                "external_verifiers": sorted(external)}

    @app.post("/authorize")
    async def authorize(body: AuthorizeRequest, request: Request) -> dict[str, Any]:
        _require_client(request)
        _require_known_scanner(body.finding)
        # The treasury re-verifies EVERY signature inside the gate; it trusts none of MOMUS's
        # claims except the signed documents. A finding whose scanner signature is invalid, or
        # that lacks an independent external-verified quorum, is refused — with reasons.
        try:
            finding = _finding_from_dict(body.finding)
            verdicts = [_verdict_from_dict(v) for v in body.verdicts]
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"malformed finding/verdicts: {exc}") from None
        decision = gate.authorize(finding, verdicts, deposit_posted_usd=body.deposit_posted_usd)
        return {**decision.to_dict(), "signature": decision.signature}

    @app.post("/deposit")
    async def deposit(body: AuthorizeRequest, request: Request) -> dict[str, Any]:
        _require_client(request)
        _require_known_scanner(body.finding)
        try:
            finding = _finding_from_dict(body.finding)
            verdicts = [_verdict_from_dict(v) for v in body.verdicts]
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"malformed finding/verdicts: {exc}") from None
        return gate.adjudicate_deposit(finding, verdicts, body.deposit_posted_usd)

    # ── The vault: balance, journal, funding, rule-based top-up ────────────────
    @app.get("/vault")
    async def vault_state() -> dict[str, Any]:
        """Balance / reserved / available, the standing allocation rule, and what every transaction
        kind MEANS. Public: this is the audit surface — anyone should be able to see the security
        budget and how it was funded."""
        return {**vault.state(), "budget": budget.state(),
                "settlement_mode": settlement.mode.value,
                "transaction_meanings": UniVault.meanings(),
                "note": "UNI tier — simulated bookkeeping; no value moves anywhere"}

    @app.get("/vault/journal")
    async def vault_journal(limit: int = 50) -> dict[str, Any]:
        """The append-only transaction journal; each entry carries its own plain-language meaning."""
        return {"transactions": vault.journal(limit), "state": vault.state()}

    @app.post("/vault/fund")
    async def vault_fund(body: dict, request: Request) -> dict[str, Any]:
        """Operator adds simulated budget. Money enters the vault only here or via a forfeited
        deposit, so it is gated exactly like the payout routes."""
        _require_client(request)
        try:
            amount = float((body or {}).get("amount_usd") or 0)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="amount_usd must be a number") from None
        if amount <= 0:
            raise HTTPException(status_code=400, detail="amount_usd must be positive")
        tx = vault.fund(amount, note=str((body or {}).get("note") or ""))
        return {"transaction": tx.to_dict(), "state": vault.state()}

    @app.post("/vault/reserve")
    async def vault_reserve(body: dict, request: Request) -> dict[str, Any]:
        """Set a bounty's pool aside before its shares are released. Refuses when the vault cannot
        cover it, which is what stops two claims spending the same dollar."""
        _require_client(request)
        fid = str((body or {}).get("finding_id") or "").strip()
        try:
            amount = float((body or {}).get("amount_usd") or 0)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="amount_usd must be a number") from None
        if not fid or amount <= 0:
            raise HTTPException(status_code=400, detail="finding_id and a positive amount_usd required")
        ok, why, tx = vault.reserve(fid, amount)
        return {"reserved": ok, "reason": why,
                "transaction": tx.to_dict() if tx else None, "state": vault.state()}

    @app.post("/vault/top-up")
    async def vault_top_up(request: Request) -> dict[str, Any]:
        """Request a refill under the standing allocation rule (hub-funded by rate, capped per
        period). Grants automatically inside the allowance; refuses and ESCALATES above it, so a
        funder can never silently defund the auditor."""
        _require_client(request)
        res = await budget.request_top_up()
        return {"allocation": res.to_dict(), "state": vault.state()}

    @app.get("/ledger")
    async def ledger_tail(limit: int = 100) -> dict[str, Any]:
        entries = ledger.entries(limit=max(1, min(limit, 500)))
        return {"count": len(entries), "entries": entries,
                "treasury_pubkey": keyring.treasury_pubkey}

    @app.post("/explain")
    async def explain(body: AuthorizeRequest, request: Request) -> dict[str, Any]:
        _require_client(request)
        _require_known_scanner(body.finding)
        # Advisory only: authorize FIRST (deterministic, no LLM), THEN narrate the finished
        # decision with the model. The model never sees the raw finding and cannot change the
        # outcome — it only writes the audit note.
        from treasury.explainer import explain_decision
        try:
            finding = _finding_from_dict(body.finding)
            verdicts = [_verdict_from_dict(v) for v in body.verdicts]
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"malformed: {exc}") from None
        decision = gate.authorize(finding, verdicts, deposit_posted_usd=body.deposit_posted_usd)
        note = await explain_decision(decision.to_dict())
        return {"decision": decision.to_dict(), "explanation": note}

    return app


app = build_app()


def main() -> None:
    import uvicorn
    uvicorn.run("treasury.service:app", host="0.0.0.0",
                port=int(os.environ.get("TREASURY_PORT", "9401")), reload=False)


if __name__ == "__main__":
    main()
