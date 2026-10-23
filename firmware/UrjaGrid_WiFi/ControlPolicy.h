#pragma once
#include <cmath>
#include <cstring>
#include "SensorValidation.h"

// Pure C++: the exact firmware interlock logic is also tested on a computer.
struct PolicyConfig {
  bool outputsEnabled = false;
  float criticalVoltage = 11.8f;
  float criticalRecoveryVoltage = 12.1f;
  float maximumLoadVoltage = 12.8f; // Provisional: unknown load tolerance above nominal.
  float maximumTemperature = 50.0f;
  float temperatureRecovery = 45.0f;
  float currentLimit[2] = {1.5f, 0.3f}; // Provisional, NOT a fuse replacement.
};

struct PolicySample {
  bool valid[2] = {false, false};
  float current[2] = {0, 0};
  float branchSupply[2] = {0, 0};
  INAStatus sensorStatus[2] = {INAStatus::Ok, INAStatus::Ok};
  bool batteryValid = false;
  float voltage = 0;
  float soc = 0;
  bool temperatureValid = false;
  float temperature = 0;
};

class ControlPolicy {
 public:
  explicit ControlPolicy(PolicyConfig settings) : config(settings) {}

  bool isAutomaticProtection() const { return automaticProtection; }

  // Re-evaluate immediately when enabling automation; never restart a load.
  void setAutomaticProtection(bool enabled) {
    automaticProtection = enabled;
    update(latest);
  }

  void update(const PolicySample& sample) {
    latest = sample;
    if (!sample.batteryValid || !std::isfinite(sample.voltage) || !std::isfinite(sample.soc)) {
      fanLocked = criticalLocked = true;
    } else {
      if (sample.soc < 50) fanLocked = true;
      else if (sample.soc >= 55) fanLocked = false;
      if (sample.voltage <= config.criticalVoltage) criticalLocked = true;
      else if (sample.voltage >= config.criticalRecoveryVoltage) criticalLocked = false;
    }
    if (!sample.temperatureValid || !std::isfinite(sample.temperature) ||
        sample.temperature >= config.maximumTemperature) temperatureLocked = true;
    else if (sample.temperature <= config.temperatureRecovery) temperatureLocked = false;
    for (unsigned i = 0; i < 2; ++i) {
      if (sample.valid[i] && std::isfinite(sample.current[i]) &&
          sample.current[i] > config.currentLimit[i]) currentTripped[i] = true;
      if (reason(i)[0]) state[i] = false;
    }
  }

  const char* reason(unsigned load) const {
    if (load > 1) return "invalid_load";
    if (!config.outputsEnabled) return "hardware_configuration_not_confirmed";
    for (unsigned i = 0; i < 2; ++i) {
      if (latest.valid[i] && std::isfinite(latest.branchSupply[i]) &&
          latest.branchSupply[i] > config.maximumLoadVoltage) return "load_supply_overvoltage";
    }
    if (!latest.batteryValid || !std::isfinite(latest.voltage) || !std::isfinite(latest.soc)) return "battery_measurement_unavailable";
    if (automaticProtection && criticalLocked) return "critical_battery_voltage";
    if (latest.voltage > config.maximumLoadVoltage) return "load_supply_overvoltage";
    if (!latest.temperatureValid || !std::isfinite(latest.temperature)) return "temperature_sensor_unavailable";
    if (temperatureLocked) return "temperature_lockout";
    if (!latest.valid[load] || !std::isfinite(latest.current[load])) return inaFaultReason(latest.sensorStatus[load]);
    if (latest.current[load] < -0.05f) return "check_sensor_polarity";
    if (currentTripped[load]) return "current_trip_send_OFF_to_reset";
    if (automaticProtection && load == 1 && fanLocked) return "soc_below_50_or_recovery_below_55";
    return "";
  }

  bool command(unsigned load, bool on) {
    if (load > 1) return false;
    if (!on) {
      state[load] = false;
      if (latest.valid[load] && std::isfinite(latest.current[load]) &&
          latest.current[load] <= config.currentLimit[load]) currentTripped[load] = false;
      return true;
    }
    if (reason(load)[0]) return false;
    state[load] = true;
    return true;
  }

  bool isOn(unsigned load) const { return load < 2 && state[load]; }

 private:
  PolicyConfig config;
  PolicySample latest;
  bool automaticProtection = true;
  bool state[2] = {false, false};
  bool currentTripped[2] = {false, false};
  bool fanLocked = true;
  bool criticalLocked = true;
  bool temperatureLocked = true;
};

// Generic VRLA voltage lookup, NOT calibrated to the iVOOMi model. Validity and
// charging/load effects must be disclosed. C20 capacity is not used as an ammeter.
inline float estimatedSoc(float voltage) {
  const float volts[] = {11.8f, 12.0f, 12.3f, 12.6f, 12.8f};
  const float percent[] = {0, 25, 50, 75, 100};
  if (voltage <= volts[0]) return 0;
  for (unsigned i = 1; i < 5; ++i) {
    if (voltage <= volts[i]) return percent[i-1] +
      (voltage - volts[i-1]) / (volts[i] - volts[i-1]) * (percent[i] - percent[i-1]);
  }
  return 100;
}
