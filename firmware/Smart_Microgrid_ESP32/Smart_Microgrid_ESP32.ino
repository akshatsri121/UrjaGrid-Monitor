/**********************************************************************************
 * PROJECT: Smart Renewable Energy Microgrid Based on IoT with Intelligent
 *          Energy Management through Data Structures and Algorithms
 * 
 * INSTITUTION: MIT Manipal - Dept. of Electronics & Instrumentation
 * PROGRAM:     B.Tech Cyber Physical Systems (3rd Semester)
 * 
 * HARDWARE:
 *  - ESP32 Development Board
 *  - Solar INA219 (I2C: 0x40 default) - Solar Panel (20W)
 *  - Load/Battery INA219 (I2C: 0x41 with A0 bridged) - 12V SLA Battery & Loads
 *  - DS18B20 Digital Temperature Sensor (1-Wire: GPIO 4)
 *  - 2-Channel Relay Module (Relay 1: GPIO 26, Relay 2: GPIO 25)
 * 
 * BLYNK VIRTUAL PIN ASSIGNMENT:
 *  - V0 : Solar Voltage (V)
 *  - V1 : Solar Current (A)
 *  - V2 : Solar Power (W)
 *  - V3 : Battery Voltage (V)
 *  - V4 : Load Current (A)
 *  - V5 : Load Power (W)
 *  - V6 : Battery State of Charge SoC (%)
 *  - V7 : Battery/System Temperature (°C)
 *  - V8 : Priority 1 Load State (LED) [0/1]
 *  - V9 : Priority 2 Load State (Fan) [0/1]
 *  - V10: System Status / Alert String
 *  - V11: Cumulative Energy Consumed (Wh)
 *  - V12: Auto / Manual Override Mode [0: Auto DSA, 1: Manual]
 *  - V13: Manual Control Priority 1 (LED)
 *  - V14: Manual Control Priority 2 (Fan)
 **********************************************************************************/

/* --- 1. BLYNK CONFIGURATION (Replace with your Blynk.Console template info) --- */
#define BLYNK_TEMPLATE_ID   "TMPL3oQ4vuRzT"
#define BLYNK_TEMPLATE_NAME "Smart Solar Microgrid"
#include "secrets.h"

/* Comment this out to disable serial debug prints */
#define BLYNK_PRINT Serial

#include <WiFi.h>
#include <WiFiClient.h>
#include <BlynkSimpleEsp32.h>
#include <Wire.h>
#include <Adafruit_INA219.h>
#include <OneWire.h>
#include <DallasTemperature.h>

/* --- 2. WI-FI CREDENTIALS --- */
char ssid[] = WIFI_SSID;
char pass[] = WIFI_PASSWORD;

/* --- 3. HARDWARE PIN DEFINITIONS --- */
#define SDA_PIN             21   // ESP32 I2C SDA
#define SCL_PIN             22   // ESP32 I2C SCL
#define ONE_WIRE_BUS_PIN     4   // DS18B20 1-Wire Data pin (needs 4.7k pullup to 3.3V)
#define RELAY_P1_PIN        26   // Relay 1: Priority 1 Load (Bulb / LED)
#define RELAY_P2_PIN        25   // Relay 2: Priority 2 Load (Fan) - GPIO 25

// Most standard relay modules are ACTIVE LOW (LOW = Relay ON, HIGH = Relay OFF)
#define RELAY_ACTIVE_LOW    true

#if RELAY_ACTIVE_LOW
  #define RELAY_ON  LOW
  #define RELAY_OFF HIGH
#else
  #define RELAY_ON  HIGH
  #define RELAY_OFF LOW
#endif

/* --- 4. SAFETY AND OPERATIONAL THRESHOLDS --- */
const float MAX_SAFE_TEMPERATURE = 50.0; // °C - Thermal shutdown threshold
const float MAX_SAFE_LOAD_CURRENT = 2.5;  // A  - Overload trip threshold

// Lead-Acid 12V Battery Thresholds
const float BATT_CRITICAL_CUTOFF  = 11.2; // V  - Disconnect all loads (protect deep discharge)
const float BATT_P2_SHED_VOLTAGE   = 11.8; // V  - Disconnect Priority 2 (Fan)
const float BATT_P2_RECONNECT_VOLT = 12.3; // V  - Hysteresis recovery to reconnect Priority 2
const float BATT_P1_RECONNECT_VOLT = 11.9; // V  - Reconnect Priority 1 (LED)

