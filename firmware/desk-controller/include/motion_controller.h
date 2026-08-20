#pragma once

#include <Arduino.h>

#include "tilt_policy.h"

class MotionController {
 public:
  bool begin();
  bool start(TiltPolicy::Direction direction, uint32_t duration_ms, int duty_percent);
  bool stop();
  bool take_timer_event();
  bool is_moving() const;

 private:
  static void IRAM_ATTR on_timer_static();
  void IRAM_ATTR on_timer();
  void force_off_main();
  void IRAM_ATTR force_off_isr();
  void arm_timer(uint32_t duration_ms);
  void disarm_timer();

  static MotionController* instance_;
  hw_timer_t* timer_ = nullptr;
  mutable portMUX_TYPE state_mux_ = portMUX_INITIALIZER_UNLOCKED;
  TiltPolicy::Direction direction_ = TiltPolicy::Direction::Stop;
  bool timer_pending_ = false;
};
