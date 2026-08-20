#pragma once

#include <Arduino.h>
#include <PubSubClient.h>

#include "relay_controller.h"

// strict MQTT wire 검증, height session과 이동 admission을 관리한다.
class ControlHandler {
 public:
  ControlHandler(PubSubClient& mqtt, RelayController& relay);

  void beginSession(uint32_t now);
  void invalidateSession();
  void handleMessage(char* topic, byte* payload, unsigned int length);
  void tick(uint32_t now);
  bool publishStatus(const char* event, const char* code, const char* detail);
  bool publishHeartbeat(uint32_t now);
  void handleTimeoutEvent();
  void failClosed();
  bool isReady(uint32_t now) const;

 private:
  void handleHeight(const byte* payload, unsigned int length);
  void handleControl(const byte* payload, unsigned int length);
  void reject(const char* code, const char* detail);
  void stopAndPublish(const char* code, const char* detail);
  void updateArming(uint32_t now);
  bool boundaryAllows(RelayDirection direction) const;
  static bool copyObservedAt(const char* source, char* destination, size_t size);

  PubSubClient& mqtt_;
  RelayController& relay_;
  bool hasBaseline_ = false;
  bool hasDistinctHeight_ = false;
  bool armed_ = false;
  char baselineObservedAt_[48] = {};
  char lastObservedAt_[48] = {};
  float lastHeightCm_ = NAN;
  uint32_t sessionStartedAt_ = 0;
  uint32_t lastDistinctHeightAt_ = 0;
};
