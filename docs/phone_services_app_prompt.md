# LLM Implementation Prompt 1: Yealink XML Phone Services Application

You are to build a production-ready Django application named **Phone Services / Call Diversion XML Service**.

This application is called by Yealink SIP-T33G handsets using the Yealink XML Browser / XML Services function. It must display a branded corporate call diversion service, allow the user to enable, disable, edit, and refresh Call Forward All status, and update Cisco Unified Communications Manager (CUCM) using AXL.

This prompt is complete enough to start implementation. Do **not** make assumptions. If a critical detail is missing, stop and ask a precise clarifying question before coding.

---

## 1. Non-negotiable requirements

- Framework: Python with Django.
- Application type: standalone Django service.
- Deployment: same host as Phone Manager, but running under a different Linux user context.
- Reverse proxy: existing Nginx will pass `/services/` through to this service.
- Nginx should not rewrite `/services/`; however, the application may support an optional development mode where it is mounted at `/`.
- The application must generate Yealink XML Browser responses, not HTML.
- The application must support Yealink SIP-T33G handsets.
- The application must integrate with CUCM 14 via AXL SOAP/XML.
- The application must use CUCM Publisher for AXL read/write operations.
- The application must use a dedicated CUCM AXL service account.
- The application must use an environment file loaded by systemd for configuration.
- The application must store operational logs in local plain text files only.
- The application must not require a functional local database unless Django requires minimal internal framework state.
- The application must be stateless from a user-flow perspective.
- The application may use in-memory cache for CUCM Call Forward All status for up to 60 minutes.
- The application must not expose a human administrative UI.
- The application must not reset or restart phones or CUCM devices.
- The application must not modify Phone Manager data.
- The application must not implement dial-plan transformation logic locally; Phone Manager owns normalisation.
- The application must not assume HTTPS from handset to service. Handset calls are expected to be unencrypted HTTP.

---

## 2. External platform facts and constraints

### Yealink XML Browser

The Yealink handset invokes a configured XML Browser URL using HTTP GET and expects a Yealink XML object or plain text response. It is not a web browser and must not be sent HTML.

The implementation must use Yealink XML objects suitable for SIP-T33G, including where appropriate:

- `YealinkIPPhoneTextMenu`
- `YealinkIPPhoneTextScreen`
- `YealinkIPPhoneInputScreen`
- `YealinkIPPhoneImageScreen`, if testing confirms it works reliably on SIP-T33G

The SIP-T33G display is 320 x 240 pixels. The service should support an corporate logo asset, but must gracefully fall back to text branding if image rendering is unsuitable.

### Cisco CUCM AXL

CUCM AXL is a SOAP/XML provisioning API over HTTPS. Support CUCM versions by using vendored local WSDL/schema files and automatic runtime version detection.

Use the CUCM Publisher:

```text
https://<hostname>>:8443/axl/
```

Bootstrap discovery:

```text
Send getCCMVersion to https://<hostname>:8443/axl/
Use the special AXL namespace http://www.cisco.com/AXL/API/1.0
Omit SOAPAction so CUCM applies its oldest supported schema for the bootstrap request
```

After discovery, select the highest compatible vendored schema available in the local `wsdl/` directory and use that WSDL for subsequent Zeep requests. Do not rely on internet access at runtime.

Vendored schemas available in this project:

- 8.0
- 8.5
- 9.0
- 9.1
- 10.0
- 10.5
- 14.0

Family mapping rules:

- CUCM 8.0 to 8.1 uses vendored schema 8.0
- CUCM 8.5 to 8.6 uses vendored schema 8.5
- CUCM 9.0 uses vendored schema 9.0
- CUCM 9.1 uses vendored schema 9.1
- CUCM 10.0 uses vendored schema 10.0
- CUCM 10.5 uses vendored schema 10.5
- CUCM 11.x and 12.x use vendored schema 10.0
- CUCM 14.x and 15.x use vendored schema 14.0

Recommended Python SOAP client: `zeep`.

CUCM certificate validation must be disabled because the CUCM server uses self-signed certificates and TLS validation is expected to fail.

---

## 3. Core service URL called by handsets

Phone Manager will provision each handset with an explicit XML Services URL. Do not depend on Yealink runtime URL macro substitution.

Canonical handset-facing URL format:

```text
http://<services-base-url>/services/?mac=<MAC>&dn=<DN>&token=<TOKEN>
```

