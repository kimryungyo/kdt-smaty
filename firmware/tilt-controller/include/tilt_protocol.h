#pragma once

#include <Arduino.h>

#include "line_sink.h"
#include "motion_controller.h"

class TiltProtocol {
 public:
  explicit TiltProtocol(MotionController& motion) : motion_(motion) {}

  // 완성된 이벤트 줄을 어디로 보낼지 바깥에서 정한다(시리얼·MQTT 등).
  void set_line_handler(LineSink::LineHandler handler) { out_.set_handler(handler); }

  void begin();
  void handle_line(char* line);
  void handle_timer_event();
  void emergency_stop(const char* reason);
  void publish_status(const char* event = "status") const;

 private:
  mutable LineSink out_;

  bool parse_float(const char* token, float* value) const;
  bool parse_int(const char* token, int* value) const;
  bool set_calibration(int duty, float speed_mm_s, const char* direction);
  void stop_and_invalidate_if_moving();
  void handle_move_to(const char* target_token, const char* duty_token);
  void handle_manual_run(const char* direction_token, const char* duty_token,
                         const char* duration_token);
  void handle_set_position(const char* position_token);
  void emit_calibrated(int duty, const char* direction) const;
  void emit_rejected(const char* reason);
  void emit_stopped(const char* reason);
  void emit_at_target() const;
  const char* direction_name(TiltPolicy::Direction direction) const;
  float speed_for(int duty, TiltPolicy::Direction direction) const;

  MotionController& motion_;
  float speeds_[101][2]{};
  float position_mm_ = 0.0F;
  float moving_target_mm_ = 0.0F;
  bool position_valid_ = false;
  bool manual_run_ = false;
  uint32_t last_status_at_ = 0;
};
