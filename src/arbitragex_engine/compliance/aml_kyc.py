"""Pluggable AML/KYC provider interface — TECHNICAL SCAFFOLDING ONLY.

Implementing this interface against Jumio/Onfido's real API does not, by
itself, make a fund AML/KYC-compliant. Compliance requires: a written
AML program, a designated compliance officer, actual regulatory
registration in your jurisdiction, and ongoing regulatory filings. This
module exists so an actual compliance program has somewhere to plug in
its verification calls and get consistent audit-trail logging — it is not
a substitute for that program. See docs/COMPLIANCE.md.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum


class VerificationStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_REVIEW = "needs_review"


@dataclass
class VerificationResult:
    provider: str
    subject_id: str
    status: VerificationStatus
    raw_response_reference: str | None = None  # store a reference/ID, never raw PII in logs


class AmlKycProvider(ABC):
    @abstractmethod
    async def verify_identity(self, subject_id: str, document_reference: str) -> VerificationResult: ...

    @abstractmethod
    async def screen_transaction(self, subject_id: str, amount_usd: float, counterparty: str) -> VerificationResult: ...


class NoOpProvider(AmlKycProvider):
    """Default when compliance.aml_kyc_provider = "none". Always returns
    NEEDS_REVIEW so a real transaction can never silently pass through an
    unconfigured compliance check."""

    async def verify_identity(self, subject_id: str, document_reference: str) -> VerificationResult:
        return VerificationResult(provider="none", subject_id=subject_id, status=VerificationStatus.NEEDS_REVIEW)

    async def screen_transaction(self, subject_id: str, amount_usd: float, counterparty: str) -> VerificationResult:
        return VerificationResult(provider="none", subject_id=subject_id, status=VerificationStatus.NEEDS_REVIEW)


class JumioProvider(AmlKycProvider):
    """Real integration point — NOT implemented. Wire this up against
    Jumio's actual API and your account credentials before using
    compliance.aml_kyc_provider: jumio in config.yaml."""

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    async def verify_identity(self, subject_id: str, document_reference: str) -> VerificationResult:
        raise NotImplementedError("Wire up Jumio's real verification API here before enabling this provider.")

    async def screen_transaction(self, subject_id: str, amount_usd: float, counterparty: str) -> VerificationResult:
        raise NotImplementedError("Wire up Jumio's real transaction-screening API here before enabling this provider.")


class OnfidoProvider(AmlKycProvider):
    """Real integration point — NOT implemented. Same caveat as JumioProvider."""

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    async def verify_identity(self, subject_id: str, document_reference: str) -> VerificationResult:
        raise NotImplementedError("Wire up Onfido's real verification API here before enabling this provider.")

    async def screen_transaction(self, subject_id: str, amount_usd: float, counterparty: str) -> VerificationResult:
        raise NotImplementedError("Wire up Onfido's real transaction-screening API here before enabling this provider.")


def build_aml_kyc_provider(provider_name: str, api_key: str | None) -> AmlKycProvider:
    if provider_name == "none":
        return NoOpProvider()
    if provider_name == "jumio":
        return JumioProvider(api_key or "")
    if provider_name == "onfido":
        return OnfidoProvider(api_key or "")
    raise ValueError(f"Unknown aml_kyc_provider '{provider_name}'")