Where:

- `mac` is the device MAC address.
- `dn` is the configured line DN.
- `token` is the first 8 characters of the SIP registration password for the configured line.

The token is not a strong cryptographic control. It is a lightweight validation guard. The services app must validate it by calling Phone Manager over localhost.

---

## 4. Phone Manager localhost dependency

Phone Manager is the authoritative source for:

- device existence
- device model
- MAC normalisation
- MAC to line mapping
- DN validation
- token validation
- site/device dial-plan normalisation

The services app must call Phone Manager over localhost only.

### 4.1 Device context endpoint

Canonical URI:

```text
GET http://127.0.0.1:8000/internal/device-context/?mac=<MAC>&dn=<DN>&token=<TOKEN>
```

Expected behaviour:

- Validate the MAC, DN, and token.
- Confirm the MAC belongs to a SIP-T33G device.
- Confirm the DN belongs to the configured line for that device.
- Reuse Phone Manager's existing MAC normalisation logic.
- Validate `token` case-sensitively against the configured line's SIP registration password.
- Return enough context for the services app to proceed.
- Do not return SIP passwords or token prefixes.
- May return SIP username if useful for CUCM validation.

Suggested successful response:

```json
{
  "valid": true,
  "mac": "805EC0ABCDEF",
  "model": "SIP-T33G",
  "dn": "+61288836500",
  "sip_username": "optional-if-useful",
  "line_count": 1,
  "device_name": "optional-phone-manager-name",
  "site": "2SYA",
  "dial_plan_id": "optional",
  "message": "OK"
}
```

Suggested invalid response:

```json
{
  "valid": false,
  "message": "Invalid device context"
}
```

The services app should treat `valid: false`, HTTP 403, HTTP 404, Phone Manager unavailability, unexpected schema, or timeout as:

```text
Call Diversion is Unavailable
```

### 4.2 Number normalisation endpoint

Phone Manager owns number normalisation. The services app must not duplicate ordered regex dial-plan logic.

Canonical URI:

```text
POST http://127.0.0.1:8000/internal/normalize-number/
```

Canonical request body:

```json
{
  "mac": "805EC0ABCDEF",
  "dn": "+61288836500",
  "token": "abcd1234",
  "entered_destination": "0288836500"
}
```

Canonical successful response:

```json
{
  "normalized_destination": "+61288836500",
  "matched": true
}
```

`matched` is optional. If returned, it indicates whether a dial-plan rule changed the number or the destination passed through unchanged.

Invalid destination should be treated as:

```text
Invalid Destination Specified
```

---

## 5. CUCM / AXL configuration

Use environment configuration for all CUCM connection details.

Required values:

```bash
CUCM_AXL_HOST=<hostname>
CUCM_AXL_PORT=8443
CUCM_AXL_USERNAME=svc_phone_diversion_axl
CUCM_AXL_PASSWORD=change-me
CUCM_AXL_VERIFY_TLS=false
CUCM_ROUTE_PARTITION=INTERNAL
CUCM_APPLY_LINE_AFTER_UPDATE=true
```

AXL account requirements:

- Create a dedicated CUCM Application User, for example `svc_phone_diversion_axl`.
- Assign it to a dedicated access control group.
- Assign the role `Standard AXL API Access`.
- Do not use `Standard CCM Super Users` unless no alternative is available.

Line identification:

- Always identify the CUCM DN by `pattern` plus `routePartitionName`.
- Route partition is always `INTERNAL` for this application.
- Phone Manager may store the DN as `+61288836500`, but CUCM may require escaping in SOAP/XML, e.g. `\+61288836500`. Handle this correctly in the AXL client layer.

---

## 6. Call Forward All behaviour

This application implements **Call Forward All only**.

User-facing language:

- Use `Divert` for actions.
- Use `Call Diversion` in status/error messages.

Enabled state:

- Diversion is enabled if `callForwardAll.destination` is non-empty.
- Voicemail is not supported in this platform.

### 6.1 Status lookup

For every initial status page, after successful Phone Manager validation:

1. Call AXL `getLine` with:
   - `pattern=<dn>`
   - `routePartitionName=INTERNAL`
2. Read `callForwardAll.destination`.
3. If destination is non-empty, show diversion active.
4. If destination is empty or null, show diversion inactive.

### 6.2 Enable / edit diversion

When the user enters a destination:

