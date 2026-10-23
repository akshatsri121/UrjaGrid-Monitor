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

## Bulb sensor unavailable: diagnosis and zero-voltage fix

The old firmware required 8–16.5V to call a branch sensor valid. A communicating
sensor on a switched-off branch could therefore be reported unavailable and
prevent its own ON command. Measurement validity and battery-source validity are
now separate. Successful register reads at 0V are valid branch measurements;
they do not produce a battery estimate. At least one real 8–16.5V source reading
is still required, and two qualifying sources differing by over 0.25V are rejected.

Every register transaction is checked, including conversion-ready, math-overflow
and calibration status. Missing/read-failed sensors still prevent ON. The 32V/2A
calibration uses CAL=4096 and a 100uA current LSB for these R100-shunt modules;
the chip's 26V maximum is unchanged. A high voltage on either valid branch blocks
both loads, even if the other branch supplies the battery estimate.

Upload the updated sketch yourself, with battery/panel disconnected. Serial
Monitor at 115200 prints address ACKs at startup, then a detailed line for each
sensor every five seconds. No test cycle or automatic relay activation is added.

- `load_sensor_i2c_not_detected_check_address_and_wiring`: inspect the expected
  address, 3.3V VCC, common GND, SDA21 and SCL22. Two devices sharing an address
  cannot be distinguished by an address scan; test each separately if needed.
- `load_sensor_i2c_read_failed`: address acknowledged but an actual register
  transaction failed; inspect power, solder joints and I2C wiring.
- `read_ok_low_or_nonbattery_supply_check_VIN`: the chip communicates, but its
  measured branch supply cannot provide a battery estimate. Check VIN wiring or
  whether the sensor is after an open relay; this alone is not a failed sensor.
- Calibration/conversion/overflow/range messages identify separate failures.

Recommended bulb path: protected battery+ → bulb INA219 VIN+ → VIN− → relay1
COM → NO → bulb+. Fan uses the equivalent path through 0x41 and relay2. Load−
returns to battery−. Sensor VCC stays at ESP32 3.3V. **VIN− is not GND.** Keep at
least one sensor upstream of its relay to retain battery visibility with loads
OFF. The old solar/combined-load wiring document describes a different prototype.

Sources: [TI INA219 register definitions](https://www.ti.com/lit/ds/symlink/ina219.pdf),
[Adafruit pinouts](https://learn.adafruit.com/adafruit-ina219-current-sensor-breakout/pinouts).
