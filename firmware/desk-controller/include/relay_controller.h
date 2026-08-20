#pragma once

#include <Arduino.h>
#include <esp32-hal-timer.h>

enum class RelayDirection : uint8_t { Stop, Up, Down };
enum class RelayStartResult : uint8_t { Started, Extended, Switched };

// GPIO 상호 배제와 network 독립 hardware timer deadline을 소유한다.
class RelayController {
 public:
  bool begin();
  RelayStartResult start(RelayDirection direction, uint16_t holdMs);
  bool stop();
  RelayDirection direction() const;
  const char* directionName() const;
  bool takeTimeoutEvent();

 private:
  static void IRAM_ATTR onTimerStatic();
  void IRAM_ATTR onTimer();
  void IRAM_ATTR forceOffFromIsr();
  void forceOffMain();
  void armTimer(uint16_t holdMs);
  void disarmTimer();
  void writeRelay(uint8_t pin, bool enabled);

  static RelayController* instance_;
  hw_timer_t* timer_ = nullptr;
  mutable portMUX_TYPE stateMux_ = portMUX_INITIALIZER_UNLOCKED;
  volatile RelayDirection direction_ = RelayDirection::Stop;
  volatile bool timeoutPending_ = false;
};
