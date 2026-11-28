# OBSERVA

OBSERVA watches a checkout application for failures. It groups related alerts into an incident (F1), investigates the logs, metrics and traces for that incident (F2), then writes a report explaining where the problem likely started and how it affected other services (F3). F4 validates and ranks recommendations using current evidence and approved F5 memory.

## Run locally

You need Docker with Compose v2 and a Groq API key. Run the commands from the repository root.

1. Create `investigation/.env` for F2 and `correlation_agent/.env` for F3. In each file, add the following line with your own key.

   ```dotenv
   GROQ_API_KEY=your_groq_key_here
   ```

2. Start the full stack from the repository root. The root Compose file includes the checkout app, F1 detector, F2/F3 worker, F5 memory initializer and F4 recommendation worker. Existing incidents are preserved.

   ```sh
   docker compose -f compose.yaml up --build -d
   ```

   F4 watches F3's published outputs and writes `correlation_agent/runs/<run>/f4/output.json` and `output.md`. Opening outputs also keep an `.initial` copy. Set `F4_USE_LLM=0` for deterministic generation without provider calls.

## Try a payment failure

1. Open the [operator](http://127.0.0.1:8090/) and wait for the services to show **Healthy**. Check `docker compose -f compose.yaml logs --tail 5 alerts` and wait for `awaiting_first_sample` to be empty.
2. Under **Manual fault injection**, select **Payment Errors**, choose **503 Service Unavailable**, set **Duration** to **120 seconds** and click **Inject fault**. The app generates checkout traffic automatically.
3. Follow the investigation in the terminal. When F1 detects the failures, F2 and F3 pick up the incident automatically. The workers also process saved incidents, so follow the ID for your new payment failure. F4 starts after F3 publishes its output.

   ```sh
   docker compose -f compose.yaml logs -f --tail 20 investigate
   ```

4. When you see `report saved to`, open `correlation_agent/runs/<run>/f3/output.md` on your machine. For this experiment, look for the failed charge in `payments-service`, the failed call from `orders-service`, and the error visible at `api-gateway`. Each step cites the evidence behind it. The **Incident history** heading says **initial** while the opening assessment is the latest report. It changes to **final** after F1 records recovery and F3 publishes the closing assessment. [Example report](correlation_agent/examples/example_report.md)

When F3 needs more evidence, the worker sends the question back to the relevant F2 signal agents. Open **Follow-up evidence** in the same report to read each answer and its citations or identify gaps in the captured evidence. If Groq rate-limits a report, the worker retries using saved F2 results.

## Feature guides

| Feature | Where to start |
| --- | --- |
| F1 · Detection and grouping | [Run the detector and inspect its incident bundles](alert-detection-grouping/README.md) |
| F2 · Investigation | [Run F2 or an individual signal agent on a saved incident](investigation/README.md) |
| F3 · Correlation | [Run the Docker worker or render a saved report](correlation_agent/README.md) |

## Project status

Detection, investigation and correlation are available in the local Docker stack. You can exercise them with database connection exhaustion, payment failures or a bad deployment. If Groq rate-limits a final report, the opening report remains available until the worker publishes an update.

Recommendations now run after F3. Incident memory is available; operator approval and final reporting are not yet connected end-to-end.
