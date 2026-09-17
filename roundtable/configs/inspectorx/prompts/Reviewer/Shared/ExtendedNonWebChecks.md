# Extended Non-Web Check Catalog (Use As Checklist, Scope Per Profiler)

This catalog expands beyond the Top 10 for backend + client code (no web/mobile). Use it as a checklist, but only run checks relevant to the Profiler's data flow map, attack surface, and inversion scenarios. If a category is out of scope, explicitly mark it as skipped in your Summary Header under Attack Surface Tested.

## Scoping Rule (MANDATORY)

```
For each check:
├─► Is the input/source/sink present in the Profiler's data flow map?
│   ├─► NO → Skip (out of scope)
│   └─► YES → Test and report
└─► If profiler highlights a unique pattern → add extra checks specific to it
```

## Access Control & Authorization
- Enforce server-side authorization on all privileged actions.
- Prevent IDOR across APIs, IPC, and local data stores.
- Default-deny policy evaluation; no implicit allow.
- Ensure least-privilege for service accounts and background jobs.
- Admin/debug endpoints gated and disabled in production.

## Authentication & Identity
- MFA for admin and service-owner access.
- Brute-force resistance (rate limit, lockout, IP throttling).
- Session/token revocation on password change or disable.
- No hard-coded, shared, or default credentials.
- Passwordless or strong password policy enforcement.

## Secrets & Key Management
- Secrets stored in vault/OS secret store, not in repo or config.
- Keys rotated on schedule and on personnel changes.
- Secrets never logged, dumped, or sent in telemetry.
- Separate keys per environment and per tenant (if multi-tenant).
- Private keys protected by filesystem ACLs and passphrase.

## Cryptography
- Approved algorithms only (AES-GCM, ChaCha20-Poly1305).
- Minimum key sizes; no legacy ciphers.
- TLS 1.2+ for all network links; verify hostnames.
- CSPRNG for tokens, keys, and nonces.
- Crypto config centralized and versioned.

## Data Protection & Privacy
- Encryption at rest for sensitive data.
- Data minimization and secure deletion policies.
- PII masked or tokenized in logs and telemetry.
- Audit trails for sensitive data access.
- Backup encryption and secure restore validation.

## Input Validation & Injection (Non-Web)
- Validate all external inputs: files, IPC, CLI args, env vars.
- Prevent command injection in process/spawn usage.
- Parameterize SQL/NoSQL queries; no string concatenation.
- Validate file paths to prevent traversal.
- Harden parsers for JSON/YAML/XML/CSV and hostile payloads.

## Dependency & Supply Chain
- Dependency pinning; verify checksums/signatures.
- Block known vulnerable versions via SCA gating.
- Trusted build tools and reproducible builds.
- Detect malicious packages and typosquatting.
- Restrict dynamic plugin/module loading.

## Build, Release & Update
- Signed builds and verified update packages.
- Update integrity checks and rollback protection.
- Separation of build and signing roles.
- No debug symbols or verbose logs in production artifacts.
- Update channels authenticated and tamper-resistant.

## Logging, Monitoring & Response
- Log auth events, privilege changes, and policy failures.
- Detect anomalous process behavior and integrity failures.
- Logs are tamper-evident and access-controlled.
- Security alerting for abnormal patterns.
- Incident response runbooks and drills exist.

## Configuration & Hardening
- Secure default configuration; no insecure defaults.
- Disable unused services, ports, and features.
- Least-privilege file permissions.
- OS hardening baselines enforced.
- Secure temporary file handling (no predictable names).

## Process & Memory Safety
- Memory-safe languages or compiler hardening flags used.
- ASLR/DEP/stack canaries/PIE enabled.
- Bounds checks for buffers and arrays.
- Safe parsing libraries for untrusted formats.
- Sandboxing for risky data processing.

## Network Security
- mTLS for service-to-service when possible.
- Certificate validation and hostname verification.
- Rate limiting and circuit breakers for APIs.
- Restrict outbound egress to approved destinations.
- Service segmentation by trust level.

## Integrity & Tamper Resistance
- Binary integrity checks at startup and during updates.
- Configuration protected with signatures or hashes.
- Detect runtime code injection or module tampering.
- Secure boot or platform attestation where available.
- Guard against time/date spoofing for security logic.

## Client Code (Desktop/Agent)
- Protect local storage using OS APIs.
- Validate IPC endpoints to prevent local privilege abuse.
- Prevent local file injection via untrusted paths.
- Harden auto-update and plugin systems.
- Crash reports exclude secrets and PII.

## Testing & Governance
- Threat models exist for high-risk components.
- CI includes SAST, SCA, and fuzzing.
- Periodic red-team exercises with tracked fixes.
- Security review required for high-risk changes.
- Security debt tracked with SLAs.
