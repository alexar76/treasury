"""Advisory decision explainer — DeepSeek by default, and NEVER in the money path.

The operator asked the Treasury to use the ecosystem's DeepSeek V4 Pro too. The Treasury's
*authorization* deliberately has no LLM in it — money must never depend on model output, which is
the whole security property the adversarial review hardened. So the model gets exactly one job
here: after a decision has ALREADY been made by the deterministic gate, narrate *why* in plain
language for the audit tail. The explainer:

  * receives the finished Decision (state, reasons, verifier set) — never the raw finding text,
    so there is no untrusted-content sink and nothing to inject through;
  * cannot change the decision — it runs after `authorize()` returns and its output is stored as
    an advisory note, tagged as such;
  * defaults to DeepSeek V4 Pro via momus.providers, and to the offline deterministic summary when
    no model is configured, so it always produces something and never blocks a payout on a model.
"""

from __future__ import annotations

import os
from typing import Any

from momus.providers import LLMConfig, Message, create_provider

_SYSTEM = (
    "You are an audit-note writer for a bounty treasury. You are given a FINISHED, immutable "
    "payout decision (already made by a deterministic policy engine). Write 1-3 plain sentences "
    "explaining the decision to a human auditor. Do not question or change the decision; it is "
    "final. Do not output anything except the explanation."
)


def _default_llm_config() -> LLMConfig:
    # Treasury's advisory model defaults to DeepSeek V4 Pro, overridable with the same MOMUS_LLM_*
    # env the scanner uses (or TREASURY_LLM_* to diverge).
    provider = (os.environ.get("TREASURY_LLM_PROVIDER")
                or os.environ.get("MOMUS_LLM_PROVIDER") or "deepseek")
    os.environ.setdefault("MOMUS_LLM_PROVIDER", provider)
    return LLMConfig.from_env()


def _deterministic_note(decision: dict[str, Any]) -> str:
    state = decision.get("state")
    amt = decision.get("amount_usd", 0)
    n = len(decision.get("distinct_verifiers") or [])
    if state == "paid":
        return f"Paid ${amt}: the finding was scanner-signed and independently confirmed by {n} distinct verifier(s)."
    if state == "held":
        return f"Held as intent (${amt}): all gates passed but real settlement is disabled (crypto off)."
    reasons = decision.get("reasons") or []
    return "Refused: " + (reasons[-1] if reasons else "gate conditions not met") + "."


async def explain_decision(decision: dict[str, Any]) -> dict[str, Any]:
    """Return an advisory note about a FINISHED decision. Always tagged advisory; never authoritative."""
    fallback = _deterministic_note(decision)
    try:
        provider = create_provider(_default_llm_config())
        kind = getattr(provider, "kind", None)
        if kind is not None and str(getattr(kind, "value", "")) == "offline":
            note, source = fallback, "deterministic"
        else:
            summary = {
                "state": decision.get("state"), "amount_usd": decision.get("amount_usd"),
                "severity": decision.get("severity"),
                "distinct_verifiers": len(decision.get("distinct_verifiers") or []),
                "reasons": decision.get("reasons"),
            }
            text = await provider.complete(
                [Message("system", _SYSTEM), Message("user", str(summary))],
                temperature=0.0, max_tokens=200)
            note = (text or "").strip() or fallback
            source = f"llm:{getattr(provider, 'model', '?')}"
            await provider.aclose()
    except Exception:  # noqa: BLE001 - the explainer must never break a decision
        note, source = fallback, "deterministic"
    return {"note": note, "source": source, "advisory": True,
            "disclaimer": "Advisory only — generated AFTER the decision; it did not influence authorization."}
