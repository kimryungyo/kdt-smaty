// 높이 relay와 틸트를 ESP32 한 대가 맡는 통합 펌웨어다.
//
// 두 장치는 서로 독립된 명령/상태 토픽을 그대로 쓴다(/desk_ctl, /tilt_ctl).
// 서버 입장에서는 보드가 둘이든 하나든 계약이 같으므로, 서버 코드는 바뀌지
// 않는다. 공유하는 것은 Wi-Fi 연결 하나와 MQTT client 하나다.
//
// hardware timer는 relay가 0번, 틸트가 1번을 쓴다. 같은 번호를 쓰면 한쪽의
// 최후 안전 정지가 사라지므로 config.h에서 분리해 두었다.

#include <Arduino.h>
#include <PubSubClient.h>
#include <WiFi.h>

#include "config.h"
#include "control_handler.h"
#include "motion_controller.h"
#include "relay_controller.h"
#include "tilt_protocol.h"

using namespace SmartDeskConfig;

namespace {
WiFiClient network;
PubSubClient mqtt(network);

RelayController relay;
ControlHandler control(mqtt, relay);

MotionController motion;
TiltProtocol protocol(motion);

uint32_t lastWifiAttempt = 0;
uint32_t lastMqttAttempt = 0;
uint32_t lastHeartbeat = 0;
uint32_t lastTiltStatusAt = 0;
bool wifiWasConnected = false;
bool mqttWasConnected = false;
int lastWifiStatus = -1;

// 틸트 이벤트 한 줄이 완성될 때마다 불린다. MQTT가 본선이고, 시리얼은 Wi-Fi가
// 끊겼을 때도 보드를 들여다볼 수 있게 항상 같이 내보낸다.
void emitTiltLine(const char* line) {
  Serial.println(line);
  if (mqtt.connected()) {
    mqtt.publish(TiltConfig::MQTT_STATUS_TOPIC, line, false);
  }
}

void handleTiltCommand(const uint8_t* payload, unsigned int length) {
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
  protocol.handle_line(command);
}

// 두 장치가 한 callback을 공유한다. 토픽으로 갈라 각자의 처리로 넘긴다.
void onMqttMessage(char* topic, byte* payload, unsigned int length) {
  if (topic != nullptr && strcmp(topic, TiltConfig::MQTT_COMMAND_TOPIC) == 0) {
    handleTiltCommand(payload, length);
    return;
  }
  control.handleMessage(topic, payload, length);
}

void onWifiEvent(WiFiEvent_t event, WiFiEventInfo_t info) {
  if (event == ARDUINO_EVENT_WIFI_STA_DISCONNECTED) {
    Serial.printf(
        "Wi-Fi 연결 해제 reason=%u\n",
        info.wifi_sta_disconnected.reason);
  }
}

// 이동 중에도 접속을 시도한다. 연결이 없으면 STOP 명령 자체를 받을 수 없어,
// 이동 중 접속을 미루면 서버가 재발행하는 명령과 맞물려 영구 미접속이 된다.
// 이동의 안전은 hardware timer ISR이 소프트웨어와 무관하게 GPIO를 끄는 것으로
// 이미 보장된다.
void connectWifi(uint32_t now) {
  if (WiFi.status() == WL_CONNECTED ||
      (lastWifiAttempt != 0 && !elapsed(now, lastWifiAttempt, WIFI_RETRY_MS))) {
    return;
  }
  lastWifiAttempt = now;
  Serial.printf("Wi-Fi 연결 시도 (status=%d)\n", static_cast<int>(WiFi.status()));
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
}

// 두 장치가 모두 멈춰 있을 때만 안전하게 재기동할 수 있다.
bool everythingStopped() {
  return relay.direction() == RelayDirection::Stop && !motion.is_moving();
}

// connectWifi와 같은 이유로 이동 중에도 broker 접속을 시도한다.
void connectMqtt(uint32_t now) {
  if (WiFi.status() != WL_CONNECTED || mqtt.connected() ||
      (lastMqttAttempt != 0 && !elapsed(now, lastMqttAttempt, MQTT_RETRY_MS))) {
    return;
  }
  lastMqttAttempt = now;
  char clientId[48];
  snprintf(
      clientId,
      sizeof(clientId),
      "smartdesk-fin-desk-%012llx",
      ESP.getEfuseMac());
  char will[192];
  snprintf(
      will,
      sizeof(will),
      "{\"event\":\"offline\",\"state\":\"STOP\","
      "\"firmware\":\"%s\",\"code\":\"mqtt_disconnected\","
      "\"detail\":\"ESP32 MQTT 연결이 끊겼습니다.\"}",
      FIRMWARE_VERSION);
  if (!mqtt.connect(
          clientId,
          nullptr,
          nullptr,
          MQTT_STATUS_TOPIC,
          0,
          false,
          will,
          true)) {
    const int mqttState = mqtt.state();
    Serial.printf("MQTT 연결 실패 (state=%d)\n", mqttState);
    // Broker restart or a lossy Wi-Fi link can leave a half-open TCP socket
    // behind.  PubSubClient will retry later, but only after the underlying
    // WiFiClient is explicitly closed can the next attempt start cleanly.
    network.stop();
    return;
  }
  Serial.printf("MQTT 연결 성공 (%s:%u)\n", MQTT_HOST, MQTT_PORT);

  control.beginSession(now);
  if (!mqtt.subscribe(MQTT_HEIGHT_TOPIC, 1) ||
      !mqtt.subscribe(MQTT_CONTROL_TOPIC, 1) ||
      !mqtt.subscribe(TiltConfig::MQTT_COMMAND_TOPIC, 1)) {
    control.failClosed();
    return;
  }
  if (!control.publishStatus(
          "online",
          "height_waiting",
          "현재 MQTT 세션의 새 높이를 기다리고 있습니다.")) {
    control.failClosed();
    return;
  }
  // 서버가 이 세션의 틸트 상태를 즉시 동기화할 수 있게 한 번 알린다.
  protocol.publish_status("ready");
  mqttWasConnected = true;
  lastHeartbeat = now;
  lastTiltStatusAt = now;
}

// Wi-Fi나 broker가 끊기면 두 장치를 모두 세운다. 명령을 받을 수 없는 동안
// 움직이고 있으면 정지 명령이 도달할 방법이 없다.
void stopEverythingAndInvalidate() {
  relay.stop();
  motion.stop();
  control.invalidateSession();
}
}  // namespace