1. Validate device context again via Phone Manager.
2. Perform basic input sanity checks:
   - non-empty
   - minimum 5 digits
   - maximum 20 digits
3. Call Phone Manager `/internal/normalize-number/`.
4. Use `normalized_destination` as the CUCM CFA destination.
5. Call AXL `getLine`.
6. Preserve existing CFA CSS and unrelated Call Forward All fields.
7. Call AXL `updateLine` to set:
   - `callForwardAll.destination = normalized_destination`
   - `callForwardAll.forwardToVoiceMail = false`
8. Attempt `applyLine` if `CUCM_APPLY_LINE_AFTER_UPDATE=true` and the AXL client supports it.
9. Call AXL `getLine` again to confirm the expected state.
10. Return a 3-second success screen.

If the new destination is the same as the current destination, treat the request as successful and idempotent.

### 6.3 Disable diversion

When the user disables diversion:

1. Validate device context again via Phone Manager.
2. Call AXL `getLine`.
3. If already disabled, return the normal status screen.
4. Call AXL `updateLine` to clear only:
   - `callForwardAll.destination`
5. Preserve CSS and unrelated Call Forward All fields.
6. Ensure voicemail flag remains false.
7. Attempt `applyLine` if configured and available.
8. Confirm via `getLine`.
9. Return a 3-second success screen.

The UI should not offer Disable if diversion is already disabled.

---

## 7. Yealink XML UI requirements

Create concrete XML response fixtures and Django templates for these screens:

- `status_off.xml`
- `status_on.xml`
- `input_destination.xml`
- `success_enabled.xml`
- `success_disabled.xml`
- `error_unavailable.xml`
- `error_invalid_destination.xml`

The implementation must include these XML fixtures in source control for handset testing.

### 7.1 Branding

Preferred first screen:

- Combined Corporate logo + status.

Logo assets:

- Prefer `corporate-logo-header-320x60.png`.
- Also support `corporate-logo-fullscreen-320x240.png` for testing.
- If ImageScreen is unreliable or not suitable on SIP-T33G, fall back to text branding:

```text
<company> Phone Services
```

### 7.2 Status screen: diversion off

Display:

```text
<company> Phone Services
Extension: <DN>
Divert: None
```

Softkey/action:

```text
Divert
Refresh
```

### 7.3 Status screen: diversion on

Display:

```text
<company> Phone Services
Extension: <DN>
Divert: <normalized_destination>
```

Softkeys/actions:

```text
Disable
Edit
Refresh
```

### 7.4 Input screen

Prompt for destination number.

- Pressing OK submits.
- Pressing X or Cancel cancels safely.
- No confirmation screen is required before enabling diversion.
- The handset input must be numeric-only.
- When editing an active diversion, pre-fill the input with the current destination using digits only.

### 7.5 Disable flow

- Pressing Disable immediately disables diversion.
- No confirmation screen is required.
- Disable is not shown when diversion is already off.

### 7.6 Timeouts

- Success screens timeout after 3 seconds.
- Failure screens timeout after 3 seconds.
- Cancel must be available at every step.
- X key must cancel safely where supported by the Yealink XML object.

### 7.7 Error messages

User-facing handset errors must be simple:

```text
Call Diversion is Unavailable
```

```text
Invalid Destination Specified
```

Technical detail must be logged server-side only.

---

## 8. Routes/endpoints in the Django service

The service must support at least these routes under `/services/`:

```text
GET /services/
GET /services/enable/
GET /services/set/
GET /services/disable/
GET /services/refresh/
GET /services/health/
```

All user-flow endpoints require:

```text
mac
DN
token
```

`/services/set/` also requires:

```text
destination
```

All generated Yealink XML action URLs must be absolute URLs using `PHONE_SERVICES_BASE_URL`.

---

## 9. Caching

The application may cache Call Forward All status in memory only.

Configuration:

```bash
CFA_CACHE_TTL_SECONDS=3600
```

Rules:

- Cache key should include MAC, DN, and route partition.
- Refresh must bypass or invalidate cache.
- Successful enable/disable must update or invalidate cache.
- Cache must never be persisted to disk.
- Cache must not be required for correctness.

---

## 10. Retry and idempotency

AXL calls must retry transient failures.

- Total attempts: 3.
- Retry only appropriate transient errors such as timeout, connection reset, temporary service unavailable.
- Do not retry validation errors or permanent AXL faults.
- Enable to the same number is success.
- Disable when already disabled is success/status refresh.

