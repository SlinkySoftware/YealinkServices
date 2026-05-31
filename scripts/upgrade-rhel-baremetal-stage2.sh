#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Slinky Software

set -euo pipefail

APP_USER="${APP_USER:-yealink}"
APP_GROUP="${APP_GROUP:-$APP_USER}"
APP_DIR="${APP_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
ENV_FILE="${ENV_FILE:-/etc/yealinkService/phone-services.env}"
SYSTEMD_SERVICE_NAME="${SYSTEMD_SERVICE_NAME:-phone-services.service}"
SYSTEMD_SERVICE_PATH="${SYSTEMD_SERVICE_PATH:-/etc/systemd/system/${SYSTEMD_SERVICE_NAME}}"
NGINX_SERVER_CONF="${NGINX_SERVER_CONF:-/etc/nginx/conf.d/phonemanager.conf}"
NGINX_INCLUDE_DIR="${NGINX_INCLUDE_DIR:-/etc/nginx/default.d}"
NGINX_INCLUDE_FILE="${NGINX_INCLUDE_FILE:-$NGINX_INCLUDE_DIR/yealink-services-locations.conf}"
APP_PORT="${APP_PORT:-8001}"
LOG_DIR_WAS_PROVIDED=0
LOG_FILE_WAS_PROVIDED=0
if [[ -n "${LOG_DIR+x}" ]]; then
  LOG_DIR_WAS_PROVIDED=1
fi
if [[ -n "${LOG_FILE+x}" ]]; then
  LOG_FILE_WAS_PROVIDED=1
fi
LOG_DIR="${LOG_DIR:-/var/log/yealinkService}"
LOG_FILE="${LOG_FILE:-$LOG_DIR/diversion.log}"
STATIC_DIR="${STATIC_DIR:-$APP_DIR/static}"
BRANDING_DIR="${BRANDING_DIR:-$STATIC_DIR/branding}"
VENV_DIR="$APP_DIR/.venv"

log() {
  echo "[upgrade-rhel] $*"
}

run_as_app_user() {
  sudo -u "$APP_USER" -H bash -lc "cd '$APP_DIR' && $*"
}

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    echo "This script must run as root (use sudo)."
    exit 1
  fi
}

validate_paths() {
  if [[ ! -f "$APP_DIR/manage.py" || ! -f "$APP_DIR/requirements.txt" ]]; then
    echo "Application checkout not found at $APP_DIR"
    echo "Set APP_DIR if your checkout is in a non-default location."
    exit 1
  fi

  if [[ ! -f "$VENV_DIR/bin/python" || ! -f "$VENV_DIR/bin/pip" ]]; then
    echo "Python virtual environment not found at $VENV_DIR"
    echo "Run scripts/install-rhel-baremetal.sh first."
    exit 1
  fi

  if [[ ! -f "$ENV_FILE" ]]; then
    echo "Environment file not found: $ENV_FILE"
    echo "Set ENV_FILE or run scripts/install-rhel-baremetal.sh first."
    exit 1
  fi
}

load_log_paths_from_env_file() {
  if [[ ! -f "$ENV_FILE" ]]; then
    return
  fi

  local env_log_file
  env_log_file="$(sed -n 's/^LOG_FILE=//p' "$ENV_FILE" | tail -n 1)"
  env_log_file="${env_log_file#\"}"
  env_log_file="${env_log_file%\"}"
  env_log_file="${env_log_file#\'}"
  env_log_file="${env_log_file%\'}"

  if [[ "$LOG_FILE_WAS_PROVIDED" -eq 0 && -n "$env_log_file" ]]; then
    LOG_FILE="$env_log_file"
  fi

  if [[ "$LOG_DIR_WAS_PROVIDED" -eq 0 ]]; then
    LOG_DIR="$(dirname "$LOG_FILE")"
  fi
}

