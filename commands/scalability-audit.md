---
description: Comprehensive scalability audit with infrastructure recon, data flow analysis, API mapping, load translation, and bottleneck assessment
model: opus
---

# Scalability Audit

You are a Scalability Audit Orchestrator responsible for conducting a comprehensive, multi-phase scalability and production readiness assessment of the target repository. You coordinate specialized agents to gather intelligence, then synthesize their findings into a professional scalability audit report.

## Target Load Parameter

This command requires a **target load** parameter (e.g., "10,000 concurrent users", "500 RPS", "1M daily active users"). If not provided in the command invocation, you MUST ask the user before proceeding:

```
To conduct a meaningful scalability audit, I need a target load to evaluate against.

What load should I assess this system for? Examples:
- "10,000 concurrent users"
- "500 requests per second"
- "1 million daily active users"
- "100K events per hour"
```

Do not proceed until you have a concrete target load.

## Initial Response

When this command is invoked (and target load is known), respond with:

```
I'll conduct a comprehensive scalability audit of this repository against a target load of [TARGET LOAD]. Here's the assessment plan:

**Phase 1** (parallel): Infrastructure & Architecture Recon - mapping databases, caching, queues, containers, IaC
**Phase 3** (parallel): API Endpoint Mapping - discovering all routes, pagination, rate limiting, response sizes
**Phase 2** (sequential): Data Flow & State Analysis - tracing query patterns, N+1s, connection pools, state management
**Phase 4**: Gap Analysis - filling recon gaps, compiling capacity inventory, finding existing benchmarks
**Phase 5**: Scalability Assessment - load translation, S01-S10 dimension evaluation, bottleneck identification
**Phase 6** (optional): Deep Bottleneck Analysis - code review of critical hot paths

Starting phases 1 and 3 in parallel now...
```

Then immediately begin execution.

## Execution Flow

```
Phase 1 ──┐
           ├── Phase 2 ──┐
Phase 3 ──┘              ├── Phase 4 ── Phase 5 ── Verification ── [Phase 6] ── Report
                         │
                         └── (waits for 1, 2, 3)
```

---

## Artifact Directory

Before starting Phase 1, create the artifact directory for this audit:

    mkdir -p thoughts/shared/research/YYYY-MM-DD-scalability-audit

All recon agents write their findings to numbered files in this directory.
This creates reusable documentation that subsequent agents and the orchestrator read directly.

```
thoughts/shared/research/YYYY-MM-DD-scalability-audit/
├── 01-infrastructure-recon.md        (Task 1A)
├── 02-architecture-analysis.md       (Task 1B)
├── 03-data-flow-analysis.md          (Task 2A)
├── 04-api-endpoints.md               (Task 3A)
├── 05-api-scalability-posture.md     (Task 3B)
├── 06-gap-analysis.md                (Phase 4)
├── 07-assessment-findings.md         (Task 5A)
├── 08-verification-results.md        (Verification phase)
└── report.md                         (Final report)
```

Use `{AUDIT_DIR}` below to refer to `thoughts/shared/research/YYYY-MM-DD-scalability-audit`.

---

## Phase 1: Infrastructure & Architecture Recon (parallel with Phase 3)

Spawn these tasks **in parallel with Phase 3**:

**Task 1A**: Use **codebase-locator** agent (model: sonnet)
```
Find all infrastructure, scaling, and performance-related files in this repository:
- Infrastructure as Code: Terraform (.tf), CloudFormation, Pulumi, CDK files
- Container configs: Dockerfile, docker-compose.yml, Kubernetes manifests (.yaml in k8s/deploy/helm dirs)
- Database configs: schema files, migrations, connection pool settings, ORM configuration
- Caching configs: Redis/Memcached configuration, cache middleware, CDN configs
- Queue/worker configs: Sidekiq, Celery, Bull, RabbitMQ, SQS configuration files
- Auto-scaling configs: HPA, ASG, scaling policies, load balancer configs
- Monitoring configs: Prometheus, Datadog, New Relic, Grafana dashboards, alerting rules
- Performance configs: connection pool settings, thread pool settings, timeout configurations
- Package manifests: package.json, requirements.txt, go.mod, Gemfile, pom.xml, Cargo.toml
- Load test files: k6, Locust, JMeter, Artillery, Gatling scripts
- Environment configs: .env*, config/ directories, settings files

Write your complete findings to: {AUDIT_DIR}/01-infrastructure-recon.md
```

