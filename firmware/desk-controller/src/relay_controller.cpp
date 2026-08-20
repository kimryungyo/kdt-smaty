#include "relay_controller.h"

#include <esp_intr_alloc.h>
#include <soc/gpio_struct.h>

#include "config.h"

using namespace SmartDeskConfig;

RelayController* RelayController::instance_ = nullptr;

bool RelayController::begin() {
  // OUTPUT 전환 전에 OFF level을 latch해 boot glitch 가능성을 줄인다.
  digitalWrite(UP_RELAY_PIN, RELAY_ACTIVE_LOW ? HIGH : LOW);
  digitalWrite(DOWN_RELAY_PIN, RELAY_ACTIVE_LOW ? HIGH : LOW);
  pinMode(UP_RELAY_PIN, OUTPUT);
  pinMode(DOWN_RELAY_PIN, OUTPUT);
  forceOffMain();

  instance_ = this;
  timer_ = timerBegin(RELAY_TIMER_INDEX, 80, true);  // 80MHz / 80 = 1us tick
  if (timer_ == nullptr) return false;
  timerStop(timer_);
  timerAttachInterruptFlag(
      timer_,
      &RelayController::onTimerStatic,
      false,
      ESP_INTR_FLAG_IRAM);
  timerAlarmDisable(timer_);
  return true;
}

RelayStartResult RelayController::start(
    RelayDirection requested,
    uint16_t holdMs) {
  disarmTimer();

  RelayDirection previous;
  portENTER_CRITICAL(&stateMux_);
  previous = direction_;
  portEXIT_CRITICAL(&stateMux_);

  if (previous == requested) {
    armTimer(holdMs);
    return RelayStartResult::Extended;
  }

  forceOffMain();
  if (previous != RelayDirection::Stop &&
      requested != RelayDirection::Stop &&
      previous != requested) {
    delay(BREAK_BEFORE_MAKE_MS);
  }

  portENTER_CRITICAL(&stateMux_);
  if (requested == RelayDirection::Up) writeRelay(UP_RELAY_PIN, true);
  if (requested == RelayDirection::Down) writeRelay(DOWN_RELAY_PIN, true);
  direction_ = requested;
  timeoutPending_ = false;
  portEXIT_CRITICAL(&stateMux_);
  armTimer(holdMs);
  return previous == RelayDirection::Stop ? RelayStartResult::Started
                                          : RelayStartResult::Switched;
}

bool RelayController::stop() {
  disarmTimer();
  portENTER_CRITICAL(&stateMux_);
  const bool wasMoving = direction_ != RelayDirection::Stop;
  // 명시적 STOP은 이미 멈춰 있어도 두 GPIO에 OFF를 다시 기록한다.
  forceOffMain();
  timeoutPending_ = false;
  portEXIT_CRITICAL(&stateMux_);
  return wasMoving;
}

RelayDirection RelayController::direction() const {
  portENTER_CRITICAL(&stateMux_);
  const RelayDirection current = direction_;
  portEXIT_CRITICAL(&stateMux_);
  return current;
}

const char* RelayController::directionName() const {
  switch (direction()) {
    case RelayDirection::Up:
      return "UP";
    case RelayDirection::Down:
      return "DOWN";
    default:
      return "STOP";
  }
}

bool RelayController::takeTimeoutEvent() {
  portENTER_CRITICAL(&stateMux_);
  const bool pending = timeoutPending_;
  timeoutPending_ = false;
  portEXIT_CRITICAL(&stateMux_);
  return pending;
}

void RelayController::armTimer(uint16_t holdMs) {
  if (timer_ == nullptr) return;
  timerStop(timer_);
  timerWrite(timer_, 0);
  timerAlarmWrite(timer_, static_cast<uint64_t>(holdMs) * 1000ULL, false);
  timerAlarmEnable(timer_);
  timerStart(timer_);
}

void RelayController::disarmTimer() {
  if (timer_ == nullptr) return;
  timerAlarmDisable(timer_);
  timerStop(timer_);
}

void RelayController::forceOffMain() {
  // OFF level은 모듈 극성에 따라 다르다. active-low 모듈에 LOW를 쓰면 끄는 게
  // 아니라 켜진다.
  if (RELAY_ACTIVE_LOW) {
    GPIO.out_w1ts = RELAY_MASK;
  } else {
    GPIO.out_w1tc = RELAY_MASK;
  }
  direction_ = RelayDirection::Stop;
}

void RelayController::writeRelay(uint8_t pin, bool enabled) {
  const bool level = RELAY_ACTIVE_LOW ? !enabled : enabled;
  if (level) {
    GPIO.out_w1ts = 1UL << pin;
  } else {
    GPIO.out_w1tc = 1UL << pin;
  }
}

void IRAM_ATTR RelayController::onTimerStatic() {
  if (instance_ != nullptr) instance_->onTimer();
}

void IRAM_ATTR RelayController::onTimer() {
  portENTER_CRITICAL_ISR(&stateMux_);
  forceOffFromIsr();
  timeoutPending_ = true;
  portEXIT_CRITICAL_ISR(&stateMux_);
}

void IRAM_ATTR RelayController::forceOffFromIsr() {
  // register write로 즉시 OFF한다. OFF level은 모듈 극성을 따라야 한다.
  // active-low 모듈에 LOW를 쓰면 최후 차단이 오히려 릴레이를 켠다.
  if (RELAY_ACTIVE_LOW) {
    GPIO.out_w1ts = RELAY_MASK;
  } else {
    GPIO.out_w1tc = RELAY_MASK;
  }
  direction_ = RelayDirection::Stop;
}
