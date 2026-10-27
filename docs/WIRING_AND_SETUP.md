# UrjaGrid wiring and setup

This guide matches the current firmware. **Default: USB-powered ESP32, separate regulated 5 V relay supply, no buck and no switch.**

The sensors measure **solar input (0x40)** and **combined fan + bulb consumption (0x41)**, not individual fan/bulb power.

## 1. ESP32 pin map

| ESP32 pin | Connection |
| --- | --- |
| 3V3 | Both INA219 VCC pins and DS18B20 VCC |
| GND | Common negative reference |
| GPIO 21 | Both INA219 SDA pins |
| GPIO 22 | Both INA219 SCL pins |
| GPIO 4 | DS18B20 DATA |
| GPIO 26 | Relay IN1: bulb |
| GPIO 25 | Relay IN2: fan |
| USB socket | Computer USB: power and backend serial |

Older references to fan GPIO 27 are incorrect for this firmware.

## 2. Power off before wiring

Disconnect the battery and USB before changing wires. Use battery-feed fuses near battery positive, sized for the wiring and components. Do not rely on software for short-circuit protection.

Never connect battery/panel voltage to ESP32 pins or INA219 VCC. Use **3.3 V** for both INA219 logic supplies so their I2C pull-ups stay compatible with ESP32 GPIO.

The controller model is unconfirmed. Follow its manual for battery chemistry, connection order and grounding; PV−, BAT− and LOAD− are not automatically interchangeable.

## 3. Battery and charge controller

| From | To |
| --- | --- |
| Battery + through appropriate fuse | Controller BAT+ |
| Battery − | Controller BAT− |

The controller feed is separate from the measured load feed below. These diagrams use battery-direct loads, not controller LOAD terminals; controller LOAD-terminal cutoff will therefore not protect these loads.

## 4. Solar panel and solar INA219

```text
Panel + --> Solar INA219 VIN+
            Solar INA219 VIN− --> Controller PV+

Panel − -----------------------> Controller PV−
```

**Confirm compatibility before using this solar measurement path.** INA219 input terminals must remain within 0–26 V relative to its GND, including worst-case panel open-circuit voltage. Some controllers isolate or switch the panel return, so the shown arrangement is not universal. A 20 W panel rating does not establish voltage compatibility.

Do not connect PV− directly to ESP32 GND unless the controller manual permits it. If panel voltage/controller grounding is unconfirmed, leave the solar measurement terminals disconnected and test the load side first.

