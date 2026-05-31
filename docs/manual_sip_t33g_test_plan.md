# Manual SIP-T33G Test Plan

## Preconditions

1. Deploy yealinkService behind Nginx with `/services/` passed through without rewrite.
2. Confirm the handset XML Browser URL is provisioned as `http://<services-base-url>/services/?mac=<MAC>&dn=<DN>&token=<TOKEN>`.
3. Confirm Phone Manager localhost endpoints return valid device context and normalized destinations.
4. Confirm CUCM AXL credentials can read and update the target DN in route partition `INTERNAL`.
5. If CUCM AXL requires legacy TLS 1.2 RSA CBC ciphers, set `CUCM_AXL_LEGACY_TLS_COMPATIBILITY=true` before testing and record the exact `CUCM_AXL_LEGACY_TLS_CIPHERS` value used.

## Validation flow

1. Open the Yealink XML Browser on a handset with diversion disabled.
Expected: branded status page shows `Status: Not diverted` and offers `Divert`, `Refresh`, and `Exit` actions.

2. Select `Divert`.
Expected: input screen opens and pre-fills the current destination when diversion is already enabled.

3. Enter an invalid destination shorter than five digits.
Expected: handset displays `Invalid Destination Specified` for roughly three seconds and returns to the flow safely.

4. Enter a valid destination that Phone Manager normalizes.
Expected: handset displays `Call Diversion Enabled`, then refreshes to `Status: Diverted` with the normalized destination.

5. Re-submit the same destination.
Expected: handset still shows a success result and CUCM state remains unchanged.

6. Select `Disable` from an enabled line.
Expected: handset displays `Call Diversion Disabled`, then refreshes to `Status: Not diverted`.

7. Select `Refresh` while diversion is enabled and again while disabled.
Expected: cache is bypassed and the displayed state matches current CUCM data.

8. Temporarily stop Phone Manager or block localhost access.
Expected: handset displays `Call Diversion is Unavailable` and technical detail is written only to the server log.

9. Temporarily force CUCM AXL failure.
Expected: handset displays `Call Diversion is Unavailable` and the log captures the failed AXL operation.

10. If testing image branding, enable the logo URL values and verify text fallback remains acceptable on SIP-T33G if image rendering is unreliable.

11. When legacy TLS compatibility is enabled, repeat a status lookup and one enable or disable action.
Expected: handset flow succeeds normally, and the server log does not show SSL handshake failures for the AXL call.