/* --- 5. DATA STRUCTURES & ALGORITHMS (DSA) DEFINITIONS --- */
struct Load {
  uint8_t id;
  const char* name;
  uint8_t priority;         // 1 = Critical (Highest), 2 = Non-Critical
  uint8_t relayPin;
  float cutoffVoltage;      // Shedding threshold
  float reconnectVoltage;   // Hysteresis reconnection threshold
  bool isConnected;         // Current physical relay state
  bool isTripped;           // Safety fault trip flag
};

// Array of managed loads sorted by priority (Priority 1 first, Priority 2 second)
const int NUM_LOADS = 2;
Load managedLoads[NUM_LOADS] = {
  { 1, "Bulb / LED (P1)", 1, RELAY_P1_PIN, BATT_CRITICAL_CUTOFF, BATT_P1_RECONNECT_VOLT, false, false },
  { 2, "Fan (P2)",        2, RELAY_P2_PIN, BATT_P2_SHED_VOLTAGE,  BATT_P2_RECONNECT_VOLT, false, false }
};

/* --- 6. SENSOR & GLOBAL OBJECTS --- */
Adafruit_INA219 solarINA(0x40);  // Solar input sensor (Default address 0x40)
Adafruit_INA219 loadINA(0x41);   // Battery/Load sensor (0x41 with A0 bridged)

bool solarInaReady = false;
bool loadInaReady  = false;

OneWire oneWire(ONE_WIRE_BUS_PIN);
DallasTemperature tempSensor(&oneWire);

BlynkTimer timer;

// System Telemetry Variables
float solarVoltage = 0.0;
float solarCurrent = 0.0;
float solarPower   = 0.0;

float batteryVoltage = 0.0;
float loadCurrent    = 0.0;
float loadPower      = 0.0;

float systemTemp     = 0.0;
int batterySoC       = 0;
float cumulativeEnergyWh = 0.0;

#define BLYNK_PUSH_INTERVAL_MS 60000L // Push to Blynk once every 60 seconds (1 min)

unsigned long lastEnergyCalcMillis = 0;
bool manualOverrideMode = false; // Controlled via V12
String systemStatusMessage = "System Normal";

// State change tracking to prevent duplicate Blynk messages
int lastSentP1 = -1;
int lastSentP2 = -1;
String lastSentStatus = "";

/* --- 7. HELPER: ESTIMATE 12V SLA BATTERY SOC (%) --- */
int calculateBatterySoC(float voltage) {
  if (voltage >= 12.70) return 100;
  if (voltage >= 12.50) return map((long)(voltage * 100), 1250, 1270, 85, 100);
  if (voltage >= 12.30) return map((long)(voltage * 100), 1230, 1250, 70, 85);
  if (voltage >= 12.00) return map((long)(voltage * 100), 1200, 1230, 45, 70);
  if (voltage >= 11.80) return map((long)(voltage * 100), 1180, 1200, 25, 45);
  if (voltage >= 11.50) return map((long)(voltage * 100), 1150, 1180, 10, 25);
  if (voltage >= 11.20) return map((long)(voltage * 100), 1120, 1150, 0, 10);
  return 0;
}

/* --- 8. PHYSICAL RELAY ACTUATION --- */
void setRelayHardware(Load &load, bool state) {
  load.isConnected = state;
  digitalWrite(load.relayPin, state ? RELAY_ON : RELAY_OFF);
}

