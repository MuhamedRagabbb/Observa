# Live Docker validation

Run from alert-detection-grouping on a host with Docker Desktop/Engine and Python 3.12.
The dummy-app sibling is read-only input. Both images receive test-only tags. Each run gets unique
Compose project names, volumes and networks. No host service ports are published.
Generated manifests and captures live in validation/ (ignored by Git and Docker build).

1. Build the source images:

       docker build -t observa-alerts:validation .
       docker build -t observa-dummy:alert-validation ../dummy-app
       python tests/prepare_docker.py

2. In PowerShell, load the generated paths and start the isolated dummy:

       $run = Get-Content validation/current-run.json -Raw | ConvertFrom-Json
       docker compose -f $run.dummy up -d --wait --wait-timeout 180 postgres collector prometheus loki jaeger inventory payments orders gateway fault-injector telemetry-health

3. Apply checkout migrations to the isolated databases:

       foreach ($serviceName in @('inventory','payments','orders')) {
         $code = "import os, asyncio; from services.shared.migrations import _main; asyncio.run(_main(os.environ['DATABASE_URL'], 'scripts/migrations/$serviceName'))"
         docker compose -f $run.dummy exec -T $serviceName python -c $code
         if ($LASTEXITCODE -ne 0) { throw "Migration failed: $serviceName" }
       }

4. Start the detector with its independent Compose project and then test traffic:

       docker compose -f $run.detector up -d
       docker compose -f $run.dummy --profile traffic up -d --no-deps traffic-generator
       python tests/run_docker_validation.py
       python tests/audit_docker_capture.py

The checkout mix runs at 1 request/second to establish a healthy baseline on a laptop.
The detector thresholds and timing remain the shipped defaults.
The runner verifies a stable baseline before injecting faults and waits for recovery between cases.
Controller calls use the expected loopback Host header; only the evaluation probe calls the controller.
The detector container has only the observability network and no access to evaluation output files.

The runner checkpoints results after every case. An interrupted run retains snapshots and execution.jsonl.
If the named probe is still alive, --resume-from payments-402 (or pool/deployment) resumes at that case.
Do not start a second runner while one is active.

Outputs: results.json, execution.jsonl, per-phase sample.json/summary.json and incident JSON/Markdown.
During-fault and recovered snapshots are distinct, so the handoff contains actual trigger and recovery evidence.
The full collector samples are evaluation captures, not ground-truth inputs to detection.
The capture audit additionally asserts a single expected incident per fault, evidence-backed cross-service
grouping for 503/pool/deployment, 402-only payment alerts, all four recoveries, and restart continuity.
Detection delays measure fault activation response to incident creation; polling, scrape and rule-window
delays are included. These are single-run measurements, not latency guarantees.

After copying desired reports, stop only these generated projects (keep volumes):

    docker compose -f $run.detector down
    docker compose -f $run.dummy --profile traffic --profile probe down

Do not use a generic down -v or prune. Retained test volumes can be removed later by exact verified names.