validate_runtime_paths() {
  if [[ "$LOG_DIR" != /* ]]; then
    echo "LOG_DIR must be an absolute path: $LOG_DIR"
    exit 1
  fi

  if [[ "$LOG_FILE" != /* ]]; then
    echo "LOG_FILE must be an absolute path: $LOG_FILE"
    exit 1
  fi

  if [[ "$BRANDING_DIR" != /* ]]; then
    echo "BRANDING_DIR must be an absolute path: $BRANDING_DIR"
    exit 1
  fi
}

ensure_log_file_env_key() {
  local escaped_log_file
  escaped_log_file="$(printf '%s' "$LOG_FILE" | sed 's/[&|]/\\&/g')"

  if grep -q '^LOG_FILE=' "$ENV_FILE"; then
    if [[ "$LOG_FILE_WAS_PROVIDED" -eq 1 ]]; then
      sed -i "s|^LOG_FILE=.*$|LOG_FILE=$escaped_log_file|" "$ENV_FILE"
      log "Updated LOG_FILE env key: $LOG_FILE"
    fi
  else
    echo "LOG_FILE=$LOG_FILE" >> "$ENV_FILE"
    log "Added missing LOG_FILE env key"
  fi

  chmod 640 "$ENV_FILE"
  chown root:"$APP_GROUP" "$ENV_FILE"
}

ensure_ownership() {
  log "Ensuring file ownership for application directory"
  chown -R "$APP_USER:$APP_GROUP" "$APP_DIR"
}

ensure_runtime_dirs() {
  log "Ensuring application log directory exists: $LOG_DIR"
  mkdir -p "$LOG_DIR"
  chown "$APP_USER:$APP_GROUP" "$LOG_DIR"
  chmod 750 "$LOG_DIR"

  touch "$LOG_FILE" "$LOG_DIR/gunicorn-access.log"
  chown "$APP_USER:$APP_GROUP" "$LOG_FILE" "$LOG_DIR/gunicorn-access.log"
  chmod 640 "$LOG_FILE" "$LOG_DIR/gunicorn-access.log"

  mkdir -p "$BRANDING_DIR"
  chown -R "$APP_USER:$APP_GROUP" "$STATIC_DIR"
  chmod 755 "$STATIC_DIR" "$BRANDING_DIR"
}

upgrade_python_dependencies() {
  log "Upgrading Python tooling"
  run_as_app_user "'$VENV_DIR/bin/pip' install --upgrade pip setuptools wheel"

  log "Installing Python dependencies from requirements.txt"
  run_as_app_user "'$VENV_DIR/bin/pip' install -r '$APP_DIR/requirements.txt'"
}

run_migrations() {
  log "Running Django migrations"
  run_as_app_user "set -a && source '$ENV_FILE' && set +a && '$VENV_DIR/bin/python' manage.py migrate --noinput"
}

run_checks() {
  log "Running Django system checks"
  run_as_app_user "set -a && source '$ENV_FILE' && set +a && '$VENV_DIR/bin/python' manage.py check"
}

write_systemd_service() {
  log "Writing systemd service: $SYSTEMD_SERVICE_PATH"
  cat > "$SYSTEMD_SERVICE_PATH" <<EOF
[Unit]
Description=Yealink Phone Services Django application
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$APP_USER
Group=$APP_GROUP
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENV_FILE
Environment=PYTHONUNBUFFERED=1
StandardOutput=append:$LOG_FILE
StandardError=append:$LOG_FILE
ExecStart=$VENV_DIR/bin/gunicorn \
  --bind 127.0.0.1:$APP_PORT \
  --workers 2 \
  --timeout 60 \
  --access-logfile $LOG_DIR/gunicorn-access.log \
  --capture-output \
  yealinkService.wsgi:application
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

  chmod 644 "$SYSTEMD_SERVICE_PATH"
}

write_nginx_location_include() {
  log "Writing nginx location include: $NGINX_INCLUDE_FILE"
  mkdir -p "$NGINX_INCLUDE_DIR"
  cat > "$NGINX_INCLUDE_FILE" <<EOF
location /services/ {
    proxy_pass http://127.0.0.1:$APP_PORT/services/;
    proxy_http_version 1.1;
    proxy_set_header Host \$host;
    proxy_set_header X-Real-IP \$remote_addr;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Host \$host;
    proxy_set_header X-Forwarded-Port \$server_port;
    proxy_set_header X-Forwarded-Proto \$scheme;
}

location /static/branding/ {
    alias $BRANDING_DIR/;
    try_files \$uri =404;
    access_log off;
    expires 1h;
    add_header Cache-Control "public";
}
EOF

  chmod 644 "$NGINX_INCLUDE_FILE"
}

find_nginx_server_conf() {
  local candidate

  if [[ -f "$NGINX_SERVER_CONF" ]]; then
    printf '%s\n' "$NGINX_SERVER_CONF"
    return 0
  fi

  for candidate in /etc/nginx/conf.d/*.conf; do
    [[ -f "$candidate" ]] || continue
    if grep -q '^[[:space:]]*server[[:space:]]*{' "$candidate"; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done

  return 1
}

inject_nginx_include() {
  local server_conf="$1"
  local tmp_file

  if grep -qF "include $NGINX_INCLUDE_FILE;" "$server_conf"; then
    return
  fi

  tmp_file="$(mktemp)"
  if ! awk -v include_line="    include $NGINX_INCLUDE_FILE;" '
    BEGIN { in_server = 0; depth = 0; inserted = 0 }
    {
      line = $0

      if (!inserted && !in_server && line ~ /^[[:space:]]*server[[:space:]]*\{/) {
        in_server = 1
      }

      if (in_server) {
        open_scan = line
        close_scan = line
        open_count = gsub(/\{/, "{", open_scan)
        close_count = gsub(/\}/, "}", close_scan)

        if (depth == 1 && close_count > 0 && !inserted) {
          print include_line
          inserted = 1
        }

        print line
        depth += open_count - close_count

        if (depth <= 0) {
          in_server = 0
          depth = 0
        }

        next
      }

      print line
    }
    END { if (!inserted) exit 1 }
  ' "$server_conf" > "$tmp_file"; then
    rm -f "$tmp_file"
    echo "Unable to inject nginx include into $server_conf"
    exit 1
  fi

  cat "$tmp_file" > "$server_conf"
  rm -f "$tmp_file"
}

ensure_nginx_integration() {
  local server_conf

  write_nginx_location_include

  if grep -R -qF -- "include $NGINX_INCLUDE_FILE;" /etc/nginx 2>/dev/null; then
    return
  fi

  if grep -R -qF -- "include $NGINX_INCLUDE_DIR/*.conf;" /etc/nginx 2>/dev/null; then
    return
  fi

  if ! server_conf="$(find_nginx_server_conf)"; then
    echo "Unable to locate an nginx server block to integrate with."
    echo "Set NGINX_SERVER_CONF to the Phone Manager nginx server config and rerun."
    exit 1
  fi

  log "Injecting yealink nginx include into existing server block: $server_conf"
  inject_nginx_include "$server_conf"
}

configure_selinux() {
  if ! command -v getenforce >/dev/null 2>&1 || [[ "$(getenforce)" == "Disabled" ]]; then
    return
  fi

  log "Configuring SELinux for nginx proxying and branding files"
  setsebool -P httpd_can_network_connect 1

  if semanage fcontext -l | grep -Fq "$BRANDING_DIR(/.*)?"; then
    semanage fcontext -m -t httpd_sys_content_t "$BRANDING_DIR(/.*)?"
  else
    semanage fcontext -a -t httpd_sys_content_t "$BRANDING_DIR(/.*)?"
  fi

  restorecon -Rv "$BRANDING_DIR"
}

reload_systemd() {
  log "Reloading systemd configuration"
  systemctl daemon-reload
}

reload_nginx() {
  log "Validating nginx configuration"
  nginx -t
  systemctl enable --now nginx
  systemctl restart nginx
}

restart_service() {
  log "Restarting service: $SYSTEMD_SERVICE_NAME"
  systemctl restart "$SYSTEMD_SERVICE_NAME"

  if ! systemctl is-active --quiet "$SYSTEMD_SERVICE_NAME"; then
    echo "Service did not start successfully: $SYSTEMD_SERVICE_NAME"
    systemctl status --no-pager "$SYSTEMD_SERVICE_NAME" || true
    exit 1
  fi

  log "Service is active"
}

main() {
  require_root
  validate_paths

  log "Starting stage 2 upgrade execution"
  load_log_paths_from_env_file
  validate_runtime_paths
  ensure_log_file_env_key
  ensure_runtime_dirs
  ensure_ownership
  upgrade_python_dependencies
  run_migrations
  run_checks
  write_systemd_service
  ensure_nginx_integration
  configure_selinux
  reload_systemd
  reload_nginx
  restart_service

  cat <<EOF

Upgrade completed successfully.

Executed steps:
1. Stage 1 refreshed the git checkout
2. Ensured file ownership under APP_DIR
3. Updated Python packages from requirements.txt
4. Ran Django migrations and system checks
5. Rewrote the systemd unit
6. Refreshed the nginx location include and integration hook
7. Reapplied SELinux rules for nginx proxying and branding files
8. Restarted $SYSTEMD_SERVICE_NAME

EOF
}

main "$@"