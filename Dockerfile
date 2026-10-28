FROM python:3.11-slim@sha256:673719e071ecf27434fd2635bbd8d00f49f4fdd81f243143438a42ced8bedf60

# Запуск от непривилегированного пользователя уменьшает последствия компрометации.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /opt/miac-inventory

COPY requirements.txt ./
RUN apt-get update \
    && apt-get install --no-install-recommends --yes fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements.txt \
    && groupadd --system app \
    && useradd --system --gid app --home-dir /opt/miac-inventory app

COPY --chown=app:app app ./app

USER app
EXPOSE 8080

# Один процесс достаточен для прототипа; в промышленной среде число воркеров
# выбирают после нагрузочных испытаний и с учётом журналирования.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1"]
