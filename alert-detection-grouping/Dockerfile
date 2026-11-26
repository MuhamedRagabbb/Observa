FROM python:3.12.8-slim-bookworm
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN useradd --uid 10001 --create-home observa && mkdir /app/data /app/reports && chown -R observa:observa /app
COPY --chown=observa:observa observa_alerts ./observa_alerts
COPY --chown=observa:observa config.json ./config.json
USER observa
CMD ["python", "-m", "observa_alerts", "watch", "--config", "config.json"]
