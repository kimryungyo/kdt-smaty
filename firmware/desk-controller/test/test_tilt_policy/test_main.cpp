#include <unity.h>

#include "tilt_policy.h"

void test_invalid_position_or_speed_is_rejected() {
  const auto invalid_position = TiltPolicy::make_motion_plan(
      0.0F, false, 10.0F, 10.0F, 10.0F);
  TEST_ASSERT_EQUAL(TiltPolicy::Direction::Stop, invalid_position.direction);

  const auto invalid_speed = TiltPolicy::make_motion_plan(
      0.0F, true, 10.0F, 0.0F, 10.0F);
  TEST_ASSERT_EQUAL(TiltPolicy::Direction::Stop, invalid_speed.direction);
}

void test_target_requires_physical_range() {
  TEST_ASSERT_FALSE(TiltPolicy::position_allowed(-0.1F));
  TEST_ASSERT_FALSE(TiltPolicy::position_allowed(TiltPolicy::MAX_POSITION_MM + 0.1F));
  TEST_ASSERT_TRUE(TiltPolicy::position_allowed(0.0F));
}

void test_motion_direction_and_duration_are_bounded() {
  const auto up = TiltPolicy::make_motion_plan(0.0F, true, 10.0F, 10.0F, 8.0F);
  TEST_ASSERT_EQUAL(TiltPolicy::Direction::Up, up.direction);
  TEST_ASSERT_GREATER_THAN(0, up.duration_ms);
  TEST_ASSERT_LESS_OR_EQUAL(TiltPolicy::ABSOLUTE_MAX_MOTION_MS, up.duration_ms);

  const auto down = TiltPolicy::make_motion_plan(10.0F, true, 0.0F, 10.0F, 8.0F);
  TEST_ASSERT_EQUAL(TiltPolicy::Direction::Down, down.direction);
}

void test_same_target_does_not_move() {
  const auto plan = TiltPolicy::make_motion_plan(10.0F, true, 10.0F, 10.0F, 10.0F);
  TEST_ASSERT_TRUE(plan.at_target);
  TEST_ASSERT_EQUAL(TiltPolicy::Direction::Stop, plan.direction);
}

void test_excessive_motion_is_rejected() {
  const auto plan = TiltPolicy::make_motion_plan(
      0.0F, true, TiltPolicy::MAX_POSITION_MM, 0.001F, 0.001F);
  TEST_ASSERT_EQUAL(TiltPolicy::Direction::Stop, plan.direction);
  TEST_ASSERT_FALSE(plan.at_target);
}

int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_invalid_position_or_speed_is_rejected);
  RUN_TEST(test_target_requires_physical_range);
  RUN_TEST(test_motion_direction_and_duration_are_bounded);
  RUN_TEST(test_same_target_does_not_move);
  RUN_TEST(test_excessive_motion_is_rejected);
  return UNITY_END();
}
