/* UrjaGrid Wi-Fi firmware, protocol v1. No Blynk or serial collector required.
 * INA219 bulb=0x40, fan=0x41; SDA=21 SCL=22; DS18B20=4.
 * Network I/O runs in a separate task; only loop() touches sensors and relays.
 * Software does not replace fuses or guarantee relay state during reset/boot.
 */
#include <Arduino.h>
#include <Wire.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <Adafruit_INA219.h>
#include <OneWire.h>
#include <DallasTemperature.h>
#include <esp_system.h>
#include <time.h>
#include "ControlPolicy.h"
#if __has_include("secrets.h")
#include "secrets.h"
#else
#include "secrets.example.h"
#endif

constexpr uint8_t RELAY_PINS[] = {26, 25};
constexpr uint8_t INA_ADDRESSES[] = {0x40, 0x41};
constexpr bool RELAY_ACTIVE_LOW = true;
constexpr uint32_t SAMPLE_MS = 1000;
constexpr uint32_t SYNC_MS = 5000;
Adafruit_INA219 bulbINA(0x40), fanINA(0x41);
Adafruit_INA219* monitors[] = {&bulbINA, &fanINA};
OneWire oneWire(4);
DallasTemperature temperatures(&oneWire);
bool inaReady[] = {false, false};
bool filterReady = false;
float filteredVoltage = 0;
char bootId[24];
char modeRevision[65];

struct Snapshot {
  BranchReading branch[2];
  PolicySample sample;
  bool on[2] = {false, false};
  char reason[2][100] = {{0}, {0}};
  uint32_t uptime = 0;
  bool automaticProtection = true;
  char modeRevision[65] = {};
};
struct RemoteCommand { char id[37]; char modeRevision[65]; unsigned load; bool on; uint32_t receivedAt, ttl; };
struct Ack { char id[37]; char status[12]; char reason[100]; };
Snapshot published;
SemaphoreHandle_t snapshotMutex;
QueueHandle_t commandQueue, ackQueue;

// Explicit declarations prevent Arduino's sketch preprocessor from moving
// custom-type prototypes ahead of the structure definitions.
void publishState(Snapshot& state);
void sampleSensors(Snapshot& state);

PolicyConfig makePolicyConfig() {
  PolicyConfig cfg;
  cfg.outputsEnabled = ENABLE_LOAD_OUTPUTS;
  cfg.maximumLoadVoltage = MAX_LOAD_SUPPLY_V;
  cfg.currentLimit[0] = BULB_CURRENT_LIMIT_A;
  cfg.currentLimit[1] = FAN_CURRENT_LIMIT_A;
  return cfg;
}
ControlPolicy policy(makePolicyConfig());

bool addressPresent(uint8_t address) {
  Wire.beginTransmission(address);
  return Wire.endTransmission() == 0;
}

bool readINARegister(uint8_t address, uint8_t reg, uint16_t& value) {
  Wire.beginTransmission(address);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom(address, static_cast<size_t>(2), true) != 2) {
    while (Wire.available()) Wire.read();
    return false;
  }
  value = static_cast<uint16_t>(Wire.read()) << 8;
  value |= static_cast<uint16_t>(Wire.read());
  return true;
}

void writeOutputs() {
  for (unsigned i = 0; i < 2; ++i) {
    bool level = RELAY_ACTIVE_LOW ? !policy.isOn(i) : policy.isOn(i);
    digitalWrite(RELAY_PINS[i], level ? HIGH : LOW);
  }
}

void publishState(Snapshot& state) {
  state.uptime = millis();
  state.automaticProtection = policy.isAutomaticProtection();
  strlcpy(state.modeRevision, modeRevision, sizeof(state.modeRevision));
  for (unsigned i = 0; i < 2; ++i) {
    state.on[i] = policy.isOn(i);
    strlcpy(state.reason[i], policy.reason(i), sizeof(state.reason[i]));
  }
  xSemaphoreTake(snapshotMutex, portMAX_DELAY);
  published = state;
  xSemaphoreGive(snapshotMutex);
}

