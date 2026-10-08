# Microgrid data archive

This local backend stores your existing ESP32 serial telemetry while Blynk continues to receive updates. Your original Desktop files are unchanged. It does not read Blynk cloud history and cannot recover measurements from before collection starts.

## Start on Windows

Install Python 3.10+ if needed, open a terminal in this folder, then:

```powershell
python -m pip install -r requirements.txt
python -m serial.tools.list_ports
python server.py --serial COM5
```

Replace COM5 with your ESP32 port. Close Arduino Serial Monitor/Plotter first: only one program can own the port. Open http://127.0.0.1:8000 in your browser. Keep this process and computer running to collect data. Opening USB serial may reset the ESP32 on some adapters; test first with loads disconnected. No relay commands are sent by this backend.

Demo, without hardware:

```powershell
python server.py --demo
```

Demo writes to `data/demo.sqlite3`, separate from real data in `data/microgrid.sqlite3`. Do not run demo and live servers on the same port. Stop with Ctrl+C. Read-only archive browsing: `python server.py`.

## Implemented

- Persistent SQLite database with timestamp indexes and WAL transactions.
- Serial reconnect attempts every 5 seconds. Timestamps are server receipt time in UTC.
- Rolling 24h, 7-day and 30-day graphs, with up to 720 average/min/max buckets in the API.
- Estimated consumption/generation in Wh, peak sampled load, maximum sampled temperature and coverage.
- Missing measurements excluded; gaps over 180 seconds never integrated as if continuously observed. No extrapolation after last sample.
- Raw CSV exports and printable reports (browser Print → Save as PDF).
- Timestamped device status, restart and connection records; persistent maintenance/experiment notes.
- Stale indicator after 180 seconds without telemetry. Demo labeled explicitly.
- Bounded singly linked list for the latest 200 device events, restored from
  SQLite on restart and exposed through `/api/recent-events`.
- Recursive merge sort combines events and maintenance notes into the report
  activity timeline. The selected period includes up to 200 records from each table.

See `../docs/DSA_EVALUATION.md` for the code walkthrough. Run the full suite
with `python -m unittest discover -v` from this folder.

## Data constraints in the supplied firmware

Serial measurements occur every 60 seconds, even though local sampling is faster. Short spikes cannot be reconstructed. The solar sensor is 0x40 and combined battery/load sensor 0x41, NOT one sensor for each load. Individual fan and bulb power cannot be inferred when both are on. V11 is calculated but not currently sent to Blynk; this backend independently estimates energy from sampled power.

The serial payload omits load current, cumulative energy, sensor validity, firmware version and an explicit timestamp. Missing solar/load sensors become zero in the firmware. The collector masks their measurements when it sees startup failure messages, otherwise marks validity unknown. The firmware keeps an old temperature value when the sensor fails, so temperature validity is always flagged unknown. Relay events describe commanded state only. SoC is voltage-based, not a validated battery-capacity measurement. The voltage labeled 'Load voltage' in earlier sketches included the shunt drop; this collector preserves the current firmware's output without relabeling it as independently measured battery state.

Notes are plain text, rendered without HTML execution. The service binds to localhost only and has no internet login/authentication. Do not expose it via port forwarding. For deployment, add TLS, authentication, backup automation and a production application server. You can back up a running SQLite database using Python sqlite3's backup API; copying only the database file while WAL writes are active can lose recent records. Stop the process before manually copying the data folder.

## Existing code observations (not modified)

- The sketch contains a Blynk token and Wi-Fi password: rotate exposed credentials and avoid including them in reports or source control.
- Manual mode returns before thermal/current checks, bypassing those software protections. Correct this before relying on automatic protection.
- Firmware uses fan GPIO 25, while the supplied wiring guide lists GPIO 27. Resolve this discrepancy against actual wiring.
- The INA219 supply should be 3.3 V when its I2C pull-ups connect directly to ESP32 GPIO. Review the guide's '3.3 V or 5 V' option before using it.
- The attached dashboard.html is an incomplete acoustic resonance/NDT page, not the Blynk microgrid frontend. This package provides an independent history page; your Blynk UI remains unchanged.

## Optional features for your approval

1. Direct Wi-Fi JSON telemetry to a hosted authenticated API: collection continues without USB/computer; requires firmware changes and hosting.
2. Sensor-validity flags, device timestamp, boot/session ID, sequence number and offline buffering: prevents stale values and duplicate uploads; requires firmware changes.
3. Notifications for missing telemetry, low battery, overload or temperature; requires thresholds and a notification destination.
4. Daily/weekly scheduled email/PDF reports; requires schedule, recipients and delivery provider.
5. Tariff-based cost estimates: provide a currency and electricity price per kWh. Label solar savings as estimates.
6. Multiple devices, user roles and audit history; requires identity/login choices.
7. Battery health and charge/discharge accounting: requires confirmed sensor placement and suitable measurement data; do not claim accurate SoC from voltage alone.

No optional features that change hardware control, send messages, or require hosting have been activated.