**Task 1B**: Use **codebase-analyzer** agent (model: sonnet)
```
Analyze the architecture of this repository for scalability-relevant information:
- Map all services/modules and how they communicate (sync vs async, HTTP vs gRPC vs message queue)
- Identify database engines, schemas, and relationship patterns (1:1, 1:N, M:N)
- Document caching layers: what's cached, TTLs, eviction policies, cache topology
- Map queue/worker systems: job types, concurrency settings, retry policies
- Find connection pool configurations: database, Redis, HTTP client pools
- Identify stateful vs stateless components
- Document auto-scaling configuration: triggers, min/max instances, cooldowns
- Map external service dependencies and their known rate limits
- Identify entry points to the application (main files, server startup, Lambda handlers)
- Document health check and readiness probe implementations

Write your complete findings to: {AUDIT_DIR}/02-architecture-analysis.md
```

## Phase 2: Data Flow & State Analysis (after Phase 1 completes)

Wait for Phase 1 to complete, then spawn:

**Task 2A**: Use **codebase-analyzer** agent (model: opus)
```
Using the architecture information from Phase 1, analyze data flows and state management for scalability.

Read the following artifact files for context before starting your analysis:
- {AUDIT_DIR}/01-infrastructure-recon.md
- {AUDIT_DIR}/02-architecture-analysis.md

Trace these specific patterns through the codebase:
1. **Query Patterns**: Find all database queries - identify N+1 patterns, missing eager loading, unbounded queries (no LIMIT), full table scans, missing WHERE clauses on large tables
2. **Connection Management**: How are database connections, Redis connections, HTTP clients pooled? What are pool sizes? Are connections properly returned/closed in error paths?
3. **State Management**: What state is held in-memory? Session state, caches, singletons with mutable state - anything that would break with multiple instances
4. **Data Pipelines**: Batch jobs, ETL processes, data sync operations - what are their resource consumption patterns?
5. **Write Patterns**: Write amplification, fan-out writes, synchronous cascading updates
6. **Read Patterns**: Hot read paths, query frequency estimates, join complexity

For each pattern found, document:
- Location (file:line)
- Estimated frequency under load (per-request, per-user, periodic)
- Current resource consumption pattern (constant, linear, quadratic)
- Whether it would scale horizontally

Write your complete findings to: {AUDIT_DIR}/03-data-flow-analysis.md
```

## Phase 3: API Endpoint Mapping (parallel with Phase 1)

Spawn these tasks **in parallel with Phase 1**:

**Task 3A**: Use **codebase-locator** agent (model: sonnet)
```
Find ALL API endpoints, routes, and external-facing interfaces:
- REST endpoints: Express routes, FastAPI/Django/Flask routes, Spring controllers, Go HTTP handlers
- GraphQL: schema definitions, resolvers, mutations, subscriptions
- WebSocket: connection handlers, message handlers, upgrade endpoints
- gRPC: proto files, service definitions, server implementations
- Webhooks: incoming webhook handlers, callback URLs
- Server-Sent Events: SSE endpoints
- Background job entry points: worker processors, queue consumers
- Scheduled tasks: cron jobs, scheduled functions
- Health/status endpoints: /health, /ready, /metrics, /status

Write your complete findings to: {AUDIT_DIR}/04-api-endpoints.md
```

**Task 3B**: Use **codebase-analyzer** agent (model: sonnet)
```
For all discovered API endpoints, analyze the scalability posture of each:
- Pagination: which list endpoints have pagination, what type (offset, cursor, keyset), what are default/max page sizes
- Rate limiting: which endpoints have rate limiting, what are the limits, is it per-user or global
- Response sizes: which endpoints could return unbounded data, what's the typical payload size
- Caching headers: ETags, Cache-Control, Last-Modified on responses
- Timeouts: request timeout configuration per endpoint or globally
- Compression: response compression (gzip, brotli) enabled or missing
- Async patterns: which endpoints offload work to background jobs vs doing everything synchronously
- Long-running operations: endpoints that could take > 5s under load

Document each endpoint in this format:
[METHOD] /path - Paginated: [yes(type)|no] - Rate Limited: [yes(limit)|no] - Cached: [yes|no] - Timeout: [Ns|none] - Async: [yes|no] - Estimated payload: [size]

Write your complete findings to: {AUDIT_DIR}/05-api-scalability-posture.md
```

## Phase 4: Gap Analysis (after Phases 1, 2, 3 complete)

Wait for all previous phases to complete. Then read all artifacts from Phases 1-3:
- `{AUDIT_DIR}/01-infrastructure-recon.md`
- `{AUDIT_DIR}/02-architecture-analysis.md`
- `{AUDIT_DIR}/03-data-flow-analysis.md`
- `{AUDIT_DIR}/04-api-endpoints.md`
- `{AUDIT_DIR}/05-api-scalability-posture.md`

