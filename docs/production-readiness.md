# Production readiness and compliance gates

## Data classification

The application may process identity, tax-residency, ownership, PEP/HIO and account-opening records. Treat all case data as Confidential; identity documents and tax identifiers are Restricted.

- Never write document contents, tax identifiers, identity numbers, access tokens, or signed URLs to logs.
- Store file bytes in a private Azure Storage account with customer-managed keys where FCC policy requires them. The database stores metadata and opaque object keys only.
- Upload through short-lived scoped URLs. Quarantine new objects, verify declared type and magic bytes, scan for malware, then move to an immutable accepted container.
- Define retention and legal-hold rules with Privacy, Records Management and Compliance before launch. Deletion must cover primary, replica, backup and object versions.

## Identity and authorization

- Replace mock authentication with Entra ID OIDC token validation against the FCC tenant, including issuer, audience, signature, expiry and nonce/state validation.
- Map approved Entra groups to Advisor, Operations, Compliance and Admin roles. Enforce case access in the API, never only in the UI.
- Require MFA and Conditional Access. Use managed identity between workloads and Key Vault; do not issue static production credentials.
- Quarterly access review and immediate offboarding are launch requirements.

## Audit and operations

- Preserve actor, action, case, timestamp, previous value, next value and correlation ID. Export audit logs to an access-controlled, immutable sink.
- Alert on repeated authorization failures, bulk exports, malware detections, unusual document access and rule publication.
- Establish RPO/RTO, tested restore procedures, incident response ownership and privacy-breach escalation.
- Run SAST, dependency scanning, secret scanning, container scanning and penetration testing before launch.

## Rules that require Compliance approval

The demonstrator currently infers requirements from `account-opening-complex-entity-clients.md` and screenshots. The following are explicitly unapproved:

1. Exact beneficial-owner and controlling-person treatment by entity type and jurisdiction.
2. When ownership percentages must total 100% versus when control-only disclosure is valid.
3. PEP/HIO escalation, evidence, approval and expiry.
4. FATCA/CRS classification, W-8/W-9/RC519 selection and Passive NFFE logic.
5. WI versus PI form variants and account-feature conditions.
6. Trusted Contact applicability to non-individual clients.
7. Document freshness, certification, signature and identity-verification rules.

Each approved rule must have an owner, source URL/document ID, effective date, expiry/review date, tests and a four-eyes publication workflow. Existing cases must retain the rule version under which their checklist was generated.

## Deployment baseline

- Azure Canada Central primary; private endpoints for PostgreSQL, Storage and Key Vault.
- Web Application Firewall, TLS 1.2+, egress allow-list, managed DDoS controls and no public database endpoint.
- Separate development, test and production subscriptions/resource groups and identities.
- Application Insights/OpenTelemetry with PII redaction and Canadian data residency.
- Blue/green or canary deployment with database migration approval and tested rollback.

## Integrations not yet implemented

- Entra tenant configuration and production token validation.
- uDirect/uniFide submission, NAAF/Resolution PDF population, authoritative form links.
- Antivirus/content-disarm pipeline and Azure Blob signing adapter.
- Customer portal, OCR/document extraction and LLM assistant.

These require FCC-owned credentials, network access, data-processing approval and vendor/security assessment.
