FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app:/app/investigation:/app/alert-detection-grouping:/app/us01_logs_agent:/app/us02_metrics_agent:/app/us03_traces_agent
COPY investigation/requirements.txt investigation/
COPY investigation/pyproject.toml investigation/
COPY investigation/observa_investigation/ investigation/observa_investigation/
COPY correlation_agent/requirements.txt correlation_agent/
RUN python -m pip install --no-cache-dir -r correlation_agent/requirements.txt
COPY alert-detection-grouping/observa_alerts/ alert-detection-grouping/observa_alerts/
COPY us01_logs_agent/logs_agent/ us01_logs_agent/logs_agent/
COPY us02_metrics_agent/metrics_agent/ us02_metrics_agent/metrics_agent/
COPY us03_traces_agent/traces_agent/ us03_traces_agent/traces_agent/
COPY observa/ observa/
COPY correlation_agent/ correlation_agent/
ENTRYPOINT ["python", "-m", "correlation_agent.docker.run"]
