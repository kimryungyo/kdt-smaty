#include <unity.h>

#include <cstdint>
#include <limits>

#include "policy.h"
#include "protocol.h"

void setUp() {}
void tearDown() {}

void test_hold_boundaries() {
  TEST_ASSERT_FALSE(SmartDeskPolicy::holdAllowed(49));
  TEST_ASSERT_TRUE(SmartDeskPolicy::holdAllowed(50));
  TEST_ASSERT_TRUE(SmartDeskPolicy::holdAllowed(500));
  TEST_ASSERT_FALSE(SmartDeskPolicy::holdAllowed(501));
}

void test_direction_boundaries_and_recovery() {
  TEST_ASSERT_TRUE(SmartDeskPolicy::directionAllowed(73.0F, true));
  TEST_ASSERT_FALSE(SmartDeskPolicy::directionAllowed(75.0F, false));
  TEST_ASSERT_TRUE(SmartDeskPolicy::directionAllowed(75.1F, false));
  TEST_ASSERT_TRUE(SmartDeskPolicy::directionAllowed(114.9F, true));
  TEST_ASSERT_FALSE(SmartDeskPolicy::directionAllowed(115.0F, true));
  TEST_ASSERT_TRUE(SmartDeskPolicy::directionAllowed(118.0F, false));
  TEST_ASSERT_FALSE(SmartDeskPolicy::directionAllowed(72.9F, true));
  TEST_ASSERT_FALSE(SmartDeskPolicy::directionAllowed(118.1F, false));
}

void test_non_finite_height_is_rejected() {
  TEST_ASSERT_FALSE(SmartDeskPolicy::directionAllowed(
      std::numeric_limits<float>::infinity(), true));
  TEST_ASSERT_FALSE(SmartDeskPolicy::directionAllowed(
      std::numeric_limits<float>::quiet_NaN(), false));
}

void test_elapsed_handles_uint32_wrap() {
  constexpr uint32_t since = UINT32_MAX - 10;
  TEST_ASSERT_FALSE(SmartDeskPolicy::elapsed(3, since, 15));
  TEST_ASSERT_TRUE(SmartDeskPolicy::elapsed(4, since, 15));
}

SmartDeskProtocol::Error parseControl(
    const char* payload,
    SmartDeskProtocol::Command& command) {
  return SmartDeskProtocol::parseControl(
      reinterpret_cast<const uint8_t*>(payload),
      std::strlen(payload),
      160,
      command);
}

void test_control_protocol_is_strict() {
  SmartDeskProtocol::Command command;
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(SmartDeskProtocol::Error::None),
      static_cast<int>(parseControl(
          "{\"command\":\"UP\",\"source\":\"desk_service\",\"hold_ms\":50}",
          command)));
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(SmartDeskProtocol::CommandType::Up),
      static_cast<int>(command.type));
  TEST_ASSERT_EQUAL_UINT16(50, command.holdMs);

  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(SmartDeskProtocol::Error::None),
      static_cast<int>(parseControl("{\"command\":\"STOP\"}", command)));
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(SmartDeskProtocol::Error::ExtraFields),
      static_cast<int>(parseControl(
          "{\"command\":\"STOP\",\"source\":\"desk_service\"}", command)));
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(SmartDeskProtocol::Error::ExtraFields),
      static_cast<int>(parseControl(
          "{\"command\":\"UP\",\"source\":\"desk_service\",\"hold_ms\":50,\"x\":1}",
          command)));
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(SmartDeskProtocol::Error::InvalidHold),
      static_cast<int>(parseControl(
          "{\"command\":\"UP\",\"source\":\"desk_service\",\"hold_ms\":true}",
          command)));
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(SmartDeskProtocol::Error::InvalidHold),
      static_cast<int>(parseControl(
          "{\"command\":\"UP\",\"source\":\"desk_service\",\"hold_ms\":50.0}",
          command)));

  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(SmartDeskProtocol::Error::None),
      static_cast<int>(parseControl(
          "{\"command\":\"WAKE\",\"source\":\"desk_service\","
          "\"direction\":\"DOWN\",\"hold_ms\":400,\"basis_height_cm\":80.5}",
          command)));
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(SmartDeskProtocol::CommandType::WakeDown),
      static_cast<int>(command.type));
  TEST_ASSERT_EQUAL_UINT16(400, command.holdMs);
  TEST_ASSERT_FLOAT_WITHIN(0.001F, 80.5F, command.basisHeightCm);
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(SmartDeskProtocol::Error::InvalidHold),
      static_cast<int>(parseControl(
          "{\"command\":\"WAKE\",\"source\":\"desk_service\","
          "\"direction\":\"DOWN\",\"hold_ms\":100,\"basis_height_cm\":80}",
          command)));
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(SmartDeskProtocol::Error::InvalidHeight),
      static_cast<int>(parseControl(
          "{\"command\":\"WAKE\",\"source\":\"desk_service\","
          "\"direction\":\"DOWN\",\"hold_ms\":400,\"basis_height_cm\":118.1}",
          command)));
}

void test_height_protocol_schema_and_range() {
  SmartDeskProtocol::Height height;
  const char* valid =
      "{\"schema\":\"smartdesk.height.v1\","
      "\"observed_at\":\"2026-08-06T10:00:00.123456Z\",\"height_cm\":80.2}";
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(SmartDeskProtocol::Error::None),
      static_cast<int>(SmartDeskProtocol::parseHeight(
          reinterpret_cast<const uint8_t*>(valid),
          std::strlen(valid),
          192,
          40,
          height)));
  TEST_ASSERT_FLOAT_WITHIN(0.001F, 80.2F, height.heightCm);

  const char* outside =
      "{\"schema\":\"smartdesk.height.v1\","
      "\"observed_at\":\"2026-08-06T10:00:01Z\",\"height_cm\":118.1}";
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(SmartDeskProtocol::Error::InvalidHeight),
      static_cast<int>(SmartDeskProtocol::parseHeight(
          reinterpret_cast<const uint8_t*>(outside),
          std::strlen(outside),
          192,
          40,
          height)));

  const char* extra =
      "{\"schema\":\"smartdesk.height.v1\","
      "\"observed_at\":\"2026-08-06T10:00:01Z\",\"height_cm\":80,\"x\":1}";
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(SmartDeskProtocol::Error::InvalidHeight),
      static_cast<int>(SmartDeskProtocol::parseHeight(
          reinterpret_cast<const uint8_t*>(extra),
          std::strlen(extra),
          192,
          40,
          height)));
}

int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_hold_boundaries);
  RUN_TEST(test_direction_boundaries_and_recovery);
  RUN_TEST(test_non_finite_height_is_rejected);
  RUN_TEST(test_elapsed_handles_uint32_wrap);
  RUN_TEST(test_control_protocol_is_strict);
  RUN_TEST(test_height_protocol_schema_and_range);
  return UNITY_END();
}
