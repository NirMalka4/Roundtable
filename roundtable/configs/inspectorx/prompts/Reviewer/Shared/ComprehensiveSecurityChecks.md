# Comprehensive Security Checks Catalog (320 Checks)

> 320 checks for backend services, client agents, data pipelines, and cloud-native microservices.
> Derived from analysis of C# .NET services, Rust native clients, Azure cloud infrastructure,
> Kubernetes deployments, IPC/FFI boundaries, and data processing pipelines across the TVM ecosystem.

## How to Use

This catalog is the **reference checklist** for the AttackSurfaceScanner agent. For each review:

1. Read the Profiler's data flow map and attack surface summary.
2. Walk each category below. If the category is relevant, evaluate every check in it.
3. If a category is entirely out of scope, skip it and note "N/A" in your summary.
4. If the Profiler highlights a unique pattern not covered here, add ad-hoc checks.

Each check has a unique ID (`SEC-XXX`) and a short title stating what to verify.

---

## 1. Authentication & Identity (SEC-001 – SEC-015)

| ID | Check |
| ---- | ------- |
| SEC-001 | Every API endpoint enforces authentication before processing |
| SEC-002 | Token validation checks issuer, audience, expiry, and signature |
| SEC-003 | Service-to-service calls use managed identity or certificate auth, not shared secrets |
| SEC-004 | Authentication middleware is registered early in the pipeline (before routing) |
| SEC-005 | Failed authentication returns generic errors, not stack traces or internal details |
| SEC-006 | Token refresh endpoints are rate-limited |
| SEC-007 | Service accounts use least-privilege scopes in their token claims |
| SEC-008 | Multi-factor authentication is enforced for admin and break-glass accounts |
| SEC-009 | Session/token revocation is immediate on password change, account disable, or role change |
| SEC-010 | No hard-coded credentials, API keys, or connection strings in source code |
| SEC-011 | Authentication tokens are not logged, even at debug/trace level |
| SEC-012 | JWT `alg: none` and HMAC/RSA confusion attacks are explicitly blocked |
| SEC-013 | Client certificate validation checks the full chain, not just the leaf |
| SEC-014 | Bearer tokens are transmitted only over TLS and never in query strings |
| SEC-015 | Health check and readiness probe endpoints do not require auth but expose no sensitive data |

## 2. Authorization & Access Control (SEC-016 – SEC-035)