---

## 11. Logging and audit

Write plain text logs to a configurable file.

Example environment variable:

```bash
LOG_FILE=/var/log/phone-services/diversion.log
```

Log at least:

- timestamp
- request ID
- source IP
- MAC
- DN
- model
- action
- entered destination
- normalised destination
- old CFA destination
- new CFA destination
- result
- error summary
- AXL operation

Destination numbers may be logged in cleartext.

Do not log:

- SIP password
- token prefix beyond the token already supplied by the handset
- CUCM AXL password
- Django secret key

---

## 12. Environment configuration

Support a systemd environment file with at least:

```bash
PHONE_SERVICES_BASE_URL=http://phoneservices.example.internal/services/
PHONE_MANAGER_DEVICE_CONTEXT_URL=http://127.0.0.1:8000/internal/device-context/
PHONE_MANAGER_NORMALIZE_NUMBER_URL=http://127.0.0.1:8000/internal/normalize-number/
PHONE_MANAGER_TIMEOUT_SECONDS=5

CUCM_AXL_HOST=<hostname>
CUCM_AXL_PORT=8443
CUCM_AXL_USERNAME=svc_phone_diversion_axl
CUCM_AXL_PASSWORD=change-me
CUCM_AXL_VERIFY_TLS=false
CUCM_ROUTE_PARTITION=INTERNAL
CUCM_APPLY_LINE_AFTER_UPDATE=true

CFA_CACHE_TTL_SECONDS=3600
DRY_RUN=false

LOG_FILE=/var/log/phone-services/diversion.log
DJANGO_SECRET_KEY=change-me
DJANGO_ALLOWED_HOSTS=phoneservices.example.internal,localhost,127.0.0.1
```

Dry-run mode:

- Must still validate Phone Manager context.
- Must still authenticate to CUCM.
- Must still retrieve CUCM line status using AXL.
- Must not call AXL `updateLine` or `applyLine`.
- Must log what would have been changed.

---

## 13. Testing requirements

Include:

- unit tests for request validation
- unit tests for Phone Manager client
- unit tests for number normalisation client handling
- unit tests for CUCM AXL client wrapper using mocks
- unit tests for XML rendering
- XML fixture files for all handset screens
- dry-run tests
- retry/idempotency tests

Do not require a real CUCM instance for the standard test suite.

The repository should include a clear manual handset test plan for SIP-T33G.

---

## 14. Suggested project structure

```text
phone_services/
  manage.py
  phone_services/
    settings.py
    urls.py
    wsgi.py
  diversion/
    views.py
    services.py
    phone_manager_client.py
    cucm_axl_client.py
    yealink_xml.py
    cache.py
    logging_utils.py
    templates/
      yealink/
        status_off.xml
        status_on.xml
        input_destination.xml
        success_enabled.xml
        success_disabled.xml
        error_unavailable.xml
        error_invalid_destination.xml
    tests/
  axl_schema/
    AXLAPI.wsdl
    ...
  deployment/
    phone-services.service
    phone-services.env.example
    nginx-location-example.conf
  README.md
```

---

## 15. Deliverables

Produce production-ready code including:

- Django application source
- CUCM AXL client wrapper
- Phone Manager client wrapper
- Yealink XML templates and fixtures
- systemd unit example
- environment file example
- Nginx location example for `/services/`
- README with deployment and testing instructions
- test suite
- manual SIP-T33G handset test plan

---

## 16. Explicitly do not implement

Do not implement:

- a human admin UI
- local dial-plan management
- local persistent diversion database
- modification of Phone Manager data
- multiple CUCM clusters
- multiple lines per SIP-T33G
- voicemail forwarding
- busy/no-answer/unregistered forwarding
- phone reset/restart operations
- HTTPS requirement from handset to Nginx
- public proxy access to Phone Manager `/internal/` endpoints

---

## 17. Stop conditions

Stop and ask a clarifying question if any of these are missing or cannot be satisfied:

- AXL WSDL/schema files for CUCM 14
- exact behaviour of `applyLine` in the selected AXL client
- exact Yealink XML object syntax required for SIP-T33G screen rendering
- inability to serve absolute Yealink XML URLs under `/services/`
- inability to call Phone Manager localhost endpoints
- ambiguity in CUCM `getLine` or `updateLine` response structure
