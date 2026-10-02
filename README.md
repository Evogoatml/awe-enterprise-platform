# AWE Enterprise Platform

> **Status: not a production-ready platform.** The narrow API runtime, strict settings validation, capability-gated sandbox, and audit events have executable tests. The content ingestion, analytics, optimization, and scheduler modules now provide a testable first workflow slice, but they are not wired into the API or an application composition root. Database migrations, live source credentials, external posting integrations, deployment hardening, and operational monitoring remain deployment work; account/bot automation and strategy modules remain experimental.

This repository contains a small Python API runtime, a capability-gated process sandbox, and library-level content-pipeline components. Its broader positioning—AI-assisted digital operations, campaign optimization, and enterprise automation—is a target direction, not a description of a fully integrated or production-ready system.

## Executive Summary

AWE Enterprise Platform is intended to become an operational layer for content production, campaign optimization, performance analytics, and platform engagement. The current repository does not yet unify these capabilities into a running multi-channel system.

The repository contains components and prototypes related to strategy, account lifecycle management, campaign optimization, and content fingerprinting. Their presence does not mean they are complete, tested, or integrated with the API runtime.

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

The intended architecture supports a feedback loop: ingest content, evaluate performance, apply optimization logic, refine strategy, and redeploy configurations. Only a fixture-backed library workflow currently exercises part of this path; it is not an enabled campaign service.

## Architecture Overview

The repository contains a small runnable API plus standalone libraries and prototypes. A directory's presence does not mean its code is loaded by the API:

- `api/` and `core/` — FastAPI application, environment settings, health/readiness/status routes
- `security/` — capability-gated subprocess sandbox and audit events
- `src/services/` and `src/workers/` — library-level content, analytics, optimization, and scheduler code; not composed into the API runtime
- `src/enterprise/`, `engine/`, and root `enterprise/` — experimental account lifecycle, strategy, and fingerprinting code
- `utils/` — standalone utility prototypes; not a deployed observability or secret-management service
- `tests/` — unit tests using fakes and fixtures, including the content-pipeline workflow
- `docs/` — proposed architectures and product guidance

### Core Components

