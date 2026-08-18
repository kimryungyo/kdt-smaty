#include "control_handler.h"

#include <math.h>
#include <string.h>

#include "config.h"
#include "policy.h"
#include "protocol.h"

using namespace SmartDeskConfig;

ControlHandler::ControlHandler(PubSubClient& mqtt, RelayController& relay)
    : mqtt_(mqtt), relay_(relay) {}

void ControlHandler::beginSession(uint32_t now) {
  relay_.stop();
  hasBaseline_ = false;
  hasDistinctHeight_ = false;
  armed_ = false;
  baselineObservedAt_[0] = '\0';
  lastObservedAt_[0] = '\0';
  lastHeightCm_ = NAN;
  sessionStartedAt_ = now;
  lastDistinctHeightAt_ = 0;
}

void ControlHandler::invalidateSession() {
  relay_.stop();
  hasBaseline_ = false;
  hasDistinctHeight_ = false;
  armed_ = false;
  baselineObservedAt_[0] = '\0';
  lastObservedAt_[0] = '\0';
  lastHeightCm_ = NAN;
  lastDistinctHeightAt_ = 0;
}

void ControlHandler::handleMessage(
    char* topic,
    byte* payload,
    unsigned int length) {
  if (strcmp(topic, MQTT_HEIGHT_TOPIC) == 0) {
    handleHeight(payload, length);
  } else if (strcmp(topic, MQTT_CONTROL_TOPIC) == 0) {
    handleControl(payload, length);
  }
}

void ControlHandler::handleHeight(const byte* payload, unsigned int length) {
  SmartDeskProtocol::Height observation;
  const SmartDeskProtocol::Error parseError = SmartDeskProtocol::parseHeight(
      payload,
      length,
      MAX_HEIGHT_PAYLOAD_BYTES,
      MAX_OBSERVED_AT_BYTES,
      observation);
  if (parseError != SmartDeskProtocol::Error::None) {
    const bool wasMoving = relay_.stop();
    invalidateSession();
    if ((wasMoving || mqtt_.connected()) &&
        !publishStatus(
            wasMoving ? "stopped" : "rejected",
            "height_invalid",
            "높이 메시지가 유효하지 않아 제어를 중단했습니다.")) {
      failClosed();
    }
    return;
  }

  const float height = observation.heightCm;
  const char* observedText = observation.observedAt;
  const uint32_t now = millis();
  if (!hasBaseline_) {
    copyObservedAt(observedText, baselineObservedAt_, sizeof(baselineObservedAt_));
    copyObservedAt(observedText, lastObservedAt_, sizeof(lastObservedAt_));
    lastHeightCm_ = height;
    hasBaseline_ = true;
    return;
  }
  if (strcmp(observedText, lastObservedAt_) == 0) {
    return;  // QoS 1 duplicate는 lease를 갱신하지 않는다.
  }

  copyObservedAt(observedText, lastObservedAt_, sizeof(lastObservedAt_));
  lastHeightCm_ = height;
  lastDistinctHeightAt_ = now;
  hasDistinctHeight_ = true;
  updateArming(now);

  if (relay_.direction() == RelayDirection::Up &&
      height >= MAX_CONTROL_HEIGHT_CM) {
    stopAndPublish("upper_limit", "제어 상한에 도달해 상승을 정지했습니다.");
  } else if (relay_.direction() == RelayDirection::Down &&
             height <= MIN_CONTROL_HEIGHT_CM) {
    stopAndPublish("lower_limit", "제어 하한에 도달해 하강을 정지했습니다.");
  }
}

void ControlHandler::handleControl(const byte* payload, unsigned int length) {
  SmartDeskProtocol::Command command;
  const SmartDeskProtocol::Error parseError = SmartDeskProtocol::parseControl(
      payload, length, MAX_CONTROL_PAYLOAD_BYTES, command);
  if (parseError != SmartDeskProtocol::Error::None) {
    const char* code = "invalid_command";
    const char* detail = "지원하지 않는 릴레이 명령입니다.";
    if (parseError == SmartDeskProtocol::Error::PayloadSize) {
      code = "invalid_payload_size";
      detail = "명령 크기가 올바르지 않습니다.";
    } else if (parseError == SmartDeskProtocol::Error::InvalidJson) {
      code = "invalid_json";
      detail = "JSON 명령 형식이 올바르지 않습니다.";
    } else if (parseError == SmartDeskProtocol::Error::UntrustedSource) {
      code = "untrusted_source";
      detail = "Desk 서비스의 명령만 실행할 수 있습니다.";
    } else if (parseError == SmartDeskProtocol::Error::InvalidHold) {
      code = "hold_ms_out_of_range";
      detail = "UP/DOWN hold_ms는 50~500 정수이고 WAKE는 정확히 400이어야 합니다.";
    } else if (parseError == SmartDeskProtocol::Error::InvalidHeight) {
      code = "invalid_height";
      detail = "WAKE basis_height_cm는 유효한 실제 높이여야 합니다.";
    }
    reject(code, detail);
    return;
  }

  if (command.type == SmartDeskProtocol::CommandType::Stop) {
    relay_.stop();
    if (!publishStatus("stopped", "command", "명시적 정지 명령을 실행했습니다.")) {
      failClosed();
    }
    return;
  }

  if (command.type == SmartDeskProtocol::CommandType::WakeUp ||
      command.type == SmartDeskProtocol::CommandType::WakeDown) {
    const RelayDirection requested =
        command.type == SmartDeskProtocol::CommandType::WakeUp
            ? RelayDirection::Up
            : RelayDirection::Down;
    // WAKE는 현재 MQTT height lease가 없어도, 서버가 전달한 마지막 실제 높이의
    // 안전 경계 안에서만 400ms pulse를 허용한다. 이후 fresh height/arming 없이는
    // 일반 UP/DOWN을 계속 거부한다.
    if (!SmartDeskPolicy::directionAllowed(
            command.basisHeightCm, requested == RelayDirection::Up)) {
      reject(
          requested == RelayDirection::Up ? "upper_limit" : "lower_limit",
          "WAKE 기준 높이에서 해당 방향 pulse를 허용할 수 없습니다.");
      return;
    }
    const RelayStartResult result = relay_.start(requested, command.holdMs);
    const char* code = result == RelayStartResult::Extended
                           ? "wake_extended"
                           : "wake_started";
    if (!publishStatus(
            "moving", code, "높이 표시기를 깨우는 400ms pulse를 적용했습니다.")) {
      failClosed();
    }
    return;
  }

  const RelayDirection requested =
      command.type == SmartDeskProtocol::CommandType::Up
          ? RelayDirection::Up
          : RelayDirection::Down;
  if (!isReady(millis())) {
    reject("height_not_ready", "현재 세션의 신선한 높이가 준비되지 않았습니다.");
    return;
  }
  if (!boundaryAllows(requested)) {
    reject(
        requested == RelayDirection::Up ? "upper_limit" : "lower_limit",
        requested == RelayDirection::Up
            ? "제어 상한에서는 상승할 수 없습니다."
            : "제어 하한에서는 하강할 수 없습니다.");
    return;
  }

  const RelayStartResult result =
      relay_.start(requested, command.holdMs);
  const char* code = "command_started";
  if (result == RelayStartResult::Extended) code = "deadline_extended";
  if (result == RelayStartResult::Switched) code = "direction_switched";
  if (!publishStatus("moving", code, "릴레이 이동 deadline을 적용했습니다.")) {
    failClosed();
  }
}

