#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Slinky Software

set -euo pipefail

APP_USER="${APP_USER:-yealink}"
APP_GROUP="${APP_GROUP:-$APP_USER}"
APP_DIR="${APP_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
ENV_DIR="${ENV_DIR:-/etc/yealinkService}"
ENV_FILE="${ENV_FILE:-$ENV_DIR/phone-services.env}"
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
PYTHON_BIN=""

log() {
  echo "[install-rhel] $*"
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

ensure_python_runtime() {
  log "Installing Python runtime and build dependencies"

  dnf -y install \
    gcc \
    git \
    libffi-devel \
    libxml2-devel \
    libxslt-devel \
    nginx \
    openssl-devel \
    policycoreutils \
    policycoreutils-python-utils \
    xmlsec1 \
    xmlsec1-openssl

  if ! dnf -y install python3.12 python3.12-devel python3.12-pip; then
    log "python3.12 packages unavailable, falling back to the default python3 packages"
    dnf -y install python3 python3-devel python3-pip
  fi

  for candidate in python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      PYTHON_BIN="$candidate"
      break
    fi
  done

  if [[ -z "$PYTHON_BIN" ]]; then
    echo "Python not found after package installation."
    exit 1
  fi

  local detected_version
  detected_version="$($PYTHON_BIN -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")"
  if ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
    echo "Python >= 3.10 is required, found $detected_version via $PYTHON_BIN"
    exit 1
  fi

  log "Using Python interpreter: $PYTHON_BIN ($detected_version)"
}

ensure_app_group_and_user() {
  if ! getent group "$APP_GROUP" >/dev/null 2>&1; then
    log "Creating system group: $APP_GROUP"
    groupadd --system "$APP_GROUP"
  fi

  if ! id "$APP_USER" >/dev/null 2>&1; then
    log "Creating system user: $APP_USER"
    useradd --system --home-dir "$APP_DIR" --shell /sbin/nologin --gid "$APP_GROUP" "$APP_USER"
  else
    usermod -g "$APP_GROUP" "$APP_USER"
  fi
}

ensure_app_ownership() {
  log "Ensuring file ownership for application directory"
  chown -R "$APP_USER:$APP_GROUP" "$APP_DIR"
}

ensure_runtime_dirs() {
  log "Ensuring environment directory exists: $ENV_DIR"
  mkdir -p "$ENV_DIR"
  chown root:"$APP_GROUP" "$ENV_DIR"
  chmod 750 "$ENV_DIR"

  log "Ensuring application log directory exists: $LOG_DIR"
  mkdir -p "$LOG_DIR"
  chown "$APP_USER:$APP_GROUP" "$LOG_DIR"
  chmod 750 "$LOG_DIR"

  touch "$LOG_FILE" "$LOG_DIR/gunicorn-access.log"
  chown "$APP_USER:$APP_GROUP" "$LOG_FILE" "$LOG_DIR/gunicorn-access.log"
  chmod 640 "$LOG_FILE" "$LOG_DIR/gunicorn-access.log"

  log "Ensuring branding directory exists: $BRANDING_DIR"
  mkdir -p "$BRANDING_DIR"
  chown -R "$APP_USER:$APP_GROUP" "$STATIC_DIR"
  chmod 755 "$STATIC_DIR" "$BRANDING_DIR"
}

setup_virtualenv() {
  log "Creating Python virtual environment"
  run_as_app_user "$PYTHON_BIN -m venv '$VENV_DIR'"

  log "Installing Python dependencies"
  run_as_app_user "'$VENV_DIR/bin/pip' install --upgrade pip setuptools wheel"
  run_as_app_user "'$VENV_DIR/bin/pip' install -r '$APP_DIR/requirements.txt'"
}

