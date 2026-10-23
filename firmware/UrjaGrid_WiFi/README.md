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

## Automatic protection switch

Every reboot starts with Automatic protection ON and both loads OFF. The website
can request an acknowledged mode change through the same authenticated, expiring
command protocol as load ON/OFF. Automatic OFF bypasses only the low-voltage
11.8V trip / 12.1V recovery latch and the fan below-50% / 55% recovery rule.
Battery measurements and each load sensor must still be valid; temperature,
current, polarity, overvoltage and output-enable checks remain active. A missing
bulb sensor therefore still prevents bulb ON in Manual mode. Readings are never
fabricated. Manual operation can over-discharge the battery and is not a charging
solution; direct battery loads bypass the controller's LOAD-terminal cutoff.

Automatic ON immediately re-evaluates the latest sample and turns off loads that
violate the policy. Neither switching modes nor recovering a valid reading turns
a load on automatically. Wi-Fi loss retains the device's last confirmed mode;
reboot restores Automatic. OFF commands remain usable during a mode change, but
new ON commands wait for mode confirmation. Mode revisions reject old ON requests
after a mode change. Both web and firmware updates are needed; deploy the backend
first, then manually upload this sketch with battery/panel disconnected. Existing
firmware remains readable, but the switch stays unavailable until it reports mode
support. The earlier `ENABLE_CRITICAL_BATTERY_CUTOFF` opt-out is retired and is no
longer used; use the acknowledged website switch instead.
