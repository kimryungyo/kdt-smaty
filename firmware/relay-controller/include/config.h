#pragma once

#include <Arduino.h>

#include "policy.h"

#if __has_include("secrets.h")
#include "secrets.h"
#else
#define WIFI_SSID ""
#define WIFI_PASSWORD ""
#endif

#ifndef SMARTDESK_MQTT_HOST
#define SMARTDESK_MQTT_HOST "192.168.0.10"
#endif

#ifndef SMARTDESK_MQTT_PORT
#define SMARTDESK_MQTT_PORT 1883
#endif

namespace SmartDeskConfig {
constexpr char FIRMWARE_VERSION[] = "smartdesk-fin-relay-1.0.0";
constexpr char MQTT_HOST[] = SMARTDESK_MQTT_HOST;
constexpr uint16_t MQTT_PORT = SMARTDESK_MQTT_PORT;

constexpr char MQTT_CONTROL_TOPIC[] = "/desk_ctl";
constexpr char MQTT_STATUS_TOPIC[] = "/desk_ctl_status";
constexpr char MQTT_HEIGHT_TOPIC[] = "/smartdesk/desk/height";

constexpr uint8_t UP_RELAY_PIN = 3;
constexpr uint8_t DOWN_RELAY_PIN = 4;
constexpr bool RELAY_ACTIVE_LOW = false;

constexpr uint16_t MIN_HOLD_MS = SmartDeskPolicy::MIN_HOLD_MS;
constexpr uint16_t MAX_HOLD_MS = SmartDeskPolicy::MAX_HOLD_MS;
constexpr float MIN_MEASURED_HEIGHT_CM =
    SmartDeskPolicy::MIN_MEASURED_HEIGHT_CM;
constexpr float MAX_MEASURED_HEIGHT_CM =
    SmartDeskPolicy::MAX_MEASURED_HEIGHT_CM;
constexpr float MIN_CONTROL_HEIGHT_CM =
    SmartDeskPolicy::MIN_CONTROL_HEIGHT_CM;
constexpr float MAX_CONTROL_HEIGHT_CM =
    SmartDeskPolicy::MAX_CONTROL_HEIGHT_CM;

// 실물 확정 전 relay 분리 검증에 사용하는 보수적인 초기 후보값이다.
constexpr uint32_t HEIGHT_LEASE_MS = 1500;
constexpr uint32_t CONTROL_ARM_DELAY_MS = 500;
constexpr uint32_t BREAK_BEFORE_MAKE_MS = 30;
constexpr uint32_t STATUS_HEARTBEAT_MS = 5000;
constexpr uint32_t WIFI_RETRY_MS = 15000;
constexpr uint32_t MQTT_RETRY_MS = 5000;
constexpr uint16_t MQTT_KEEPALIVE_SECONDS = 15;
constexpr uint16_t MQTT_SOCKET_TIMEOUT_SECONDS = 1;
constexpr size_t MAX_CONTROL_PAYLOAD_BYTES = 160;
constexpr size_t MAX_HEIGHT_PAYLOAD_BYTES = 192;
constexpr size_t MAX_OBSERVED_AT_BYTES = 40;
constexpr size_t MQTT_BUFFER_BYTES = 384;

constexpr uint32_t RELAY_MASK =
    (1UL << UP_RELAY_PIN) | (1UL << DOWN_RELAY_PIN);

inline bool elapsed(uint32_t now, uint32_t since, uint32_t interval) {
  return SmartDeskPolicy::elapsed(now, since, interval);
}
}  // namespace SmartDeskConfig
