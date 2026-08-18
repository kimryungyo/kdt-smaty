#pragma once

#include <cmath>
#include <cstdint>

namespace TiltPolicy {

enum class Direction : uint8_t { Stop, Up, Down };

struct MotionPlan {
  Direction direction = Direction::Stop;
  uint32_t duration_ms = 0;
  bool at_target = false;
};

constexpr float MIN_POSITION_MM = 0.0F;
// tilt_project.zip의 실제 HW-039 액추에이터 stroke다. server가 보내는 목표와
// 별개로 firmware가 지키는 최후 물리 범위다.
constexpr float MAX_POSITION_MM = 220.0F;
constexpr uint32_t MOTION_SETTLE_MARGIN_MS = 150;
constexpr uint32_t ABSOLUTE_MAX_MOTION_MS = 16000;

inline bool finite(float value) { return std::isfinite(value); }

inline bool position_allowed(float position_mm) {
  return finite(position_mm) && position_mm >= MIN_POSITION_MM &&
         position_mm <= MAX_POSITION_MM;
}

inline MotionPlan make_motion_plan(float current_mm, bool position_valid,
                                   float target_mm, float up_speed_mm_s,
                                   float down_speed_mm_s) {
  if (!position_valid || !position_allowed(current_mm) ||
      !position_allowed(target_mm)) {
    return {};
  }
  const float distance = target_mm - current_mm;
  if (std::fabs(distance) < 0.01F) {
    MotionPlan plan;
    plan.at_target = true;
    return plan;
  }
  const Direction direction = distance > 0 ? Direction::Up : Direction::Down;
  const float speed = direction == Direction::Up ? up_speed_mm_s : down_speed_mm_s;
  if (!finite(speed) || speed <= 0) return {};

  const float duration = (std::fabs(distance) / speed) * 1000.0F +
                         static_cast<float>(MOTION_SETTLE_MARGIN_MS);
  if (!finite(duration) || duration <= 0 || duration > ABSOLUTE_MAX_MOTION_MS) {
    return {};
  }
  MotionPlan plan;
  plan.direction = direction;
  plan.duration_ms = static_cast<uint32_t>(std::ceil(duration));
  return plan;
}

}  // namespace TiltPolicy
