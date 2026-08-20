#include "tilt_protocol.h"

#include <cmath>
#include <cstdlib>
#include <cstring>

#include "config.h"
#include "tilt_policy.h"

using namespace TiltConfig;

namespace {
constexpr int UP_INDEX = 0;
constexpr int DOWN_INDEX = 1;
}

void TiltProtocol::begin() {
  last_status_at_ = millis();
  publish_status("ready");
}

void TiltProtocol::handle_line(char* line) {
  char* command = strtok(line, " ");
  if (command == nullptr) {
    stop_and_invalidate_if_moving();
    emit_rejected("empty_command");
    return;
  }
  if (strcmp(command, "STOP") == 0) {
    if (strtok(nullptr, " ") != nullptr) {
      stop_and_invalidate_if_moving();
      emit_rejected("stop_arguments");
      return;
    }
    stop_and_invalidate_if_moving();
    emit_stopped("command");
    return;
  }
  if (strcmp(command, "STATUS") == 0) {
    if (strtok(nullptr, " ") != nullptr) {
      stop_and_invalidate_if_moving();
      emit_rejected("status_arguments");
      return;
    }
    publish_status();
    return;
  }
  if (strcmp(command, "SET_POSITION") == 0) {
    handle_set_position(strtok(nullptr, " "));
    return;
  }
  if (strcmp(command, "CALIBRATE") == 0) {
    char* duty_token = strtok(nullptr, " ");
    char* speed_token = strtok(nullptr, " ");
    char* direction = strtok(nullptr, " ");
    if (strtok(nullptr, " ") != nullptr || duty_token == nullptr ||
        speed_token == nullptr || direction == nullptr || motion_.is_moving()) {
      stop_and_invalidate_if_moving();
      emit_rejected("invalid_calibration");
      return;
    }
    int duty = 0;
    float speed = 0;
    if (!parse_int(duty_token, &duty) || !parse_float(speed_token, &speed) ||
        !set_calibration(duty, speed, direction)) {
      stop_and_invalidate_if_moving();
      emit_rejected("invalid_calibration");
      return;
    }
    emit_calibrated(duty, direction);
    return;
  }
  if (strcmp(command, "MOVE_TO") == 0) {
    char* target_token = strtok(nullptr, " ");
    char* duty_token = strtok(nullptr, " ");
    if (strtok(nullptr, " ") != nullptr || target_token == nullptr ||
        duty_token == nullptr) {
      stop_and_invalidate_if_moving();
      emit_rejected("invalid_move_to");
      return;
    }
    handle_move_to(target_token, duty_token);
    return;
  }
  if (strcmp(command, "RUN") == 0) {
    char* direction_token = strtok(nullptr, " ");
    char* duty_token = strtok(nullptr, " ");
    char* duration_token = strtok(nullptr, " ");
    if (strtok(nullptr, " ") != nullptr || direction_token == nullptr ||
        duty_token == nullptr || duration_token == nullptr) {
      stop_and_invalidate_if_moving();
      emit_rejected("invalid_run");
      return;
    }
    handle_manual_run(direction_token, duty_token, duration_token);
    return;
  }
  stop_and_invalidate_if_moving();
  emit_rejected("unknown_command");
}

void TiltProtocol::handle_timer_event() {
  if (!motion_.take_timer_event()) return;
  if (manual_run_) {
    manual_run_ = false;
    position_valid_ = false;
    emit_stopped("manual_complete");
    return;
  }
  position_mm_ = moving_target_mm_;
  position_valid_ = true;
  emit_at_target();
}

void TiltProtocol::emergency_stop(const char* reason) {
  stop_and_invalidate_if_moving();
  emit_rejected(reason);
}

void TiltProtocol::publish_status(const char* event) const {
  out_.printf(
      "{\"event\":\"%s\",\"firmware\":\"%s\",\"position_valid\":%s",
      event, FIRMWARE_VERSION, position_valid_ ? "true" : "false");
  if (position_valid_) out_.printf(",\"position_mm\":%.2f", position_mm_);
  out_.println("}");
}

bool TiltProtocol::parse_float(const char* token, float* value) const {
  if (token == nullptr || value == nullptr || *token == '\0') return false;
  char* end = nullptr;
  const float parsed = strtof(token, &end);
  if (end == token || *end != '\0' || !std::isfinite(parsed)) return false;
  *value = parsed;
  return true;
}

bool TiltProtocol::parse_int(const char* token, int* value) const {
  if (token == nullptr || value == nullptr || *token == '\0') return false;
  char* end = nullptr;
  const long parsed = strtol(token, &end, 10);
  if (end == token || *end != '\0' || parsed < -2147483647L ||
      parsed > 2147483647L) {
    return false;
  }
  *value = static_cast<int>(parsed);
  return true;
}

bool TiltProtocol::set_calibration(int duty, float speed_mm_s,
                                   const char* direction) {
  if (duty < 1 || duty > 100 || !std::isfinite(speed_mm_s) ||
      speed_mm_s <= 0) {
    return false;
  }
  if (strcmp(direction, "UP") == 0) {
    speeds_[duty][UP_INDEX] = speed_mm_s;
    return true;
  }
  if (strcmp(direction, "DOWN") == 0) {
    speeds_[duty][DOWN_INDEX] = speed_mm_s;
    return true;
  }
  return false;
}

