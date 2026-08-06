#pragma once

#include <cmath>
#include <cstdint>

namespace SmartDeskPolicy {
constexpr long MIN_HOLD_MS = 50;
constexpr long MAX_HOLD_MS = 500;
constexpr float MIN_MEASURED_HEIGHT_CM = 73.0F;
constexpr float MAX_MEASURED_HEIGHT_CM = 118.0F;
constexpr float MIN_CONTROL_HEIGHT_CM = 75.0F;
constexpr float MAX_CONTROL_HEIGHT_CM = 115.0F;

constexpr bool elapsed(uint32_t now, uint32_t since, uint32_t interval) {
  return static_cast<uint32_t>(now - since) >= interval;
}

constexpr bool holdAllowed(long holdMs) {
  return holdMs >= MIN_HOLD_MS && holdMs <= MAX_HOLD_MS;
}

inline bool measuredHeightAllowed(float heightCm) {
  return std::isfinite(heightCm) && heightCm >= MIN_MEASURED_HEIGHT_CM &&
         heightCm <= MAX_MEASURED_HEIGHT_CM;
}

inline bool directionAllowed(float heightCm, bool up) {
  if (!measuredHeightAllowed(heightCm)) return false;
  return up ? heightCm < MAX_CONTROL_HEIGHT_CM
            : heightCm > MIN_CONTROL_HEIGHT_CM;
}
}  // namespace SmartDeskPolicy