write_env_file() {
  log "Writing environment file: $ENV_FILE"
  mkdir -p "$ENV_DIR"
  chown root:"$APP_GROUP" "$ENV_DIR"
  chmod 750 "$ENV_DIR"

  if [[ ! -f "$ENV_FILE" ]]; then
    cat > "$ENV_FILE" <<EOF
PHONE_SERVICES_BASE_URL=http://phoneservices.example.internal/services/
PHONE_SERVICES_ENABLE_ROOT_MOUNT=false
PHONE_SERVICES_COMPANY_NAME=ExampleCorp
PHONE_MANAGER_DEVICE_CONTEXT_URL=http://127.0.0.1:8000/internal/device-context/
PHONE_MANAGER_NORMALIZE_NUMBER_URL=http://127.0.0.1:8000/internal/normalize-number/
PHONE_MANAGER_TIMEOUT_SECONDS=5

CUCM_AXL_HOST=cucm-publisher.example.internal
CUCM_AXL_PORT=8443
CUCM_AXL_USERNAME=svc_phone_diversion_axl
CUCM_AXL_PASSWORD=change-me
CUCM_AXL_VERIFY_TLS=false
CUCM_AXL_TIMEOUT_SECONDS=10
CUCM_AXL_LEGACY_TLS_COMPATIBILITY=false
CUCM_AXL_LEGACY_TLS_CIPHERS=AES128-SHA:@SECLEVEL=0
CUCM_ROUTE_PARTITION=INTERNAL
CUCM_APPLY_LINE_AFTER_UPDATE=true

CFA_CACHE_TTL_SECONDS=3600
DRY_RUN=false

LOG_FILE=$LOG_FILE
DEBUG=False
DJANGO_SECRET_KEY=change-me
DJANGO_ALLOWED_HOSTS=phoneservices.example.internal,localhost,127.0.0.1

# Optional branding assets served by nginx from $BRANDING_DIR
# PHONE_SERVICES_HEADER_LOGO_URL=http://phoneservices.example.internal/static/branding/corporate-logo-header-320x60.png
# PHONE_SERVICES_FULLSCREEN_LOGO_URL=http://phoneservices.example.internal/static/branding/corporate-logo-fullscreen-320x240.png
EOF
  else
    log "Existing environment file detected, leaving values unchanged"
  fi

  chmod 640 "$ENV_FILE"
  chown root:"$APP_GROUP" "$ENV_FILE"
}

ensure_log_file_env_key() {
  local escaped_log_file
  escaped_log_file="$(printf '%s' "$LOG_FILE" | sed 's/[&|]/\\&/g')"

  if grep -q '^LOG_FILE=' "$ENV_FILE"; then
    if [[ "$LOG_FILE_WAS_PROVIDED" -eq 1 ]]; then
      sed -i "s|^LOG_FILE=.*$|LOG_FILE=$escaped_log_file|" "$ENV_FILE"
      log "Updated LOG_FILE in environment file"
    fi
  else
    echo "LOG_FILE=$LOG_FILE" >> "$ENV_FILE"
    log "Added missing LOG_FILE to environment file"
  fi

  chmod 640 "$ENV_FILE"
  chown root:"$APP_GROUP" "$ENV_FILE"
}

run_django_checks() {
  log "Running Django migrations"
  run_as_app_user "set -a && source '$ENV_FILE' && set +a && '$VENV_DIR/bin/python' manage.py migrate --noinput"

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

reload_services() {
  log "Reloading systemd configuration"
  systemctl daemon-reload

  log "Enabling and starting service: $SYSTEMD_SERVICE_NAME"
  systemctl enable --now "$SYSTEMD_SERVICE_NAME"

  if ! systemctl is-active --quiet "$SYSTEMD_SERVICE_NAME"; then
    echo "Service did not start successfully: $SYSTEMD_SERVICE_NAME"
    systemctl status --no-pager "$SYSTEMD_SERVICE_NAME" || true
    exit 1
  fi

  log "Validating nginx configuration"
  nginx -t

  log "Reloading nginx"
  systemctl enable --now nginx
  systemctl restart nginx
}

main() {
  require_root
  validate_paths

  log "Starting stage 2 installation execution"
  load_log_paths_from_env_file
  validate_runtime_paths
  ensure_python_runtime
  ensure_app_group_and_user
  ensure_app_ownership
  write_env_file
  load_log_paths_from_env_file
  validate_runtime_paths
  ensure_log_file_env_key
  ensure_runtime_dirs
  setup_virtualenv
  run_django_checks
  write_systemd_service
  ensure_nginx_integration
  configure_selinux
  reload_services

  cat <<EOF

Installation completed.

Next steps:
1. Edit $ENV_FILE with real CUCM, Phone Manager, and hostname values.
2. Place any handset branding files under $BRANDING_DIR.
3. If you add branding images, set PHONE_SERVICES_HEADER_LOGO_URL and/or PHONE_SERVICES_FULLSCREEN_LOGO_URL in $ENV_FILE.
4. Restart the service after environment changes:
   sudo systemctl restart $SYSTEMD_SERVICE_NAME

EOF
}

main "$@"