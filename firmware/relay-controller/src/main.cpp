#include <Arduino.h>
#include <PubSubClient.h>
#include <WiFi.h>

#include "config.h"
#include "control_handler.h"
#include "relay_controller.h"

using namespace SmartDeskConfig;

namespace {
WiFiClient network;
PubSubClient mqtt(network);
RelayController relay;
ControlHandler control(mqtt, relay);
uint32_t lastWifiAttempt = 0;
uint32_t lastMqttAttempt = 0;
uint32_t lastHeartbeat = 0;
bool wifiWasConnected = false;
bool mqttWasConnected = false;
int lastWifiStatus = -1;
uint8_t consecutiveMqttSocketFailures = 0;

void onMqttMessage(char* topic, byte* payload, unsigned int length) {
  control.handleMessage(topic, payload, length);
}

void onWifiEvent(WiFiEvent_t event, WiFiEventInfo_t info) {
  if (event == ARDUINO_EVENT_WIFI_STA_DISCONNECTED) {
    Serial.printf(
        "Wi-Fi 연결 해제 reason=%u\n",
        info.wifi_sta_disconnected.reason);
  }
}

#if defined(SMARTDESK_RELAY_BENCH)
const char* startResultName(RelayStartResult result) {
  if (result == RelayStartResult::Extended) return "extended";
  if (result == RelayStartResult::Switched) return "switched";
  return "started";
}

void printBenchState(const char* event) {
  Serial.printf(
      "BENCH event=%s direction=%s up=%d down=%d at_ms=%lu\n",
      event,
      relay.directionName(),
      digitalRead(UP_RELAY_PIN),
      digitalRead(DOWN_RELAY_PIN),
      static_cast<unsigned long>(millis()));
}

void startBenchRelay(RelayDirection direction, uint16_t holdMs) {
  const RelayStartResult result = relay.start(direction, holdMs);
  Serial.printf(
      "BENCH command=%s hold_ms=%u result=%s\n",
      direction == RelayDirection::Up ? "UP" : "DOWN",
      holdMs,
      startResultName(result));
  printBenchState("moving");
}

void handleBenchSerial() {
  while (Serial.available() > 0) {
    const char command = static_cast<char>(Serial.read());
    if (command == 'u') startBenchRelay(RelayDirection::Up, 50);
    if (command == 'U') startBenchRelay(RelayDirection::Up, 500);
    if (command == 'd') startBenchRelay(RelayDirection::Down, 50);
    if (command == 'D') startBenchRelay(RelayDirection::Down, 500);
    if (command == 'b') {
      startBenchRelay(RelayDirection::Up, 500);
      delay(1000);
      printBenchState("loop_stall_complete");
    }
    if (command == 's') {
      relay.stop();
      printBenchState("explicit_stop");
    }
    if (command == 'p') printBenchState("snapshot");
  }
}
#endif

void connectWifi(uint32_t now) {
  if (relay.direction() != RelayDirection::Stop ||
      WiFi.status() == WL_CONNECTED ||
      (lastWifiAttempt != 0 && !elapsed(now, lastWifiAttempt, WIFI_RETRY_MS))) {
    return;
  }
  lastWifiAttempt = now;
  Serial.printf("Wi-Fi 연결 시도 (status=%d)\n", static_cast<int>(WiFi.status()));
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
}

void connectMqtt(uint32_t now) {
  if (relay.direction() != RelayDirection::Stop ||
      WiFi.status() != WL_CONNECTED || mqtt.connected() ||
      (lastMqttAttempt != 0 && !elapsed(now, lastMqttAttempt, MQTT_RETRY_MS))) {
    return;
  }
  lastMqttAttempt = now;
  char clientId[48];
  snprintf(
      clientId,
      sizeof(clientId),
      "smartdesk-fin-relay-%012llx",
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
    if (mqttState == MQTT_CONNECT_FAILED &&
        ++consecutiveMqttSocketFailures >= MQTT_SOCKET_RECOVERY_FAILURES) {
      // A full ESP restart recovered the observed C3 Wi-Fi stack wedge after
      // an EMQX restart.  This branch runs only while relay.direction() is
      // Stop (the guard at the beginning of connectMqtt), so it is fail-safe.
      Serial.println("MQTT TCP 재연결 실패 누적으로 ESP32를 안전 재기동합니다.");
      ESP.restart();
    }
    return;
  }
  consecutiveMqttSocketFailures = 0;
  Serial.printf("MQTT 연결 성공 (%s:%u)\n", MQTT_HOST, MQTT_PORT);

  control.beginSession(now);
  if (!mqtt.subscribe(MQTT_HEIGHT_TOPIC, 1) ||
      !mqtt.subscribe(MQTT_CONTROL_TOPIC, 1)) {
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
  mqttWasConnected = true;
  lastHeartbeat = now;
}
}  // namespace

void setup() {
  if (!relay.begin()) {
    // GPIO OFF는 begin()의 timer 생성 전 이미 적용됐다.
    while (true) delay(1000);
  }
  Serial.begin(115200);
  Serial.printf("SMART DESK FIN relay %s\n", FIRMWARE_VERSION);

#if defined(SMARTDESK_RELAY_BENCH)
  Serial.println(
      "BENCH mode: u/U=UP 50/500, d/D=DOWN 50/500, "
      "b=blocked-loop timer, s=STOP, p=state");
  printBenchState("boot");
  return;
#endif

  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.setAutoReconnect(true);
  WiFi.onEvent(onWifiEvent);
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setCallback(onMqttMessage);
  mqtt.setBufferSize(MQTT_BUFFER_BYTES);
  mqtt.setKeepAlive(MQTT_KEEPALIVE_SECONDS);
  mqtt.setSocketTimeout(MQTT_SOCKET_TIMEOUT_SECONDS);
  connectWifi(millis());
}

void loop() {
  const uint32_t now = millis();

#if defined(SMARTDESK_RELAY_BENCH)
  if (relay.takeTimeoutEvent()) printBenchState("timeout");
  handleBenchSerial();
  delay(1);
  return;
#endif

  if (relay.takeTimeoutEvent()) control.handleTimeoutEvent();

  const int wifiStatus = static_cast<int>(WiFi.status());
  if (wifiStatus != lastWifiStatus) {
    Serial.printf("Wi-Fi 상태 변경: %d\n", wifiStatus);
    lastWifiStatus = wifiStatus;
  }
  const bool wifiConnected = wifiStatus == WL_CONNECTED;
  if (!wifiConnected) {
    if (wifiWasConnected || relay.direction() != RelayDirection::Stop) {
      relay.stop();
      control.invalidateSession();
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
    if (mqttWasConnected || relay.direction() != RelayDirection::Stop) {
      relay.stop();
      control.invalidateSession();
    }
    mqttWasConnected = false;
    connectMqtt(now);
    delay(1);
    return;
  }

  if (!mqtt.loop()) {
    relay.stop();
    control.invalidateSession();
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
  delay(1);
}