/* --- 9. DSA ENERGY MANAGEMENT & DECISION LOGIC --- */
void runEnergyManagementAlgorithm() {
  // If user enabled manual control from Blynk App, bypass autonomous DSA logic
  if (manualOverrideMode) {
    systemStatusMessage = "Manual Control Active";
    return;
  }

  // Check 1: Over-Temperature Emergency Trip
  if (systemTemp >= MAX_SAFE_TEMPERATURE) {
    for (int i = 0; i < NUM_LOADS; i++) {
      setRelayHardware(managedLoads[i], false);
      managedLoads[i].isTripped = true;
    }
    systemStatusMessage = "EMERGENCY: Overheat Trip (" + String(systemTemp, 1) + " C)!";
    return;
  }

  // Check 2: Load Overload Current Emergency Trip
  if (loadCurrent >= MAX_SAFE_LOAD_CURRENT) {
    for (int i = 0; i < NUM_LOADS; i++) { 
      setRelayHardware(managedLoads[i], false);
      managedLoads[i].isTripped = true;
    }
    systemStatusMessage = "EMERGENCY: Overload Trip (" + String(loadCurrent, 2) + " A)!";
    return;
  }

  // Check 3: Clear trips if sensors returned to safe operating windows
  if (systemTemp < (MAX_SAFE_TEMPERATURE - 3.0) && loadCurrent < (MAX_SAFE_LOAD_CURRENT - 0.5)) {
    for (int i = 0; i < NUM_LOADS; i++) {
      managedLoads[i].isTripped = false;
    }
  }

  // Check 4: Priority Queue Energy Allocation Algorithm based on Battery Voltage
  if (batteryVoltage <= BATT_CRITICAL_CUTOFF) {
    // Critical: Deep discharge danger -> Cut off all loads
    setRelayHardware(managedLoads[0], false);
    setRelayHardware(managedLoads[1], false);
    systemStatusMessage = "CRITICAL: Battery Low! All Loads Shed.";
  }
  else if (batteryVoltage <= BATT_P2_SHED_VOLTAGE) {
    // Moderate low: Shed Priority 2 (Fan), keep Priority 1 (LED) if above critical
    setRelayHardware(managedLoads[1], false);
    if (batteryVoltage >= BATT_P1_RECONNECT_VOLT) {
      setRelayHardware(managedLoads[0], true);
      systemStatusMessage = "Load Shedding: Fan OFF, LED Protected.";
    }
  }
  else if (batteryVoltage >= BATT_P2_RECONNECT_VOLT) {
    // Battery recovered to safe level: Reconnect both loads
    setRelayHardware(managedLoads[0], true);
    setRelayHardware(managedLoads[1], true);
    if (solarPower > 5.0) {
      systemStatusMessage = "Optimal: Solar Generating & All Loads Active.";
    } else {
      systemStatusMessage = "Normal: Battery Operating with All Loads.";
    }
  }
  else {
    // Hysteresis Deadband (11.8V to 12.3V): Maintain current state to prevent relay flapping
    if (!managedLoads[1].isConnected) {
      systemStatusMessage = "Hysteresis Hold: Waiting for Battery to reach 12.3V.";
    } else {
      systemStatusMessage = "Normal Operation (Deadband).";
    }
  }
}

/* --- 10. TELEMETRY ACQUISITION & INTEGRATION --- */
void sampleSensors() {
  // Read INA219 #1 (Solar Panel at 0x40 if present)
  if (solarInaReady) {
    float busV_solar = solarINA.getBusVoltage_V();
    float shuntV_solar = solarINA.getShuntVoltage_mV() / 1000.0;
    float current_mA_solar = solarINA.getCurrent_mA();
    if (current_mA_solar < 0) current_mA_solar = 0;
    solarVoltage = busV_solar + shuntV_solar;
    solarCurrent = current_mA_solar / 1000.0;
    solarPower   = solarVoltage * solarCurrent;
  } else {
    solarVoltage = 0.0;
    solarCurrent = 0.0;
    solarPower   = 0.0;
  }

  // Read INA219 #2 (Battery / Loads at 0x41)
  if (loadInaReady) {
    float busVoltage = loadINA.getBusVoltage_V();
    float shuntVoltage = loadINA.getShuntVoltage_mV() / 1000.0;
    float current_mA = loadINA.getCurrent_mA();
    if (current_mA < 0) current_mA = 0; // Filter negative noise
    float current_A = current_mA / 1000.0;
    float loadVoltage = busVoltage + shuntVoltage;
    float power_W = loadVoltage * current_A;

    batteryVoltage = busVoltage;
    loadCurrent    = current_A;
    loadPower      = power_W;
  } else {
    batteryVoltage = 0.0;
    loadCurrent    = 0.0;
    loadPower      = 0.0;
  }

  // Read DS18B20 Temperature
  tempSensor.requestTemperatures();
  float tempC = tempSensor.getTempCByIndex(0);
  if (tempC != DEVICE_DISCONNECTED_C && tempC > -50.0) {
    systemTemp = tempC;
  }

  // Estimate State of Charge
  batterySoC = calculateBatterySoC(batteryVoltage);

  // Compute Cumulative Energy Consumed in Watt-hours (Wh)
  unsigned long currentMillis = millis();
  float deltaHours = (float)(currentMillis - lastEnergyCalcMillis) / 3600000.0;
  cumulativeEnergyWh += (loadPower * deltaHours);
  lastEnergyCalcMillis = currentMillis;
}