void sampleSensors(Snapshot& state) {
  PolicySample sample;
  for (unsigned i = 0; i < 2; ++i) {
    BranchReading reading;
    reading.present = addressPresent(INA_ADDRESSES[i]);
    if (!reading.present) inaReady[i] = false;
    else if (!inaReady[i]) {
      inaReady[i] = monitors[i]->begin(&Wire);
      if (inaReady[i]) {
        monitors[i]->setCalibration_32V_2A();
        inaReady[i] = monitors[i]->success();
      }
    }
    reading.initialized = inaReady[i];
    if (inaReady[i]) {
      uint16_t bus = 0, shunt = 0, current = 0, calibration = 0;
      // Check each actual read, not just the address ACK. Do not turn a failed
      // library read into a plausible zero-voltage/current measurement.
      reading.readOK = readINARegister(INA_ADDRESSES[i], INA219_REG_BUSVOLTAGE, bus) &&
        readINARegister(INA_ADDRESSES[i], INA219_REG_SHUNTVOLTAGE, shunt) &&
        readINARegister(INA_ADDRESSES[i], INA219_REG_CURRENT, current) &&
        readINARegister(INA_ADDRESSES[i], INA219_REG_CALIBRATION, calibration);
      if (reading.readOK) decodeINARegisters(reading, bus, shunt, current, calibration);
    }
    validateINAReading(reading);
    if (reading.status == INAStatus::ReadFailed || reading.status == INAStatus::CalibrationLost)
      inaReady[i] = false; // Reinitialize on the next sample; never energize a relay to recover.
    state.branch[i] = reading;
    sample.valid[i] = reading.valid;
    sample.current[i] = reading.current;
    sample.branchSupply[i] = reading.sourceVoltage;
    sample.sensorStatus[i] = reading.status;
  }
  float raw = 0;
  bool batteryValid = selectBatteryVoltage(state.branch, raw);
  if (batteryValid) {
    filteredVoltage = filterReady ? .2f * raw + .8f * filteredVoltage : raw;
    filterReady = true;
    // Use raw voltage for immediate critical/overvoltage protection; filtering
    // is used only in the explicitly approximate SOC display and fan rule.
    sample.voltage = raw;
    sample.soc = estimatedSoc(filteredVoltage);
    sample.batteryValid = true;
  } else filterReady = false;
  // setup() and the preceding sample requested an asynchronous conversion.
  float temperature = temperatures.getTempCByIndex(0);
  sample.temperatureValid = std::isfinite(temperature) && temperature != DEVICE_DISCONNECTED_C &&
    temperature != 85.0f && temperature >= -55 && temperature <= 125;
  sample.temperature = temperature;
  temperatures.requestTemperatures();
  state.sample = sample;
  policy.update(sample);
  writeOutputs();
  publishState(state);
}

bool localHttpAllowed(const String& base) {
  if (!ALLOW_LOCAL_HTTP || !base.startsWith("http://")) return false;
  String host = base.substring(7);
  int end = host.indexOf('/'); if (end >= 0) host = host.substring(0, end);
  int port = host.indexOf(':'); if (port >= 0) host = host.substring(0, port);
  IPAddress ip;
  if (!ip.fromString(host)) return false;
  return ip[0] == 10 || (ip[0] == 192 && ip[1] == 168) ||
    (ip[0] == 172 && ip[1] >= 16 && ip[1] <= 31);
}

