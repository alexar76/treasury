"""Treasury service — the separate payer. Proves it authorizes only what it should, with its own
key, and refuses tampered claims."""

from __future__ import annotations

import dataclasses

import httpx
import pytest


def _finding(scanner, severity="high"):
    from momus.findings import Evidence, Finding, Outcome, Status
    f = Finding(target="oracles", target_kind="oracle", probe="p", category="authz",
                severity=severity, outcome=Outcome.FINDING.value, title="t", detail="d",
                evidence=Evidence("sha256-a", "sha256-b"), status=Status.RAW.value)
    return scanner.sign_finding(f)


def _confirm(vs, f):
    from momus.findings import Verdict, finding_digest
    return vs.sign_verdict(Verdict(f.finding_id, finding_digest(f), "confirmed", "replay", 0.95, "r", vs.pubkey[:6], subject_target=f.target, subject_probe=f.probe))


@pytest.fixture
def treasury_client(tmp_path, monkeypatch):
    from momus.findings import FindingSigner
    ext = FindingSigner(str(tmp_path / "ext.key"))
    monkeypatch.setenv("TREASURY_KEY_PATH", str(tmp_path / "treasury.key"))
    monkeypatch.setenv("TREASURY_SCANNER_KEY_PATH", str(tmp_path / "tref.key"))
    monkeypatch.setenv("TREASURY_LEDGER_PATH", str(tmp_path / "ledger.jsonl"))
    monkeypatch.setenv("AIFACTORY_CRYPTO_ENABLED", "1")
    monkeypatch.setenv("AIFACTORY_PROD", "0")
    monkeypatch.setenv("MOMUS_EXTERNAL_VERIFIERS", ext.pubkey)
    import importlib
    from treasury import service as svc
    importlib.reload(svc)
    app = svc.build_app()
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://tr.local")
    return client, ext, str(tmp_path)


@pytest.mark.asyncio
async def test_health_exposes_treasury_pubkey_only(treasury_client):
    client, _ext, _ = treasury_client
    async with client as c:
        body = (await c.get("/health")).json()
        assert body["service"] == "treasury"
        assert body["treasury_pubkey"]
        assert len(body["external_verifiers"]) == 1


@pytest.mark.asyncio
async def test_authorize_pays_valid_finding(treasury_client, tmp_path):
    client, ext, keydir = treasury_client
    from momus.findings import FindingSigner
    scanner = FindingSigner(keydir + "/scanner.key")
    v1 = FindingSigner(keydir + "/v1.key")
    f = _finding(scanner, "high")
    async with client as c:
        # treasury pubkey must differ from scanner pubkey
        h = (await c.get("/health")).json()
        assert h["treasury_pubkey"] != scanner.pubkey
        payload = {"finding": dataclasses.asdict(f),
                   "verdicts": [dataclasses.asdict(_confirm(ext, f)), dataclasses.asdict(_confirm(v1, f))],
                   "deposit_posted_usd": 25}

        # An UNFUNDED treasury must HOLD, not invent money — the vault is the balance.
        held = (await c.post("/authorize", json=payload)).json()
        assert held["state"] == "held", held["reasons"]
        assert "vault refused" in held["settlement"]["reason"]

        # Fund it and reserve this bounty's pool, then the same claim pays.
        assert (await c.post("/vault/fund", json={"amount_usd": 200.0})).json()["state"]["balance_usd"] == 200.0
        res = (await c.post("/vault/reserve",
                            json={"finding_id": f.finding_id, "amount_usd": 50.0})).json()
        assert res["reserved"] is True
        dec = (await c.post("/authorize", json=payload)).json()
        assert dec["state"] == "paid", dec["reasons"]
        assert dec["amount_usd"] == 50.0
        assert dec["signature"]["value"]  # signed by the treasury key
        # the money actually left the vault
        vs = (await c.get("/vault")).json()
        assert vs["balance_usd"] == 150.0 and vs["available_usd"] == 150.0


@pytest.mark.asyncio
async def test_authorize_refuses_tampered_finding(treasury_client, tmp_path):
    client, ext, keydir = treasury_client
    from momus.findings import FindingSigner
    scanner = FindingSigner(keydir + "/scanner.key")
    v1 = FindingSigner(keydir + "/v1.key")
    f = _finding(scanner, "high")
    bad = dataclasses.asdict(f)
    bad["severity"] = "critical"  # not re-signed
    async with client as c:
        payload = {"finding": bad,
                   "verdicts": [dataclasses.asdict(_confirm(ext, f)), dataclasses.asdict(_confirm(v1, f))],
                   "deposit_posted_usd": 100}
        dec = (await c.post("/authorize", json=payload)).json()
        assert dec["state"] == "refused"