/* --- 10.5 LOCAL SENSOR & SAFETY CONTROL LOOP --- */
void runLocalControl() {
  sampleSensors();
  runEnergyManagementAlgorithm();
}

/* --- 11. BLYNK TELEMETRY DISPATCH (QUOTA-OPTIMIZED) --- */
void pushDataToBlynk() {
  // Write only the active 5 dashboard widgets
  Blynk.virtualWrite(V2, solarPower);
  Blynk.virtualWrite(V3, batteryVoltage);
  Blynk.virtualWrite(V5, loadPower);
  Blynk.virtualWrite(V6, batterySoC);
  Blynk.virtualWrite(V7, systemTemp);

  // Send Relays (V8, V9) and Status (V10) ONLY when changed to save messages
  int curP1 = managedLoads[0].isConnected ? 1 : 0;
  if (curP1 != lastSentP1) {
    Blynk.virtualWrite(V8, curP1);
    lastSentP1 = curP1;
  }

  int curP2 = managedLoads[1].isConnected ? 1 : 0;
  if (curP2 != lastSentP2) {
    Blynk.virtualWrite(V9, curP2);
    lastSentP2 = curP2;
  }

  if (systemStatusMessage != lastSentStatus) {
    Blynk.virtualWrite(V10, systemStatusMessage);
    lastSentStatus = systemStatusMessage;
  }

  // Debug prints to Serial (Free - does NOT count towards Blynk quota!)
  Serial.printf("[SOLAR] %.2fV, %.2fA, %.2fW | [BATT] %.2fV, SoC: %d%% | [LOAD] %.2fW | [TEMP] %.1fC\n",
                solarVoltage, solarCurrent, solarPower, batteryVoltage, batterySoC, loadPower, systemTemp);
  Serial.printf("[STATUS] %s | P1(Bulb): %s, P2(Fan): %s\n",
                systemStatusMessage.c_str(),
                managedLoads[0].isConnected ? "ON" : "OFF",
                managedLoads[1].isConnected ? "ON" : "OFF");
}

/* --- 12. BLYNK INCOMING APP CONTROLS (V12, V13, V14) --- */
// Auto / Manual Toggle
BLYNK_WRITE(V12) {
  manualOverrideMode = (param.asInt() == 1);
  Serial.print("Mode Changed: ");
  Serial.println(manualOverrideMode ? "MANUAL" : "AUTOMATIC (DSA)");
}

// Manual Switch for Priority 1 (LED)
BLYNK_WRITE(V13) {
  if (manualOverrideMode) {
    bool state = (param.asInt() == 1);
    setRelayHardware(managedLoads[0], state);
    Blynk.virtualWrite(V8, state ? 1 : 0);
  }
}

// Manual Switch for Priority 2 (Fan)
BLYNK_WRITE(V14) {
  if (manualOverrideMode) {
    bool state = (param.asInt() == 1);
    setRelayHardware(managedLoads[1], state);
    Blynk.virtualWrite(V9, state ? 1 : 0);
  }
}

void printHelp() {
  Serial.println();
  Serial.println("=== Serial Commands ===");
  Serial.println("1 = Fan ON (GPIO 25)");
  Serial.println("2 = Fan OFF");
  Serial.println("3 = Bulb ON (GPIO 26)");
  Serial.println("4 = Bulb OFF");
  Serial.println("5 = Fan and Bulb ON");
  Serial.println("6 = Fan and Bulb OFF");
  Serial.println("a = Resume AUTO DSA Mode");
  Serial.println("h = Show commands");
  Serial.println("=======================");
  Serial.println();
}

