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

// 이동 중에도 접속을 시도한다. 연결이 없으면 STOP 명령 자체를 받을 수 없어,
// 이동 중 접속을 미루면 서버가 재발행하는 명령과 맞물려 영구 미접속이 된다.
// 정지는 hardware timer ISR이 force_off_isr()로 GPIO를 직접 끄므로 loop
// 블로킹과 무관하게 보장된다.
void connect_wifi(uint32_t now) {
  if (WiFi.status() == WL_CONNECTED ||
      (last_wifi_attempt != 0 &&
       !TiltConfig::elapsed(now, last_wifi_attempt, TiltConfig::WIFI_RETRY_MS))) {
    return;
  }
  last_wifi_attempt = now;
  Serial.printf("Wi-Fi 연결 시도 (status=%d)\n", static_cast<int>(WiFi.status()));
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
}

// connect_wifi와 같은 이유로 이동 중에도 broker 접속을 시도한다.
void connect_mqtt(uint32_t now) {
  if (WiFi.status() != WL_CONNECTED || mqtt.connected() ||
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
  // 접속 정보를 flash에 남기지 않고, 재접속 시점은 아래 루프가 직접 정한다.
  WiFi.persistent(false);
  WiFi.setSleep(false);
  WiFi.setAutoReconnect(false);
  // relay에서 실측으로 고른 최저 출력이다. 이 보드도 같은 전원을 쓰므로 맞춘다.
  WiFi.setTxPower(WIFI_POWER_8_5dBm);
  mqtt.setServer(TiltConfig::MQTT_HOST, TiltConfig::MQTT_PORT);
  mqtt.setCallback(on_mqtt_message);
  mqtt.setBufferSize(TiltConfig::EVENT_LINE_MAX_BYTES + 64);
  mqtt.setKeepAlive(TiltConfig::MQTT_KEEPALIVE_SECONDS);
  mqtt.setSocketTimeout(TiltConfig::MQTT_SOCKET_TIMEOUT_SECONDS);
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
