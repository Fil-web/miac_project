#!/usr/bin/env bash
set -Eeuo pipefail

# Обновление выполняется из каталога проекта независимо от текущего каталога оболочки.
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${MIAC_BACKUP_DIR:-${PROJECT_DIR}/backups}"
MIN_ENGINE_VERSION="29.5.1"
CHECK_ONLY=false
BACKUP_PARTIAL=""

log() {
    printf '[МИАЦ] %s\n' "$*"
}

fail() {
    printf '[МИАЦ] Ошибка: %s\n' "$*" >&2
    exit 1
}

usage() {
    cat <<'EOF'
Использование: bash scripts/update.sh [--check]

  --check       Проверить настройки и текущие версии без изменений.

Скрипт запускается на Linux-сервере с Docker Engine 29.5.1+ и Compose v2.
Для существующей базы создаётся резервная копия в каталоге backups/.
EOF
}

cleanup() {
    # Неполный архив БД нельзя оставлять рядом с действительными копиями.
    if [[ -n "$BACKUP_PARTIAL" && -f "$BACKUP_PARTIAL" ]]; then
        rm -f -- "$BACKUP_PARTIAL"
    fi
}

trap cleanup EXIT
trap 'printf "[МИАЦ] Ошибка на строке %s. Обновление остановлено; проверьте вывод выше.\n" "$LINENO" >&2' ERR

for argument in "$@"; do
    case "$argument" in
        --check) CHECK_ONLY=true ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; fail "Неизвестный параметр: $argument" ;;
    esac
done

cd "$PROJECT_DIR"
umask 077

require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "Не найдена команда $1."
}

