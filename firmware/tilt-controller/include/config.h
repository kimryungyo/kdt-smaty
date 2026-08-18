#pragma once

#include <Arduino.h>

#include "policy.h"

namespace TiltConfig {

constexpr char FIRMWARE_VERSION[] = "tilt-hw039-1.0.1";

// tilt_project.zip에서 검증된 HW-039(BTS7960) 배선이다. UP은 RPWM, DOWN은
// LPWM으로 구동하며 enable 두 개는 hardware timer의 최후 OFF 차단선이다.
constexpr uint8_t R_ENABLE_PIN = 4;
constexpr uint8_t L_ENABLE_PIN = 10;
constexpr uint8_t R_PWM_PIN = 20;
constexpr uint8_t L_PWM_PIN = 21;
constexpr uint8_t R_PWM_CHANNEL = 0;
constexpr uint8_t L_PWM_CHANNEL = 1;
constexpr uint32_t PWM_FREQUENCY_HZ = 20000;
constexpr uint8_t PWM_RESOLUTION_BITS = 10;
constexpr uint16_t PWM_MAX_DUTY = (1U << PWM_RESOLUTION_BITS) - 1U;

constexpr size_t SERIAL_LINE_MAX_BYTES = 128;
constexpr uint32_t STATUS_HEARTBEAT_MS = 5000;

constexpr uint32_t DRIVER_ENABLE_MASK =
    (1UL << R_ENABLE_PIN) | (1UL << L_ENABLE_PIN);

inline bool elapsed(uint32_t now, uint32_t since, uint32_t interval) {
  return static_cast<uint32_t>(now - since) >= interval;
}

}  // namespace TiltConfig
