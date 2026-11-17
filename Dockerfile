# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --retries 8 --timeout 600 -r requirements.txt

COPY src/ ./src/
COPY dashboard.py ./dashboard.py
COPY .streamlit/ ./.streamlit/

RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8501

CMD ["python", "src/etl.py", "--schedule"]
