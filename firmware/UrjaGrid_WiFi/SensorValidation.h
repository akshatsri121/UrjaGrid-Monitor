#pragma once
#include <cmath>
#include <cstdint>
#include <limits>

enum class INAStatus {
  Ok, NotDetected, InitFailed, ReadFailed, CalibrationLost, ConversionPending,
  Overflow, NonFinite, VoltageOutOfRange, ShuntOutOfRange, CurrentOutOfRange
};

struct BranchReading {
  bool present = false, initialized = false, readOK = false;
  bool calibrated = false, conversionReady = false, overflow = false;
  bool valid = false, batteryCandidate = false;
  float voltage = std::numeric_limits<float>::quiet_NaN();
  float current = std::numeric_limits<float>::quiet_NaN();
  float shuntVoltage = std::numeric_limits<float>::quiet_NaN();
  float power = std::numeric_limits<float>::quiet_NaN();
  float sourceVoltage = std::numeric_limits<float>::quiet_NaN();
  INAStatus status = INAStatus::NotDetected;
};

// 32V/2A library calibration: CAL=4096, current LSB=100uA, R100 shunt.
inline void decodeINARegisters(BranchReading& r, uint16_t bus, uint16_t shunt,
                               uint16_t current, uint16_t calibration) {
  r.voltage = (bus >> 3) * .004f;
  const int32_t signedShunt = shunt < 0x8000 ? shunt : static_cast<int32_t>(shunt) - 65536;
  const int32_t signedCurrent = current < 0x8000 ? current : static_cast<int32_t>(current) - 65536;
  r.shuntVoltage = signedShunt * .00001f;
  r.current = signedCurrent * .0001f;
  r.sourceVoltage = r.voltage + r.shuntVoltage;
  r.power = r.voltage * r.current;
  r.calibrated = calibration == 4096;
  r.conversionReady = (bus & 0x02) != 0;
  r.overflow = (bus & 0x01) != 0;
}

inline void validateINAReading(BranchReading& r) {
  r.valid = r.batteryCandidate = false;
  if (!r.present) r.status = INAStatus::NotDetected;
  else if (!r.initialized) r.status = INAStatus::InitFailed;
  else if (!r.readOK) r.status = INAStatus::ReadFailed;
  else if (!r.calibrated) r.status = INAStatus::CalibrationLost;
  else if (!r.conversionReady) r.status = INAStatus::ConversionPending;
  else if (r.overflow) r.status = INAStatus::Overflow;
  else if (!std::isfinite(r.voltage) || !std::isfinite(r.shuntVoltage) ||
           !std::isfinite(r.current) || !std::isfinite(r.sourceVoltage) ||
           !std::isfinite(r.power)) r.status = INAStatus::NonFinite;
  else if (r.voltage < 0 || r.voltage > 26 || r.sourceVoltage < -.32f ||
           r.sourceVoltage > 26) r.status = INAStatus::VoltageOutOfRange;
  else if (std::fabs(r.shuntVoltage) > .32f) r.status = INAStatus::ShuntOutOfRange;
  else if (std::fabs(r.current) > 2.0f) r.status = INAStatus::CurrentOutOfRange;
  else {
    r.status = INAStatus::Ok;
    r.valid = true;
    // Zero volts can be a valid measurement, but never a battery estimate.
    r.batteryCandidate = r.sourceVoltage >= 8 && r.sourceVoltage <= 16.5f;
  }
}

inline bool selectBatteryVoltage(const BranchReading (&r)[2], float& voltage) {
  if (r[0].batteryCandidate && r[1].batteryCandidate &&
      std::fabs(r[0].sourceVoltage - r[1].sourceVoltage) > .25f) return false;
  if (!r[0].batteryCandidate && !r[1].batteryCandidate) return false;
  voltage = r[r[0].batteryCandidate ? 0 : 1].sourceVoltage;
  return true;
}

inline const char* inaFaultReason(INAStatus status) {
  switch (status) {
    case INAStatus::NotDetected: return "load_sensor_i2c_not_detected_check_address_and_wiring";
    case INAStatus::InitFailed: return "load_sensor_initialization_failed";
    case INAStatus::ReadFailed: return "load_sensor_i2c_read_failed";
    case INAStatus::CalibrationLost: return "load_sensor_calibration_lost_retrying";
    case INAStatus::ConversionPending: return "load_sensor_conversion_pending";
    case INAStatus::Overflow: return "load_sensor_adc_math_overflow";
    case INAStatus::NonFinite: return "load_sensor_nonfinite_reading";
    case INAStatus::VoltageOutOfRange: return "load_sensor_voltage_out_of_range_check_wiring";
    case INAStatus::ShuntOutOfRange: return "load_sensor_shunt_out_of_range";
    case INAStatus::CurrentOutOfRange: return "load_sensor_current_out_of_range";
    default: return "load_sensor_unavailable";
  }
}