Reference: [TI INA219 datasheet](https://www.ti.com/lit/gpn/INA219). The library's 32 V calibration name does **not** raise the chip's 26 V limit.

## 5. Load INA219, bulb and fan

```text
Battery +
    |
    +-- appropriate fuse --> Load INA219 VIN+
                             Load INA219 VIN−
                                     |
                         +-----------+-----------+
                         |                       |
                    Relay 1 COM             Relay 2 COM
                    Relay 1 NO              Relay 2 NO
                         |                       |
                       Bulb +                  Fan +

Bulb − ----------------------------------> Battery −
Fan − -----------------------------------> Battery −
```

| From | To |
| --- | --- |
| Fused battery + | Load INA219 VIN+ |
| Load INA219 VIN− | Relay 1 COM and Relay 2 COM |
| Relay 1 NO | Bulb + |
| Relay 2 NO | Fan + |
| Bulb − | Battery − |
| Fan − | Battery − |

Leave both NC terminals unused. Follow terminal labels, not assumed physical positions.

The controller BAT+ wire does not pass through this load sensor. It measures combined consumption, not battery charging current. Check combined current, fan startup and bulb inrush against shunt, module, relay and wire ratings.

## 6. INA219 logic wiring — both modules

Connect these four pins on **each** module:

| INA219 pin | ESP32 connection |
| --- | --- |
| VCC | 3V3 |
| GND | GND |
| SDA | GPIO 21 |
| SCL | GPIO 22 |

```text
GPIO 21 ---+--- Solar INA219 SDA
           +--- Load INA219 SDA

GPIO 22 ---+--- Solar INA219 SCL
           +--- Load INA219 SCL
```

| Board | Jumper setting | Address |
| --- | --- | --- |
| Solar | A0 and A1 open | 0x40 |
| Load | A0 bridged, A1 open | 0x41 |

**VCC is logic power. VIN+ and VIN− are measurement terminals.** Never connect battery/panel voltage to VCC/SDA/SCL.

Reference: [INA219 pinouts and address settings](https://learn.adafruit.com/adafruit-ina219-current-sensor-breakout/pinouts).

## 7. Relay control and separate 5 V supply

These are separate from COM/NO load-contact connections.

| Relay pin | Connection |
| --- | --- |
| VCC | Regulated 5 V supply positive |
| GND | 5 V supply negative and ESP32 GND |
| IN1 | GPIO 26: bulb |
| IN2 | GPIO 25: fan |

Use a 5 V relay module with inputs explicitly compatible with 3.3 V logic, or a suitable driver. A 5 V coil rating alone does not establish input compatibility. Do not expose ESP32 GPIO to a 5 V input pull-up.

This assumes a standard common-ground module. If it has JD-VCC/isolated supply jumpers, verify its schematic first.

Keep the ESP32 USB-powered. Do not join the separate supply's 5 V positive to ESP32 3V3. Code assumes active-low relay inputs. If a relay continuously buzzes/chatter occurs, power off and check the supply.

## 8. DS18B20 and common ground

### Temperature sensor

| Sensor pin | ESP32 connection |
| --- | --- |
| VCC | 3V3 |
| GND | GND |
| DATA | GPIO 4 |

```text
3V3 --------+------------- DS18B20 VCC
            |
          [4.7 kΩ]
            |
GPIO 4 -----+------------- DS18B20 DATA

GND ---------------------- DS18B20 GND
```

Identify your sensor pins from its documentation; wire colors vary.

### Common negative reference

```text
Battery −
    +--- Controller BAT−
    +--- Fan −
    +--- Bulb −
    +--- ESP32 GND
    +--- Both INA219 GND pins
    +--- DS18B20 GND
    +--- Relay GND / separate 5 V supply negative
```

Give fan/bulb current returns their own adequately rated wires to this distribution point. Do not route load current through ESP32 GND pins or breadboard rails. PV− remains connected to controller PV− as described in section 4.

## 9. Optional buck converter

**Leave it disconnected for the default USB/backend setup.**

For future battery operation:

```text
Fused battery + --> Buck IN+
Battery − --------> Buck IN−
Buck OUT+ --------> Relay 5 V supply input
Buck OUT− --------> Common ground
```

Verify regulated 5.0 V with a reliable meter before connecting electronics. Replacing the separate relay supply with the buck requires adequate current capacity.

Additionally powering ESP32 from buck OUT+ requires checking your exact board's 5V/VIN and USB power isolation. Do not connect external 5 V and computer USB power simultaneously without verified backfeed protection.

---

## 10. Arduino libraries
Open the **Library Manager** in Arduino IDE (`Ctrl + Shift + I`) and install:
1. **Blynk** by Volodymyr Shymanskyy
2. **Adafruit INA219** by Adafruit
3. **DallasTemperature** by Miles Burton
4. **OneWire** by Paul Stoffregen

---

## 11. Blynk configuration
1. Log in to [blynk.cloud](https://blynk.cloud).
2. Go to **Developer Zone** $\rightarrow$ **Templates** $\rightarrow$ **+ New Template**:
   - Name: `Smart Solar Microgrid`
   - Hardware: `ESP32`
   - Connection: `WiFi`
3. Under **Datastreams**, add:
   - `V0`: Double, `0 - 25`, Unit: `V` (Solar Voltage)
   - `V1`: Double, `0 - 3`, Unit: `A` (Solar Current)
   - `V2`: Double, `0 - 30`, Unit: `W` (Solar Power)
   - `V3`: Double, `0 - 16`, Unit: `V` (Battery Voltage)
   - `V4`: Double, `0 - 5`, Unit: `A` (Load Current)
   - `V5`: Double, `0 - 50`, Unit: `W` (Load Power)
   - `V6`: Integer, `0 - 100`, Unit: `%` (Battery SoC)
   - `V7`: Double, `-10 - 85`, Unit: `°C` (Temperature)
   - `V8`: Integer, `0 - 1` (P1 LED Status)
   - `V9`: Integer, `0 - 1` (P2 Fan Status)
   - `V10`: String (System Status / Alerts)
   - `V11`: Double, `0 - 9999`, Unit: `Wh` (Cumulative Energy)
   - `V12`: Integer, `0 - 1` (Auto / Manual Mode Switch)
   - `V13`: Integer, `0 - 1` (Manual P1 Switch)
   - `V14`: Integer, `0 - 1` (Manual P2 Switch)
4. Set **Template ID** and **Template Name** in `firmware/Smart_Microgrid_ESP32/Smart_Microgrid_ESP32.ino`. Copy `secrets.example.h` to `secrets.h` in the same sketch folder and fill in the device token and Wi-Fi credentials. The local `secrets.h` is ignored by Git.