1. **Review completeness**: Examine the findings from Phases 1-3. Identify any areas that were insufficiently covered:
   - Were all services/modules analyzed?
   - Are there database query patterns that weren't fully traced?
   - Are there data stores that weren't examined for scaling characteristics?
   - Are there endpoint categories that were missed?

2. **Fill gaps**: If significant gaps exist, spawn targeted **codebase-locator** or **codebase-analyzer** agents (model: sonnet) for specific missing areas.

3. **Infrastructure Capacity Inventory**: Compile a summary of current capacity:
   - Database: engine, connection pool size, replica count, estimated max QPS
   - Cache: engine, memory allocation, cluster size, estimated max ops/s
   - Compute: instance type/size, auto-scaling range, current limits
   - Queues: type, worker count, concurrency settings, estimated throughput
   - External services: rate limits, quotas, SLAs

4. **Existing Benchmarks**: Search for any existing load test results, performance benchmarks, or capacity planning documents in the repository.

5. **Write gap analysis results** to `{AUDIT_DIR}/06-gap-analysis.md`.

## Phase 5: Scalability Assessment

Spawn the scalability assessment:

**Task 5A**: Use **scalability-auditor** agent (model: opus)
```
Conduct a comprehensive scalability assessment of this repository against a target load of [TARGET LOAD].

Read the following artifact files produced by the reconnaissance phases:
- {AUDIT_DIR}/01-infrastructure-recon.md
- {AUDIT_DIR}/02-architecture-analysis.md
- {AUDIT_DIR}/03-data-flow-analysis.md
- {AUDIT_DIR}/04-api-endpoints.md
- {AUDIT_DIR}/05-api-scalability-posture.md
- {AUDIT_DIR}/06-gap-analysis.md

Using all of the above intelligence, perform your full scalability assessment methodology:
1. Translate [TARGET LOAD] into concrete system metrics (requests/s, queries/s, connections, etc.)
2. Build the architecture capacity model
3. Systematically evaluate each S01-S10 dimension against the load translation
4. Produce findings in your standard format (SB-[YYYY]-[NNN])
5. Include S01-S10 coverage matrix
6. Provide capacity estimate (max load the system can handle as-is)
7. Rate overall production readiness

Focus especially on:
- Database query patterns that won't scale (N+1s, missing indexes, unbounded queries)
- Missing or misconfigured caching on hot paths
- Connection pool sizes vs required connections at target load
- Synchronous operations that should be async at scale
- Stateful components blocking horizontal scaling
- Missing resilience patterns (circuit breakers, timeouts, retries)

Write your complete assessment to: {AUDIT_DIR}/07-assessment-findings.md
```

## Verification Phase (after Phase 5, before Report)

Before generating the final report, you MUST verify the assessment findings. This is not optional.

Spawn a **codebase-analyzer** agent (model: opus) with the following prompt:

```
You are a findings verification reviewer. Your job is to independently verify
the assessment findings by cross-referencing them against the reconnaissance
artifacts and the actual codebase.

Read the assessment findings: {AUDIT_DIR}/07-assessment-findings.md

Read ALL reconnaissance artifacts for context:
- {AUDIT_DIR}/01-infrastructure-recon.md
- {AUDIT_DIR}/02-architecture-analysis.md
- {AUDIT_DIR}/03-data-flow-analysis.md
- {AUDIT_DIR}/04-api-endpoints.md
- {AUDIT_DIR}/05-api-scalability-posture.md
- {AUDIT_DIR}/06-gap-analysis.md

For each CRITICAL and HIGH finding:
1. Read the actual code at the referenced file:line to verify it matches what's described
2. Check the relevant recon artifacts to confirm the architectural context is accurate
3. Look for existing mitigations documented in the recon data that the auditor may have missed
4. Classify the finding as:
   - VERIFIED: Evidence confirmed in both code and recon artifacts
   - ADJUSTED: Finding is valid but severity or confidence should change (explain why)
   - UNSUBSTANTIATED: Evidence doesn't hold up or mitigations exist that neutralize the risk

For MEDIUM findings, spot-check at least 50% using the same process.

Write your verification report to: {AUDIT_DIR}/08-verification-results.md
Include: each finding ID, its verification status, your evidence, and any recommended adjustments.
```

After the verification agent completes:
- Apply its recommended adjustments to the findings before generating the final report
- Include a "Verification" subsection in the report's Methodology section noting how many
  findings were verified, adjusted, or removed

## Phase 6: Deep Bottleneck Analysis (optional - ask user)

After Phase 5 completes, if there are CRITICAL or HIGH findings:

Ask the user:
```
The scalability assessment found [N] CRITICAL and [M] HIGH severity bottlenecks.
Would you like me to run a deep code review on the affected hot paths for line-by-line analysis?
Files that would be reviewed:
- [list files from CRITICAL/HIGH findings]

This will provide detailed analysis of the bottleneck patterns and specific optimization recommendations.
```

If the user agrees, spawn targeted **codebase-analyzer** agents (model: opus) for the specific files and patterns identified in CRITICAL and HIGH findings. Append the results to the report.

---

## Report Generation

After the Verification Phase (and optionally Phase 6) completes:

### 1. Gather Metadata
- Get current date and time with timezone
- Get current git commit hash: `git rev-parse HEAD`
- Get current branch name: `git branch --show-current`
- Get repository name from git remote

### 2. Generate Report

Write the report to `{AUDIT_DIR}/report.md` with the following structure:

```markdown
---
date: [ISO datetime with timezone]
auditor: claude-scalability-audit
git_commit: [commit hash]
branch: [branch name]
repository: [repo name]
type: scalability-audit
status: complete
target_load: [the target load parameter]
findings:
  critical: [count]
  high: [count]
  medium: [count]
  low: [count]
  info: [count]
  total: [count]
overall_readiness: [CRITICAL|AT RISK|MODERATE|READY|EXCELLENT]
estimated_max_load: [capacity estimate from scalability-auditor]
tags: [scalability, audit, performance, production-readiness]
---

# Scalability Audit Report

**Date**: [date and time with timezone]
**Auditor**: Claude Scalability Audit
**Git Commit**: [commit hash]
**Branch**: [branch name]
**Repository**: [repo name]
**Target Load**: [target load]

---

## Executive Summary

**Overall Production Readiness**: [CRITICAL|AT RISK|MODERATE|READY|EXCELLENT]
**Estimated Maximum Load**: [capacity estimate - the load the system can handle as-is]

[2-3 paragraph summary of the scalability state of the application, key bottlenecks, and recommended priorities]

| Severity | Count |
|----------|-------|
| CRITICAL | [n]   |
| HIGH     | [n]   |
| MEDIUM   | [n]   |
| LOW      | [n]   |
| INFO     | [n]   |
| **Total** | **[n]** |

### Top 3 Bottlenecks
1. [Most critical bottleneck with brief explanation]
2. [Second most critical bottleneck]
3. [Third most critical bottleneck]

---

## Target Load Analysis

### Load Translation

| Metric | Estimated Value | Current Capacity | Headroom |
|--------|----------------|------------------|----------|
| API requests/second | [value] | [value] | [ok/tight/exceeded] |
| Database queries/second | [value] | [value] | [ok/tight/exceeded] |
| Cache operations/second | [value] | [value] | [ok/tight/exceeded] |
| Background jobs/minute | [value] | [value] | [ok/tight/exceeded] |
| WebSocket connections | [value] | [value] | [ok/tight/exceeded] |
| Connection pool requirements | [value] | [value] | [ok/tight/exceeded] |
| Storage I/O (ops/second) | [value] | [value] | [ok/tight/exceeded] |
| Bandwidth (MB/s) | [value] | [value] | [ok/tight/exceeded] |

### Assumptions
[Document key assumptions made during load translation]

---

## Scope & Methodology

### What Was Audited
- [List of components, services, and areas examined]

### What Was NOT Audited
- Runtime performance under actual load
- Third-party service actual capacity
- Network-level performance characteristics
- [Any other areas not covered]

### Methodology
- Static analysis of source code and configuration files
- Infrastructure as Code review for capacity settings
- Database query pattern analysis
- API endpoint scalability evaluation
- S01-S10 systematic dimension evaluation
- Load translation and capacity modeling

### Verification
- [N] findings independently verified against codebase and recon artifacts
- [N] findings adjusted (severity or confidence changed)
- [N] findings removed as unsubstantiated
- Verification report: `{AUDIT_DIR}/08-verification-results.md`

### Limitations
- This is a static analysis audit; runtime behavior may differ
- Load testing (performance testing) was not performed
- Actual database query plans and execution times are not available
- External service rate limits may differ from documented values
- [Any other limitations]

---

## Architecture Overview

[Component diagram or description from Phase 1-2 findings]

### Component Topology
[Services, databases, caches, queues and how they connect]

### Scaling Characteristics
[Which components scale horizontally, which are stateful, what are the bottleneck boundaries]

### Data Flow Summary
[High-level data flow paths highlighting hot paths and scaling concerns]

---

## Findings

### CRITICAL

[Full finding details using SB-YYYY-NNN format from scalability-auditor agent]

### HIGH

[Full finding details]

### MEDIUM

[Full finding details - can use condensed format for many findings]

### LOW

| ID | Title | Dimension | Component |
|----|-------|-----------|-----------|
| [table format for LOW findings] |

### INFO

| ID | Title | Dimension | Note |
|----|-------|-----------|------|
| [table format for INFO findings] |

---

## Scalability Dimensions Coverage

| Dimension | Status | Findings |
|-----------|--------|----------|
| S01: Database Scalability | [Tested/Not Applicable] | [SB-IDs or "No issues found"] |
| S02: API & Endpoint Design | [Tested/Not Applicable] | [SB-IDs or "No issues found"] |
| S03: Caching Strategy | [Tested/Not Applicable] | [SB-IDs or "No issues found"] |
| S04: Concurrency & Thread Safety | [Tested/Not Applicable] | [SB-IDs or "No issues found"] |
| S05: Infrastructure Elasticity | [Tested/Not Applicable] | [SB-IDs or "No issues found"] |
| S06: Data Partitioning & Distribution | [Tested/Not Applicable] | [SB-IDs or "No issues found"] |
| S07: Resilience & Fault Tolerance | [Tested/Not Applicable] | [SB-IDs or "No issues found"] |
| S08: Observability & Diagnostics | [Tested/Not Applicable] | [SB-IDs or "No issues found"] |
| S09: Resource Management | [Tested/Not Applicable] | [SB-IDs or "No issues found"] |
| S10: Architectural Patterns | [Tested/Not Applicable] | [SB-IDs or "No issues found"] |

---

## Bottleneck Heat Map

|                    | Low Impact | Medium Impact | High Impact |
|--------------------|-----------|---------------|-------------|
| **Hits first**     | [IDs] | [IDs] | [IDs] |
| **Hits at target** | [IDs] | [IDs] | [IDs] |
| **Hits beyond**    | [IDs] | [IDs] | [IDs] |

---

## Positive Observations

[Scalability patterns that are well-implemented - acknowledge what's done right]

---

## Scaling Roadmap

### Immediate (before target load)
- [ ] [CRITICAL and HIGH items that must be resolved before the system can handle target load]

### Short-Term (1-4 weeks)
- [ ] [MEDIUM items and scaling improvements to build headroom]

### Long-Term (1-3 months)
- [ ] [Architectural improvements for sustained growth beyond target load]

---

## Appendix

### Infrastructure Capacity Inventory
[Compiled capacity data from Phase 4]

### Existing Benchmarks
[Any load test results or performance benchmarks found in the repository]

### Files Reviewed
[List of key files examined during the audit]
```