void networkTask(void*) {
  String base = SERVER_BASE_URL;
  while (base.endsWith("/")) base.remove(base.length() - 1);
  bool secure = base.startsWith("https://");
  bool configured = strlen(DEVICE_TOKEN) >= 32 && strstr(DEVICE_TOKEN, "REPLACE") == nullptr &&
    strstr(WIFI_SSID, "YOUR_") == nullptr && strstr(SERVER_BASE_URL, "YOUR_") == nullptr &&
    (secure ? strstr(SERVER_ROOT_CA, "-----BEGIN CERTIFICATE-----") != nullptr : localHttpAllowed(base));
  if (!configured) {
    Serial.println("Network disabled: fill secrets.h, endpoint and trusted root CA (or explicit private-LAN HTTP).");
    vTaskDelete(nullptr);
    return;
  }
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  configTime(0, 0, "pool.ntp.org", "time.google.com");
  uint64_t sequence = 0;
  Ack pending[8];
  unsigned pendingCount = 0;
  uint32_t lastReconnect = 0;
  for (;;) {
    if (WiFi.status() != WL_CONNECTED) {
      if (millis() - lastReconnect >= 15000) { WiFi.reconnect(); lastReconnect = millis(); }
      vTaskDelay(pdMS_TO_TICKS(1000));
      continue;
    }
    if (secure && time(nullptr) < 1700000000) {
      Serial.println("Waiting for network time to validate HTTPS certificate; local protection continues.");
      vTaskDelay(pdMS_TO_TICKS(5000));
      continue;
    }
    // Drain acknowledgements before copying the state: loop() publishes its
    // new relay state before enqueueing the corresponding acknowledgement.
    while (pendingCount < 8 && xQueueReceive(ackQueue, &pending[pendingCount], 0) == pdTRUE) ++pendingCount;
    Snapshot state;
    xSemaphoreTake(snapshotMutex, portMAX_DELAY);
    state = published;
    xSemaphoreGive(snapshotMutex);
    JsonDocument doc;
    doc["protocol"] = 1;
    doc["device_id"] = DEVICE_ID;
    doc["boot_id"] = bootId;
    doc["sequence"] = ++sequence;
    doc["uptime_ms"] = static_cast<uint64_t>(state.uptime);
    doc["automatic_protection"] = state.automaticProtection;
    doc["mode_revision"] = state.modeRevision;
    time_t epoch = time(nullptr);
    if (epoch >= 1700000000) doc["measured_at"] = static_cast<double>(epoch);
    else doc["measured_at"] = nullptr;
    if (state.sample.batteryValid) {
      doc["battery_voltage"] = state.sample.voltage;
      doc["soc_estimate"] = state.sample.soc;
    } else { doc["battery_voltage"] = nullptr; doc["soc_estimate"] = nullptr; }
    doc["soc_source"] = "generic_vrla_voltage_v1";
    if (state.sample.temperatureValid) doc["temperature_c"] = state.sample.temperature;
    else doc["temperature_c"] = nullptr;
    for (unsigned i = 0; i < 2; ++i) {
      JsonObject branch = doc[i == 0 ? "bulb" : "fan"].to<JsonObject>();
      branch["valid"] = state.branch[i].valid;
      branch["on"] = state.on[i];
      branch["blocked_reason"] = state.reason[i];
      if (state.branch[i].valid) {
        branch["voltage_v"] = state.branch[i].voltage;
        branch["current_a"] = state.branch[i].current;
        branch["power_w"] = state.branch[i].power;
      } else { branch["voltage_v"] = nullptr; branch["current_a"] = nullptr; branch["power_w"] = nullptr; }
    }
    JsonArray acks = doc["acks"].to<JsonArray>();
    for (unsigned i = 0; i < pendingCount; ++i) {
      JsonObject ack = acks.add<JsonObject>();
      ack["id"] = pending[i].id; ack["status"] = pending[i].status; ack["reason"] = pending[i].reason;
    }
    String body; serializeJson(doc, body);
    WiFiClient plain;
    WiFiClientSecure tls;
    HTTPClient http;
    if (secure) tls.setCACert(SERVER_ROOT_CA);
    bool begun = secure ? http.begin(tls, base + "/api/device/sync") : http.begin(plain, base + "/api/device/sync");
    if (begun) {
      http.setConnectTimeout(3000);
      http.setTimeout(5000);
      http.setReuse(false);
      http.addHeader("Content-Type", "application/json");
      http.addHeader("Authorization", String("Bearer ") + DEVICE_TOKEN);
      uint32_t started = millis();
      int status = http.POST(body);
      if (status == 200) {
        String response = http.getString();
        JsonDocument reply;
        DeserializationError error = deserializeJson(reply, response);
        if (!error && reply["accepted"].is<bool>() && reply["accepted"].as<bool>()) {
          pendingCount = 0;
          for (JsonObject item : reply["commands"].as<JsonArray>()) {
            const char* id = item["id"] | "";
            const char* load = item["load"] | "";
            const char* revision = item["mode_revision"] | "";
            long ttl = item["ttl_ms"] | 0;
            uint32_t elapsed = millis() - started;
            if (strlen(id) != 36 || !item["on"].is<bool>() || ttl <= 0 || ttl > 15000 ||
                elapsed >= static_cast<uint32_t>(ttl) || strlen(revision) > 64 ||
                (strcmp(load, "bulb") && strcmp(load, "fan") && strcmp(load, "automation"))) continue;
            RemoteCommand command;
            strlcpy(command.id, id, sizeof(command.id));
            strlcpy(command.modeRevision, revision, sizeof(command.modeRevision));
            command.load = strcmp(load, "bulb") == 0 ? 0 : strcmp(load, "fan") == 0 ? 1 : 2;
            command.on = item["on"].as<bool>();
            command.receivedAt = millis();
            command.ttl = static_cast<uint32_t>(ttl) - elapsed;
            xQueueSend(commandQueue, &command, 0); // Full queue: server retries until expiry.
          }
        }
      } else Serial.printf("Device sync HTTP status: %d (local protection remains active)\n", status);
      http.end();
    }
    vTaskDelay(pdMS_TO_TICKS(SYNC_MS));
  }
}

