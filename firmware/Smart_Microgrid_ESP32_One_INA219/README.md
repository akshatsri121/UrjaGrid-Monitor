# Temporary single-INA219 firmware

Open Smart_Microgrid_ESP32_One_INA219.ino in Arduino IDE. The original two-sensor sketch is unchanged.

## Connections

| Component | ESP32 connection |
| --- | --- |
| INA219 VCC / GND | 3.3V / GND |
| INA219 SDA / SCL | GPIO 21 / GPIO 22 |
| DS18B20 VCC / GND / DATA | 3.3V / GND / GPIO 4 |
| Relay IN1 (bulb) / IN2 (fan) | GPIO 26 / GPIO 25 |

Use a 4.7 kohm pull-up between DS18B20 DATA and 3.3V.
Use the working INA219 with A0 bridged, address 0x41.
Battery positive -> appropriate fuse -> INA219 VIN+.
INA219 VIN- -> both relay COM terminals.
Each relay NO -> its load positive; both load negatives -> battery negative.
This measures the combined fan and bulb consumption, not each load separately.
Power the ESP32 by USB; power a 5V relay module from a suitable separate regulated 5V supply.
All logic grounds and battery negative need a common reference for this setup.
Never connect 12V to ESP32 or INA219 VCC. Confirm relay inputs are 3.3V-compatible.
Do not use the discarded overheated sensor. Disconnect power before wiring.

## Upload and run

Install Blynk, Adafruit INA219, OneWire, and DallasTemperature libraries.
Copy secrets.example.h to secrets.h if needed and enter your credentials.
Select your ESP32 board and port, then upload. Serial Monitor: 115200 baud.
Existing credentials have been copied locally when available and remain Git-ignored.
Blynk.begin waits for Wi-Fi/cloud connection before the normal control loop starts.
Readings print every 60 seconds; sensor sampling happens every second.

Serial commands: 1 fan on, 2 fan off, 3 bulb on, 4 bulb off,
5 both on, 6 both off, a automatic, h help.
Commands 1-6 enable manual mode. Automatic mode can turn loads on when battery voltage recovers.

## Measurement limitations

Only 0x41 is initialized. Solar voltage/current/power are unavailable.
The legacy serial format still includes zero solar placeholders, preceded by a
missing-sensor marker so the backend can treat solar measurements as unavailable.
Blynk V2 remains a zero placeholder: hide or rename that widget for this setup.
V10 includes a solar-unavailable message. Existing V3/V5/V6/V7 and relay controls remain.
The voltage-based SoC is an estimate, not measured battery capacity.

This variant retains the original control policy, including manual mode bypassing
software temperature/current trips. It is test firmware, not independent electrical
protection: use appropriate fusing and supervised testing.
