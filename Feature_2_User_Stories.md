# Feature 2 — Multi-Signal Analysis

**Scope:** Investigate each grouped incident using three signal agents (Logs, Metrics, Traces), managed by the Investigation Orchestrator, and produce structured, evidence-backed results for the Correlation Agent (Feature 3).

**Suggested implementation order:** US-04 (orchestration core) → US-01 / US-02 / US-03 (signal agents) → US-04 (aggregation)

---

## US-01: Analyze Application Logs

### User Story
**As a** developer,  
**I want** the Logs Agent to analyze incident-related logs,  
**So that** the system can identify errors, exceptions, and abnormal events.

### Acceptance Criteria
- [ ] Retrieve logs within the incident time range.
- [ ] Identify relevant errors and affected services.
- [ ] Return structured findings with evidence references.
- [ ] Report missing or insufficient log data.

### Technical Tasks
- [ ] Implement the Logs Agent.
- [ ] Implement the `query_logs` tool.
- [ ] Extract structured findings and evidence.
- [ ] Write automated tests.

**Related Feature:** Feature 2 — Multi-Signal Analysis  
**Dependencies:** Feature 1 — Alert Detection & Grouping; Logs telemetry source; US-04 Orchestrate & Aggregate Investigation (dispatching)  
**Status:** Draft — Pending Instructor Review

---

## US-02: Analyze Application Metrics

### User Story
**As a** developer,  
**I want** the Metrics Agent to analyze incident-related metrics,  
**So that** the system can identify performance issues and measure their impact.

### Acceptance Criteria
- [ ] Retrieve metrics within the incident time range.
- [ ] Identify deviations in latency, error rate, and resource utilization.
- [ ] Return findings with supporting metric references.
- [ ] Report missing or insufficient metric data.

### Technical Tasks
- [ ] Implement the Metrics Agent.
- [ ] Implement the `query_metrics` tool.
- [ ] Analyze metric deviations.
- [ ] Write automated tests.

**Related Feature:** Feature 2 — Multi-Signal Analysis  
**Dependencies:** Feature 1 — Alert Detection & Grouping; Metrics telemetry source; US-04 Orchestrate & Aggregate Investigation (dispatching)  
**Status:** Draft — Pending Instructor Review

---

## US-03: Analyze Distributed Traces

### User Story
**As a** developer,  
**I want** the Traces Agent to analyze incident-related traces,  
**So that** the system can identify slow requests, failed operations, and service bottlenecks.

### Acceptance Criteria
- [ ] Retrieve relevant distributed traces.
- [ ] Identify slow or failed spans and affected services.
- [ ] Return findings with trace and span references.
- [ ] Report missing or incomplete trace data.

### Technical Tasks
- [ ] Implement the Traces Agent.
- [ ] Implement the `query_traces` tool.
- [ ] Analyze request paths and span durations.
- [ ] Write automated tests.

**Related Feature:** Feature 2 — Multi-Signal Analysis  
**Dependencies:** Feature 1 — Alert Detection & Grouping; Traces telemetry source; US-04 Orchestrate & Aggregate Investigation (dispatching)  
**Status:** Draft — Pending Instructor Review

---

## US-04: Orchestrate & Aggregate Investigation

### User Story
**As a** developer,  
**I want** the Orchestrator to manage the investigation across the three signal agents and aggregate their results,  
**So that** the Correlation Agent receives structured, evidence-backed results from a reliable investigation.

### Acceptance Criteria
- [ ] Receive the incident context (time range, affected services, grouped alerts) and initialize the investigation state.
- [ ] Create an investigation plan from the incident context and update it when additional evidence or targeted re-investigation is required.
- [ ] Dispatch investigation tasks to the Logs, Metrics, and Traces Agents and track each agent's status (pending, running, completed, failed).
- [ ] Handle agent failures and timeouts with bounded, configurable retries, and preserve partial results if an agent keeps failing.
- [ ] Support re-running the investigation (fully or for a specific agent) with updated parameters, using configurable limits to prevent infinite loops.
- [ ] Collect findings and evidence references from all three agents into the investigation state.
- [ ] Check evidence completeness and explicitly record missing or unavailable evidence before handing off to the Correlation Agent.
- [ ] Accept targeted follow-up investigation requests from Feature 3 and structured reviewer intent from Feature 6, without treating reviewer feedback as verified evidence.
- [ ] Produce a structured output containing all three agent results and hand it to the Correlation Agent.

### Technical Tasks
- [ ] Implement investigation planning, plan updates, task dispatching, investigation state, and agent status tracking.
- [ ] Implement configurable retry/timeout and re-investigation limits.
- [ ] Implement aggregation and explicit evidence completeness checks, preserving partial and failed statuses.
- [ ] Define and integrate the targeted follow-up contract from Feature 3 and reviewer-intent handoff from Feature 6.
- [ ] Generate the structured output for the Correlation Agent.
- [ ] Write automated tests for initial investigation, missing evidence, partial failure, targeted follow-up, and retry-limit exhaustion.

### Scope Note
This story covers the **ability** to re-run an investigation and respond to Feature 3 evidence-gap requests. The **decision** to re-run based on human rejection or feedback belongs to Feature 6 — HITL & Feedback. Implement the initial orchestration core using agent interfaces/mocks before integrating US-01 to US-03, then complete aggregation after those agents are ready.

**Related Feature:** Feature 2 — Multi-Signal Analysis  
**Dependencies:** Feature 1 — Alert Detection & Grouping; US-01 Analyze Application Logs; US-02 Analyze Application Metrics; US-03 Analyze Distributed Traces  
**Status:** Draft — Pending Instructor Review