version_at_least() {
    # Сравниваем три числовых компонента без зависимости от внешних утилит.
    local actual="${1#v}" minimum="$2" index current required
    local -a actual_parts minimum_parts
    actual="${actual%%[-+]*}"
    IFS='.' read -r -a actual_parts <<< "$actual"
    IFS='.' read -r -a minimum_parts <<< "$minimum"
    for index in 0 1 2; do
        current="${actual_parts[index]:-0}"
        required="${minimum_parts[index]:-0}"
        [[ "$current" =~ ^[0-9]+$ && "$required" =~ ^[0-9]+$ ]] || return 1
        if (( 10#$current > 10#$required )); then return 0; fi
        if (( 10#$current < 10#$required )); then return 1; fi
    done
    return 0
}

check_project() {
    [[ "$(uname -s)" == Linux ]] || fail "Скрипт предназначен для Linux-сервера."
    require_command docker
    require_command python3
    [[ -f .env ]] || fail "Нет .env. Создайте его из .env.example и задайте секреты."
    [[ -f requirements.txt && -f Dockerfile && -f docker-compose.yml ]] \
        || fail "Не найдены обязательные файлы проекта."
    grep -Eq '^FROM python:3\.11-slim@sha256:[0-9a-f]{64}$' Dockerfile \
        || fail "Базовый образ Python не закреплён по SHA-256 digest."
    grep -Eq '^[[:space:]]*image:[[:space:]]*postgres:15-alpine@sha256:[0-9a-f]{64}[[:space:]]*$' docker-compose.yml \
        || fail "Образ PostgreSQL не закреплён по SHA-256 digest."
    docker compose version >/dev/null
    docker compose config --quiet
    if grep -Eq "^(POSTGRES_PASSWORD|SESSION_SECRET)=[\"']?change_me" .env; then
        fail "В .env остались примерные секреты. Замените их перед запуском."
    fi
    local fastapi_version starlette_version multipart_version
    fastapi_version="$(awk -F'==' '$1 == "fastapi" {print $2}' requirements.txt)"
    starlette_version="$(awk -F'==' '$1 == "starlette" {print $2}' requirements.txt)"
    multipart_version="$(awk -F'==' '$1 == "python-multipart" {print $2}' requirements.txt)"
    version_at_least "$fastapi_version" "0.162.0" \
        || fail "FastAPI должен быть версии 0.162.0 или новее."
    version_at_least "$starlette_version" "1.10.0" \
        || fail "Starlette должен быть версии 1.10.0 или новее."
    version_at_least "$multipart_version" "0.0.22" \
        || fail "python-multipart должен быть версии 0.0.22 или новее."
    docker info >/dev/null 2>&1 || fail "Docker Engine недоступен. Запустите службу Docker на сервере."
}

backup_database() {
    local db_id running backup_file volume_name
    db_id="$(docker compose ps -a -q db)"
    if [[ -z "$db_id" ]]; then
        volume_name="$(docker compose config --format json | python3 -c 'import json,sys; print(json.load(sys.stdin)["volumes"]["postgres_data"]["name"])')"
        if docker volume inspect "$volume_name" >/dev/null 2>&1; then
            fail "Том БД $volume_name существует без контейнера. Нужна ручная резервная копия перед обновлением."
        fi
        log "Контейнер БД ещё не создан; резервная копия для первого запуска не нужна."
        return 0
    fi
    running="$(docker inspect --format '{{.State.Running}}' "$db_id")"
    [[ "$running" == true ]] \
        || fail "Контейнер БД остановлен. Запустите его и повторите обновление для создания копии."

    mkdir -p -- "$BACKUP_DIR"
    chmod 700 -- "$BACKUP_DIR"
    backup_file="${BACKUP_DIR}/miac-$(date '+%Y%m%d-%H%M%S')-$$.dump"
    BACKUP_PARTIAL="${backup_file}.partial"
    log "Создаю резервную копию PostgreSQL: $backup_file"
    docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' \
        > "$BACKUP_PARTIAL"
    docker compose exec -T db pg_restore --list < "$BACKUP_PARTIAL" >/dev/null
    mv -- "$BACKUP_PARTIAL" "$backup_file"
    BACKUP_PARTIAL=""
    chmod 600 -- "$backup_file"
    log "Резервная копия создана и проверена."
}

verify_engine() {
    local engine_version
    engine_version="$(docker version --format '{{.Server.Version}}')"
    version_at_least "$engine_version" "$MIN_ENGINE_VERSION" \
        || fail "Docker Engine $engine_version ниже $MIN_ENGINE_VERSION. Обновите пакет Engine на сервере и повторите запуск."
    log "Docker Engine: $engine_version."
}

show_runtime_versions() {
    local engine_version app_id db_id
    engine_version="$(docker version --format '{{.Server.Version}}')"
    log "Docker Engine: $engine_version"
    if ! version_at_least "$engine_version" "$MIN_ENGINE_VERSION"; then
        log "Engine ниже требуемого $MIN_ENGINE_VERSION; обновите пакет Docker на сервере."
    fi
    app_id="$(docker compose ps -q app)"
    db_id="$(docker compose ps -q db)"
    if [[ -n "$app_id" ]]; then
        docker compose exec -T app python -c \
            'from importlib.metadata import version; print("FastAPI", version("fastapi"), "Starlette", version("starlette"), "python-multipart", version("python-multipart"))'
    fi
    if [[ -n "$db_id" ]]; then
        docker compose exec -T db postgres --version
    fi
}

verify_application() {
    local attempt
    log "Проверяю версии библиотек внутри нового контейнера."
    docker compose exec -T app python - <<'PY'
from importlib.metadata import version
from pathlib import Path

# Проверяем фактическую установку по версиям, закреплённым в образе.
required = {"fastapi", "starlette", "python-multipart"}
expected = {}
for line in Path("requirements.txt").read_text(encoding="utf-8").splitlines():
    name, separator, pinned = line.partition("==")
    if separator and name in required:
        expected[name] = pinned.strip()
if set(expected) != required:
    raise SystemExit("Отсутствует закреплённая версия одной из обязательных библиотек")
for name in sorted(required):
    actual = version(name)
    print(f"{name}: {actual}")
    if actual != expected[name]:
        raise SystemExit(f"Версия {name} не совпадает с requirements.txt: {expected[name]}")
PY
    docker compose exec -T app python --version
    docker compose exec -T db postgres --version
    for ((attempt = 1; attempt <= 30; attempt++)); do
        if docker compose exec -T app python -c \
            'from urllib.request import urlopen; response = urlopen("http://127.0.0.1:8080/health", timeout=5); assert response.status == 200' \
            >/dev/null 2>&1; then
            log "Приложение и PostgreSQL прошли внутреннюю проверку /health."
            return 0
        fi
        sleep 2
    done
    fail "Сервис не прошёл проверку /health за 60 секунд. Проверьте docker compose logs."
}

check_project
if [[ "$CHECK_ONLY" == true ]]; then
    log "Конфигурация корректна; изменений не внесено."
    show_runtime_versions
    exit 0
fi

verify_engine
backup_database
log "Загружаю закреплённый образ PostgreSQL."
docker compose pull db
log "Пересобираю приложение без кэша из закреплённого Python-образа."
docker compose build --pull --no-cache app
log "Проверяю импорт приложения в новом образе до переключения контейнеров."
docker compose run --no-deps --rm --pull never app python -c 'from app.main import app; print("Приложение импортируется")'
log "Пересоздаю контейнеры и ожидаю готовности."
docker compose up -d --no-build --force-recreate --wait --wait-timeout 180
verify_application
log "Обновление завершено. Доступ пользователей обеспечивается через серверный reverse proxy с HTTPS."
