# Privacy Controls Reference

> **Inlined into the Privacy Specialist Agent prompt via the `helpers:` frontmatter key.**
>
> This file is the regulatory reference corpus for ISO/IEC 27701:2025, ISO/IEC 29100, ISO/IEC 27018, GDPR, and HIPAA — extracted from the Privacy agent body to keep the body lean while preserving the complete control-coverage map.

---

## Regulatory Framework: ISO/IEC 27701:2025

This agent enforces controls derived from **ISO/IEC 27701:2025**, the international standard for Privacy Information Management Systems (PIMS), extending ISO/IEC 27001 and ISO/IEC 27002 with privacy-specific requirements.

### Coverage Map: All ISO 27701 Clauses

> **Design Principle**: Every clause is either enforced directly by code review checks, or flagged as an organizational control with code-level implications noted.

#### Clause 5 — PIMS-Specific Requirements (ISO 27001 Extension)

| Sub-Clause | Control Area | Code Review Focus | Applicability |
|------------|-------------|-------------------|---------------|
| **5.2.1** | Understanding the organization's context | Identify PII/PHI processing scope in code | Contextual |
| **5.2.2** | Understanding needs of interested parties | Verify code respects data subject expectations | Contextual |
| **5.4.1.2** | Privacy risk assessment | Flag code introducing new PII processing without documented DPIA | **Enforceable** |
| **5.4.1.3** | Privacy risk treatment | Verify mitigations (encryption, masking) are implemented in code | **Enforceable** |

#### Clause 6 — PIMS-Specific Guidance (ISO 27002 Extension)

| Sub-Clause | Control Area | Code Review Focus | Applicability |
|------------|-------------|-------------------|---------------|
| **6.3.2.1** | Screening (PII access) | Verify code enforces role-based access to PII data | **Enforceable** |
| **6.5.2.1** | Classification of information | Check PII/PHI fields have classification annotations/attributes | **Enforceable** |
| **6.5.2.2** | Labeling of information | Verify data models label sensitive fields (`[SensitiveData]`, `[PersonalData]`) | **Enforceable** |
| **6.5.3.1** | Transfer of PII | Audit all data transfer code paths for encryption | **Enforceable** |
| **6.6.2.1** | Access control for PII | Verify authorization checks before PII access | **Enforceable** |
| **6.6.2.2** | Privileged access to PII | Flag direct DB access to PII tables without middleware/DAL | **Enforceable** |
| **6.7.4** | Use of cryptography for PII | Verify encryption algorithms meet standards (no MD5/SHA1 for PII) | **Enforceable** |
| **6.9.3.1** | Backup of PII | Verify backups containing PII are encrypted | Contextual |
| **6.9.4.1** | Event logging of PII access | Verify audit trails for PII read/write operations | **Enforceable** |

#### Clause 7 — PII Controller Guidance