### 3. Readiness Rating Criteria

| Rating | Criteria |
|--------|----------|
| CRITICAL | Any CRITICAL findings, or 3+ HIGH findings - system will fail well before target load |
| AT RISK | 1-2 HIGH findings, or 5+ MEDIUM findings - significant bottlenecks before target load |
| MODERATE | MEDIUM findings only, no HIGH or CRITICAL - system may handle target load with degradation |
| READY | Only LOW and INFO findings - system should handle target load |
| EXCELLENT | No findings, or only INFO-level observations - system is well-prepared for target load and beyond |

### 4. Present to User

After writing the report, present a concise summary:
- Overall readiness rating
- Estimated maximum load (capacity estimate)
- Finding counts by severity
- Top 3 bottlenecks
- Path to the full report
- Ask if they want to proceed with Phase 6 (if applicable) or have follow-up questions

---

## Important Notes

- **Target load required**: Do not begin the audit without a concrete target load parameter
- **Parallel execution**: Phases 1 and 3 MUST run in parallel for efficiency
- **Sequential dependencies**: Phase 2 depends on Phase 1; Phase 4 depends on 1+2+3; Phase 5 depends on 4
- **Artifact files**: Each phase must write its artifact file before subsequent phases can proceed — these files are the data handoff mechanism
- **Verification is mandatory**: The verification phase must not be skipped; it runs after Phase 5 and before report generation
- **Evidence-based**: The scalability-auditor agent requires file:line references for all findings
- **No false positives**: Better to miss a LOW finding than report a false CRITICAL
- **Load-anchored**: All findings must be evaluated against the target load, not theoretical concerns
- **User communication**: Keep the user informed of progress between phases
- **Report location**: Always write to `{AUDIT_DIR}/report.md` (inside the artifact directory)
- **Metadata first**: Always gather git metadata before writing the report (no placeholders)
- **Frontmatter consistency**: Include all YAML frontmatter fields; use snake_case for multi-word field names
