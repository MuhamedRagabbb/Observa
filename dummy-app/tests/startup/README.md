# Docker startup checks

The Compose stack waits for PostgreSQL, applies the service migrations and then starts the checkout services. A successful `db-migrate` container exits with code 0. For normal startup, see the [checkout application guide](../../README.md).

## Run the isolated verifier

From `dummy-app/`, with Python and Docker Compose available:

```sh
docker build -t observa-dummy:startup-validation .
python tests/startup/verify_docker.py
```

The verifier creates a separate Compose project with fresh volumes, its own journal and no published host ports. It checks that:

- The migrations complete and a checkout succeeds on a fresh database.
- Grafana starts with the default services.
- Restarting the stack preserves orders and migration history.
- Failed database setup prevents the dependent checkout services from starting.

Inspect the run's `result.json` under `dummy-app/test-results/` for completed checks and any error. The verifier retains the test volumes for inspection. A successful migration by itself only verifies the database setup step.
