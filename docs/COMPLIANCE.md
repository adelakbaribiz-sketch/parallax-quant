# Compliance — Read This Before Taking Any Real Investor Capital

## The single most important sentence in this document

**Nothing in this repository makes a fund legally compliant, licensed, or
authorized to take investor money.** `compliance/aml_kyc.py`,
`compliance/audit_log.py`, and the fee-calculation fields in
`config.ComplianceConfig` are technical scaffolding for a *real*
compliance program to plug into. They are not the program itself.

## What actually makes an investment vehicle legal

This is jurisdiction-specific and requires licensed professionals, not
code:

- **Legal entity formation** — a private fund, an asset-management
  entity, or another structure appropriate to your jurisdiction (the
  original specification named Cayman, BVI, and Europe as examples;
  each has materially different requirements)
- **Securities/investment-adviser registration** — with the relevant
  regulator (SEC/CFTC in the US, FCA in the UK, CIMA in the Cayman
  Islands, national regulators in the EU, etc.) depending on where you
  operate and who your investors are
- **A licensed administrator and auditor** for the fund's NAV and
  financial statements
- **Actual AML/KYC procedures**, run by a designated compliance officer,
  not just an API call to an identity-verification vendor
- **Ongoing regulatory filings** appropriate to your registration

Engage a securities/funds attorney in your target jurisdiction before
taking a single dollar of outside capital. This is not optional and this
document is not legal advice.

## What the compliance module in this codebase actually does

`compliance/aml_kyc.py` defines an `AmlKycProvider` interface with a
`NoOpProvider` default (always returns `NEEDS_REVIEW` — it never silently
approves anything) and unimplemented `JumioProvider`/`OnfidoProvider`
stubs that raise `NotImplementedError` until someone wires them up to
the real vendor API with real credentials.

`compliance/audit_log.py` writes an append-only, timestamped record of
every order, risk decision, and compliance check to Postgres, retained
for `audit_log_retention_days` (default ~7 years — confirm the actual
requirement for your license). This is useful infrastructure *for* an
audit trail; it does not constitute one on its own without a real
compliance process generating meaningful entries.

`config.ComplianceConfig`'s `performance_fee_pct` (default 20%),
`management_fee_pct_annual` (default 2%), and `high_water_mark` (default
true) are calculation parameters for a standard 2-and-20 fee structure
with a high-water mark — implementing the *arithmetic* correctly is not
the same as having a fee structure that's properly disclosed to
investors in a legally binding fund document.

## Suspicious transaction threshold

`suspicious_transaction_usd_threshold` (default $10,000) is a
config-driven flag point matching a commonly-cited reporting threshold
in several jurisdictions — verify the actual threshold and reporting
obligation for your license before relying on this default for anything.

## If you're reading this because you want to actually launch a fund

Stop here and talk to a lawyer first. Come back to this codebase for the
execution/risk engineering after the legal structure exists, not before.
