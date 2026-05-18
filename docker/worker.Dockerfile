FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY . .
RUN pip install --upgrade pip && pip install -e ".[llm]"

CMD ["python", "-u", "apps/worker/main.py"]
