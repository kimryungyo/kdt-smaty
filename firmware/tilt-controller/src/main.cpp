#include <Arduino.h>
#include <PubSubClient.h>
#include <WiFi.h>

#include "config.h"
#include "motion_controller.h"
#include "tilt_protocol.h"

namespace {
MotionController motion;
TiltProtocol protocol(motion);
WiFiClient network;
PubSubClient mqtt(network);

char line_buffer[TiltConfig::SERIAL_LINE_MAX_BYTES + 1]{};
size_t line_length = 0;
uint32_t last_status_at = 0;
uint32_t last_wifi_attempt = 0;
uint32_t last_mqtt_attempt = 0;

// 이벤트 한 줄이 완성될 때마다 불린다. MQTT가 본선이고, 시리얼은 Wi-Fi가
// 끊겼을 때 보드를 들여다볼 수 있게 항상 같이 내보낸다.
void emit_line(const char* line) {
  Serial.println(line);
  if (mqtt.connected()) {
    mqtt.publish(TiltConfig::MQTT_STATUS_TOPIC, line, false);
  }
}

void handle_incoming(char* payload) {
  protocol.handle_line(payload);
}

void on_mqtt_message(char* topic, uint8_t* payload, unsigned int length) {
  (void)topic;
  if (length == 0 || length > TiltConfig::SERIAL_LINE_MAX_BYTES) {
    protocol.emergency_stop("invalid_mqtt_payload");
    return;
  }
  char command[TiltConfig::SERIAL_LINE_MAX_BYTES + 1];
  memcpy(command, payload, length);
  command[length] = '\0';
  // 줄바꿈이 섞여 와도 첫 줄만 명령으로 본다.
  char* newline = strpbrk(command, "\r\n");
  if (newline != nullptr) *newline = '\0';
  handle_incoming(command);
}

void consume_serial() {
  while (Serial.available() > 0) {
    const char character = static_cast<char>(Serial.read());
    if (character == '\r') continue;
    if (character == '\n') {
      line_buffer[line_length] = '\0';
      handle_incoming(line_buffer);
      line_length = 0;
      continue;
    }
    if (line_length >= TiltConfig::SERIAL_LINE_MAX_BYTES) {
      line_length = 0;
      protocol.emergency_stop("line_too_long");
      continue;
    }
    line_buffer[line_length++] = character;
  }
}

// 재접속은 모터가 멈춰 있을 때만 시도한다. 이동 중 블로킹으로 timer 처리가
// 밀리면 정지 시점을 놓칠 수 있기 때문이다.
void connect_wifi(uint32_t now) {
  if (motion.is_moving() || WiFi.status() == WL_CONNECTED ||
      (last_wifi_attempt != 0 &&
       !TiltConfig::elapsed(now, last_wifi_attempt, TiltConfig::WIFI_RETRY_MS))) {
    return;
  }
  last_wifi_attempt = now;
  Serial.printf("Wi-Fi 연결 시도 (status=%d)\n", static_cast<int>(WiFi.status()));
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
}

void connect_mqtt(uint32_t now) {
  if (motion.is_moving() || WiFi.status() != WL_CONNECTED || mqtt.connected() ||
      (last_mqtt_attempt != 0 &&
       !TiltConfig::elapsed(now, last_mqtt_attempt, TiltConfig::MQTT_RETRY_MS))) {
    return;
  }
  last_mqtt_attempt = now;
  char client_id[48];
  snprintf(client_id, sizeof(client_id), "smartdesk-fin-tilt-%012llx", ESP.getEfuseMac());
  char will[160];
  snprintf(
      will,
      sizeof(will),
      "{\"event\":\"offline\",\"firmware\":\"%s\",\"position_valid\":false}",
      TiltConfig::FIRMWARE_VERSION);
  if (!mqtt.connect(client_id, nullptr, nullptr, TiltConfig::MQTT_STATUS_TOPIC, 0, false, will,
                    true)) {
    Serial.printf("MQTT 연결 실패 (state=%d)\n", mqtt.state());
    network.stop();
    return;
  }
  Serial.printf("MQTT 연결 성공 (%s:%u)\n", TiltConfig::MQTT_HOST, TiltConfig::MQTT_PORT);
  mqtt.subscribe(TiltConfig::MQTT_COMMAND_TOPIC, 1);
  // 서버가 지금 상태를 알고 보정을 다시 넣을 수 있게 곧바로 알린다.
  protocol.publish_status("ready");
}
}  // namespace

void setup() {
  Serial.begin(115200);
  protocol.set_line_handler(emit_line);
  if (!motion.begin()) {
    Serial.println("{\"event\":\"fault\",\"reason\":\"timer_init_failed\",\"position_valid\":false}");
    return;
  }
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  mqtt.setServer(TiltConfig::MQTT_HOST, TiltConfig::MQTT_PORT);
  mqtt.setCallback(on_mqtt_message);
  mqtt.setBufferSize(TiltConfig::EVENT_LINE_MAX_BYTES + 64);
  protocol.begin();
  last_status_at = millis();
}

void loop() {
  consume_serial();
  protocol.handle_timer_event();
  const uint32_t now = millis();
  connect_wifi(now);
  connect_mqtt(now);
  mqtt.loop();
  if (TiltConfig::elapsed(now, last_status_at, TiltConfig::STATUS_HEARTBEAT_MS)) {
    protocol.publish_status();
    last_status_at = now;
  }
}