void setup() {
  // Serial을 먼저 연다. begin() 실패를 조용한 정지로 만들면 USB 진단조차
  // 불가능해져 원인 파악이 막힌다.
  Serial.begin(115200);
  if (!relay.begin()) {
    // GPIO OFF는 begin()의 timer 생성 전 이미 적용됐다. timer 없이는 hold
    // 상한을 보장할 수 없으므로 이동은 하지 않고, 진단 로그만 계속 낸다.
    while (true) {
      Serial.println(
          "{\"event\":\"fault\",\"reason\":\"relay_timer_init_failed\"}");
      delay(1000);
    }
  }
  protocol.set_line_handler(emitTiltLine);
  if (!motion.begin()) {
    while (true) {
      Serial.println(
          "{\"event\":\"fault\",\"reason\":\"tilt_timer_init_failed\"}");
      delay(1000);
    }
  }
  Serial.printf("SMART DESK FIN desk %s\n", FIRMWARE_VERSION);

  WiFi.mode(WIFI_STA);
  WiFi.persistent(false);
  WiFi.setSleep(false);
  WiFi.setAutoReconnect(false);
  WiFi.onEvent(onWifiEvent);
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setCallback(onMqttMessage);
  mqtt.setBufferSize(MQTT_BUFFER_BYTES);
  mqtt.setKeepAlive(MQTT_KEEPALIVE_SECONDS);
  mqtt.setSocketTimeout(MQTT_SOCKET_TIMEOUT_SECONDS);
  protocol.begin();
  connectWifi(millis());
}

void loop() {
  const uint32_t now = millis();

  // 두 장치의 timer 만료는 연결 상태와 무관하게 항상 먼저 처리한다.
  if (relay.takeTimeoutEvent()) control.handleTimeoutEvent();
  protocol.handle_timer_event();

  const int wifiStatus = static_cast<int>(WiFi.status());
  if (wifiStatus != lastWifiStatus) {
    Serial.printf("Wi-Fi 상태 변경: %d\n", wifiStatus);
    lastWifiStatus = wifiStatus;
  }
  const bool wifiConnected = wifiStatus == WL_CONNECTED;
  if (!wifiConnected) {
    if (wifiWasConnected || !everythingStopped()) {
      stopEverythingAndInvalidate();
    }
    wifiWasConnected = false;
    mqttWasConnected = false;
    if (mqtt.connected()) mqtt.disconnect();
    connectWifi(now);
    delay(1);
    return;
  }
  if (!wifiWasConnected) {
    Serial.printf("Wi-Fi 연결 성공: %s\n", WiFi.localIP().toString().c_str());
  }
  wifiWasConnected = true;

  if (!mqtt.connected()) {
    if (mqttWasConnected || !everythingStopped()) {
      stopEverythingAndInvalidate();
    }
    mqttWasConnected = false;
    connectMqtt(now);
    delay(1);
    return;
  }

  if (!mqtt.loop()) {
    stopEverythingAndInvalidate();
    mqtt.disconnect();
    mqttWasConnected = false;
    delay(1);
    return;
  }

  // mqtt.loop()의 callback은 이 루프에서 캡처한 now보다 늦은 시각으로
  // lastDistinctHeightAt_을 갱신할 수 있다. callback 뒤 시간을 다시 읽어
  // unsigned 경과 시간 계산이 역전되어 새 height lease를 즉시 만료시키지 않는다.
  const uint32_t controlNow = millis();
  control.tick(controlNow);
  if (mqtt.connected() &&
      elapsed(controlNow, lastHeartbeat, STATUS_HEARTBEAT_MS)) {
    if (!control.publishHeartbeat(controlNow)) {
      control.failClosed();
    }
    lastHeartbeat = controlNow;
  }
  if (mqtt.connected() &&
      TiltConfig::elapsed(controlNow, lastTiltStatusAt,
                          TiltConfig::STATUS_HEARTBEAT_MS)) {
    protocol.publish_status();
    lastTiltStatusAt = controlNow;
  }
  delay(1);
}