@pytest.mark.asyncio
async def test_deposit_forfeit_endpoint(treasury_client, tmp_path):
    client, ext, keydir = treasury_client
    from momus.findings import FindingSigner, Verdict, finding_digest
    scanner = FindingSigner(keydir + "/scanner.key")
    v1 = FindingSigner(keydir + "/v1.key")
    f = _finding(scanner, "high")
    def refute(vs):
        return vs.sign_verdict(Verdict(f.finding_id, finding_digest(f), "refuted", "replay", 0.9, "r", vs.pubkey[:6], subject_target=f.target, subject_probe=f.probe))
    async with client as c:
        payload = {"finding": dataclasses.asdict(f),
                   "verdicts": [dataclasses.asdict(refute(ext)), dataclasses.asdict(refute(v1))],
                   "deposit_posted_usd": 25}
        rule = (await c.post("/deposit", json=payload)).json()
        assert rule["ruling"] == "forfeit"


@pytest.mark.asyncio
async def test_ledger_records_decisions(treasury_client, tmp_path):
    client, ext, keydir = treasury_client
    from momus.findings import FindingSigner
    scanner = FindingSigner(keydir + "/scanner.key")
    v1 = FindingSigner(keydir + "/v1.key")
    f = _finding(scanner, "high")
    async with client as c:
        payload = {"finding": dataclasses.asdict(f),
                   "verdicts": [dataclasses.asdict(_confirm(ext, f)), dataclasses.asdict(_confirm(v1, f))],
                   "deposit_posted_usd": 25}
        await c.post("/authorize", json=payload)
        led = (await c.get("/ledger")).json()
        assert led["count"] >= 1


def test_the_vault_is_attached_when_the_sandbox_rail_can_be_reached(monkeypatch, tmp_path):
    """Regression. The vault used to be attached only when the tier IS UNI, so a BASE tier that
    fell back to the sandbox paid with no balance behind it — a simulated treasury paying for ever,
    which is the exact failure the vault exists to prevent."""
    from momus.settlement import SettlementMode

    monkeypatch.setenv("TREASURY_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TREASURY_VAULT_PATH", str(tmp_path / "vault.jsonl"))
    monkeypatch.setenv("AIFACTORY_CRYPTO_ENABLED", "1")
    monkeypatch.setenv("MOMUS_BOUNTY_ONCHAIN", "1")
    monkeypatch.setenv("MOMUS_SETTLEMENT", "base")
    monkeypatch.setenv("MOMUS_BOUNTY_SPLITTER", "0x" + "cd" * 20)
    monkeypatch.setenv("MOMUS_REWARD_FALLBACK", "sandbox")

    from momus.settlement import SettlementBackend
    from momus.vault import UniVault

    settlement = SettlementBackend.from_env(crypto_enabled=True)
    assert settlement.mode is SettlementMode.BASE          # a real tier…
    vault = UniVault(str(tmp_path / "vault.jsonl"))
    from momus.settlement import FALLBACK_SANDBOX

    reaches = (settlement.mode is SettlementMode.UNI
               or settlement.fallback == FALLBACK_SANDBOX)
    assert reaches, "a BASE tier with the sandbox fallback still reaches the sandbox rail"

    # …and with the vault attached, the fallback must be REFUSED when the balance is empty.
    settlement.vault = vault
    d = settlement.settle_share(finding_id="mom-1", role="finder", recipient="0xr",
                                amount_usd=50.0)
    assert d.settled is False and "refused" in d.reason


def test_a_held_fallback_leaves_the_vault_off(monkeypatch, tmp_path):
    """The other direction: with the stalling fallback the sandbox rail is unreachable, so a vault
    would never be consulted and attaching one would be misleading."""
    monkeypatch.setenv("AIFACTORY_CRYPTO_ENABLED", "1")
    monkeypatch.setenv("MOMUS_BOUNTY_ONCHAIN", "1")
    monkeypatch.setenv("MOMUS_SETTLEMENT", "base")
    monkeypatch.setenv("MOMUS_BOUNTY_SPLITTER", "0x" + "cd" * 20)
    monkeypatch.setenv("MOMUS_REWARD_FALLBACK", "held")

    from momus.settlement import FALLBACK_SANDBOX, SettlementBackend, SettlementMode

    s = SettlementBackend.from_env(crypto_enabled=True)
    assert s.mode is SettlementMode.BASE and s.fallback != FALLBACK_SANDBOX
    assert not (s.mode is SettlementMode.UNI or s.fallback == FALLBACK_SANDBOX)
