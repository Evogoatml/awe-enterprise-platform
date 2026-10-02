# AWE Enterprise Platform

> **Status: not a production-ready platform.** The narrow API runtime, strict settings validation, capability-gated sandbox, and audit events have executable tests. The content ingestion, analytics, optimization, and scheduler modules now provide a testable first workflow slice, but they are not wired into the API or an application composition root. Database migrations, live source credentials, external posting integrations, deployment hardening, and operational monitoring remain deployment work; account/bot automation and strategy modules remain experimental.

AWE Enterprise Platform is a Python-based, modular platform for AI-assisted digital operations, campaign optimization, content intelligence, and enterprise-grade automation. The repository is designed as a strategic foundation for modern performance marketing and operational orchestration, combining analytics, experimentation, intelligent routing, and adaptive strategy generation into a single extensible system.

## Executive Summary

AWE Enterprise Platform represents a next-generation operational layer for businesses that need to orchestrate content production, campaign optimization, performance analytics, and automated platform engagement at scale. Rather than treating each function as a disconnected tool, the platform unifies them into a system that can sense performance, identify opportunities, adapt strategy, and coordinate execution across multiple digital channels.

The platform combines a clear service-oriented architecture with advanced logic for meta-cognitive strategy development, enterprise account lifecycle management, campaign optimization, and intelligent content deduplication. In practical terms, it is built to support marketing and digital operations teams that require not only automation, but adaptive decision-making grounded in measurable outcomes.

From a strategic perspective, the repository is positioned around three core themes:

- Data-informed decision support for campaign execution
- Adaptive optimization through experiment-driven strategy evolution
- Enterprise-scale workflow control with operational observability and resilience

This makes the solution relevant not only to technical engineering teams, but also to executive stakeholders seeking a platform that can improve efficiency, reduce operational drift, and unlock more scalable digital growth.

## Strategic Value

AWE Enterprise Platform is intended to provide decision-makers with a disciplined operational model for high-volume digital environments. It is especially relevant for organizations that manage:

- Multi-channel digital campaigns
- Large volumes of content and creative assets
- Performance-based acquisition programs
- Experimentation across audiences and offer structures
- Governance and lifecycle controls across customer and affiliate accounts

The architecture is explicitly designed to support a feedback loop: ingest content, evaluate performance, apply optimization logic, refine strategy, and re-deploy high-performing configurations. This creates a platform mindset in which operational insight is continuously converted into action.

## Architecture Overview

The repository is organized into modular components aligned to platform capabilities:

- `src/` — primary application runtime, orchestration, and service layer
- `engine/` — meta-cognitive strategy and reasoning components
- `api/` — API-facing integrations and endpoints
- `enterprise/` — enterprise controls, lifecycle management, and fingerprinting logic
- `docs/` — supporting documentation and product guidance
- `utils/` — cross-cutting utilities, resilience, safety, and observability

### Core Components

1. Analytics and Optimization Services
   - Performance tracking
   - KPI measurement and evaluation
   - Campaign strategy refinement
   - Operational learning loops

2. Content Ingestion and Workflow Automation
   - Content acquisition and organization
   - Structured processing pipelines
   - Source normalization and readiness checks

3. Enterprise Lifecycle Management
   - Account state tracking and operational controls
   - Lifecycle transitions and maintenance
   - Policy-aware operational governance

4. Meta-Cognitive Strategy Engine
   - Strategy exploration and adaptation
   - Experimental branching and optimization patterns
   - Decision support based on historical performance and emerging signals

5. Content Deduplication and Fingerprinting
   - Asset similarity analysis
   - Content overlap detection
   - Efficiency gains via reduced redundancy

6. Observability, Resilience, and Rate Control
   - Circuit breakers and operational safeguards
   - Request throttling and adaptive pacing
   - Monitoring and telemetry for health visibility

## Repository Structure

```text
.
├── api/
│   └── API-facing logic and integrations
├── docs/
│   └── Product, operational, and technical documentation
├── engine/
│   └── Strategy and reasoning engines
├── enterprise/
│   └── Enterprise lifecycle and fingerprinting services
├── security/
│   └── Capability-gated process sandbox (see "Sandboxed Execution Security Model")
├── src/
│   ├── database/
│   ├── enterprise/
│   ├── services/
│   ├── utils/
│   ├── workers/
│   ├── main.py
│   └── platform services and orchestration
├── requirements.txt
├── README.md
└── .gitignore (if present in the repository)
```

## Technology Stack

The platform is built primarily in Python and uses a modern data and operations stack centered on reliability and experimentation:

- Python 3.x
- Asyncio-based orchestration
- SQLAlchemy for persistence abstraction
- PostgreSQL-friendly design patterns
- Redis and job queue patterns
- NumPy, pandas, and scikit-learn for analytical workloads
- OpenCV and imagehash for content similarity and vision-based analysis
- Prometheus-style observability and telemetry patterns
- Cloud and secret-management compatibility via Vault and AWS integrations

## Key Platform Behaviors

### Adaptive Strategy Generation
The repository includes a meta-cognitive strategy engine designed to generate, evaluate, and iterate on campaign concepts. This type of logic enables a platform to move from static campaign rules to dynamic strategic experimentation.

### Performance-Oriented Discovery
The design emphasizes measurable improvement across content, delivery, and engagement workflows, reinforcing a culture of continuous optimization rather than reactionary change.

### Enterprise Readiness
The enterprise layer introduces governance patterns needed for operational scale, including lifecycle governance, account controls, and operational safeguards relevant to regulated or high-risk business environments.

### Resilience Engineering
The platform includes patterns for monitoring, throttling, and circuit-breaking to protect system health and reduce cascading failures in dynamic production environments.

## Sandboxed Execution Security Model

The `security/` package provides a capability-gated process sandbox (`security.sandbox.Sandbox`) used to run untrusted or semi-trusted commands. It replaces any prior notion of an unsigned, client-supplied "capability token" with the following, simpler model:

- **Authorization boundary**: every call to `Sandbox.execute()` must present a token issued by `security.capability.CapabilityManager`. Tokens are HMAC-SHA256 signed with a server-side secret (`AWE_SANDBOX_SECRET`), carry explicit scopes (e.g. `sandbox:execute`, `sandbox:network`), and have a short expiry. Tokens can also be revoked by id before they expire. This signature check is the only actual security boundary in the module.
- **Heuristics are advisory only**: simple command-string pattern matching (`security.sandbox.detect_anomalies`) is attached to results purely as telemetry for logging/alerting. It is never used to allow or deny execution, because string heuristics are trivially bypassed.
- **Network is denied by default**: enabling it requires both `SandboxPolicy.allow_network=True` and a token carrying the `sandbox:network` scope. When the host has the privilege to create a network namespace, that denial is additionally enforced at the OS level; when it does not, the result reports that OS-level isolation was not enforced so callers aren't misled.
- **Filesystem writes are restricted**: only paths listed in `SandboxPolicy.writable_paths` are made available (via an ephemeral per-run sandbox directory), which is also cleaned up after every run.
- **Hardening**: `PR_SET_NO_NEW_PRIVS`, CPU/memory/file-descriptor `RLIMIT_*`s, wall-clock timeouts that kill the whole process group, capped stdout/stderr, and optional non-root `run_as_uid`/`run_as_gid` execution.
- **No Docker requirement**: the sandbox runs as a regular subprocess so it keeps working in environments without a container runtime; this is a deliberate trade-off documented in `security/sandbox.py`, where the exact guarantees and limitations (e.g. filesystem isolation is best-effort without a container/mount namespace) are spelled out.

See `tests/test_capability.py` and `tests/test_sandbox.py` for executable examples of the expected security behavior (verification, expiry, revocation, denied execution, timeouts).

### Sandbox operations and audit

Set `AWE_SANDBOX_SECRET` to a securely generated value of at least 32 bytes and keep it in the deployment secret manager. Rotate it by updating every worker and restarting them; changing the key invalidates all existing tokens, so issue fresh tokens after rotation. Revoke an active token by verifying it and passing its `jti` to `CapabilityManager.revoke()`. Revocation is process-local; multi-worker deployments need a shared revocation store.

Security events are emitted as JSON lines through the `security.audit` Python logger. Retain this logger at `INFO` or higher in the centralized logging system. Records cover token issuance, verification failures, denied execution, network-access requests, timeout kills, and revocation; token contents and signing secrets are never logged. `capability_verification_failed` indicates a malformed, expired, revoked, invalid-signature, or insufficient-scope token. `sandbox_timeout_kill` means the wall-clock deadline was exceeded and the process group was killed. A failed command exit is returned in `SandboxResult`; `network_isolation_enforced=False` means the host could not enforce OS-level network isolation.

Run `python -m unittest discover -s tests -v` to exercise the sandbox. The network-namespace integration test uses the host's real `unshare --net` support and skips with an explicit reason when Linux privileges or runtime support are unavailable.

## Content pipeline status

