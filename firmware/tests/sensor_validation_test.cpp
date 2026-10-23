#include "../UrjaGrid_WiFi/SensorValidation.h"
#include "../UrjaGrid_WiFi/ControlPolicy.h"
#include <cassert>
#include <cstdio>

BranchReading reading(uint16_t millivolts, uint16_t shunt = 0, uint16_t current = 0,
                      uint16_t calibration = 4096, uint16_t flags = 2) {
  BranchReading r;
  r.present = r.initialized = r.readOK = true;
  decodeINARegisters(r, static_cast<uint16_t>((millivolts / 4) << 3) | flags,
                     shunt, current, calibration);
  validateINAReading(r);
  return r;
}

int main() {
  BranchReading r = reading(0);
  assert(r.valid && !r.batteryCandidate && r.voltage == 0 && r.power == 0);
  r = reading(12000, 1000, 1000); // 10mV across R100 = 100mA; CAL=4096.
  assert(r.valid && r.batteryCandidate);
  assert(std::fabs(r.voltage - 12) < .001f);
  assert(std::fabs(r.shuntVoltage - .01f) < .00001f);
  // Current register LSB is 100uA.
  assert(std::fabs(r.current - .1f) < .00001f);
  r = reading(12000, 65526, 65526);
  assert(r.valid && r.current < 0 && r.shuntVoltage < 0);
  assert(std::fabs(r.current + .001f) < .00001f);
  BranchReading missing;
  validateINAReading(missing);
  assert(!missing.valid && missing.status == INAStatus::NotDetected);
  r = reading(0); r.readOK = false; validateINAReading(r);
  assert(!r.valid && !r.batteryCandidate && r.status == INAStatus::ReadFailed);
  r = reading(12000); r.initialized = false; validateINAReading(r);
  assert(!r.valid && r.status == INAStatus::InitFailed);
  assert(!reading(12000, 0, 0, 0).valid);
  assert(reading(12000, 0, 0, 0).status == INAStatus::CalibrationLost);
  assert(reading(12000, 0, 0, 4096, 0).status == INAStatus::ConversionPending);
  assert(reading(12000, 0, 0, 4096, 3).status == INAStatus::Overflow);
  assert(reading(27000).status == INAStatus::VoltageOutOfRange);
  assert(reading(12000, 32001).status == INAStatus::ShuntOutOfRange);
  assert(reading(12000, 0, 20001).status == INAStatus::CurrentOutOfRange);
  r = reading(12000); r.current = std::numeric_limits<float>::quiet_NaN(); validateINAReading(r);
  assert(!r.valid && r.status == INAStatus::NonFinite);

  BranchReading pair[2] = {reading(0), reading(12600)};
  float battery = 0;
  assert(selectBatteryVoltage(pair, battery) && std::fabs(battery - 12.6f) < .001f);
  pair[0] = reading(12000);
  assert(!selectBatteryVoltage(pair, battery));
  pair[0] = pair[1] = reading(0);
  assert(!selectBatteryVoltage(pair, battery));
  pair[0] = reading(12000); pair[1] = missing;
  assert(selectBatteryVoltage(pair, battery));
  assert(reading(20000).valid && !reading(20000).batteryCandidate);

  // A relay-OFF branch at 0V need not prevent ON when another branch supplies
  // a genuine battery reading. Both branches at 0V still provide no battery SOC.
  PolicyConfig cfg;
  cfg.outputsEnabled = true;
  ControlPolicy policy(cfg);
  PolicySample s;
  s.valid[0] = s.valid[1] = s.batteryValid = s.temperatureValid = true;
  s.voltage = 12.6f; s.soc = 75; s.temperature = 25;
  s.branchSupply[0] = 0; s.branchSupply[1] = 12.6f;
  policy.update(s);
  assert(policy.command(0, true));
  s.valid[0] = false; s.sensorStatus[0] = INAStatus::NotDetected;
  policy.update(s);
  assert(!policy.isOn(0) && !policy.command(0, true));
  assert(std::strcmp(policy.reason(0), inaFaultReason(INAStatus::NotDetected)) == 0);
  s.valid[0] = true; s.branchSupply[0] = 20;
  policy.setAutomaticProtection(false); policy.update(s);
  assert(!policy.command(0, true) && !policy.command(1, true));
  s.branchSupply[0] = 0; s.batteryValid = false; policy.update(s);
  assert(!policy.command(0, true));
  std::puts("INA219 tests passed: verified zero readings, bus faults, calibration, conversion flags, ranges, battery-source selection, retained interlocks.");
}
