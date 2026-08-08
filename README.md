<!-- aicom-mirror-notice -->
> **📖 Read-only mirror.** `treasury` is published from the canonical AI-Factory monorepo.
> **Pull requests are not accepted** — any commit pushed here is overwritten by
> `scripts/mirror_satellites.sh` on the next sync.
> 🐞 Found a bug or have a request? Please **[open an issue](https://github.com/alexar76/treasury/issues)**.

# Treasury

<!-- aicom-readme-badges -->
<p align="center">
  <a href="https://github.com/alexar76/treasury/actions/workflows/ci.yml"><img src="https://raw.githubusercontent.com/alexar76/treasury/main/docs/badges/ci.svg" alt="CI" /></a>
  <a href="https://alexar76.github.io/treasury/"><img src="https://raw.githubusercontent.com/alexar76/treasury/main/docs/badges/landing.svg" alt="Landing" /></a>
  <a href="https://github.com/alexar76/momus"><img src="https://raw.githubusercontent.com/alexar76/treasury/main/docs/badges/momus.svg" alt="Pays MOMUS findings" /></a>
  <img src="https://raw.githubusercontent.com/alexar76/treasury/main/docs/badges/python.svg" alt="Python >=3.11" />
  <img src="https://raw.githubusercontent.com/alexar76/treasury/main/docs/badges/docker.svg" alt="Docker ready" />
  <img src="https://raw.githubusercontent.com/alexar76/treasury/main/docs/badges/separation.svg" alt="Separation of duties" />
  <a href="https://github.com/alexar76/treasury/blob/main/LICENSE"><img src="https://raw.githubusercontent.com/alexar76/treasury/main/docs/badges/license.svg" alt="License: MIT" /></a>
</p>
<!-- /aicom-readme-badges -->


**MOMUS Treasury** — the separate payer for red-team bounties.

[MOMUS](https://github.com/alexar76/momus) finds and Ed25519-signs findings. **This service** holds the only key that can release a bounty, and only after independent verification. Different container, different volume, different trust boundary.

| | |
|---|---|
| **Role** | Payout gate for MOMUS findings |
| **Port** | `:9401` |
| **Package** | `aimarket-treasury` |
| **Landing** | [alexar76.github.io/treasury](https://alexar76.github.io/treasury/) |
| **Sibling** | [alexar76/momus](https://github.com/alexar76/momus) |

## Run (monorepo)

```bash
# from monorepo root
docker build -f treasury/Dockerfile -t momus-treasury .
docker run --rm -p 9401:9401 -v treasury-keys:/keys momus-treasury
```

Standalone GitHub mirror vendors `vendor/oracle-core` and `vendor/momus` at publish time — see `Dockerfile.standalone`.

## Why separate

If the auditor could pay itself, signed findings would not be a meaningful control. Treasury exists so that **finding ≠ payment**.

MIT · part of the [AICOM / AIMarket](https://magic-ai-factory.com/) ecosystem.
