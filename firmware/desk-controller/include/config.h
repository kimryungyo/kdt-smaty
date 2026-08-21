#pragma once

#include <Arduino.h>

#include "policy.h"
#include "tilt_policy.h"

#if __has_include("secrets.h")
#include "secrets.h"
#else
#define WIFI_SSID ""
#define WIFI_PASSWORD ""
#endif

#ifndef SMARTDESK_MQTT_HOST
#define SMARTDESK_MQTT_HOST "192.168.0.20"
#endif

#ifndef SMARTDESK_MQTT_PORT
#define SMARTDESK_MQTT_PORT 1883
#endif

// 높이 relay와 틸트를 ESP32-WROOM-32E 한 대가 맡는다. 핀은 모두 클래식 ESP32의
// 안전한 범용 출력만 쓴다. 아래는 의도적으로 피한 것들이다.
//
//   GPIO 6~11   내장 SPI 플래시 전용. 쓰면 보드가 부팅하지 못한다.
//   GPIO 0,2,15 strapping. 부팅 모드를 바꾼다.
//   GPIO 12     strapping. 부팅 시 HIGH면 플래시 전압이 1.8V로 잡혀 벽돌이 된다.
//   GPIO 34~39  입력 전용. 출력으로 쓸 수 없다.
//   GPIO 1,3    UART0. 시리얼 진단 로그를 잃는다.

namespace SmartDeskConfig {
constexpr char FIRMWARE_VERSION[] = "smartdesk-fin-desk-1.0.0";
constexpr char MQTT_HOST[] = SMARTDESK_MQTT_HOST;
constexpr uint16_t MQTT_PORT = SMARTDESK_MQTT_PORT;

constexpr char MQTT_CONTROL_TOPIC[] = "/desk_ctl";
constexpr char MQTT_STATUS_TOPIC[] = "/desk_ctl_status";
constexpr char MQTT_HEIGHT_TOPIC[] = "/smartdesk/desk/height";

// GPIO 25/26은 쓰지 않는다. DAC1/DAC2이자 RTC 도메인 핀이라 부팅 순간 레벨이
// 확정되지 않고, active-high 릴레이에서는 그 순간이 그대로 책상 이동이 된다.
// 실제로 25/26에서는 전원을 넣자마자 UP이 걸렸다. 22/23은 부팅 내내
// 하이임피던스라 모듈 풀다운이 OFF를 유지한다.
constexpr uint8_t UP_RELAY_PIN = 22;
constexpr uint8_t DOWN_RELAY_PIN = 23;
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
// 두 장치의 명령과 상태를 한 client가 처리하므로 relay 단독일 때보다 크게 잡는다.
constexpr size_t MQTT_BUFFER_BYTES = 512;

// relay는 hardware timer 0을 쓴다. 틸트는 1을 써야 서로 덮어쓰지 않는다.
constexpr uint8_t RELAY_TIMER_INDEX = 0;

constexpr uint32_t RELAY_MASK =
    (1UL << UP_RELAY_PIN) | (1UL << DOWN_RELAY_PIN);

// 틸트 enable과 같은 이유다. relay의 최후 차단도 GPIO 0~31 레지스터를 쓴다.
static_assert(UP_RELAY_PIN < 32 && DOWN_RELAY_PIN < 32,
              "relay 핀은 GPIO 0~31이어야 ISR 최후 차단이 동작한다.");

inline bool elapsed(uint32_t now, uint32_t since, uint32_t interval) {
  return SmartDeskPolicy::elapsed(now, since, interval);
}
}  // namespace SmartDeskConfig

namespace TiltConfig {

constexpr char FIRMWARE_VERSION[] = "tilt-hw039-2.0.0";

constexpr char MQTT_COMMAND_TOPIC[] = "/tilt_ctl";
constexpr char MQTT_STATUS_TOPIC[] = "/tilt_ctl_status";

// Wi-Fi/MQTT 재시도 간격과 broker 설정은 relay 쪽 값을 그대로 따른다. 한 대가
// 하나의 연결을 공유하므로 실제 접속은 relay 경로에서만 수행한다.
constexpr uint32_t WIFI_RETRY_MS = SmartDeskConfig::WIFI_RETRY_MS;
constexpr uint32_t MQTT_RETRY_MS = SmartDeskConfig::MQTT_RETRY_MS;
constexpr uint16_t MQTT_KEEPALIVE_SECONDS =
    SmartDeskConfig::MQTT_KEEPALIVE_SECONDS;
constexpr uint16_t MQTT_SOCKET_TIMEOUT_SECONDS =
    SmartDeskConfig::MQTT_SOCKET_TIMEOUT_SECONDS;
constexpr char MQTT_HOST[] = SMARTDESK_MQTT_HOST;
constexpr uint16_t MQTT_PORT = SMARTDESK_MQTT_PORT;

// HW-039(BTS7960). UP은 RPWM, DOWN은 LPWM으로 구동하고, enable 두 개가
// hardware timer의 최후 OFF 차단선이다.
//
// enable은 반드시 GPIO 0~31이어야 한다. ISR이 GPIO.out_w1tc로 레지스터를 직접
// 끄는데 그 레지스터가 0~31만 담당하기 때문이다. 32 이상을 쓰면 마스크가
// 넘쳐 최후 차단이 조용히 동작하지 않는다.
constexpr uint8_t R_ENABLE_PIN = 27;
constexpr uint8_t L_ENABLE_PIN = 14;
constexpr uint8_t R_PWM_PIN = 18;
constexpr uint8_t L_PWM_PIN = 19;
constexpr uint8_t R_PWM_CHANNEL = 0;
constexpr uint8_t L_PWM_CHANNEL = 1;
constexpr uint32_t PWM_FREQUENCY_HZ = 20000;
constexpr uint8_t PWM_RESOLUTION_BITS = 10;
constexpr uint16_t PWM_MAX_DUTY = (1U << PWM_RESOLUTION_BITS) - 1U;

// relay가 timer 0을 점유한다. 같은 번호를 쓰면 한쪽의 안전 정지가 사라진다.
constexpr uint8_t TILT_TIMER_INDEX = 1;

constexpr size_t SERIAL_LINE_MAX_BYTES = 128;
constexpr size_t EVENT_LINE_MAX_BYTES = 192;
constexpr uint32_t STATUS_HEARTBEAT_MS = 5000;

constexpr uint32_t DRIVER_ENABLE_MASK =
    (1UL << R_ENABLE_PIN) | (1UL << L_ENABLE_PIN);

// ISR의 최후 차단은 GPIO 0~31 레지스터만 건드린다. 핀을 옮길 때 이 조건을
// 깨면 안전 정지가 조용히 사라지므로 빌드에서 막는다.
static_assert(R_ENABLE_PIN < 32 && L_ENABLE_PIN < 32,
              "틸트 enable 핀은 GPIO 0~31이어야 ISR 최후 차단이 동작한다.");

inline bool elapsed(uint32_t now, uint32_t since, uint32_t interval) {
  return static_cast<uint32_t>(now - since) >= interval;
}

}  // namespace TiltConfig