| Sub-Clause | Control Area | Code Review Focus | Applicability |
|------------|-------------|-------------------|---------------|
| **7.2.1** | Identify and document purpose | Verify PII collection has documented purpose in code/config | **Enforceable** |
| **7.2.2** | Identify lawful basis | Check consent capture code exists before PII collection | **Enforceable** |
| **7.2.3** | Determine when and how consent obtained | Verify consent flow implementation (UI + backend) | **Enforceable** |
| **7.2.4** | Obtain and record consent | Verify consent records are persisted and timestamped | **Enforceable** |
| **7.2.5** | Privacy impact assessment | Flag new PII processing pipelines without DPIA reference | Contextual |
| **7.2.6** | Contracts with PII processors | Verify processor SDK/API calls include DPA references | Contextual |
| **7.2.7** | Joint PII controller | Verify shared data handling has clear ownership in code | Contextual |
| **7.2.8** | Records related to processing PII | Verify processing activity logging is implemented | **Enforceable** |
| **7.3.1** | Obligations to PII principals — Determine and fulfill | Verify data subject rights APIs exist (access, rectification, erasure) | **Enforceable** |
| **7.3.2** | Providing information to PII principals | Verify privacy notices are served before data collection | **Enforceable** |
| **7.3.3** | Providing mechanism to modify/withdraw consent | Verify consent withdrawal functionality exists | **Enforceable** |
| **7.3.4** | Providing mechanism to object to processing | Verify opt-out mechanism is implemented | **Enforceable** |
| **7.3.5** | Providing copy of PII processed | Verify data export/portability API exists | **Enforceable** |
| **7.3.6** | Handling requests regarding PII | Verify request handling has response time tracking | **Enforceable** |
| **7.3.7** | Automated decision-making | Flag ML/AI decisions on PII without human override option | **Enforceable** |
| **7.4.1** | Limit collection | Verify code collects ONLY necessary PII fields | **Enforceable** |
| **7.4.2** | Limit processing | Verify PII is used only for documented purpose | **Enforceable** |
| **7.4.3** | Accuracy and quality | Verify PII update mechanisms exist | Contextual |
| **7.4.4** | PII minimization objectives | Verify data models don't over-collect (SELECT * audits) | **Enforceable** |
| **7.4.5** | PII de-identification and deletion | Verify anonymization/pseudonymization is applied where applicable | **Enforceable** |
| **7.4.6** | Temporary files | Verify PII in temp files is cleaned up | **Enforceable** |
| **7.4.7** | Retention | Verify PII retention policies are enforced in code (TTL, purge jobs) | **Enforceable** |
| **7.4.8** | Disposal | Verify PII deletion is complete (not soft-delete leaving PII in DB) | **Enforceable** |
| **7.4.9** | PII transmission controls | Verify PII in transit is encrypted (TLS/mTLS) | **Enforceable** |
| **7.5.1** | Identify basis for PII transfer | Audit cross-boundary PII flows for legal basis | **Enforceable** |
| **7.5.2** | Countries and international organizations | Flag PII transfers to regions without adequacy decisions | Contextual |
| **7.5.3** | Records of PII transfers | Verify transfer logging exists | **Enforceable** |
| **7.5.4** | Records of PII disclosure to third parties | Verify third-party PII sharing is logged and auditable | **Enforceable** |

#### Clause 8 — PII Processor Guidance

| Sub-Clause | Control Area | Code Review Focus | Applicability |
|------------|-------------|-------------------|---------------|
| **8.2.1** | Customer agreement | Verify processing stays within agreed scope (no feature creep on PII) | Contextual |
| **8.2.2** | Organization's purposes | Verify PII is not used for undocumented secondary purposes | **Enforceable** |
| **8.2.3** | Marketing and advertising | Flag PII use in analytics/marketing code without explicit consent | **Enforceable** |
| **8.2.4** | Infringing instructions | Verify code doesn't process PII in ways that violate documented agreements | Contextual |
| **8.2.5** | Customer's obligations | Verify data quality checks on inbound PII | Contextual |
| **8.2.6** | Records related to processing PII | Verify processor-side processing activity logging | **Enforceable** |
| **8.3.1** | Obligations to PII principals | Verify processor forwards data subject requests appropriately | **Enforceable** |
| **8.3.2** | Providing information to PII principals | Verify processor surfaces privacy information | Contextual |
| **8.4.1** | Notification of sub-processor changes | Verify sub-processor addition triggers notification | Contextual |
| **8.4.2** | Restrict sub-processing | Verify sub-processor calls don't forward PII beyond scope | **Enforceable** |
| **8.4.3** | Sub-processor PII transfer | Verify encryption on all downstream PII transfers | **Enforceable** |
| **8.5.1** | PII transfer to third countries | Flag cross-region PII transfers without adequacy safeguards | **Enforceable** |
| **8.5.2** | Identify basis for PII transfer | Verify legal basis documentation for cross-border flows | Contextual |
| **8.5.3** | Records of PII transfer | Verify cross-border transfer audit logging | **Enforceable** |
| **8.5.4** | Records of PII disclosure to third parties | Verify processor-side disclosure logging | **Enforceable** |
| **8.5.5** | Return, transfer, or disposal of PII | Verify PII cleanup/return on contract termination | **Enforceable** |

#### Annexes (Reference Controls)

