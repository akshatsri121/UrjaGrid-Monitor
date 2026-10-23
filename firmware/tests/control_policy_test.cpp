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
  std::puts("Firmware policy tests passed: boot, independent loads, SOC hysteresis, no auto-restart, temperature, current latch, invalid sensors, overvoltage, output guard.");
}