| ID | Check |
| ---- | ------- |
| SEC-016 | Every mutation endpoint enforces authorization after authentication |
| SEC-017 | Authorization checks use server-side claims/roles, not client-supplied values |
| SEC-018 | Object-level authorization prevents IDOR (accessing another tenant's data via ID manipulation) |
| SEC-019 | Default-deny policy: endpoints without explicit rules reject requests |
| SEC-020 | Admin/debug/diagnostic endpoints are disabled or gated in production configurations |
| SEC-021 | Tenant context (OrgId) is extracted from the authenticated token, not from request parameters |
| SEC-022 | Batch/bulk endpoints enforce per-item authorization, not just per-request |
| SEC-023 | API gateway routes enforce authorization before proxying to downstream services |
| SEC-024 | Feature flags that gate authorization are evaluated server-side with tamper-proof state |
| SEC-025 | Role hierarchy is enforced (e.g., reader cannot escalate to admin via API parameter) |
| SEC-026 | Org-level settings changes require org-admin authorization, not just any authenticated user |
| SEC-027 | Export/download endpoints check data classification before returning results |
| SEC-028 | Background jobs and scheduled tasks run under dedicated service accounts, not user context |
| SEC-029 | Orchestrator/dispatcher verifies caller authorization before queuing work |
| SEC-030 | Remediation actions (create task, create ticket) verify the caller has remediation permissions |
| SEC-031 | Notification/feedback endpoints verify the caller owns the resource being referenced |
| SEC-032 | Cache keys include tenant context to prevent cross-tenant cache poisoning |
| SEC-033 | Rate limiting is per-tenant, not only global, to prevent noisy-neighbor abuse |
| SEC-034 | Impersonation/delegation flows are audit-logged and time-bounded |
| SEC-035 | Service mesh policies restrict which services can call which endpoints |

## 3. Input Validation & Injection (SEC-036 – SEC-060)

| ID | Check |
| ---- | ------- |
| SEC-036 | All API inputs are validated against a schema (type, range, length, pattern) |
| SEC-037 | Query parameters used in database queries are parameterized (no string concatenation) |
| SEC-038 | Kusto/ADX queries use parameterized `declare query_parameters` instead of string interpolation |
| SEC-039 | Cosmos DB queries use parameterized `SqlQuerySpec`, not string formatting |
| SEC-040 | File paths from external input are canonicalized and validated against an allowlist |
| SEC-041 | JSON/XML/YAML deserializers have depth and size limits configured |
| SEC-042 | XML parsing disables external entity resolution (XXE prevention) |
| SEC-043 | Regex patterns used on untrusted input are anchored and tested for ReDoS |
| SEC-044 | Command/process spawning never passes unsanitized input to shell interpreters |
| SEC-045 | IPC messages (named pipes, Unix sockets) validate message type and size before processing |
| SEC-046 | FFI/extern boundaries validate all pointer arguments and lengths before dereferencing |
| SEC-047 | Deserialization of untrusted data uses safe deserializers (no `BinaryFormatter`, no `pickle`) |
| SEC-048 | Protobuf/MessagePack/Avro schemas enforce required fields and value constraints |
| SEC-049 | HTTP headers from external sources are sanitized before use in logging or downstream requests |
| SEC-050 | Blob/object names from external input are validated for length and illegal characters |
| SEC-051 | Enum values from external input are validated against known variants (no default fallthrough) |
| SEC-052 | Integer inputs are checked for overflow, underflow, and negative-when-positive-expected |
| SEC-053 | String inputs are checked for null bytes, control characters, and encoding issues |
| SEC-054 | Collection/array inputs have bounded size to prevent memory exhaustion |
| SEC-055 | gRPC/WCF service inputs are validated at the service boundary, not deferred to business logic |
| SEC-056 | Registry keys/values read from the OS are validated before use in logic |
| SEC-057 | Environment variables used in security decisions are validated and from trusted sources |
| SEC-058 | Glob/wildcard patterns from external input are bounded to prevent excessive filesystem enumeration |
| SEC-059 | User-supplied sort/filter/order-by fields are allowlisted, not passed through to queries |
| SEC-060 | Content-Type headers are validated and enforced, not just trusted from the client |

## 4. Secrets & Key Management (SEC-061 – SEC-075)

| ID | Check |
| ---- | ------- |
| SEC-061 | Connection strings, API keys, and certificates are stored in Key Vault or Managed HSM |
| SEC-062 | Key rotation happens on schedule and during incident response |
| SEC-063 | Secrets are never written to application logs, telemetry, or crash dumps |
| SEC-064 | Configuration files that reference secrets use Key Vault references, not literal values |
| SEC-065 | Service principal credentials use certificate-based auth, not client secrets |
| SEC-066 | Each environment (dev, staging, prod) uses distinct keys and certificates |
| SEC-067 | Multi-tenant services use per-tenant keys for data encryption where required |
| SEC-068 | Private keys and certificates have restrictive file permissions (600 or equivalent ACLs) |
| SEC-069 | Key Vault access policies follow least-privilege (get-only for apps, no list/delete) |
| SEC-070 | Secrets passed via environment variables are not visible in `/proc` or process listings |
| SEC-071 | Build pipelines do not bake secrets into container images or build artifacts |
| SEC-072 | Secret scanning is enabled in the repo to prevent accidental commits |
| SEC-073 | Symmetric encryption keys are at least 256 bits; RSA keys are at least 2048 bits |
| SEC-074 | TLS certificates are auto-renewed before expiry with monitoring alerts |
| SEC-075 | Secrets in Kubernetes are stored as Kubernetes Secrets or CSI driver mounts, not ConfigMaps |

## 5. Cryptography (SEC-076 – SEC-090)

| ID | Check |
| ---- | ------- |
| SEC-076 | Only approved algorithms are used (AES-GCM, ChaCha20-Poly1305 for symmetric; RSA-OAEP, ECDSA for asymmetric) |
| SEC-077 | No custom cryptographic implementations; use OS/framework-provided libraries |
| SEC-078 | Random values for tokens, nonces, and IVs use CSPRNG (not `Random`/`rand`) |
| SEC-079 | TLS 1.2 or higher is enforced for all network connections; TLS 1.0/1.1 is disabled |
| SEC-080 | Certificate pinning or CA restriction is used for critical service-to-service connections |
| SEC-081 | IV/nonce is never reused with the same key |
| SEC-082 | HMAC or AEAD is used for integrity verification; plain hashing (SHA256 alone) is not used to authenticate data |
| SEC-083 | Hashing of passwords uses bcrypt, scrypt, or Argon2 with per-user salt, not SHA/MD5 |
| SEC-084 | Crypto configuration is centralized in shared libraries, not scattered across services |
| SEC-085 | Encrypted data includes authenticated metadata (algorithm, key version) for migration |
| SEC-086 | Compression is not applied before encryption (CRIME/BREACH defense) |
| SEC-087 | Side-channel-resistant comparison is used for HMAC/hash verification (constant-time compare) |
| SEC-088 | Ephemeral keys (Diffie-Hellman) provide forward secrecy for session establishment |
| SEC-089 | Crypto error messages do not reveal whether decryption, authentication, or padding failed |
| SEC-090 | Key derivation functions (HKDF, PBKDF2) are used to derive subkeys from master keys |

## 6. Data Protection & Privacy (SEC-091 – SEC-110)

| ID | Check |
| ---- | ------- |
| SEC-091 | Sensitive data at rest (blobs, databases, queues) is encrypted using platform encryption |
| SEC-092 | PII fields are masked, tokenized, or redacted in logs and telemetry |
| SEC-093 | Data retention policies are enforced with automated deletion/archiving |
| SEC-094 | Data classification labels are applied to storage containers and databases |
| SEC-095 | Cross-border data transfer controls are enforced for regulated data |
| SEC-096 | Tenant data isolation is enforced at the storage layer (separate containers, partitions, or keys) |
| SEC-097 | Backup encryption uses keys managed independently from primary storage keys |
| SEC-098 | Secure deletion (crypto-shred or overwrite) is used for decommissioned data |
| SEC-099 | Export endpoints (CSV, Parquet, JSON) redact fields based on the caller's data classification level |
| SEC-100 | Scrubbed/anonymized data exports verify that the scrubbing is irreversible |
| SEC-101 | Telemetry and diagnostic data do not contain OrgId, DeviceId, or UserId unless explicitly approved |
| SEC-102 | Data flowing through Service Bus/Event Hub/Kafka is encrypted in transit |
| SEC-103 | Error responses do not leak internal data (stack traces, connection strings, internal IPs) |
| SEC-104 | Data normalization services strip or validate fields before persisting to downstream stores |
| SEC-105 | Assessment/vulnerability data includes provenance (source, timestamp, collection method) |
| SEC-106 | Parquet/columnar files are validated for schema conformance before ingestion |
| SEC-107 | KQL/ADX ingestion validates row count and schema before writing |
| SEC-108 | Data migration scripts handle encryption key transition (re-encrypt with new key) |
| SEC-109 | Soft-deleted data is inaccessible via normal APIs and purged within the defined retention window |
| SEC-110 | Audit logs record who accessed what data, when, and from where |

## 7. Service-to-Service Communication (SEC-111 – SEC-125)

| ID | Check |
| ---- | ------- |
| SEC-111 | All inter-service HTTP calls use HTTPS; plaintext HTTP is never used |
| SEC-112 | mTLS or managed identity is used for service-to-service authentication |
| SEC-113 | Server certificates are validated (hostname, expiry, chain) on every outbound call |
| SEC-114 | Retry policies include jitter and exponential backoff to prevent thundering herd |
| SEC-115 | Circuit breakers are configured on all outbound dependencies |
| SEC-116 | Timeouts are set on all outbound HTTP/gRPC calls |
| SEC-117 | Service Bus / Event Hub message handlers validate message schema before processing |
| SEC-118 | Dead-letter queues are monitored and processed securely |
| SEC-119 | Outbound egress is restricted to approved destinations (network policy or allowlist) |
| SEC-120 | API gateway does not forward internal headers (X-Internal-*, auth context) to external callers |
| SEC-121 | Idempotency keys/tokens are used for mutating cross-service calls |
| SEC-122 | Service discovery uses authenticated registries, not unauthenticated DNS/mDNS |
| SEC-123 | Webhook/callback URLs are validated against an allowlist before use |
| SEC-124 | gRPC metadata and HTTP headers are size-bounded to prevent header-flood DoS |
| SEC-125 | Cross-service correlation IDs are propagated but do not contain sensitive data |

## 8. Cloud Infrastructure & Azure (SEC-126 – SEC-145)

| ID | Check |
| ---- | ------- |
| SEC-126 | Storage accounts use private endpoints or service endpoints, not public access |
| SEC-127 | Cosmos DB accounts use AAD auth (RBAC) instead of primary/secondary keys where possible |
| SEC-128 | Blob containers default to private access; no anonymous/public read |
| SEC-129 | SAS tokens are generated with minimum scope, short expiry, and IP restrictions |
| SEC-130 | Azure Table Storage uses AAD-based RBAC, not Shared Key auth |
| SEC-131 | Key Vault uses private endpoints and network ACLs |
| SEC-132 | Azure Monitor diagnostic settings export to a secured Log Analytics workspace |
| SEC-133 | Resource locks (CanNotDelete) are applied to critical production resources |
| SEC-134 | Managed identities are used in preference to service principals with secrets |
| SEC-135 | Azure Policy enforces encryption, access restrictions, and tagging on all resources |
| SEC-136 | Activity logs and Azure Monitor alerts are configured for critical operations (key access, role assignment) |
| SEC-137 | Cosmos DB partition keys are designed to prevent hot partitions that cause throttling |
| SEC-138 | Kusto/ADX cluster access is restricted via AAD roles and IP firewall |
| SEC-139 | Event Hub/Service Bus namespaces use private endpoints and SAS with minimal claims |
| SEC-140 | Azure Container Registry images are signed and scanned for vulnerabilities before deployment |
| SEC-141 | Storage account encryption uses customer-managed keys (CMK) for regulated data |
| SEC-142 | Azure Functions/WebJobs use managed identity for all Azure resource access |
| SEC-143 | VNet integration is used for services that access private resources |
| SEC-144 | Azure SQL / Cosmos DB have geo-redundancy with replication lag monitoring |
| SEC-145 | Resource naming conventions do not expose environment, tenant, or internal structure |

## 9. Kubernetes & Container Security (SEC-146 – SEC-165)

| ID | Check |
| ---- | ------- |
| SEC-146 | Containers run as non-root user with read-only root filesystem |
| SEC-147 | Pod security standards (restricted) are enforced at the namespace level |
| SEC-148 | Resource limits (CPU, memory) are set on all containers |
| SEC-149 | Network policies restrict pod-to-pod communication to required paths only |
| SEC-150 | Images are pulled from private registries with image pull secrets, not public DockerHub |
| SEC-151 | Secrets are mounted via CSI driver or Kubernetes Secrets, never baked into images |
| SEC-152 | Service accounts have minimal RBAC and do not use the default cluster service account |
| SEC-153 | Pod-to-pod mTLS is enforced via service mesh (Istio/Linkerd) or network policy |
| SEC-154 | Liveness and readiness probes are configured correctly and do not expose debug info |
| SEC-155 | Horizontal Pod Autoscaler limits are bounded to prevent cost explosion |
| SEC-156 | Image tags use digests (sha256), not mutable tags like `latest` |
| SEC-157 | Init containers follow the same security context as main containers |
| SEC-158 | Kubernetes audit logging is enabled and exported to a SIEM |
| SEC-159 | Cluster nodes run hardened OS with automatic security patching |
| SEC-160 | Ingress controllers validate TLS termination and do not pass plaintext internally unless within mTLS mesh |
| SEC-161 | Helm charts and manifests are scanned for misconfigurations before deploy (kubesec, OPA) |
| SEC-162 | Pod disruption budgets are set to prevent total service loss during node maintenance |
| SEC-163 | Ephemeral storage limits are set to prevent disk exhaustion on nodes |
| SEC-164 | Sidecar containers (logging, proxy) follow the same security policy as the main workload |
| SEC-165 | ConfigMaps do not contain sensitive values; sensitive data uses Secrets or external vaults |

## 10. Process & Memory Safety — Rust/C/C++ (SEC-166 – SEC-185)

| ID | Check |
| ---- | ------- |
| SEC-166 | All `unsafe` blocks have a documented safety invariant comment |
| SEC-167 | FFI functions validate all pointer arguments for null and alignment before dereferencing |
| SEC-168 | FFI string conversions handle non-UTF-8 and embedded null bytes safely |
| SEC-169 | Buffer sizes at FFI boundaries are validated on both sides (caller and callee) |
| SEC-170 | Allocations freed across FFI use the same allocator on both sides |
| SEC-171 | Panic unwinding does not cross FFI boundaries (`catch_unwind` at FFI entry points) |
| SEC-172 | Integer casts between pointer-sized and fixed-width types check for truncation |
| SEC-173 | ASLR, DEP/NX, stack canaries, and PIE are enabled in build flags |
| SEC-174 | `.cargo/config.toml` or equivalent sets security-relevant linker flags for release builds |
| SEC-175 | `#[repr(C)]` structs used in FFI match the C header layout exactly (size, alignment, padding) |
| SEC-176 | Thread-safety analysis: Rust types crossing thread boundaries implement `Send`/`Sync` correctly |
| SEC-177 | Global mutable state is protected by `Mutex`, `RwLock`, or atomic operations |
| SEC-178 | Clippy lints for `unsafe` code are enabled and clean (`clippy::undocumented_unsafe_blocks`) |
| SEC-179 | Fuzzing harnesses exist for all parser/deserializer code that handles untrusted input |
| SEC-180 | Stack-allocated buffers for external data have fixed maximum sizes with overflow checks |
| SEC-181 | `Deref` and `Drop` implementations for types wrapping raw pointers are tested for double-free |
| SEC-182 | C headers included via `bindgen` are pinned to a specific version/commit |
| SEC-183 | Use-after-free is prevented by ownership discipline; raw pointers have lifetime documentation |
| SEC-184 | Signal handlers in native code are async-signal-safe (no heap allocation, no mutex) |
| SEC-185 | AddressSanitizer (ASan) and MemorySanitizer (MSan) are run in CI on native code |

## 11. Dependency & Supply Chain (SEC-186 – SEC-200)

| ID | Check |
| ---- | ------- |
| SEC-186 | Cargo.lock / packages.lock.json / package-lock.json is committed and reviewed |
| SEC-187 | Dependency versions are pinned (exact or bounded), not floating |
| SEC-188 | `cargo audit` / `dotnet list package --vulnerable` / `npm audit` runs in CI with blocking |
| SEC-189 | No `*` version ranges in dependency specs |
| SEC-190 | Transitive dependencies are reviewed for known vulnerabilities |
| SEC-191 | Private registry credentials are not stored in plaintext config files |
| SEC-192 | Build scripts (`build.rs`, `.csproj` pre/post-build) are reviewed for arbitrary code execution |
| SEC-193 | NuGet/npm/crate packages are verified against checksums or signatures |
| SEC-194 | Internal packages use private feeds with access controls |
| SEC-195 | Dependency confusion protection: internal package names are reserved on public registries |
| SEC-196 | Git dependencies (submodules, git sources in Cargo.toml) pin to commit hashes, not branches |
| SEC-197 | No `allow-scripts` or equivalent for post-install hooks on untrusted packages |
| SEC-198 | SBOM (Software Bill of Materials) is generated and stored for each release |
| SEC-199 | Container base images are pinned to digest and rebuilt periodically |
| SEC-200 | Third-party actions in CI pipelines are pinned to commit SHA, not branch/tag |

## 12. Logging, Monitoring & Incident Response (SEC-201 – SEC-220)

| ID | Check |
| ---- | ------- |
| SEC-201 | All authentication events (success, failure, lockout) are logged |
| SEC-202 | Authorization failures are logged with caller identity, resource, and action |
| SEC-203 | Privilege changes (role assignment, policy update) are logged immutably |
| SEC-204 | Logs are structured (JSON) with consistent schemas for automated parsing |
| SEC-205 | Log destinations are tamper-evident (append-only storage, WORM, or SIEM) |
| SEC-206 | Log access is restricted to security and ops teams via RBAC |
| SEC-207 | Alerting is configured for: repeated auth failures, privilege escalation, unusual data access patterns |
| SEC-208 | Metrics/health dashboards expose request rate, error rate, and latency per endpoint |
| SEC-209 | Security events are correlated across services using distributed trace IDs |
| SEC-210 | PII, secrets, and tokens are never logged even at DEBUG level |
| SEC-211 | Log volume is bounded (rate limiting on log writes) to prevent log-flood DoS |
| SEC-212 | Error handling paths log the error securely but return generic messages to callers |
| SEC-213 | Incident response runbooks exist for: secret leak, service compromise, data breach, DDoS |
| SEC-214 | Regular incident simulation drills are conducted (tabletop or live) |
| SEC-215 | Alerting thresholds are tuned to minimize false positives without creating blind spots |
| SEC-216 | Service startup and shutdown events are logged with version and config hash |
| SEC-217 | Background job execution is logged with start, end, duration, and outcome |
| SEC-218 | Log retention meets regulatory requirements (typically 90 days hot, 1 year archive) |
| SEC-219 | Security-critical logs are replicated to a secondary region |
| SEC-220 | Application Performance Monitoring (APM) traces are sampled securely without capturing sensitive request bodies |

## 13. Configuration & Hardening (SEC-221 – SEC-240)

| ID | Check |
| ---- | ------- |
| SEC-221 | Default configuration is secure; insecure options require explicit opt-in |
| SEC-222 | Debug mode, verbose logging, and developer tools are disabled in production config |
| SEC-223 | Configuration files are validated at startup; invalid config causes fail-fast, not silent fallback |
| SEC-224 | Environment-specific config (dev/staging/prod) is separated and access-controlled |
| SEC-225 | CORS policy is restrictive (specific origins), not `*` |
| SEC-226 | HTTP security headers are set: HSTS, X-Content-Type-Options, X-Frame-Options |
| SEC-227 | Rate limiting is configured per-endpoint based on expected traffic patterns |
| SEC-228 | Request body size limits are configured on all endpoints |
| SEC-229 | File upload endpoints validate file type, size, and scan for malware |
| SEC-230 | Temporary files use OS-provided secure temp directories with unpredictable names |
| SEC-231 | Unused ports, protocols, and services are disabled |
| SEC-232 | Filesystem permissions follow least-privilege (app user owns only required dirs) |
| SEC-233 | OS-level security baselines (CIS benchmarks) are enforced on production hosts |
| SEC-234 | Configuration changes are tracked in version control with approval workflows |
| SEC-235 | Feature flags are evaluated server-side; client cannot override flag values |
| SEC-236 | Graceful shutdown drains in-flight requests before terminating |
| SEC-237 | Startup probes prevent traffic routing to pods that are still initializing |
| SEC-238 | JSON serialization settings forbid `$type` / polymorphic deserialization by default |
| SEC-239 | Swagger/OpenAPI endpoints are disabled or restricted in production |
| SEC-240 | Health endpoints report status only; they do not trigger side effects or return sensitive data |

## 14. Concurrency & State Management (SEC-241 – SEC-260)

| ID | Check |
| ---- | ------- |
| SEC-241 | Shared mutable state is protected by locks, atomics, or actor patterns |
| SEC-242 | Lock ordering is consistent to prevent deadlocks |
| SEC-243 | Lock-held duration is minimized; no I/O or network calls while holding locks |
| SEC-244 | cache.GetOrAdd / ConcurrentDictionary patterns are checked for time-of-check-time-of-use (TOCTOU) |
| SEC-245 | Optimistic concurrency (ETags, row versions) is used for database updates |
| SEC-246 | Distributed locks (Redis, Cosmos lease) have TTL to prevent deadlock on holder failure |
| SEC-247 | Async code does not block with `.Result` or `.Wait()` (C#) / `block_on` in async context (Rust) |
| SEC-248 | SemaphoreSlim / throttling gates bound concurrent access to expensive resources |
| SEC-249 | Event-driven processing is idempotent: reprocessing the same event is safe |
| SEC-250 | Race conditions in feature flag evaluation are handled (flag change mid-request) |
| SEC-251 | Counter/metric increments use atomic operations, not read-modify-write |
| SEC-252 | Background job schedulers use distributed locking to prevent duplicate execution |
| SEC-253 | Thread-local and `AsyncLocal` storage is cleared at request boundaries |
| SEC-254 | HttpClient instances are reused (IHttpClientFactory), not created per-request |
| SEC-255 | Database connections are pooled with bounded maximum size and health checks |
| SEC-256 | Cancellation tokens are propagated through all async call chains |
| SEC-257 | Fire-and-forget tasks capture exceptions (unobserved task exceptions) |
| SEC-258 | Parallel.ForEach / rayon par_iter operations have partition size limits |
| SEC-259 | Database transaction scopes are as short as possible with explicit commit/rollback |
| SEC-260 | Event processing pipelines handle backpressure (bounded channels, flow control) |

## 15. Build, Release & CI/CD (SEC-261 – SEC-280)

| ID | Check |
| ---- | ------- |
| SEC-261 | CI pipelines run on ephemeral agents with no persistent state |
| SEC-262 | Build artifacts are signed and signatures are verified before deployment |
| SEC-263 | Debug symbols are stripped from release binaries (or stored separately) |
| SEC-264 | CI secrets are scoped per-pipeline, not shared globally |
| SEC-265 | Pipeline definitions require approval for changes to deployment stages |
| SEC-266 | Container image builds use multi-stage dockerfiles; build tools not in final image |
| SEC-267 | Static analysis (SAST) runs on every PR with blocking quality gates |
| SEC-268 | Dependency vulnerability scan (SCA) runs on every PR with blocking for critical/high |
| SEC-269 | Unit tests include negative/adversarial cases for security-sensitive code paths |
| SEC-270 | Integration tests verify that authentication and authorization are enforced end-to-end |
| SEC-271 | Release branches are protected; direct pushes are blocked |
| SEC-272 | Deployment rolls forward; rollback is available but requires explicit approval |
| SEC-273 | Canary/blue-green deployments limit blast radius of bad releases |
| SEC-274 | Build reproducibility: same source commit produces identical artifacts |
| SEC-275 | CI logs do not contain secrets (masked in pipeline variables) |
| SEC-276 | Post-deployment smoke tests verify core functionality after each release |
| SEC-277 | Infrastructure-as-Code (Terraform/Bicep/ARM) is reviewed and versioned like application code |
| SEC-278 | Database migration scripts are reviewed for data safety and reversibility |
| SEC-279 | Feature branch deploys are isolated and cannot access production data |
| SEC-280 | CI/CD pipeline has a "break glass" mechanism for emergency hotfixes with audit trail |

## 16. API Design & Contract Safety (SEC-281 – SEC-300)

| ID | Check |
| ---- | ------- |
| SEC-281 | API versioning is enforced; breaking changes require a new version |
| SEC-282 | Response schemas are validated in tests to detect accidental exposure of new fields |
| SEC-283 | Pagination is enforced on list endpoints with bounded page sizes |
| SEC-284 | Bulk/batch endpoints cap the number of items per request |
| SEC-285 | Error responses use a consistent schema with error codes, not string matching |
| SEC-286 | Deprecation headers and sunset dates are used before removing endpoints |
| SEC-287 | API contracts are tested with consumer-driven contract tests (Pact or equivalent) |
| SEC-288 | Rate limit headers (X-RateLimit-Remaining, Retry-After) guide callers to back off |
| SEC-289 | GraphQL endpoints (if used) enforce query depth and complexity limits |
| SEC-290 | Webhook payloads are signed (HMAC) so receivers can verify authenticity |
| SEC-291 | API responses include only fields the caller is authorized to see (field-level filtering) |
| SEC-292 | ETags or version fields are used for conditional updates to prevent lost updates |
| SEC-293 | Retry-safe (idempotent) endpoints document idempotency key expectations |
| SEC-294 | Long-running operations return 202 Accepted with status polling, not blocking HTTP responses |
| SEC-295 | File/blob download endpoints set Content-Disposition headers to prevent inline execution |
| SEC-296 | OpenAPI spec is auto-generated from code and validated in CI |
| SEC-297 | Nullable fields are explicitly marked in the contract; implicit null is disallowed |
| SEC-298 | API keys in query strings are rejected; only header-based auth is accepted |
| SEC-299 | Cross-origin resource sharing (CORS) preflight caching is bounded (short max-age) |
| SEC-300 | API responses set Cache-Control: no-store for sensitive data endpoints |

## 17. Resilience & Denial of Service (SEC-301 – SEC-320)

| ID | Check |
| ---- | ------- |
| SEC-301 | All external-facing endpoints have request rate limiting |
| SEC-302 | Rate limiting uses token bucket or sliding window, not fixed window |
| SEC-303 | Compute-intensive operations (hashing, encryption, image processing) have concurrency bounds |
| SEC-304 | Memory-intensive operations have allocation caps (max collection size, max response size) |
| SEC-305 | Timeouts are set on all external dependencies (HTTP, DB, cache, queue) |
| SEC-306 | Circuit breakers trip after configurable failure thresholds |
| SEC-307 | Graceful degradation: non-critical feature failures do not crash core functionality |
| SEC-308 | Health probes distinguish between liveness (process health) and readiness (dependency health) |
| SEC-309 | Retry budgets limit total retries across the call chain (no retry amplification) |
| SEC-310 | Backpressure mechanisms reject new work when queues or buffers are full |
| SEC-311 | Startup caches and warm-up are bounded; cold start does not accept traffic |
| SEC-312 | Regex/parsing operations on untrusted input have execution time limits |
| SEC-313 | Connection pool exhaustion alerts trigger before hard limits are reached |
| SEC-314 | Disk write operations are bounded and monitored (log rotation, temp cleanup) |
| SEC-315 | Large response bodies are streamed, not buffered entirely in memory |
| SEC-316 | Database query timeouts are set at the client and the server |
| SEC-317 | Poison message handling: unprocessable queue messages are dead-lettered, not retried infinitely |
| SEC-318 | Thread pool / task pool size is configured explicitly, not left at runtime defaults |
| SEC-319 | Startup dependencies have timeouts; inability to connect to a dep fails fast |
| SEC-320 | Load shedding: under extreme load, the service returns 503 with Retry-After rather than degrading |