| Annex | Control Area | Code Review Focus |
|-------|-------------|-------------------|
| **Annex A / B** | PIMS controls for PII Controllers / Processors | Enforced via the Clause 7 / Clause 8 checks above |
| **Annex C** | Mapping to ISO/IEC 29100 (Privacy Principles) | Seven privacy principles used as design validation (see below) |
| **Annex D** | Mapping to GDPR | Cross-referenced in GDPR table below |
| **Annex E** | Mapping to ISO 27018 / ISO 29151 | Cloud-specific PII controls enforced for cloud deployments |
| **Annex F** | Application guidance | Systematic PIMS approach applied to review methodology |

### ISO 29100 Privacy Principles (Annex C)

These seven principles from ISO/IEC 29100 are used as a design validation checklist:

| # | Principle | Code Review Question |
|---|-----------|---------------------|
| 1 | **Consent and choice** | Does the code collect PII only after valid consent? |
| 2 | **Purpose legitimacy and specification** | Is PII used only for the documented purpose? |
| 3 | **Collection limitation** | Does code collect the minimum PII required? |
| 4 | **Data minimization** | Are PII fields pruned before storage/forwarding? |
| 5 | **Use, retention, and disclosure limitation** | Is PII retained only as long as needed? Is sharing limited? |
| 6 | **Accuracy and quality** | Can PII be corrected/updated? |
| 7 | **Openness, transparency, and notice** | Is the user informed about PII processing? |

### ISO 27018 Cloud Privacy Controls (Annex E)

For services deployed in cloud environments, additional controls apply:

| Control | Code Review Focus |
|---------|-------------------|
| **Public cloud PII processor obligations** | Verify cloud storage of PII uses provider encryption APIs |
| **PII return and deletion** | Verify cloud teardown includes PII purge |
| **PII transmission protection** | Verify cloud-to-cloud PII transit uses TLS 1.2+ |
| **Sub-processing transparency** | Verify cloud provider SDK calls don't forward PII to unknown endpoints |
| **PII disclosure notification** | Verify logging when PII is disclosed to cloud provider support |

### GDPR Cross-Reference (Annex D)

| GDPR Article | Requirement | Code Review Check |
|-------------|-------------|-------------------|
| **Art. 5(1)(b)** | Purpose limitation | PII used only for documented purpose |
| **Art. 5(1)(c)** | Data minimization | Only necessary PII fields collected |
| **Art. 5(1)(e)** | Storage limitation | PII retention enforced with TTL/purge |
| **Art. 5(1)(f)** | Integrity and confidentiality | PII encrypted at rest and in transit |
| **Art. 6** | Lawful basis for processing | Consent/legal basis check before PII processing |
| **Art. 7** | Conditions for consent | Consent capture is clear, specific, informed, unambiguous |
| **Art. 13/14** | Information to data subjects | Privacy notice served before collection |
| **Art. 15** | Right of access | Data export API exists |
| **Art. 16** | Right to rectification | PII update mechanism exists |
| **Art. 17** | Right to erasure | Hard-delete (not soft-delete) for PII |
| **Art. 20** | Right to data portability | Machine-readable export format |
| **Art. 22** | Automated decision-making | Human override available for AI/ML on PII |
| **Art. 25** | Privacy by design/default | PII collection disabled by default (opt-in) |
| **Art. 30** | Records of processing activities | Processing activity logging implemented |
| **Art. 32** | Security of processing | Encryption + access controls on PII |
| **Art. 33/34** | Breach notification | Breach detection logging in code |
| **Art. 44-49** | Cross-border transfers | Transfer safeguards verified |

### HIPAA Cross-Reference

| HIPAA Section | Requirement | Code Review Check |
|--------------|-------------|-------------------|
| **§164.502** | Minimum necessary standard | Code accesses only needed PHI fields |
| **§164.508** | Uses requiring authorization | PHI use beyond treatment/payment/operations requires authorization check |
| **§164.512** | Permitted uses and disclosures | PHI disclosure matches permitted purpose |
| **§164.514** | De-identification | Identifiers removed/masked per Safe Harbor method |
| **§164.520** | Notice of privacy practices | Privacy notice served before PHI collection |
| **§164.522** | Rights to request restrictions | Restriction mechanism exists |
| **§164.524** | Right of access | PHI export API exists |
| **§164.526** | Amendment of PHI | PHI correction mechanism exists |
| **§164.528** | Accounting of disclosures | PHI access/disclosure audit trail exists |
| **§164.530** | Administrative requirements | PHI access policies enforced in code |
