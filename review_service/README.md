# OBSERVA review service (F7 reports + F6 human review)

This service turns an incident's F1–F4 outputs into a versioned report (F7) and puts every version in front of an SRE reviewer (F6). The reviewer either approves that exact version or requests changes in writing. An approved version is handed to incident memory (F5). A change request is interpreted into structured revision requests, sent to F2 for a targeted re-investigation, and the revised report comes back as the next version for another review. Nothing is approved automatically.

F4 (recommendations) and F5 (memory) don't exist yet, and the F2 follow-up API isn't final. Until they ship, the service uses local stand-ins:

- F4 output comes from the fixture bundles in `fixtures/`.
- F5 writes one JSON file per approved decision.
- The F2 mock answers from the fixture bundles. If a bundle exists for the incident with an F2 run that hasn't been published, it becomes the revision. Otherwise the revision fails visibly.

## Packages

| Folder | What it owns |
| --- | --- |
| `f7_reports/` (`observa_reports`) | Assembly, immutable content-addressed versions, compare-and-swap version numbers, Markdown export |
| `f6_hitl/` (`observa_hitl`) | Reviewer identities, the review gate, the transactional outbox, feedback interpretation (fake or Groq), the re-investigation loop |
| `review_service/` (`observa_review_service`) | The FastAPI app, dashboard, worker, migrations, mocks and fixtures |

F6 is optional. Without it (`pip install -e ./review_service` instead of `[f6]`), the reports are read-only and every version shows as pending review.

## Run with Docker

Run these commands from the repository root. Docker Desktop must be running.

1. Create the two local configuration files. Both are git-ignored.

   ```sh
   cp review_service/.env.example review_service/.env
   cp review_service/reviewers.example.json review_service/reviewers.json
   ```

2. Pick a reviewer token and store only its hash in `review_service/reviewers.json` (see [Reviewer tokens](#reviewer-tokens)). Set `SESSION_SECRET` in `review_service/.env`. Set `SERVICE_TOKEN_SHA256` too if F7 or F2 will call `/internal/v1/*`.

3. Start the UI and the worker, then load the demo reports:

   ```sh
   docker compose -f review_service/compose.yaml up --build -d
   docker compose -f review_service/compose.yaml exec review python -m observa_review_service.seed --database-url sqlite:////data/review.sqlite3 --bundles payments_v1 db_exhaustion bad_deployment
   ```

4. Open http://127.0.0.1:8095/ui and sign in with your reviewer token.

The UI listens on 127.0.0.1:8095 only. The compose project is called `observa-review`, and the database and the F5 records live in its `review-data` volume. Stop it with `docker compose -f review_service/compose.yaml down`. Add `-v` only if you want to delete the reviews.

## Run without Docker

Use Python 3.12. Run these from the repository root.

```sh
python -m venv .venv-review
.venv-review/Scripts/python -m pip install -r review_service/requirements.lock      # bin/python on Linux/macOS
.venv-review/Scripts/python -m pip install --no-deps -e ./investigation -e ./f7_reports -e ./f6_hitl -e "./review_service[test,f6]"
cd review_service
../.venv-review/Scripts/python -m observa_review_service.seed --bundles payments_v1 db_exhaustion bad_deployment
../.venv-review/Scripts/uvicorn observa_review_service.app:create_app --factory --host 127.0.0.1 --port 8095
../.venv-review/Scripts/python -m observa_review_service.worker --loop      # in a second terminal
```

Use `python -m observa_review_service.worker --once` to deliver everything that is due and exit.

## Reviewer tokens

`reviewers.json` holds sha256 hashes, never tokens. Generate a token, keep it somewhere safe, and store its hash:

```sh
python -c "import secrets; print(secrets.token_urlsafe(24))"                                   # the token you sign in with
python -c "import hashlib, sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest())" <token>  # goes into reviewers.json
```

```json
{"reviewers": [{"reviewer_id": "rev-ahmed", "display_name": "Ahmed (SRE)", "roles": ["reviewer", "viewer"],
                "token_sha256": "<64 hex characters>"}]}
```

- **`viewer`** can read reports.
- **`reviewer`** can also approve or request changes.

API clients send `Authorization: Bearer <token>`. The dashboard signs you in with the same token and keeps a signed, HttpOnly session cookie. Every form also carries a CSRF token. Without a `reviewers.json` file, reads stay open on localhost and nobody can decide.

## How a review goes

1. A version arrives through `POST /internal/v1/reports` or the seed command, and waits in **pending review**.
2. **Approve** records the decision for exactly that version. The worker hands it to F5, and the badge goes from `pending` to `stored`. If F5 is down, it shows `failed` and can be retried with `POST /internal/v1/outbox/{event_id}/retry`.
3. **Request changes** needs written feedback. The worker interprets it:
   - **Unclear feedback** → *clarification needed*. Nothing is re-investigated, and the reviewer decides again.
   - **Clear feedback** → a targeted F2 follow-up. Its revision becomes **vN+1**, pending review, and the older version is marked *superseded*.
   - **Failed revision** → *revision failed*. vN stays current, and the reviewer can still approve it explicitly.
4. After `MAX_REVIEW_CYCLES` rounds, a further change request is still recorded, but the cycle stops in *limit reached* and nothing is dispatched.

Two features are reviewable now: decisions use an `Idempotency-Key`, and a stale version or a competing decision gets `409 VERSION_CONFLICT`. Every error body is the contract `ErrorEnvelope`.

## API

| Method and path | Auth | Purpose |
| --- | --- | --- |
| `GET /api/v1/reports[/{id}[/versions[/{v}[/report.md]]]]` | viewer | Reports, history, exact versions, Markdown export |
| `POST /api/v1/reports/{id}/versions/{v}/decisions` | reviewer + `Idempotency-Key` | `{"decision": "approve" \| "request_changes", "feedback": ...}`: 201 new, 200 replay |
| `GET /api/v1/reports/{id}/review` | viewer | Cycles, decisions, interpreted feedback, re-investigations, memory sync, limit |
| `POST /internal/v1/reports` | service token | Publish F1–F4 inputs (`ReportInputs`) as the next version |
| `POST /internal/v1/reports/{id}/revisions` | service token | F2's revised inputs for a re-investigating cycle |
| `POST /internal/v1/reinvestigations/{request_id}/failed` | service token | F2 could not revise |
| `POST /internal/v1/outbox/{event_id}/retry` | service token | Requeue a failed event |

The interactive schema is at http://127.0.0.1:8095/docs.

## Groq interpretation

The default `INTERPRETER=fake` is a deterministic keyword mapper, and the tests use it.

For real interpretation, set `INTERPRETER=groq` and `GROQ_API_KEY` in `.env`. Groq output must validate against the `InterpretedFeedback` schema and may only name findings, causes, recommendations, signals or services of the reviewed version. instructor feeds validation errors back up to 2 times. If the output is still invalid, the feedback is marked inconclusive and the raw output is kept. Groq outages and rate limits are retried by the outbox. Reviewer text is treated as data: it can never approve a report or call a tool.

## Tests

From the repository root:

```sh
.venv-review/Scripts/python -m pytest -q -p no:cacheprovider --rootdir=. f7_reports/tests f6_hitl/tests review_service/tests
```

`--rootdir` matters: each package has its own pytest settings. The end-to-end loop is `review_service/tests/e2e/`. On Windows, add `--basetemp=<a folder you own>` if pytest can't use the default temp folder.