void TiltProtocol::handle_move_to(const char* target_token, const char* duty_token) {
  if (motion_.is_moving()) {
    stop_and_invalidate_if_moving();
    emit_rejected("busy");
    return;
  }
  float target = 0;
  int duty = 0;
  if (!parse_float(target_token, &target) || !parse_int(duty_token, &duty) ||
      duty < 1 || duty > 100) {
    stop_and_invalidate_if_moving();
    emit_rejected("invalid_move_to");
    return;
  }
  const TiltPolicy::MotionPlan plan = TiltPolicy::make_motion_plan(
      position_mm_, position_valid_, target,
      speed_for(duty, TiltPolicy::Direction::Up),
      speed_for(duty, TiltPolicy::Direction::Down));
  if (plan.at_target) {
    emit_at_target();
    return;
  }
  if (plan.direction == TiltPolicy::Direction::Stop ||
      !motion_.start(plan.direction, plan.duration_ms, duty)) {
    stop_and_invalidate_if_moving();
    emit_rejected("move_not_armed");
    return;
  }
  moving_target_mm_ = target;
  manual_run_ = false;
  out_.printf(
      "{\"event\":\"moving\",\"target_mm\":%.2f,\"direction\":\"%s\",\"position_valid\":true,\"position_mm\":%.2f}\n",
      target, direction_name(plan.direction), position_mm_);
}

void TiltProtocol::handle_manual_run(const char* direction_token,
                                     const char* duty_token,
                                     const char* duration_token) {
  if (motion_.is_moving()) {
    stop_and_invalidate_if_moving();
    emit_rejected("busy");
    return;
  }
  int duty = 0;
  int duration_ms = 0;
  const TiltPolicy::Direction direction = strcmp(direction_token, "UP") == 0
                                               ? TiltPolicy::Direction::Up
                                               : strcmp(direction_token, "DOWN") == 0
                                                     ? TiltPolicy::Direction::Down
                                                     : TiltPolicy::Direction::Stop;
  if (direction == TiltPolicy::Direction::Stop || !parse_int(duty_token, &duty) ||
      !parse_int(duration_token, &duration_ms) || duty < 1 || duty > 100 ||
      duration_ms < 50 ||
      duration_ms > static_cast<int>(TiltPolicy::ABSOLUTE_MAX_MOTION_MS)) {
    stop_and_invalidate_if_moving();
    emit_rejected("invalid_run");
    return;
  }
  if (!motion_.start(direction, static_cast<uint32_t>(duration_ms), duty)) {
    stop_and_invalidate_if_moving();
    emit_rejected("run_not_armed");
    return;
  }
  // 시간 기반 수동 이동은 위치 센서가 없으므로 절대 위치를 주장하지 않는다.
  position_valid_ = false;
  manual_run_ = true;
  out_.printf(
      "{\"event\":\"moving\",\"direction\":\"%s\",\"duty\":%d,\"duration_ms\":%d,\"position_valid\":false}\n",
      direction_name(direction), duty, duration_ms);
}

void TiltProtocol::handle_set_position(const char* position_token) {
  if (motion_.is_moving()) {
    stop_and_invalidate_if_moving();
    emit_rejected("busy");
    return;
  }
  float position = 0;
  if (!parse_float(position_token, &position) || !TiltPolicy::position_allowed(position)) {
    stop_and_invalidate_if_moving();
    emit_rejected("invalid_position");
    return;
  }
  position_mm_ = position;
  position_valid_ = true;
  publish_status("ready");
}

void TiltProtocol::stop_and_invalidate_if_moving() {
  if (motion_.stop()) position_valid_ = false;
  manual_run_ = false;
}

void TiltProtocol::emit_calibrated(int duty, const char* direction) const {
  out_.printf(
      "{\"event\":\"calibrated\",\"duty\":%d,\"direction\":\"%s\",\"position_valid\":%s",
      duty, direction, position_valid_ ? "true" : "false");
  if (position_valid_) out_.printf(",\"position_mm\":%.2f", position_mm_);
  out_.println("}");
}

void TiltProtocol::emit_rejected(const char* reason) {
  out_.printf("{\"event\":\"rejected\",\"reason\":\"%s\",\"position_valid\":%s",
                reason, position_valid_ ? "true" : "false");
  if (position_valid_) out_.printf(",\"position_mm\":%.2f", position_mm_);
  out_.println("}");
}

void TiltProtocol::emit_stopped(const char* reason) {
  out_.printf("{\"event\":\"stopped\",\"reason\":\"%s\",\"position_valid\":%s",
                reason, position_valid_ ? "true" : "false");
  if (position_valid_) out_.printf(",\"position_mm\":%.2f", position_mm_);
  out_.println("}");
}

void TiltProtocol::emit_at_target() const {
  out_.printf("{\"event\":\"at_target\",\"position_valid\":%s",
                position_valid_ ? "true" : "false");
  if (position_valid_) out_.printf(",\"position_mm\":%.2f", position_mm_);
  out_.println("}");
}

const char* TiltProtocol::direction_name(TiltPolicy::Direction direction) const {
  return direction == TiltPolicy::Direction::Up ? "UP" : "DOWN";
}

float TiltProtocol::speed_for(int duty, TiltPolicy::Direction direction) const {
  return speeds_[duty][direction == TiltPolicy::Direction::Up ? UP_INDEX : DOWN_INDEX];
}
