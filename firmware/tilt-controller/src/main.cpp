#include <Arduino.h>

#include "config.h"
#include "motion_controller.h"
#include "tilt_protocol.h"

namespace {
MotionController motion;
TiltProtocol protocol(motion);
char line_buffer[TiltConfig::SERIAL_LINE_MAX_BYTES + 1]{};
size_t line_length = 0;
uint32_t last_status_at = 0;

void consume_serial() {
  while (Serial.available() > 0) {
    const char character = static_cast<char>(Serial.read());
    if (character == '\r') continue;
    if (character == '\n') {
      line_buffer[line_length] = '\0';
      protocol.handle_line(line_buffer);
      line_length = 0;
      continue;
    }
    if (line_length >= TiltConfig::SERIAL_LINE_MAX_BYTES) {
      line_length = 0;
      protocol.emergency_stop("line_too_long");
      continue;
    }
    line_buffer[line_length++] = character;
  }
}
}  // namespace

void setup() {
  Serial.begin(115200);
  if (!motion.begin()) {
    Serial.println("{\"event\":\"fault\",\"reason\":\"timer_init_failed\",\"position_valid\":false}");
    return;
  }
  protocol.begin();
  last_status_at = millis();
}

void loop() {
  consume_serial();
  protocol.handle_timer_event();
  const uint32_t now = millis();
  if (TiltConfig::elapsed(now, last_status_at, TiltConfig::STATUS_HEARTBEAT_MS)) {
    protocol.publish_status();
    last_status_at = now;
  }
}
