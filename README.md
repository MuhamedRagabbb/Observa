# OBSERVA

**OBSERVA** is an Agentic AI-powered observability system designed to automate incident investigation and root cause analysis.

The system uses a **multi-agent architecture** to analyze and correlate three main observability signals: **Logs, Metrics, and Traces**.

By combining these signals, OBSERVA aims to identify abnormal system behavior, investigate potential failures, and generate actionable insights and recommendations.

### Key Features
- AI-powered incident investigation
- Multi-agent analysis using LangGraph
- Logs, Metrics, and Traces correlation
- Automated Root Cause Analysis (RCA)
- Evidence-based incident reports

### Tech Stack
Python · LangGraph · LangChain · LLMs · Prometheus · OpenTelemetry · Docker

### Repository layout
- [`dummy-app/`](dummy-app/README.md): the local test bed OBSERVA investigates (four FastAPI services, PostgreSQL, Collector, Prometheus, Loki, Jaeger, a traffic generator and three injectable faults). Start there to run it. Feature work such as F1 Alert Detection & Grouping reads its logs, metrics and traces; see "For OBSERVA feature teams" in that README.

### Project Status
🚧 **Under Development**
