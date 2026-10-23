# UrjaGrid Wi-Fi software: run locally, then deploy

## What this version does

- Independent bulb and fan controls, acknowledged by the ESP32.
- Bulb INA219 at **0x40**, fan INA219 at **0x41**; no solar sensor or generation claim.
- Battery voltage and **generic VRLA voltage-estimated SOC**, temperature,
  per-load V/A/W, 24-hour / 7-day / rolling-30-day history, Wh estimates,
  CSV, browser Print / Save as PDF, command audit and maintenance notes.
- Firmware-local fan lockout below 50% estimated SOC, recovery at 55%, fresh
  user ON required. Both loads boot OFF. Missing battery/temperature data,
  critical voltage and configured overload/overvoltage checks block ON.
- An acknowledged **Automatic protection ON/OFF** switch. Automatic ON applies
  battery policies; OFF allows manual operation despite low battery percentage
  or voltage, while retaining sensor-fault and electrical checks. Reboot restores
  Automatic ON. Manual battery override can damage a depleted battery.
- Blynk and USB serial collection are not required. USB supplies power only
  after uploading. Use router/hotspot Wi-Fi with internet access for cloud mode.

This is a prototype, not a battery-management or electrical protection system.
Software does not replace suitable fuses, holders, rated power wiring or the
charge controller. Ordinary jumper connectors are not certified battery wiring.
Relay inputs must be verified for 3.3V compatibility, and the USB-to-VIN path
must support the relay supply. Verify bulb/fan tolerance at the battery's full
charging voltage (label: 14.5–15.0V), or provide appropriately regulated load power.

## 1. Preview without hardware

In PowerShell, from the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe backend/control_app.py --demo
```

Open **http://127.0.0.1:8000**. Stop with Ctrl+C. If the port is occupied, add
`--port 8010` and open that port instead. Do not run the legacy server on the same port.

Root `requirements.txt` explicitly lists the same pins as the tested
`backend/requirements-control.lock.txt`; Vercel's deployment parser rejected a
nested `-r` include. Keep both pin lists synchronized (checked by the tests).
`.python-version` selects Python 3.12 for hosting. The separate
`backend/requirements-control.txt` lists the direct dependency ranges for future,
deliberate updates.

The gold SIMULATION banner stays visible. Thirty days of generated five-minute
readings are seeded once into `backend/data/control-demo.sqlite3`; live simulated
samples update while the page polls. Controls affect the simulation only.
Click **45%**: fan turns OFF and ON is disabled. **52%**: still locked.
**60%**: unlocked but still OFF; click fan ON to request a simulated restart.
The demo is loopback-only, rejects device telemetry, and never opens COM ports.

Old demo data is retained; it is not silently deleted or regenerated on restart.
For a fresh demonstration, set `DEMO_DATABASE` to a new, unused local filename.

## 2. Configure local real-device operation

1. Copy root `.env.example` to root `.env` using your editor.
2. Set `DEVICE_ID=urjagrid-01`.
3. Generate an independent secret for `DEVICE_TOKEN` (at least 32 characters):

   ```powershell
   .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_hex(32))"
   ```

4. Set `DASHBOARD_PASSWORD` to a strong password of at least 12 characters.
5. Generate a different random value for `SESSION_SECRET` (at least 32 characters).
6. Leave database and Supabase fields empty for local SQLite/password mode.
7. For same-computer browsing, keep `APP_ORIGIN=http://127.0.0.1:8000`.

Start authenticated local mode:

```powershell
.\.venv\Scripts\python.exe backend/control_app.py
```

Sign in with the configured local dashboard password. This mode uses
`backend/data/control-live.sqlite3`, separate from demo and legacy archives.
Authentication missing or incomplete fails closed; no default password exists.

### Connecting the ESP32 to a local laptop server

The ESP32 cannot reach the laptop using `localhost` or `127.0.0.1`.
Find the laptop's private Wi-Fi IPv4 address with `ipconfig`, then:

- Set `APP_ORIGIN=http://YOUR_LAPTOP_PRIVATE_IP:8000` and restart the service.
- Start it with `--host 0.0.0.0` and browse using exactly that private-IP URL.
- Set firmware `SERVER_BASE_URL` to the same private-IP URL.
- Set firmware `ALLOW_LOCAL_HTTP=true` **only for development on a trusted LAN**.
  The firmware accepts cleartext HTTP only to literal RFC1918 private IPv4 addresses.