void setup() {
  for (unsigned i = 0; i < 2; ++i) {
    pinMode(RELAY_PINS[i], OUTPUT);
    digitalWrite(RELAY_PINS[i], RELAY_ACTIVE_LOW ? HIGH : LOW);
  }
  Serial.begin(115200);
  snapshotMutex = xSemaphoreCreateMutex();
  commandQueue = xQueueCreate(8, sizeof(RemoteCommand));
  ackQueue = xQueueCreate(16, sizeof(Ack));
  if (!snapshotMutex || !commandQueue || !ackQueue) {
    Serial.println("Allocation failure; outputs remain OFF.");
    while (true) delay(1000);
  }
  snprintf(bootId, sizeof(bootId), "%08lx%08lx", (unsigned long)esp_random(), (unsigned long)esp_random());
  strlcpy(modeRevision, bootId, sizeof(modeRevision));
  Wire.begin(21, 22);
  Wire.setClock(100000);
  Wire.setTimeOut(50);
  Serial.print("INA219-range I2C address ACKs:");
  for (uint8_t address = 0x40; address <= 0x4F; ++address) {
    if (addressPresent(address)) Serial.printf(" 0x%02X", address);
  }
  Serial.println(" (expected bulb 0x40, fan 0x41)");
  temperatures.begin();
  temperatures.setResolution(12);
  temperatures.setWaitForConversion(false);
  temperatures.requestTemperatures();
  Serial.println("UrjaGrid Wi-Fi: bulb=0x40 fan=0x41. Both outputs start OFF.");
  Serial.println(ENABLE_LOAD_OUTPUTS ? "Outputs enabled; software interlocks active." : "OUTPUTS DISABLED: verify hardware before enabling in secrets.h.");
  Serial.println("Automatic protection ON. Website mode changes require device acknowledgement; restart restores Automatic.");
  Snapshot initial;
  publishState(initial);
  if (xTaskCreate(networkTask, "network", 12288, nullptr, 1, nullptr) != pdPASS)
    Serial.println("Network task failed; local protection continues, outputs start OFF.");
}

void loop() {
  static Snapshot state;
  static uint32_t lastSample = 0;
  static uint32_t lastDiagnostics = 0;
  static Ack processed[8] = {};
  static unsigned processedNext = 0;
  if (millis() - lastSample >= SAMPLE_MS) {
    sampleSensors(state);
    lastSample = millis();
    Serial.printf("Battery estimate: %.1f%% | Bulb: %s | Fan: %s | fan reason: %s | Automatic: %s\n",
      state.sample.batteryValid ? state.sample.soc : -1,
      policy.isOn(0) ? "ON" : "OFF", policy.isOn(1) ? "ON" : "OFF", policy.reason(1),
      policy.isAutomaticProtection() ? "ON" : "OFF");
  }
  if (millis() - lastDiagnostics >= 5000) {
    for (unsigned i = 0; i < 2; ++i) {
      const BranchReading& r = state.branch[i];
      Serial.printf("%s INA219 0x%02X | I2C=%s | status=%s | bus=%.3f V | shunt=%.3f mV | current=%.4f A | source=%.3f V | battery_source=%s\n",
        i == 0 ? "Bulb" : "Fan", INA_ADDRESSES[i], r.present ? "ACK" : "NO_ACK",
        r.valid ? (r.batteryCandidate ? "ok" : "read_ok_low_or_nonbattery_supply_check_VIN") : inaFaultReason(r.status),
        r.voltage, r.shuntVoltage * 1000, r.current, r.sourceVoltage, r.batteryCandidate ? "yes" : "no");
    }
    lastDiagnostics = millis();
  }
  RemoteCommand command;
  if (xQueueReceive(commandQueue, &command, 0) == pdTRUE) {
    Ack ack = {};
    bool seen = false;
    for (unsigned i = 0; i < 8; ++i) {
      if (strcmp(processed[i].id, command.id) == 0) { ack = processed[i]; seen = true; break; }
    }
    if (!seen) {
      strlcpy(ack.id, command.id, sizeof(ack.id));
      bool expired = millis() - command.receivedAt >= command.ttl;
      bool loadOff = command.load < 2 && !command.on;
      bool staleMode = !loadOff && strcmp(command.modeRevision, modeRevision) != 0;
      bool applied = false;
      if (!expired && !staleMode) {
        if (command.load == 2) {
          policy.setAutomaticProtection(command.on);
          strlcpy(modeRevision, command.id, sizeof(modeRevision));
          applied = true;
        } else applied = policy.command(command.load, command.on);
      }
      strlcpy(ack.status, applied ? "applied" : "rejected", sizeof(ack.status));
      strlcpy(ack.reason, expired ? "command_expired" : staleMode ? "mode_changed_send_new_request" :
              applied ? "" : policy.reason(command.load), sizeof(ack.reason));
      processed[processedNext++ % 8] = ack;
      writeOutputs();
      publishState(state);
    }
    xQueueSend(ackQueue, &ack, 0);
  }
  delay(5);
}
