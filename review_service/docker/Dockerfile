FROM python:3.12-slim
# One image for the review UI/API and the outbox worker (compose.yaml picks the command).
# Build context is the repository root:  docker compose -f review_service/compose.yaml build
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

# Third-party dependencies first, from the exact versions the test suite ran against.
COPY review_service/requirements.lock review_service/requirements.lock
RUN python -m pip install -r review_service/requirements.lock

# The four local packages: F2 contracts, F7 reports, F6 review gate and the composition root.
COPY investigation/pyproject.toml investigation/pyproject.toml
COPY investigation/observa_investigation/ investigation/observa_investigation/
COPY f7_reports/pyproject.toml f7_reports/pyproject.toml
COPY f7_reports/observa_reports/ f7_reports/observa_reports/
COPY f6_hitl/pyproject.toml f6_hitl/pyproject.toml
COPY f6_hitl/observa_hitl/ f6_hitl/observa_hitl/
COPY review_service/ review_service/
# Editable installs keep review_service/fixtures next to the code: the seed command and the mock F2 read them.
RUN python -m pip install --no-deps -e ./investigation -e ./f7_reports -e ./f6_hitl -e ./review_service \
 && useradd --system --uid 10001 --no-create-home observa \
 && mkdir -p /data && chown observa /data

USER observa
WORKDIR /app/review_service
EXPOSE 8000
CMD ["python", "-m", "observa_review_service.worker", "--loop"]
