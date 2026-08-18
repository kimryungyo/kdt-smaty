#include <Arduino.h>
#include <WiFi.h>

#include "config.h"

#if !ARDUINO_USB_CDC_ON_BOOT
#error "The Wi-Fi diagnostic requires USB CDC serial output."
#endif

namespace {
struct PowerTest {
  wifi_power_t value;
  const char* label;
};

constexpr PowerTest POWER_TESTS[] = {
    {WIFI_POWER_8_5dBm, "8.5 dBm"},
    {WIFI_POWER_11dBm, "11 dBm"},
    {WIFI_POWER_13dBm, "13 dBm"},
    {WIFI_POWER_15dBm, "15 dBm"},
    {WIFI_POWER_17dBm, "17 dBm"},
    {WIFI_POWER_19_5dBm, "19.5 dBm"},
};
constexpr size_t POWER_TEST_COUNT = sizeof(POWER_TESTS) / sizeof(POWER_TESTS[0]);
constexpr uint32_t CONNECT_TIMEOUT_MS = 15000;
constexpr uint32_t SUCCESS_HOLD_MS = 3000;

size_t currentTest = 0;
uint32_t attemptStartedAt = 0;
bool attemptActive = false;
bool softApStarted = false;

void onWifiEvent(WiFiEvent_t event, WiFiEventInfo_t info) {
  if (event == ARDUINO_EVENT_WIFI_STA_DISCONNECTED) {
    Serial.printf(
        "DIAG sta_disconnected reason=%u\n",
        info.wifi_sta_disconnected.reason);
  }
}

void scanNetworks() {
  Serial.println("DIAG scan_start");
  const int count = WiFi.scanNetworks();
  Serial.printf("DIAG scan_count=%d\n", count);
  for (int index = 0; index < count; ++index) {
    const String ssid = WiFi.SSID(index);
    Serial.printf(
        "DIAG scan ssid=%s rssi=%d channel=%d security=%d%s\n",
        ssid.c_str(),
        WiFi.RSSI(index),
        WiFi.channel(index),
        static_cast<int>(WiFi.encryptionType(index)),
        ssid == WIFI_SSID ? " target" : "");
  }
  WiFi.scanDelete();
}

void startNextAttempt() {
  if (currentTest >= POWER_TEST_COUNT) return;
  const PowerTest& test = POWER_TESTS[currentTest];
  WiFi.disconnect(false, true);
  delay(250);
  WiFi.setTxPower(test.value);
  Serial.printf("DIAG sta_attempt power=%s ssid=%s\n", test.label, WIFI_SSID);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  attemptStartedAt = millis();
  attemptActive = true;
}

void beginSoftApTest() {
  if (softApStarted) return;
  softApStarted = true;
  WiFi.disconnect(false, true);
  WiFi.mode(WIFI_AP);
  const bool started = WiFi.softAP("ESP32-C3-TEST", "12345678");
  Serial.printf(
      "DIAG softap started=%d ssid=ESP32-C3-TEST ip=%s\n",
      started,
      WiFi.softAPIP().toString().c_str());
}
}  // namespace

void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println("SMART DESK FIN temporary Wi-Fi diagnostic");
  WiFi.persistent(false);
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.setAutoReconnect(false);
  WiFi.onEvent(onWifiEvent);
  scanNetworks();
  startNextAttempt();
}

void loop() {
  if (softApStarted) {
    delay(100);
    return;
  }

  if (!attemptActive) {
    startNextAttempt();
    return;
  }

  const uint32_t elapsed = millis() - attemptStartedAt;
  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf(
        "DIAG sta_result power=%s result=connected ip=%s rssi=%d\n",
        POWER_TESTS[currentTest].label,
        WiFi.localIP().toString().c_str(),
        WiFi.RSSI());
    if (elapsed >= SUCCESS_HOLD_MS) {
      attemptActive = false;
      ++currentTest;
    }
  } else if (elapsed >= CONNECT_TIMEOUT_MS) {
    Serial.printf(
        "DIAG sta_result power=%s result=timeout status=%d\n",
        POWER_TESTS[currentTest].label,
        static_cast<int>(WiFi.status()));
    attemptActive = false;
    ++currentTest;
  }
  delay(100);

  if (currentTest >= POWER_TEST_COUNT && !attemptActive) beginSoftApTest();
}
