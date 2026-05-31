# yealinkService

yealinkService is a standalone Django service that exposes Yealink XML Browser screens for Call Forward All management on SIP-T33G handsets and updates Cisco CUCM 14 through AXL.

## Features

- Yealink XML Browser responses for status, enable, disable, refresh, and errors.
- Phone Manager integration over localhost for device validation and destination normalization.
- CUCM AXL integration using the vendored CUCM 14 WSDL under `wsdl/`.
- In-memory CFA caching with refresh bypass and cache invalidation after updates.
- Plain-text JSON-line audit logging to a configurable file.
- Dry-run mode that still validates handset context and reads CUCM state without writing changes.
- Unit tests for request validation, XML rendering, Phone Manager handling, CUCM client behavior, retry logic, and dry-run/idempotency.

## Project layout

```text
manage.py
requirements.txt
yealinkService/
diversion/
deployment/
docs/
wsdl/
```

## Runtime configuration

Set these environment variables through a systemd `EnvironmentFile`:

```bash
PHONE_SERVICES_BASE_URL=http://phoneservices.example.internal/services/
PHONE_SERVICES_ENABLE_ROOT_MOUNT=false
PHONE_SERVICES_COMPANY_NAME=ExampleCorp
PHONE_MANAGER_DEVICE_CONTEXT_URL=http://127.0.0.1:8000/internal/device-context/
PHONE_MANAGER_NORMALIZE_NUMBER_URL=http://127.0.0.1:8000/internal/normalize-number/
PHONE_MANAGER_TIMEOUT_SECONDS=5

CUCM_AXL_HOST=cucm-publisher.example.internal
CUCM_AXL_PORT=8443
CUCM_AXL_VERSION=14.0
CUCM_AXL_USERNAME=svc_phone_diversion_axl
CUCM_AXL_PASSWORD=change-me
CUCM_AXL_VERIFY_TLS=false
CUCM_AXL_TIMEOUT_SECONDS=10
CUCM_AXL_LEGACY_TLS_COMPATIBILITY=false
CUCM_AXL_LEGACY_TLS_CIPHERS=AES128-SHA:@SECLEVEL=0
CUCM_ROUTE_PARTITION=INTERNAL
CUCM_APPLY_LINE_AFTER_UPDATE=true
AXL_WSDL_PATH=/opt/yealinkService/wsdl/AXLAPI.wsdl

CFA_CACHE_TTL_SECONDS=3600
DRY_RUN=false

LOG_FILE=/var/log/phone-services/diversion.log
DJANGO_SECRET_KEY=change-me
DJANGO_ALLOWED_HOSTS=phoneservices.example.internal,localhost,127.0.0.1
```

Optional branding values:

```bash
PHONE_SERVICES_HEADER_LOGO_URL=http://phoneservices.example.internal/static/branding/corporate-logo-header-320x60.png
PHONE_SERVICES_FULLSCREEN_LOGO_URL=http://phoneservices.example.internal/static/branding/corporate-logo-fullscreen-320x240.png
```

The handset templates default to text branding. The logo URLs are exposed in template context so a handset-tested image workflow can be enabled without changing the service contract.

Legacy CUCM TLS compatibility:

- Leave `CUCM_AXL_LEGACY_TLS_COMPATIBILITY=false` unless the AXL endpoint rejects modern OpenSSL 3 handshakes.
- Enable `CUCM_AXL_LEGACY_TLS_COMPATIBILITY=true` only for older CUCM HTTPS listeners that require TLS 1.2 with legacy RSA CBC ciphers.
- `CUCM_AXL_LEGACY_TLS_CIPHERS` defaults to `AES128-SHA:@SECLEVEL=0`, which matches older CUCM deployments that only accept `TLS_RSA_WITH_AES_128_CBC_SHA`.
- This compatibility mode is scoped to the CUCM AXL client only, but it intentionally lowers TLS security for that connection and should be treated as an interoperability workaround.

## Local setup

```bash
./dev.sh setup
./dev.sh start
```

The root [dev.sh](dev.sh) helper supports `setup`, `start`, `stop`, `restart`, and `status`. It uses `.venv`, writes runtime state to `.dev-runtime/`, defaults `DEBUG=true`, and binds Django to `0.0.0.0:8001`. It automatically sources [.dev.env](.dev.env) before applying dev fallbacks; override the file location with `DEV_ENV_FILE` if needed. Override the bind target with `DEV_HOST` and `DEV_PORT` if needed.

## Test suite

```bash
. .venv/bin/activate
pytest
python manage.py check
```

## Deployment notes

- Nginx must proxy `/services/` without rewriting the prefix.
- The Django app itself can optionally mount at `/` for development by setting `PHONE_SERVICES_ENABLE_ROOT_MOUNT=true` and adjusting `PHONE_SERVICES_BASE_URL`.
- The service keeps no user-flow state outside process memory.
- The only database dependency is the minimal Django SQLite database file created for framework state; the diversion workflow itself does not use models or persistent application data.
- The CUCM client escapes leading `+` digits before `getLine`, `updateLine`, and `applyLine` requests.
- If CUCM AXL only offers legacy TLS 1.2 RSA CBC suites, set `CUCM_AXL_LEGACY_TLS_COMPATIBILITY=true` in the deployment environment file and restart the service.

## Handset testing

See [docs/manual_sip_t33g_test_plan.md](docs/manual_sip_t33g_test_plan.md) for the manual SIP-T33G validation plan.