void handleSerialCommands() {
  if (Serial.available() > 0) {
    char command = Serial.read();
    switch (command) {
      case '1':
        manualOverrideMode = true;
        setRelayHardware(managedLoads[1], true); // Fan ON
        Blynk.virtualWrite(V9, 1);
        Blynk.virtualWrite(V12, 1);
        Serial.println("Command: Fan ON (Manual Mode)");
        break;
      case '2':
        manualOverrideMode = true;
        setRelayHardware(managedLoads[1], false); // Fan OFF
        Blynk.virtualWrite(V9, 0);
        Blynk.virtualWrite(V12, 1);
        Serial.println("Command: Fan OFF (Manual Mode)");
        break;
      case '3':
        manualOverrideMode = true;
        setRelayHardware(managedLoads[0], true); // Bulb ON
        Blynk.virtualWrite(V8, 1);
        Blynk.virtualWrite(V12, 1);
        Serial.println("Command: Bulb ON (Manual Mode)");
        break;
      case '4':
        manualOverrideMode = true;
        setRelayHardware(managedLoads[0], false); // Bulb OFF
        Blynk.virtualWrite(V8, 0);
        Blynk.virtualWrite(V12, 1);
        Serial.println("Command: Bulb OFF (Manual Mode)");
        break;
      case '5':
        manualOverrideMode = true;
        setRelayHardware(managedLoads[1], true);
        setRelayHardware(managedLoads[0], true);
        Blynk.virtualWrite(V8, 1);
        Blynk.virtualWrite(V9, 1);
        Blynk.virtualWrite(V12, 1);
        Serial.println("Command: Fan and Bulb ON (Manual Mode)");
        break;
      case '6':
        manualOverrideMode = true;
        setRelayHardware(managedLoads[1], false);
        setRelayHardware(managedLoads[0], false);
        Blynk.virtualWrite(V8, 0);
        Blynk.virtualWrite(V9, 0);
        Blynk.virtualWrite(V12, 1);
        Serial.println("Command: Fan and Bulb OFF (Manual Mode)");
        break;
      case 'a':
      case 'A':
        manualOverrideMode = false;
        Blynk.virtualWrite(V12, 0);
        Serial.println("Command: AUTO DSA Mode Restored");
        break;
      case 'h':
      case 'H':
        printHelp();
        break;
    }
  }
}

/* --- 13. SETUP & INITIALIZATION --- */
void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println("\n=== Starting Smart Renewable Energy Microgrid ESP32 ===");

  // Initialize Relay GPIOs (Bulb: 26, Fan: 25)
  pinMode(RELAY_P1_PIN, OUTPUT);
  pinMode(RELAY_P2_PIN, OUTPUT);

  // Start with both loads OFF for safety
  setRelayHardware(managedLoads[0], false);
  setRelayHardware(managedLoads[1], false);

  // Initialize I2C Bus at 100 kHz (SDA: GPIO 21, SCL: GPIO 22)
  Wire.begin(SDA_PIN, SCL_PIN);
  Wire.setClock(100000);

  // Initialize DS18B20 Temperature Sensor
  tempSensor.begin();
  Serial.println("DS18B20 temperature sensor initialized.");

  // Initialize INA219 Sensors
  solarInaReady = solarINA.begin();
  if (solarInaReady) {
    solarINA.setCalibration_32V_2A();
    Serial.println("Solar INA219 (0x40) detected and calibrated (32V, 2A).");
  } else {
    Serial.println("Solar INA219 (0x40) not detected (will report 0V).");
  }

  loadInaReady = loadINA.begin();
  if (loadInaReady) {
    loadINA.setCalibration_32V_2A();
    Serial.println("Load INA219 (0x41) detected and calibrated (32V, 2A).");
  } else {
    Serial.println("WARNING: Load INA219 (0x41) NOT detected!");
  }

  lastEnergyCalcMillis = millis();

  // Connect to Wi-Fi and Blynk
  Serial.print("Connecting to Blynk & Wi-Fi: ");
  Serial.println(ssid);
  Blynk.begin(BLYNK_AUTH_TOKEN, ssid, pass);

  // 1. Local hardware safety checks & sensor sampling every 1 second (1000 ms)
  timer.setInterval(1000L, runLocalControl);

  // 2. Cloud telemetry to Blynk using optimized interval (60 seconds)
  timer.setInterval(BLYNK_PUSH_INTERVAL_MS, pushDataToBlynk);

  Serial.println("System Ready.");
  printHelp();
}

/* --- 14. MAIN EXECUTION LOOP --- */
void loop() {
  Blynk.run();
  timer.run();
  handleSerialCommands();
}

