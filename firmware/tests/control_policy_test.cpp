#include "../UrjaGrid_WiFi/ControlPolicy.h"
#include <cassert>
#include <cstdio>
#include <limits>

int main() {
  PolicyConfig cfg;
  cfg.outputsEnabled = true;
  ControlPolicy p(cfg);
  assert(!p.isOn(0) && !p.isOn(1));
  assert(!p.command(1, true));
  PolicySample s;
  s.valid[0] = s.valid[1] = s.batteryValid = s.temperatureValid = true;
  s.voltage = 12.6f; s.soc = 75; s.temperature = 27;
  s.current[0] = .75f; s.current[1] = .1f;
  p.update(s);
  assert(p.command(0, true) && p.command(1, true));
  s.soc = 49; p.update(s);
  assert(p.isOn(0) && !p.isOn(1) && !p.command(1, true));
  s.soc = 52; p.update(s); assert(!p.command(1, true));
  s.soc = 55; p.update(s); assert(!p.isOn(1) && p.command(1, true));
  s.temperature = 51; p.update(s);
  assert(!p.isOn(0) && !p.isOn(1) && !p.command(0, true));
  s.temperature = 47; p.update(s); assert(!p.command(0, true));
  s.temperature = 44; p.update(s); assert(!p.isOn(0) && p.command(0, true));
  s.current[0] = 1.6f; p.update(s); assert(!p.isOn(0));
  s.current[0] = 0; p.update(s); assert(!p.command(0, true));
  assert(p.command(0, false) && p.command(0, true));
  s.voltage = 14.5f; p.update(s); assert(!p.isOn(0) && !p.command(1, true));
  s.voltage = 12.6f; s.batteryValid = false; p.update(s);
  assert(!p.command(0, true) && !p.command(1, true));
  assert(p.command(0, false));
  s.batteryValid = true; s.soc = 75; s.temperatureValid = false; p.update(s);
  assert(!p.command(0, true));
  s.temperatureValid = true; s.valid[1] = false; p.update(s);
  assert(p.command(0, true) && !p.command(1, true));
  s.valid[1] = true; s.current[1] = -.1f; p.update(s); assert(!p.command(1, true));
  s.current[1] = std::numeric_limits<float>::quiet_NaN(); p.update(s); assert(!p.command(1, true));
  cfg.outputsEnabled = false; ControlPolicy disabled(cfg); disabled.update(s);
  assert(!disabled.command(0, true));
  assert(estimatedSoc(11.0f) == 0 && estimatedSoc(15.0f) == 100);
  assert(std::fabs(estimatedSoc(12.3f) - 50) < .01f);

  // Default remains protected, including the startup/recovery hysteresis.
  PolicyConfig normalCfg;
  normalCfg.outputsEnabled = true;
  ControlPolicy normal(normalCfg);
  PolicySample low;
  low.valid[0] = low.valid[1] = low.batteryValid = low.temperatureValid = true;
  low.voltage = 11.88f; low.soc = estimatedSoc(low.voltage); low.temperature = 25;
  normal.update(low);
  assert(!normal.command(0, true));
  assert(std::strcmp(normal.reason(0), "critical_battery_voltage") == 0);
  low.voltage = 12.1f; low.soc = estimatedSoc(low.voltage); normal.update(low);
  assert(!normal.isOn(0) && normal.command(0, true));

  // Explicit opt-out changes only the whole-system undervoltage latch.
  ControlPolicy optionalCutoff(normalCfg);
  assert(optionalCutoff.isAutomaticProtection());
  optionalCutoff.setAutomaticProtection(false);
  assert(!optionalCutoff.isAutomaticProtection());
  low.voltage = 11.69f; low.soc = 0;
  optionalCutoff.update(low);
  assert(!optionalCutoff.isOn(0) && !optionalCutoff.isOn(1));
  assert(optionalCutoff.command(0, true));
  assert(optionalCutoff.command(1, true));
  optionalCutoff.setAutomaticProtection(true);
  assert(!optionalCutoff.isOn(0) && !optionalCutoff.isOn(1));
  assert(!optionalCutoff.command(0, true) && !optionalCutoff.command(1, true));
  optionalCutoff.setAutomaticProtection(false);
  assert(!optionalCutoff.isOn(0) && !optionalCutoff.isOn(1));
  assert(optionalCutoff.command(0, true) && optionalCutoff.command(1, true));
  low.voltage = 12.25f; low.soc = 45; optionalCutoff.update(low);
  optionalCutoff.setAutomaticProtection(true);
  assert(optionalCutoff.isOn(0) && !optionalCutoff.isOn(1));
  assert(std::strcmp(optionalCutoff.reason(1), "soc_below_50_or_recovery_below_55") == 0);
  low.voltage = 12.324f; low.soc = 52; optionalCutoff.update(low);
  assert(!optionalCutoff.command(1, true));
  low.voltage = 12.36f; low.soc = 55; optionalCutoff.update(low);
  assert(!optionalCutoff.isOn(1) && optionalCutoff.command(1, true));
  low.voltage = 11.69f; low.soc = 0; optionalCutoff.update(low);
  assert(!optionalCutoff.isOn(0) && !optionalCutoff.isOn(1));
  optionalCutoff.setAutomaticProtection(false);
  assert(!optionalCutoff.isOn(0) && optionalCutoff.command(0, true));
  low.valid[0] = false; optionalCutoff.update(low);
  assert(!optionalCutoff.isOn(0) && !optionalCutoff.command(0, true));
  low.valid[0] = true; low.batteryValid = false; optionalCutoff.update(low);
  assert(!optionalCutoff.command(0, true));
  low.batteryValid = true; low.temperatureValid = false; optionalCutoff.update(low);
  assert(!optionalCutoff.command(0, true));
  low.temperatureValid = true; low.temperature = 51; optionalCutoff.update(low);
  assert(!optionalCutoff.command(0, true));
  low.temperature = 25; low.voltage = 14.5f; low.soc = 100; optionalCutoff.update(low);
  assert(!optionalCutoff.command(0, true) && !optionalCutoff.command(1, true));
  low.voltage = 12.6f; low.soc = 75; low.current[0] = 1.6f; optionalCutoff.update(low);
  assert(!optionalCutoff.command(0, true));
  ControlPolicy restarted(normalCfg);
  assert(restarted.isAutomaticProtection() && !restarted.isOn(0) && !restarted.isOn(1));
  disabled.setAutomaticProtection(false);
  assert(!disabled.command(0, true));
  std::puts("Firmware policy tests passed: Automatic/Manual modes, immediate protection restoration, SOC hysteresis, no auto-restart, sensor/temperature/current/overvoltage checks, output guard.");
}