void ControlHandler::tick(uint32_t now) {
  updateArming(now);
  if (armed_ && elapsed(now, lastDistinctHeightAt_, HEIGHT_LEASE_MS)) {
    const bool wasMoving = relay_.stop();
    armed_ = false;
    hasDistinctHeight_ = false;
    if (wasMoving &&
        !publishStatus(
            "stopped",
            "height_stale",
            "높이 lease가 만료되어 릴레이를 정지했습니다.")) {
      failClosed();
    }
  }
}

bool ControlHandler::publishStatus(
    const char* event,
    const char* code,
    const char* detail) {
  if (!mqtt_.connected()) return false;
  JsonDocument message;
  message["event"] = event;
  message["state"] = relay_.directionName();
  message["firmware"] = FIRMWARE_VERSION;
  message["code"] = code;
  message["detail"] = detail;
  char payload[300];
  const size_t length = serializeJson(message, payload, sizeof(payload));
  if (length == 0 || length >= sizeof(payload)) return false;
  return mqtt_.publish(
      MQTT_STATUS_TOPIC,
      reinterpret_cast<const uint8_t*>(payload),
      length,
      false);
}

bool ControlHandler::publishHeartbeat(uint32_t now) {
  return publishStatus(
      "heartbeat",
      isReady(now) ? "ready" : "height_waiting",
      isReady(now) ? "릴레이 제어기가 준비되었습니다."
                   : "새 높이 측정을 기다리고 있습니다.");
}

void ControlHandler::handleTimeoutEvent() {
  const bool ready = isReady(millis());
  if (mqtt_.connected() &&
      !publishStatus(
          "stopped",
          ready ? "ready" : "timeout",
          ready
              ? "이동 deadline이 끝났지만 신선한 높이 lease는 준비되었습니다."
              : "이동 deadline이 끝나 hardware timer가 정지했습니다.")) {
    failClosed();
  }
}

void ControlHandler::failClosed() {
  relay_.stop();
  invalidateSession();
  if (mqtt_.connected()) mqtt_.disconnect();
}

bool ControlHandler::isReady(uint32_t now) const {
  return armed_ && hasDistinctHeight_ && isfinite(lastHeightCm_) &&
         !elapsed(now, lastDistinctHeightAt_, HEIGHT_LEASE_MS);
}

void ControlHandler::reject(const char* code, const char* detail) {
  relay_.stop();
  if (!publishStatus("rejected", code, detail)) failClosed();
}

void ControlHandler::stopAndPublish(const char* code, const char* detail) {
  relay_.stop();
  if (!publishStatus("stopped", code, detail)) failClosed();
}

void ControlHandler::updateArming(uint32_t now) {
  if (armed_ || !hasBaseline_ || !hasDistinctHeight_) return;
  if (!elapsed(now, sessionStartedAt_, CONTROL_ARM_DELAY_MS)) return;
  if (elapsed(now, lastDistinctHeightAt_, HEIGHT_LEASE_MS)) return;
  armed_ = true;
  if (mqtt_.connected() &&
      !publishStatus("online", "ready", "릴레이 제어기가 준비되었습니다.")) {
    failClosed();
  }
}

bool ControlHandler::boundaryAllows(RelayDirection direction) const {
  if (direction == RelayDirection::Up) {
    return SmartDeskPolicy::directionAllowed(lastHeightCm_, true);
  }
  if (direction == RelayDirection::Down) {
    return SmartDeskPolicy::directionAllowed(lastHeightCm_, false);
  }
  return false;
}

bool ControlHandler::copyObservedAt(
    const char* source,
    char* destination,
    size_t size) {
  if (source == nullptr || destination == nullptr || size == 0) return false;
  const size_t length = strlen(source);
  if (length == 0 || length >= size) return false;
  memcpy(destination, source, length + 1);
  return true;
}
