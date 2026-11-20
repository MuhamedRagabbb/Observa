# OBSERVA

OBSERVA watches a checkout application for failures. It groups related alerts into an incident (F1), investigates the logs, metrics and traces for that incident (F2), then writes a report explaining where the problem likely started and how it affected other services (F3).

## Run locally

You need Docker with Compose v2 and a Groq API key. Run the commands from the repository root.

1. Create `investigation/.env` for F2 and `correlation_agent/.env` for F3. In each file, add the following line with your own key.

   ```dotenv
   GROQ_API_KEY=your_groq_key_here
   ```

2. Start the checkout application, the detector, then the investigation worker. If the first two are already running, use just the last command.

   ```sh
   docker compose -f dummy-app/docker-compose.yml up --build -d
   docker compose -f alert-detection-grouping/docker-compose.yml up --build -d
   docker compose -f correlation_agent/compose.yaml up --build -d
   ```

## Try a payment failure

1. Open the [local operator](http://127.0.0.1:8090/) and wait for the services to show **Healthy**.
2. Under **Manual fault injection**, select **Payment Errors**, choose **503 Service Unavailable**, set **Duration** to **120 seconds** and click **Inject fault**. The app generates checkout traffic automatically.
3. Follow the investigation in the terminal. When F1 detects the failures, F2 and F3 pick up the incident automatically. The worker also processes saved incidents, so follow the ID for your new payment failure.

   ```sh
   docker compose -f correlation_agent/compose.yaml logs -f --tail 20 investigate
   ```

4. When you see `report saved to`, open `correlation_agent/runs/<incident-id>/report.md` on your machine. For this experiment, look for the failed charge in `payments-service`, the failed call from `orders-service`, and the error visible at `api-gateway`. Each step cites the evidence behind it. The **Incident history** heading says **initial** while the opening assessment is the latest report. It changes to **final** after F1 records recovery and F3 publishes the closing assessment. [Example report](correlation_agent/examples/example_report.md)

## Project status

| Stage | Progress |
| --- | --- |
| F1–F3 · Detection, investigation and correlation | Running locally with Docker. The operator can simulate database connection exhaustion, payment errors and a bad deployment. |
| Current testing | Checking that each experiment produces a final report. Payment errors and database exhaustion produced final reports in the latest test. The bad deployment produced an opening report identifying the faulty release, but Groq rate limits delayed the final report through repeated retries. |
| F4 · Recommendations | Next planned feature. It will use the correlation report to suggest what the operator can do about the incident. |
| F5–F7 · Incident memory, human review and final reporting | Planned. These stages will retain past investigations, collect operator feedback and assemble the reviewed report. |
