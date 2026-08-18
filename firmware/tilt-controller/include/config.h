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

namespace TiltConfig {

constexpr char FIRMWARE_VERSION[] = "tilt-hw039-2.0.0";

// 서버와는 MQTT로 이야기한다. relay와 같은 결의 장치 토픽이다.
constexpr char MQTT_COMMAND_TOPIC[] = "/tilt_ctl";
constexpr char MQTT_STATUS_TOPIC[] = "/tilt_ctl_status";
// Wi-Fi 접속은 몇 초가 걸린다. 그보다 자주 begin()을 다시 부르면 진행 중인
// 시도를 끊어 WL_CONNECT_FAILED만 반복한다. relay와 같은 간격을 쓴다.
constexpr uint32_t WIFI_RETRY_MS = 15000;
constexpr uint32_t MQTT_RETRY_MS = 5000;
constexpr char MQTT_HOST[] = SMARTDESK_MQTT_HOST;
constexpr uint16_t MQTT_PORT = SMARTDESK_MQTT_PORT;

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
// 이벤트 한 줄을 통째로 담아 두었다가 MQTT로 내보내기 위한 버퍼다.
constexpr size_t EVENT_LINE_MAX_BYTES = 192;
constexpr uint32_t STATUS_HEARTBEAT_MS = 5000;

constexpr uint32_t DRIVER_ENABLE_MASK =
    (1UL << R_ENABLE_PIN) | (1UL << L_ENABLE_PIN);

inline bool elapsed(uint32_t now, uint32_t since, uint32_t interval) {
  return static_cast<uint32_t>(now - since) >= interval;
}

}  // namespace TiltConfig
