#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"
VENV_PYTHON="${VENV_DIR}/bin/python"
PYTHON_BIN="${PYTHON_BIN:-python3}"
RUNTIME_DIR="${ROOT_DIR}/.dev-runtime"
PID_FILE="${RUNTIME_DIR}/django.pid"
LOG_FILE="${RUNTIME_DIR}/django.log"
HOST="${DEV_HOST:-0.0.0.0}"
PORT="${DEV_PORT:-8001}"

usage() {
    cat <<EOF
Usage: ./dev.sh {setup|start|stop|restart|status}

Commands:
  setup    Create .venv if needed, install requirements, run Django checks
  start    Start the Django development server in the background
  stop     Stop the running Django development server
  restart  Restart the Django development server
  status   Show whether the Django development server is running

Environment overrides:
  PYTHON_BIN            Python used to create the virtualenv (default: python3)
  DEV_HOST              Bind host for runserver (default: 0.0.0.0)
  DEV_PORT              Bind port for runserver (default: 8001)
  DJANGO_SECRET_KEY     Defaults to a local development value
  DEBUG                 Defaults to true when using this script
  DJANGO_ALLOWED_HOSTS  Defaults to localhost,127.0.0.1
EOF
}

ensure_runtime_dir() {
    mkdir -p "${RUNTIME_DIR}"
}

load_pid() {
    if [[ ! -f "${PID_FILE}" ]]; then
        return 1
    fi

    pid="$(<"${PID_FILE}")"
    if [[ ! "${pid}" =~ ^[0-9]+$ ]]; then
        rm -f "${PID_FILE}"
        return 1
    fi
}

is_running() {
    if ! load_pid; then
        return 1
    fi

    if kill -0 "${pid}" 2>/dev/null; then
        return 0
    fi

    rm -f "${PID_FILE}"
    return 1
}

require_venv() {
    if [[ ! -x "${VENV_PYTHON}" ]]; then
        printf 'Virtualenv not found. Run ./dev.sh setup first.\n' >&2
        exit 1
    fi
}

export_dev_env() {
    export DJANGO_SECRET_KEY="${DJANGO_SECRET_KEY:-dev-only-change-me}"
    export DEBUG="${DEBUG:-true}"
    export DJANGO_ALLOWED_HOSTS="${DJANGO_ALLOWED_HOSTS:-localhost,127.0.0.1}"
    export PHONE_SERVICES_BASE_URL="${PHONE_SERVICES_BASE_URL:-http://localhost:${PORT}/services/}"
}

run_manage_check() {
    (
        cd "${ROOT_DIR}"
        "${VENV_PYTHON}" manage.py check
    )
}

setup() {
    if [[ ! -d "${VENV_DIR}" ]]; then
        "${PYTHON_BIN}" -m venv "${VENV_DIR}"
    fi

    "${VENV_PYTHON}" -m pip install --upgrade pip
    "${VENV_PYTHON}" -m pip install -r "${ROOT_DIR}/requirements.txt"

    export_dev_env
    run_manage_check

    printf 'Setup complete. Use ./dev.sh start to launch Django.\n'
}

start() {
    require_venv
    export_dev_env
    ensure_runtime_dir

    if is_running; then
        printf 'Django dev server is already running (PID %s).\n' "${pid}"
        printf 'Log: %s\n' "${LOG_FILE}"
        return 0
    fi

    if ! command -v setsid >/dev/null 2>&1; then
        printf 'setsid is required to manage the dev server process group.\n' >&2
        exit 1
    fi

    run_manage_check
    : > "${LOG_FILE}"

    (
        cd "${ROOT_DIR}"
        setsid "${VENV_PYTHON}" manage.py runserver "${HOST}:${PORT}" >>"${LOG_FILE}" 2>&1 &
        server_pid=$!
        printf '%s\n' "${server_pid}" > "${PID_FILE}"
    )

    if is_running; then
        printf 'Started Django dev server (PID %s) on http://localhost:%s\n' "${pid}" "${PORT}"
        printf 'Log: %s\n' "${LOG_FILE}"
        return 0
    fi

    printf 'Failed to start Django dev server. Check %s\n' "${LOG_FILE}" >&2
    exit 1
}

stop() {
    if ! is_running; then
        printf 'Django dev server is not running.\n'
        return 0
    fi

    running_pid="${pid}"
    kill -TERM -- "-${running_pid}" 2>/dev/null || true

    if kill -0 "${running_pid}" 2>/dev/null; then
        kill -KILL -- "-${running_pid}" 2>/dev/null || true
    fi

    rm -f "${PID_FILE}"
    printf 'Stopped Django dev server (PID %s).\n' "${running_pid}"
}

status() {
    if is_running; then
        printf 'Django dev server is running (PID %s) on %s:%s\n' "${pid}" "${HOST}" "${PORT}"
        printf 'Log: %s\n' "${LOG_FILE}"
        return 0
    fi

    printf 'Django dev server is stopped.\n'
}

restart() {
    stop
    start
}

command_name="${1:-}"

case "${command_name}" in
    setup)
        setup
        ;;
    start)
        start
        ;;
    stop)
        stop
        ;;
    restart)
        restart
        ;;
    status)
        status
        ;;
    -h|--help|help)
        usage
        ;;
    *)
        usage >&2
        exit 1
        ;;
esac