#include "motion_controller.h"

#include <esp_intr_alloc.h>
#include <soc/gpio_struct.h>

#include "config.h"

using namespace TiltConfig;

MotionController* MotionController::instance_ = nullptr;

bool MotionController::begin() {
  digitalWrite(R_ENABLE_PIN, LOW);
  digitalWrite(L_ENABLE_PIN, LOW);
  pinMode(R_ENABLE_PIN, OUTPUT);
  pinMode(L_ENABLE_PIN, OUTPUT);
  pinMode(R_PWM_PIN, OUTPUT);
  pinMode(L_PWM_PIN, OUTPUT);
  if (ledcSetup(R_PWM_CHANNEL, PWM_FREQUENCY_HZ, PWM_RESOLUTION_BITS) == 0 ||
      ledcSetup(L_PWM_CHANNEL, PWM_FREQUENCY_HZ, PWM_RESOLUTION_BITS) == 0) {
    return false;
  }
  ledcAttachPin(R_PWM_PIN, R_PWM_CHANNEL);
  ledcAttachPin(L_PWM_PIN, L_PWM_CHANNEL);
  force_off_main();

  instance_ = this;
  timer_ = timerBegin(TILT_TIMER_INDEX, 80, true);  // 1us tick
  if (timer_ == nullptr) return false;
  timerStop(timer_);
  timerAttachInterruptFlag(timer_, &MotionController::on_timer_static, false,
                           ESP_INTR_FLAG_IRAM);
  timerAlarmDisable(timer_);
  return true;
}

bool MotionController::start(TiltPolicy::Direction direction, uint32_t duration_ms,
                             int duty_percent) {
  if (direction == TiltPolicy::Direction::Stop || duration_ms == 0 ||
      duration_ms > TiltPolicy::ABSOLUTE_MAX_MOTION_MS || duty_percent < 1 ||
      duty_percent > 100) {
    return false;
  }
  disarm_timer();
  force_off_main();
  const uint16_t duty = static_cast<uint16_t>(
      (static_cast<uint32_t>(duty_percent) * PWM_MAX_DUTY) / 100U);
  portENTER_CRITICAL(&state_mux_);
  if (direction == TiltPolicy::Direction::Up) {
    ledcWrite(R_PWM_CHANNEL, duty);
    ledcWrite(L_PWM_CHANNEL, 0);
  } else {
    ledcWrite(R_PWM_CHANNEL, 0);
    ledcWrite(L_PWM_CHANNEL, duty);
  }
  GPIO.out_w1ts = DRIVER_ENABLE_MASK;
  direction_ = direction;
  timer_pending_ = false;
  portEXIT_CRITICAL(&state_mux_);
  arm_timer(duration_ms);
  return true;
}

bool MotionController::stop() {
  disarm_timer();
  portENTER_CRITICAL(&state_mux_);
  const bool was_moving = direction_ != TiltPolicy::Direction::Stop;
  force_off_main();
  timer_pending_ = false;
  portEXIT_CRITICAL(&state_mux_);
  return was_moving;
}

bool MotionController::take_timer_event() {
  portENTER_CRITICAL(&state_mux_);
  const bool pending = timer_pending_;
  timer_pending_ = false;
  portEXIT_CRITICAL(&state_mux_);
  return pending;
}

bool MotionController::is_moving() const {
  portENTER_CRITICAL(&state_mux_);
  const bool moving = direction_ != TiltPolicy::Direction::Stop;
  portEXIT_CRITICAL(&state_mux_);
  return moving;
}

void MotionController::arm_timer(uint32_t duration_ms) {
  timerStop(timer_);
  timerWrite(timer_, 0);
  timerAlarmWrite(timer_, static_cast<uint64_t>(duration_ms) * 1000ULL, false);
  timerAlarmEnable(timer_);
  timerStart(timer_);
}

void MotionController::disarm_timer() {
  if (timer_ == nullptr) return;
  timerAlarmDisable(timer_);
  timerStop(timer_);
}

void MotionController::force_off_main() {
  ledcWrite(R_PWM_CHANNEL, 0);
  ledcWrite(L_PWM_CHANNEL, 0);
  GPIO.out_w1tc = DRIVER_ENABLE_MASK;
  direction_ = TiltPolicy::Direction::Stop;
}

void IRAM_ATTR MotionController::on_timer_static() {
  if (instance_ != nullptr) instance_->on_timer();
}

void IRAM_ATTR MotionController::on_timer() {
  portENTER_CRITICAL_ISR(&state_mux_);
  force_off_isr();
  timer_pending_ = true;
  portEXIT_CRITICAL_ISR(&state_mux_);
}

void IRAM_ATTR MotionController::force_off_isr() {
  // PWM peripheral와 무관하게 driver enable을 끊어 ISR에서도 즉시 OFF한다.
  GPIO.out_w1tc = DRIVER_ENABLE_MASK;
  direction_ = TiltPolicy::Direction::Stop;
}