The following are intended capability areas, not a list of fully implemented runtime features. See [Implementation status](#implementation-status) for current coverage and integration gaps.

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
│   └── FastAPI health, readiness, and status endpoints
├── core/
│   └── Runtime settings
├── docs/
│   └── Product notes and explicitly aspirational architecture diagrams
├── engine/
│   └── Experimental strategy code
├── enterprise/
│   └── Fingerprinting prototype
├── security/
│   └── Capability-gated process sandbox (see "Sandboxed Execution Security Model")
├── src/
│   ├── database/                       # Standalone SQL schema files, not migrations
│   ├── enterprise/                     # Account lifecycle prototype
│   ├── services/                       # Library-level services and prototypes
│   ├── workers/                        # Injectable RQ scheduler, not a deployed worker
│   └── main.py                         # Compatibility entry point for API
├── tests/
├── utils/
├── Dockerfile                          # Minimal API image only
├── requirements.txt
├── README.md
└── .gitignore
```

## Technology Stack

The implemented API runtime is Python-based and uses FastAPI, Uvicorn, asyncpg, and Redis. The minimal `Dockerfile` installs only `requirements-api.txt`; the broader requirements include libraries used by disconnected modules or prototypes. Their inclusion does not imply an integrated data, ML, cloud, or monitoring stack:

- `requirements-api.txt` provides the API runtime dependencies.
- `requirements.txt` includes additional libraries for standalone or experimental modules, including RQ, SQLAlchemy, NumPy, pandas, scikit-learn, imagehash, OpenCV, Vault/AWS clients, and Prometheus client.
- Database schema files under `src/database/` are not versioned migrations, and the API does not initialize the content workflow or worker.

## Intended Platform Behaviors

These describe product direction; they are not claims that all behaviors are available in the current runtime.

### Adaptive Strategy Generation
The repository includes a meta-cognitive strategy engine designed to generate, evaluate, and iterate on campaign concepts. This type of logic enables a platform to move from static campaign rules to dynamic strategic experimentation.

### Performance-Oriented Discovery
The design emphasizes measurable improvement across content, delivery, and engagement workflows, reinforcing a culture of continuous optimization rather than reactionary change.

### Enterprise Readiness
Enterprise-oriented modules and governance patterns exist as prototypes, but they are not evidence of operational readiness or integrated account controls.

### Resilience Engineering
Standalone resilience, throttling, and observability prototypes exist; they are not integrated into the API runtime or a verified production monitoring setup.

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

## Implementation status

The table below distinguishes code that currently runs and has tests from library modules and prototypes. “Dependencies” describes what a deployed or integrated application still needs to provide; it is not a list of Python package requirements.

| Area | Current state | Tests | Not yet provided or verified |
| --- | --- | --- | --- |
| API runtime (`api/main.py`, `core/config.py`) | FastAPI app with liveness/readiness checks, `/api/status`, bearer-token gate, and settings validation | `tests/test_api.py`, `tests/test_config.py` | Production identity/roles, application feature routes, and reachable PostgreSQL/Redis services |
| Capability sandbox and audit (`security/`) | Capability-gated subprocess execution and structured audit events | `tests/test_capability.py`, `tests/test_sandbox.py`, `tests/test_audit_logging.py`; host-dependent namespace test in `tests/test_sandbox_integration.py` | Shared revocation storage for multiple workers and deployment-specific sandbox hardening |
| Content pipeline (`src/services/`, `src/workers/scheduler.py`) | Ingestion, analytics, optimization, and queue-backed scheduling components; tested as a library workflow | `tests/test_content_pipeline.py` uses fixtures and fakes | Application composition, database/schema provisioning, queue and worker deployment, content selection, caption generation, and platform-posting integrations |
| Database artifacts (`src/database/`) | Standalone SQL schema files are present | No migration or live-database test in the current suite | Versioned migration tooling and schema lifecycle |
| Strategy, account/bot, proxy, fingerprinting, and broader observability modules | Prototype or aspirational code; not wired into the API runtime | Not covered by the current test suite | Completion, safe integration, dependencies/configuration, and tests before operational use |

`src/main.py` is a compatibility entry point that delegates to `api.main.main`; it does not initialize the broader platform. The API application exposes only health and status endpoints. CI currently installs the development requirements, compiles selected Python paths, and runs unittest discovery on Python 3.11 and 3.12 (`.github/workflows/ci.yml`). Compilation does not cover every prototype or all repository Python files.

The content-pipeline test verifies a fixture-backed path from ingestion through injected posting components; it does not establish that the API or a deployed worker configures those components (`tests/test_content_pipeline.py`; `src/workers/scheduler.py`).

## Operational Considerations

To move this early-stage repository toward a production platform, the next steps typically include:

- Defining a formal service contract and API specification
- Establishing a production configuration and secret-management model
- Adding migration tooling and database schema governance
- Extending automated test coverage and adding deployment/release workflows
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

## Intended Product Direction (Not Current Runtime Capabilities)

The following phrases describe the product vision only, not capabilities available in the current API:

- an AI-augmented operational platform
- a strategy-driven digital optimization engine
- a modular enterprise workflow foundation
- a performance marketing orchestration system

This framing aligns the technology with real business value: faster decision cycles, improved operational efficiency, and more scalable digital execution.

## Conclusion

AWE Enterprise Platform is an early-stage repository containing a narrow API runtime, tested security components, and a testable but unintegrated workflow slice.

The broader analytics, automation, experimentation, lifecycle-management, and strategy capabilities remain incomplete or disconnected. Reaching production use requires the integration, migration, identity, deployment, and operational work listed above.

## License

No explicit license was identified in the repository metadata at the time of writing. Before public or commercial deployment, it is recommended to add a formal open-source license or enterprise licensing model to define legal usage and distribution terms.

---

**Aspirational vision:** AWE Enterprise Platform may develop into a strategic operating system for digital growth, combining machine-guided optimization, resilient infrastructure, and enterprise control. This is not a description of the current integrated runtime.