- HTTP sends device credentials, local login and telemetry without encryption.
  Do not use shared/public Wi-Fi or expose this port on the internet. Cloud mode uses HTTPS.
- If Windows Firewall blocks the connection, configure a narrowly scoped private-network
  rule yourself. This implementation does not change firewall settings.

Local Wi-Fi collection still requires the laptop/server to remain running.
Cloud deployment removes that requirement.

## 3. Upload the NEW firmware

Open **only** `firmware/UrjaGrid_WiFi/UrjaGrid_WiFi.ino` in Arduino IDE.
Do not paste it as an additional tab into the old sketch folder.

Install the ESP32 board package and libraries:

- Adafruit INA219 (and its Adafruit BusIO dependency)
- OneWire
- DallasTemperature
- ArduinoJson **7.x**

Copy this sketch's `secrets.example.h` to `secrets.h` in the same folder.
Do not reuse the old Blynk credential header: it has different fields.

Fill in:

- Wi-Fi SSID/password.
- The same `DEVICE_ID` and `DEVICE_TOKEN` as the backend.
- `SERVER_BASE_URL`, with no `/api/device/sync` suffix.
- For HTTPS, the trusted root CA PEM for the actual endpoint certificate chain.
  No `setInsecure()` fallback is present. A placeholder or wrong CA prevents syncing.
  Network time must synchronize before HTTPS certificate validation; local control
  does not wait for it.

### Load outputs are deliberately disabled initially

`ENABLE_LOAD_OUTPUTS=false` lets you verify telemetry without energizing loads.
Only set it true after confirming electrical protection, relay input compatibility,
USB relay power capacity and load-voltage tolerance. Software cannot guarantee
relay state during reset/boot: use a verified hardware default-OFF arrangement.

Review the provisional settings before enabling:

| Setting | Default | Meaning |
|---|---|---|
| `MAX_LOAD_SUPPLY_V` | 12.8V | Blocks both loads above this provisional voltage; do not raise beyond verified load tolerance |
| `BULB_CURRENT_LIMIT_A` | 1.5A | Provisional software trip, not a wiring/fuse rating |
| `FAN_CURRENT_LIMIT_A` | 0.3A | Provisional; actual fan rating/startup remains unverified |
| Automatic-mode critical cutoff / recovery | 11.8V / 12.1V | Prototype values in `ControlPolicy.h`; bypassed only while the device confirms Automatic OFF |
| Temperature trip / recovery | 50°C / 45°C | Prototype interlock; not battery-charge temperature compensation |

In Automatic mode, the fan locks below 50% estimated SOC and stays locked until at least 55%.
Recovery never restarts a load automatically. A current trip stays latched until
an explicit OFF request resets it after measured current has fallen below its limit.
Manual mode bypasses only low battery voltage/SOC policies. A failed sensor blocks its own load; missing
battery or temperature measurements block both. Battery readings differing by
more than 0.25V are treated as invalid. The INA219's calibrated "32V" library mode
does not change the chip's 26V common-mode limit.

### Using the Automatic protection switch

1. Deploy the updated backend/frontend before uploading the updated ESP32 sketch.
2. Existing device telemetry still works, but the switch displays an update message
   until the ESP32 reports `automatic_protection` and `mode_revision`.
3. With the updated device online, click the switch. It stays at its previous
   confirmed value while the request is queued/delivered. New load ON requests
   pause until the ESP32 acknowledges; load OFF requests remain available.
4. **OFF / Manual:** low-voltage and below-50% fan rules no longer block ON.
   Valid battery/temperature/load measurements are still required, and all
   sensor-fault, polarity, output-enable, temperature, current and overvoltage
   checks remain active. Manual mode does not fix an invalid INA219 or change
   the displayed battery percentage. Continued discharge can damage the battery.
5. **ON / Automatic:** the firmware immediately restores both-load critical-voltage
   protection and the fan's 50% / 55% hysteresis. Ineligible running loads turn OFF;
   no load starts automatically. Send a fresh ON after any recovery.
6. Power cycling always restores Automatic ON and both loads OFF. Wi-Fi loss
   retains the last confirmed mode; it does not act as an emergency stop.

