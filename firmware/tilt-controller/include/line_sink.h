#pragma once

#include <Arduino.h>

#include "config.h"

// TiltProtocol은 한 이벤트를 printf 여러 번으로 나눠 쓰고 마지막에 println으로
// 닫는다. 그 조각을 모아 완성된 줄 하나로 만들어 두면, 그 줄을 시리얼로 보낼지
// MQTT로 보낼지는 바깥에서 정할 수 있다.
class LineSink {
 public:
  using LineHandler = void (*)(const char* line);

  void set_handler(LineHandler handler) { handler_ = handler; }

  void printf(const char* format, ...) {
    va_list args;
    va_start(args, format);
    const int remaining = static_cast<int>(sizeof(buffer_) - length_ - 1);
    if (remaining > 0) {
      const int written = vsnprintf(buffer_ + length_, remaining, format, args);
      if (written > 0) {
        length_ += (written < remaining) ? written : remaining - 1;
      }
    }
    va_end(args);
  }

  // 줄을 닫는다. 인자는 마지막 조각이다.
  void println(const char* tail) {
    printf("%s", tail);
    buffer_[length_] = '\0';
    if (handler_ != nullptr && length_ > 0) handler_(buffer_);
    length_ = 0;
  }

 private:
  LineHandler handler_ = nullptr;
  char buffer_[TiltConfig::EVENT_LINE_MAX_BYTES]{};
  size_t length_ = 0;
};
