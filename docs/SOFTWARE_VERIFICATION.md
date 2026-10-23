# Software verification — 23 October 2026

## Verified locally

- **42 Python tests pass**, using temporary databases. Includes all 11 preserved
  legacy tests plus the new Wi-Fi API/archive and dependency-manifest tests.
- Tested authentication failures, HttpOnly/Secure hosted cookies, operator allowlist,
  cross-origin rejection, device-token separation, request-size limits, strict
  boolean/finite telemetry validation, and redaction of validation inputs.
- Tested durable telemetry, duplicate/out-of-order handling, reboot cancellation,
  command idempotence, delivery/acknowledgement, expiry, superseding, rejected-request
  audit, sensor validity, per-load energy/gap handling and exact boundary integration.
- Tested linked-list reconstruction, recursive activity ordering, concurrent notes,
  demo isolation and simulated SOC hysteresis.
- **Host C++ tests pass** against the actual `ControlPolicy.h`, including boot OFF,
  independent loads, below-50% fan cutoff, 55% recovery without automatic restart,
  temperature hysteresis, latched current trips, missing/NaN sensor data, overvoltage
  and the default output-enable guard.
- Tested Automatic/Manual mode acknowledgement, persistence, authentication and
  strict Boolean validation; pending-mode ON blocking; cancellation of stale ON
  requests; revision/state acknowledgement matching; command expiry and reboot
  behavior; retained OFF commands; legacy-device compatibility; additive table
  creation without archive deletion; CSV mode history; and the simulated round trip.
- C++ tests also verify Manual bypasses only low-voltage/fan-SOC rules, Automatic
  immediately restores them, reboot defaults to Automatic, and mode changes never
  restart a load or bypass missing sensors, temperature/current/overvoltage checks.
- `node --check frontend/control.js` passes; Python dependency check passes.
- Root `requirements.txt` lists explicit dependency pins matching the tested lock,
  checked by a regression test. This removes the nested `-r` directive rejected
  by the first Vercel deployment; the user subsequently confirmed that fix deployed.
- ESP32 sketch compiles for `esp32:esp32:esp32`, ESP32 core **3.3.12**, with the
  output-disabled example configuration. No upload command was run.
- A separate compile-only copy with dummy credentials also links the enabled
  HTTPS/network and output-policy paths: **1,074,628 bytes flash (81%)**, **52,280
  bytes static RAM (15%)**. The dummy endpoint/certificate are not operational;
  this is a build check, not a network connection or hardware test. Dummy build
  inputs and binaries stay inside ignored `.venv/`, not the production sketch.
- Browser-verified demo: independent load display, 45% fan lockout, continued lockout
  at 52%, unlock without restart at 60%, fresh ON request and simulated acknowledgement,
  30-day charts and saved maintenance notes. The simulation banner remains visible.
- Browser-verified new switch in the isolated simulation: at 45% the fan is locked
  in Automatic, Manual confirmation enables a fresh fan ON, restoring Automatic
  turns the running fan OFF again, and the warning/confirmed/pending states are
  displayed. The responsive narrow-panel layout was visually checked.
- The current ESP32 sketch compiles with the user's local configuration (without
  printing credentials): **1,078,020 bytes flash (82%)**, **52,472 bytes static RAM
  (16%)**. This is compilation only, not upload or physical relay verification.
- Legacy sketches, serial server, existing archives and user hardware were not changed.

The automated Supabase Auth test uses mocked provider responses. The browser demo
checks above used synthetic data, not live loads. No serial port was opened by
the agent.

## Subsequent local-to-cloud integration checks

- The user configured and uploaded the ESP32 firmware, confirmed local Wi-Fi
  telemetry, and configured their own Supabase project and operator login.
- An agent read-only database connection check succeeded without displaying
  credentials. The user then restarted the backend with the cloud configuration.
- The user's Supabase SQL result shows stored telemetry rows, including 24.75°C
  temperature. Battery voltage and per-load power are null in that result, so
  those fields have not been verified as valid live measurements by this check.
- The user subsequently confirmed Vercel login and direct ESP32-to-cloud telemetry
  while powered by a power bank. Read-only archive checks later showed low battery
  voltage and an invalid bulb measurement. These are not certified hardware tests.
- The new automation-switch behavior is verified locally in tests and simulation;
  it still needs the new hosted deployment and a user-performed firmware upload
  before it can be verified on the physical device.

## Still required before real operation

1. Verify physical protection/wiring and load voltage/current ratings; the previously
   stated unfused jumper-wire battery circuit is not certified safe by these tests.
2. Keep configured `.env` and firmware `secrets.h` ignored. Confirm the trusted root CA
   for the eventual HTTPS endpoint; no certificate-validation bypass is implemented.
3. Deploy the new switch-enabled backend/frontend to the existing Vercel project,
   then verify the hosted deployment. Keep existing environment variables.
4. Upload the new switch-enabled firmware for the hosted HTTPS endpoint yourself, initially
   with battery/panel disconnected and `ENABLE_LOAD_OUTPUTS=false`. Verify battery
   and per-load telemetry using an appropriately protected setup before enabling outputs.
5. Verify real relay commands/interlocks with appropriate electrical protection.

Voltage-derived SOC is an uncalibrated generic VRLA estimate. These tests do not
establish true battery capacity, real 50% SOC, electrical protection performance,
physical relay contact feedback or compatibility with charging voltages up to 15V.