`src/services/content_ingestion.py` parses RSS 2.0 and the configured video API response schema, applies request timeouts and bounded retries, and stores normalized records using `(source, external_id)` deduplication. `src/services/analytics.py` calculates metrics from persisted posts and daily costs; fetching AWE reports requires an explicit `partner_hash`. `src/services/optimization.py` applies bounded campaign actions idempotently per sub-affiliate decision and day. `src/workers/scheduler.py` provides a queue-backed RQ entry point and an injectable content-to-post workflow.

These modules are library-level building blocks, not an enabled campaign automation service. The application must provide database, queue, content selection, caption generation, and platform-posting integrations. Missing external integrations fail explicitly; the platform-bot/account lifecycle prototypes are not used by this path. See `tests/test_content_pipeline.py` for the fixture-backed workflow contract.

## Getting Started

### Prerequisites

- Python 3.10+
- PostgreSQL and Redis for readiness checks
- pip

### Installation

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
cp .env.example .env
```

### Running the Application

```bash
set -a
. ./.env
set +a
python -m api.main
```

The server listens on `HOST`/`PORT` (default `0.0.0.0:8000`). `GET /health/live` checks that the process responds; `GET /health/ready` checks PostgreSQL and Redis connectivity and returns 503 if either URL is unset or a dependency is unavailable. All `/api/` routes require bearer-token authentication and fail closed when no token is configured. Production mode also requires database and Redis URLs and an API token of at least 32 characters, and disables the interactive API documentation.

Set `APP_ENV=production` and supply `DATABASE_URL`, `REDIS_URL`, and a securely generated `API_TOKEN` through the deployment secret manager. The shared API token is only a minimal gate; it does not provide user identity, roles, or per-user authorization and is not a substitute for a complete production identity system. Build the minimal API image with `docker build -t awe-api .`; provide these environment variables at container runtime. Never commit `.env` files or production credentials. Use `python -m unittest discover -s tests -v` to run the current test suite.

`src/main.py` remains as a compatibility entry point (`python -m src.main`). The services under `src/services`, `engine`, and `enterprise` are not all wired into this API runtime; several remain incomplete prototypes. Do not enable external account actions until platform policy, authorization, consent, rate limits, audit, and recovery controls are implemented and verified.

## Notable Implementation Signals

Several areas of the codebase demonstrate strong platform intent:

- `src/main.py` initializes the platform and logs the major system subsystems
- `engine/meta_cognitive_strategy.py` implements strategy-tree exploration and adaptive campaign experimentation
- `src/services/analytics.py` indicates performance analytics support
- `src/services/content_ingestion.py` suggests systematic content acquisition and ingestion workflows
- `src/services/optimization.py` indicates optimization logic to improve operational outcomes
- `src/services/platform_bots.py` supports platform-level automation and orchestration
- `src/enterprise/account_lifecycle.py` confirms enterprise lifecycle and governance workflows
- `enterprise/fingerprinting.py` adds content identity and deduplication intelligence

## Operational Considerations

To move this repository from a strong technical foundation to an enterprise-grade production platform, the next steps typically include:

- Defining a formal service contract and API specification
- Establishing a production configuration and secret-management model
- Adding migration tooling and database schema governance
- Implementing automated tests and CI/CD enforcement
- Creating deployment and environment management policies
- Formalizing monitoring dashboards and operational SLAs
- Establishing security, compliance, and data-handling review processes

## Risk and Governance

Before scaling into production, leadership should ensure:

- Data privacy and handling policies are explicit
- User or affiliate account governance is clearly defined
- Content ingestion pipelines comply with platform and legal requirements
- Security review covers external APIs, credentials, and operational secrets
- Monitoring and audit traces exist for decision-making processes

## Recommended Product Positioning

This repository is best described as:

- an AI-augmented operational platform
- a strategy-driven digital optimization engine
- a modular enterprise workflow foundation
- a performance marketing orchestration system

This framing aligns the technology with real business value: faster decision cycles, improved operational efficiency, and more scalable digital execution.

## Conclusion

AWE Enterprise Platform is a substantive technology foundation for organizations seeking an intelligent and adaptive digital operations environment. It brings together analytics, automation, experimentation, lifecycle management, and strategic reasoning in a single architecture designed for operational scale.

The repository reflects a thoughtful engineering approach, with modular separation, strong service orientation, and a clear emphasis on optimization and enterprise governance. With disciplined implementation, testing, and operational hardening, it has the potential to evolve from a strategic platform prototype into a high-value production system for digital growth operations.

## License

No explicit license was identified in the repository metadata at the time of writing. Before public or commercial deployment, it is recommended to add a formal open-source license or enterprise licensing model to define legal usage and distribution terms.

---

AWE Enterprise Platform is designed as a strategic operating system for digital growth—combining machine-guided optimization, resilient infrastructure, and enterprise control into a single extensible platform.
