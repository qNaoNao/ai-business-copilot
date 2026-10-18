FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    COPILOT_RETRIEVAL=bm25 \
    LANGSMITH_TRACING=false \
    BUSINESS_DB_PATH=/app/.demo/business.db \
    COPILOT_ANALYSIS_STORE=/app/runs/analyses.sqlite3 \
    COPILOT_DEMO_ONLY=true \
    STREAMLIT_SERVER_HEADLESS=true

WORKDIR /app
COPY requirements.lock.txt ./
RUN python -m pip install --no-cache-dir -r requirements.lock.txt \
    && python -m pip check \
    && useradd --create-home --uid 10001 copilot \
    && mkdir -p /app/runs /app/.demo /app/.cache \
    && chown -R copilot:copilot /app
COPY --chown=copilot:copilot src ./src
COPY --chown=copilot:copilot knowledge_base ./knowledge_base
COPY --chown=copilot:copilot .streamlit/config.toml ./.streamlit/config.toml
COPY --chown=copilot:copilot app.py ./
USER copilot
RUN python -m src.demo_data

EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=3s --start-period=30s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=2)"
CMD ["python", "-m", "streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501", "--browser.gatherUsageStats=false"]
