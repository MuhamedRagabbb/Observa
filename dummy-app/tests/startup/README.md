# Automatic startup validation — 2026-11-06

Normal use, from `dummy-app`:

```powershell
docker compose up --build -d
```

The startup change adds a one-shot migration job, gates checkout services on its
successful exit, waits for PostgreSQL TCP readiness, and includes Grafana by default.
Versioned SQL files and the migration engine's execution logic are unchanged.

Validation to date:

- Application image built successfully as `observa-dummy:startup-validation`.
- Startup, preview isolation and live-test safety unit checks: 19 passed before
  the final TCP readiness assertion. Final rerun: 11 passed (startup and safety);
  eight preview checks could not launch Docker from Python on Windows
  (`WinError 5: Access is denied`, reported as two failures and six setup errors).
  Direct `docker compose config --quiet` validation succeeded for both default
  and preview Compose files after the final change.
- Isolated fresh-volume Docker run: `db-migrate` exited with code 0.
- Full checkout/restart/failure-gate verification is **incomplete**. During the
  run Docker reported a read-only filesystem and its PostgreSQL container stopped;
  Windows drive C had only about 0.4 GB free. This is not a passing end-to-end run.
- Test project `observa-startup-check-6c9e3657` was stopped. Docker could not remove
  its containers because its metadata filesystem was read-only. Volumes were kept.
- The normal application stack was not redeployed by this validation.

After storage is available and Docker is healthy, developers can reproduce the
isolated validation (no published ports; its own project, image and volumes):

```powershell
docker build -t observa-dummy:startup-validation .
python tests/startup/verify_docker.py
```

The verifier checks a fresh checkout, Grafana without a profile, restart with
unchanged order/migration history, and blocked checkout services after failed
database setup. Reports are written under ignored `test-results/`; test volumes
are retained, never pruned. These are developer checks, not required startup steps.

## Follow-up review — 2026-11-06

The full dummy unit suite now passes: **807 passed, 2 skipped**. The earlier
Windows subprocess errors did not recur in this run. One existing invalid-escape
warning came from a test string.

Docker recovered temporarily and existing business containers were observed
healthy. A fresh isolated run (`observa-startup-check-4992aee9`) completed its
migration job, but Docker's API returned HTTP 500 during subsequent service
startup. Checkout, restart preservation and failure-gate checks did not complete.
Automatic removal failed; a later explicit stop succeeded for every container
in this test project. Containers and volumes were retained. See that run's
`test-results/observa-startup-check-4992aee9/result.json` for the captured error.

The alert feature subsequently built and started successfully and exported 13
saved incidents to its host reports directory. Docker was unavailable again at
final inspection. No Docker reset or volume deletion was performed.