Mode changes are audited and bound to the current device boot and mode revision,
with the same 15-second expiry as load commands. Older pending ON requests are
cancelled on mode change, and firmware rejects ON requests from an older revision.
The earlier `ENABLE_CRITICAL_BATTERY_CUTOFF` header override is retired; the
website's acknowledged switch now controls battery-policy behavior.

Upload with battery and panel disconnected. The sketch does not auto-enable
loads or perform an automatic relay cycle. USB serial is diagnostic only, at 115200.
The network task communicates independently of the local sensor/control loop.
No code in the backend uploads firmware or opens the ESP32's serial port.

## 4. SOC limitations and measurement placement

Battery: iVOOMi IV175-7212V, 12V 7.2Ah C20 VRLA. Its label is not an SOC curve.
The current generic lookup uses 11.8 / 12.0 / 12.3 / 12.6 / 12.8V mapped to
0 / 25 / 50 / 75 / 100%, with interpolation. It is **not manufacturer-calibrated**
and is not proof of the battery's true 50% capacity point. Charging, load drop,
temperature, battery age and surface charge can substantially change the estimate.

Battery-feed voltage comes from INA219 bus voltage plus its shunt drop. SOC uses
a filtered voltage; critical and high-voltage checks use the raw valid measurement.
No charging-current, true battery capacity or solar-generation measurement is inferred
from the two load sensors. These two sensors do not implement coulomb counting.

The sensor for each load goes **before** its relay on the positive side:
protected battery positive → sensor VIN+ → VIN− → relay COM → NO → load positive.
Thus its branch supply voltage can remain present with the load OFF. Load negative
returns to battery negative. Sensor VCC is 3.3V, not battery voltage. Both SDA share
GPIO21 and SCL share GPIO22. DS18B20 data is GPIO4 with a 4.7kΩ pull-up to 3.3V.
GPIO26 controls bulb relay IN1; GPIO25 controls fan IN2, through an interface if needed.

## 5. Supabase PostgreSQL and login

These steps require your accounts; nothing has been provisioned automatically.

1. Create/select a Supabase project.
2. In Auth, create the operator email/password user(s). The dashboard has no public
   sign-up route. Disable public signups if not needed for your project.
3. Obtain the project URL and **publishable** API key. Do not put a database password,
   service-role key or device token in frontend JavaScript.
4. Obtain the PostgreSQL **transaction pooler** connection URL from Connect.
   Use port/details provided for your project, URL-encode the password and require TLS.
5. Configure `DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`,
   `ALLOWED_EMAILS` (comma-separated exact operator emails), `DEVICE_ID` and `DEVICE_TOKEN`.

Example shape only:

```text
DATABASE_URL=postgresql://postgres.PROJECT:ENCODED_PASSWORD@POOLER_HOST:6543/postgres?sslmode=require
```

The application creates its `ug_*` tables in a separate **urjagrid** schema on first
database use. Keep this schema out of Supabase's exposed Data API schemas and do not
grant browser/anonymous roles access. Only the backend connects using the database credential.
SQLAlchemy uses short-lived connections and disables psycopg prepared statements for
transaction-pooler compatibility. PostgreSQL, not process memory, owns device state,
pending commands, measurements, audit records and notes.

The backend verifies the Supabase access token with Auth and checks the email allowlist.
It stores the token in an HttpOnly, SameSite=Strict cookie (Secure in hosting), not
browser localStorage. Sessions last at most an hour; sign in again when expired.
Logout clears this browser's session cookie; it does not revoke every provider session.
The local shared-password mode is deliberately unavailable on Vercel.

## 6. Deploy to Vercel

1. Push the reviewed source to your GitHub repository. This task does not push it for you.
2. Import that repository into Vercel, with the **repository root** as Root Directory.
   The project contains root `app.py`, `vercel.json` and `requirements.txt`.
   Vercel's FastAPI integration routes requests to the app; no catch-all rewrite is needed.
3. Add the hosted environment variables from section 5 using Vercel's settings.
4. Set `APP_ORIGIN` to the exact production HTTPS URL, with no trailing slash.
   Do not use a wildcard or a different preview deployment's origin.
