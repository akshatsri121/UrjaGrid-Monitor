# UrjaGrid Wi-Fi firmware

Open `UrjaGrid_WiFi.ino` as its own sketch. Follow
[`docs/SOFTWARE_SETUP.md`](../../docs/SOFTWARE_SETUP.md) before configuration/upload.

This is the new **bulb=0x40 / fan=0x41** version. It does not use Blynk.
Copy `secrets.example.h` to ignored `secrets.h`, fill in Wi-Fi, device credentials,
endpoint and trusted CA. Outputs default to disabled; do not enable until hardware
and provisional thresholds have been reviewed. Battery and panel must be disconnected
during the initial upload and telemetry checks.

Libraries: Adafruit INA219 + BusIO, OneWire, DallasTemperature, ArduinoJson 7.x.
Board target for a classic ESP32 Dev Module: `esp32:esp32:esp32`.
`ControlPolicy.h` is also covered by the host C++ test in `../tests/`.
