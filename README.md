# UrjaGrid-Monitor
3rd Sem DSA + ST (Sensor Technology) Project

## New Wi-Fi dashboard and independent load controls

The new version uses **bulb INA219 `0x40` and fan INA219 `0x41`**, not a
solar sensor. It includes a cloud-ready authenticated API, separate-load
history, commands with ESP32 acknowledgements, and firmware-local fan lockout
below **50% voltage-estimated SOC** (55% recovery, no automatic restart).

Start with the isolated synthetic demo from the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe backend/control_app.py --demo
```

Open **http://127.0.0.1:8000**. The simulation never connects to a serial port
or accepts real-device telemetry. Its data is stored separately from live data.
Use the 45%, 52% and 60% simulation buttons to demonstrate fan hysteresis.

**Follow [the complete software setup](docs/SOFTWARE_SETUP.md)** for real
Wi-Fi telemetry, credential setup, firmware, Supabase and Vercel deployment.
New sketch: `firmware/UrjaGrid_WiFi/UrjaGrid_WiFi.ino`. Load outputs are
disabled by default until hardware configuration is confirmed. No firmware
was uploaded automatically. No hosted account or deployment is provisioned.

The sections below describe the **preserved legacy Blynk/serial prototype**;
its sensor roles and safety behaviour do not apply to the new version.

## Project structure

```text
firmware/Smart_Microgrid_ESP32/  ESP32 sketch and credential template
frontend/index.html            Microgrid graphs, reports and notes frontend
frontend/reference/            Original supplied frontend and requirements
backend/                       Telemetry collector, database, API and tests
docs/WIRING_AND_SETUP.md        Hardware wiring and Blynk configuration
```

The Blynk dashboard lives in your Blynk account. Its template/dashboard export
was not supplied. The repository includes firmware with its virtual-pin mapping
and the configuration guide. The original supplied HTML is preserved under
`frontend/reference/`: it is an incomplete acoustic/NDT page from a different
project. The working microgrid frontend is `frontend/index.html`, served by the backend.

## Arduino firmware

Open `firmware/Smart_Microgrid_ESP32/Smart_Microgrid_ESP32.ino` in Arduino IDE.
Install the ESP32 board package and Blynk, Adafruit INA219, DallasTemperature,
and OneWire libraries. Select the matching ESP32 board and COM port.

For a new checkout, copy `secrets.example.h` to `secrets.h` in the sketch folder
and fill in the Blynk device token, Wi-Fi SSID and password. The local copy in
this workspace already contains your supplied credentials; Git ignores it.
Do not force-add it. Template ID/name remain in the sketch.

The active firmware uses bulb GPIO 26 and fan GPIO 25; the copied wiring guide
has been corrected to match. Sensor logic power is documented as 3.3 V.
The firmware's manual mode bypasses software temperature/current checks;
this behavior has not been changed in this repository import.

Read [wiring and Blynk setup](docs/WIRING_AND_SETUP.md) before uploading.

## Microgrid history backend

The `backend/` folder contains the local telemetry collector, SQLite archive,
history dashboard, CSV exports, printable reports, and maintenance notes.
It reads the existing ESP32 serial output alongside Blynk.

```powershell
cd backend
python -m pip install -r requirements.txt
python server.py --serial COM5
```

Replace `COM5` with the ESP32 port and close Arduino Serial Monitor first.
Open http://127.0.0.1:8000 for 24-hour, 7-day, and 30-day history.
Collection requires the computer and backend to remain running.

For a separate synthetic demonstration:

```powershell
python server.py --demo
```

Run backend tests:

```powershell
python -m unittest -v test_backend.py
```

See [backend setup and limitations](backend/README.md) for details.

## Evaluation: classes, inheritance, recursion and linked lists

The running backend now uses a bounded singly linked list for recent device
events and recursive merge sort for event/note report timelines. The dashboard
shows both Recent device activity and Report activity timeline sections.
`Archive` and the HTTP handler demonstrate classes and inheritance.

See [DSA walkthrough and demonstration](docs/DSA_EVALUATION.md).
Run all tests from `backend/` with `python -m unittest discover -v`.
Databases, virtual environments, and credentials are excluded from Git.
The firmware uses an ignored local credential header rather than embedded secrets.
