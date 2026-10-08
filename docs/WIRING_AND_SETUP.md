# Hardware Wiring & Setup Guide

## 1. Pin Connections Summary

| Component | Component Pin | ESP32 Pin | Notes |
| :--- | :--- | :--- | :--- |
| **INA219 #1 (Solar)** | VCC | 3.3V | Power supply |
| | GND | GND | Common ground |
| | SDA | GPIO 21 | Shared I2C SDA bus |
| | SCL | GPIO 22 | Shared I2C SCL bus |
| | **I2C Address** | `0x40` | Default (no address solder jumper bridged) |
| | VIN+ / VIN- | Solar Panel (+) | Placed in series with solar panel positive |
| **INA219 #2 (Battery/Load)** | VCC | 3.3V or 5V | Power supply |
| | GND | GND | Common ground |
| | SDA | GPIO 21 | Shared I2C SDA bus |
| | SCL | GPIO 22 | Shared I2C SCL bus |
| | **I2C Address** | `0x41` | **Solder bridge `A0` jumper pad on the board** |
| | VIN+ / VIN- | Battery / Load (+) | Placed in series with battery/load feed |
| **DS18B20 (Temp)** | VCC (Red) | 3.3V | Power supply |
| | GND (Black) | GND | Ground |
| | DATA (Yellow) | GPIO 4 | Connect a **4.7kΩ pull-up resistor** between DATA and 3.3V |
| **2-Channel Relay Module** | VCC | 5V | Relay coil supply |
| | GND | GND | Common ground |
| | IN1 | GPIO 26 | Priority 1 Load (LED - Critical) |
| | IN2 | GPIO 25 | Priority 2 Load (DC Fan - Non-critical) |
| **12V-to-5V Buck Converter** | IN+ / IN- | Battery 12V / GND | Regulates battery to 5V |
| | OUT+ / OUT- | ESP32 VIN / GND | Powers ESP32 safely from battery |

---

## 2. Setting Up INA219 I2C Addresses
* By default, INA219 modules have I2C address `0x40`.
* For the second INA219 module, bridge the small solder pads labeled **`A0`** with solder. This shifts its I2C address to **`0x41`**, allowing both sensors to work seamlessly on the same `GPIO 21 (SDA)` / `GPIO 22 (SCL)` bus without bus conflicts.

---

## 3. Required Arduino IDE Libraries
Open the **Library Manager** in Arduino IDE (`Ctrl + Shift + I`) and install:
1. **Blynk** by Volodymyr Shymanskyy
2. **Adafruit INA219** by Adafruit
3. **DallasTemperature** by Miles Burton
4. **OneWire** by Paul Stoffregen

---

## 4. Blynk Template Setup Steps
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