5. Deploy/redeploy. The frontend and API share one origin. There is no always-running
   serial collector or background loop in the serverless backend.
6. Update ESP32 `SERVER_BASE_URL` and trusted CA for that HTTPS endpoint; keep
   `ALLOW_LOCAL_HTTP=false`, then upload the configured firmware yourself.
7. Sign in and verify that real telemetry appears with **no SIMULATION banner**.

Preview deployments need their own matching origin and intentionally selected
credentials/database; do not casually share a live production device with previews.
Deployment protection must permit the intended device endpoint to be reached; a Vercel
SSO/protection login page is not a device API response. Never disable application authentication.

Vercel configuration and PostgreSQL support are provided but are **not an actual
verified hosted deployment** until configured and tested in your accounts. Check
provider quotas/costs before continuous collection: five-second sampling is about
17,280 samples per day, approximately 518,400 in 30 days. Multiple viewers add requests.
No unapproved paid service or subscription is created.

## 7. Protocol and validation

Firmware POSTs `/api/device/sync` with its own Bearer device token every roughly
five seconds plus request duration. Receipt time is used for history; optional
device measurement time is stored separately. Protocol: `backend/control_models.py`.
Missing readings are null with validity flags, not fabricated zero values.
New telemetry additionally reports the confirmed `automatic_protection` Boolean
and `mode_revision`. Legacy devices omit both fields. An additive
`ug_command_context` table binds commands to mode revisions without modifying or
deleting existing archive columns or historical samples.

Website controls POST explicit ON/OFF commands. Each has a unique UUID and a 15-second
expiry. ESP32 receives pending commands in sync responses, rechecks local interlocks,
deduplicates command IDs and acknowledges application/rejection in a following sync.
The server validates acknowledgement against reported software state. It does not claim
physical contact feedback. Reboots cancel pending commands from the previous boot.
Offline devices cannot receive new website commands; old requests expire, not replay.

Wi-Fi loss does not disable local protection. Existing valid load states remain subject
to the local policy; losing the webpage is not an emergency stop. No infinite offline
telemetry buffer is implemented: missing intervals remain missing in history.
Server locks and database transactions serialize a device's command/telemetry updates.
Duplicate/out-of-order samples cannot overwrite the latest state or keep it online.

Readings are aggregated into at most 720 buckets. Per-load Wh uses trapezoidal
integration; real-data gaps longer than 30 seconds, invalid readings and reboot boundaries
are excluded. Coverage is shown per load. No consumption is extrapolated past the last
measurement. Negative power is shown as measured but excluded from consumption energy.

## 8. Tests and DSA evaluation

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s backend -v
g++ -std=c++11 -Wall -Wextra -pedantic firmware/tests/control_policy_test.cpp -o .venv/control_policy_test.exe
.\.venv\control_policy_test.exe
node --check frontend/control.js
```

- **Classes / inheritance:** `ArchiveBase` → `SQLArchive` → `SQLiteArchive` / `PostgresArchive`.
  Storage implementations share the same command and archive interface.
- **Linked list:** `RecentEventList` reconstructs the latest 200 committed events.
  Appending/evicting is O(1); traversal is O(n). It is not the permanent cloud store.
- **Recursion:** `recursive_merge_sort` orders the combined note/event report timeline
  using divide-and-conquer, O(n log n) time and O(log n) recursion depth.
- **Firmware class:** `ControlPolicy` encapsulates output states and non-bypassable
  interlocks; the host test compiles and exercises the actual firmware policy header.

Tests do not prove physical wiring, true SOC accuracy, relay contact operation or a
cloud deployment. Test real commands initially with the battery/load circuit disconnected.

## 9. Retention and backups

No automatic deletion is enabled. Raw data continues accumulating until you choose a
retention policy. Review database capacity regularly; configure backups in your provider
and verify restores. Existing legacy SQLite data remains untouched and is not silently
relabeled as per-load data. It cannot be separated into bulb/fan history retroactively.

Sources: [Vercel FastAPI](https://vercel.com/docs/frameworks/backend/fastapi),
[Supabase pooler guidance](https://supabase.com/docs/guides/database/connecting-to-postgres),
[voltage/SOC limitations](https://discoverbattery.com/component/content/article/how-can-you-tell-if-a-battery-is-fully-charged?Itemid=101&catid=37).
