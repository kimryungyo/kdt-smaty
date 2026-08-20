#pragma once

#include <ArduinoJson.h>

#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstring>

#include "policy.h"

namespace SmartDeskProtocol {
enum class Error : uint8_t {
  None,
  PayloadSize,
  InvalidJson,
  InvalidCommand,
  ExtraFields,
  UntrustedSource,
  InvalidHold,
  InvalidHeight,
};

enum class CommandType : uint8_t { Stop, Up, Down, WakeUp, WakeDown };

struct Command {
  CommandType type = CommandType::Stop;
  uint16_t holdMs = 0;
  float basisHeightCm = NAN;
};

struct Height {
  float heightCm = NAN;
  char observedAt[48] = {};
};

inline bool hasExactKeys(
    ArduinoJson::JsonObjectConst object,
    const char* const* keys,
    size_t count) {
  if (object.size() != count) return false;
  for (ArduinoJson::JsonPairConst pair : object) {
    bool allowed = false;
    for (size_t index = 0; index < count; ++index) {
      if (std::strcmp(pair.key().c_str(), keys[index]) == 0) {
        allowed = true;
        break;
      }
    }
    if (!allowed) return false;
  }
  return true;
}

inline Error parseControl(
    const uint8_t* payload,
    size_t length,
    size_t maximumLength,
    Command& output) {
  if (payload == nullptr || length == 0 || length > maximumLength) {
    return Error::PayloadSize;
  }
  ArduinoJson::JsonDocument document;
  if (ArduinoJson::deserializeJson(document, payload, length) ||
      !document.is<ArduinoJson::JsonObject>()) {
    return Error::InvalidJson;
  }
  const ArduinoJson::JsonObjectConst object =
      document.as<ArduinoJson::JsonObjectConst>();
  const ArduinoJson::JsonVariantConst commandValue = object["command"];
  if (!commandValue.is<const char*>()) return Error::InvalidCommand;
  const char* command = commandValue.as<const char*>();

  if (std::strcmp(command, "STOP") == 0) {
    constexpr const char* KEYS[] = {"command"};
    if (!hasExactKeys(object, KEYS, 1)) return Error::ExtraFields;
    output.type = CommandType::Stop;
    output.holdMs = 0;
    return Error::None;
  }

  if (std::strcmp(command, "WAKE") == 0) {
    constexpr const char* KEYS[] = {
        "command", "source", "direction", "hold_ms", "basis_height_cm"};
    if (!hasExactKeys(object, KEYS, 5)) return Error::ExtraFields;
    const ArduinoJson::JsonVariantConst sourceValue = object["source"];
    const ArduinoJson::JsonVariantConst directionValue = object["direction"];
    const ArduinoJson::JsonVariantConst holdValue = object["hold_ms"];
    const ArduinoJson::JsonVariantConst basisValue = object["basis_height_cm"];
    if (!sourceValue.is<const char*>() ||
        std::strcmp(sourceValue.as<const char*>(), "desk_service") != 0) {
      return Error::UntrustedSource;
    }
    if (holdValue.is<bool>() || !holdValue.is<long>() ||
        holdValue.as<long>() != 400) {
      return Error::InvalidHold;
    }
    if (!basisValue.is<float>() && !basisValue.is<double>() &&
        !basisValue.is<long>() && !basisValue.is<unsigned long>()) {
      return Error::InvalidHeight;
    }
    const float basisHeight = basisValue.as<float>();
    if (!SmartDeskPolicy::measuredHeightAllowed(basisHeight)) {
      return Error::InvalidHeight;
    }
    if (!directionValue.is<const char*>()) return Error::InvalidCommand;
    const char* direction = directionValue.as<const char*>();
    if (std::strcmp(direction, "UP") == 0) {
      output.type = CommandType::WakeUp;
    } else if (std::strcmp(direction, "DOWN") == 0) {
      output.type = CommandType::WakeDown;
    } else {
      return Error::InvalidCommand;
    }
    output.holdMs = 400;
    output.basisHeightCm = basisHeight;
    return Error::None;
  }

  CommandType type;
  if (std::strcmp(command, "UP") == 0) {
    type = CommandType::Up;
  } else if (std::strcmp(command, "DOWN") == 0) {
    type = CommandType::Down;
  } else {
    return Error::InvalidCommand;
  }
  constexpr const char* KEYS[] = {"command", "source", "hold_ms"};
  if (!hasExactKeys(object, KEYS, 3)) return Error::ExtraFields;

  const ArduinoJson::JsonVariantConst sourceValue = object["source"];
  if (!sourceValue.is<const char*>() ||
      std::strcmp(sourceValue.as<const char*>(), "desk_service") != 0) {
    return Error::UntrustedSource;
  }
  const ArduinoJson::JsonVariantConst holdValue = object["hold_ms"];
  if (holdValue.is<bool>() || !holdValue.is<long>()) return Error::InvalidHold;
  const long holdMs = holdValue.as<long>();
  if (!SmartDeskPolicy::holdAllowed(holdMs)) return Error::InvalidHold;
  output.type = type;
  output.holdMs = static_cast<uint16_t>(holdMs);
  output.basisHeightCm = NAN;
  return Error::None;
}

inline Error parseHeight(
    const uint8_t* payload,
    size_t length,
    size_t maximumLength,
    size_t maximumObservedAtLength,
    Height& output) {
  if (payload == nullptr || length == 0 || length > maximumLength) {
    return Error::PayloadSize;
  }
  ArduinoJson::JsonDocument document;
  if (ArduinoJson::deserializeJson(document, payload, length) ||
      !document.is<ArduinoJson::JsonObject>()) {
    return Error::InvalidJson;
  }
  const ArduinoJson::JsonObjectConst object =
      document.as<ArduinoJson::JsonObjectConst>();
  constexpr const char* KEYS[] = {"schema", "observed_at", "height_cm"};
  if (!hasExactKeys(object, KEYS, 3)) return Error::InvalidHeight;

  const ArduinoJson::JsonVariantConst schemaValue = object["schema"];
  const ArduinoJson::JsonVariantConst observedValue = object["observed_at"];
  const ArduinoJson::JsonVariantConst heightValue = object["height_cm"];
  if (!schemaValue.is<const char*>() ||
      std::strcmp(schemaValue.as<const char*>(), "smartdesk.height.v1") != 0 ||
      !observedValue.is<const char*>()) {
    return Error::InvalidHeight;
  }
  const char* observedAt = observedValue.as<const char*>();
  const size_t observedLength = std::strlen(observedAt);
  if (observedLength == 0 || observedLength > maximumObservedAtLength ||
      observedLength >= sizeof(output.observedAt) || heightValue.is<bool>() ||
      !(heightValue.is<float>() || heightValue.is<double>() ||
        heightValue.is<long>() || heightValue.is<unsigned long>())) {
    return Error::InvalidHeight;
  }
  const float height = heightValue.as<float>();
  if (!SmartDeskPolicy::measuredHeightAllowed(height)) {
    return Error::InvalidHeight;
  }
  std::memcpy(output.observedAt, observedAt, observedLength + 1);
  output.heightCm = height;
  return Error::None;
}
}  // namespace SmartDeskProtocol
