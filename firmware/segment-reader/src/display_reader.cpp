#include "display_reader.h"

namespace {
constexpr char FIRMWARE_VERSION[] = "smartdesk-fin-segment-reader-1.0.0";

constexpr byte SEG_A = 7;
constexpr byte SEG_B = 6;
constexpr byte SEG_C = 5;
constexpr byte SEG_D = 4;
constexpr byte SEG_E = 3;
constexpr byte SEG_F = 2;
constexpr byte SEG_G = 12;
constexpr byte SEG_DP = 11;
constexpr byte DIGIT1 = 10;
constexpr byte DIGIT2 = 9;
constexpr byte DIGIT3 = 8;

constexpr byte SEGMENT_PINS[8] = {
    SEG_A, SEG_B, SEG_C, SEG_D, SEG_E, SEG_F, SEG_G, SEG_DP};
constexpr byte DIGIT_PINS[3] = {DIGIT1, DIGIT2, DIGIT3};
}  // namespace

void DisplayReader::begin() {
  // 외부 패널을 손상시키지 않도록 표시기 관련 핀은 고임피던스 입력만 사용한다.
  for (byte pin : SEGMENT_PINS) {
    pinMode(pin, INPUT);
  }
  for (byte pin : DIGIT_PINS) {
    pinMode(pin, INPUT);
  }

  Serial.begin(115200);
  Serial.print(F("{\"status\":\"reader_started\",\"firmware\":\""));
  Serial.print(FIRMWARE_VERSION);
  Serial.println(F("\",\"baudrate\":115200}"));
}

void DisplayReader::update() {
  sampleSelectedDigit();
  reportIfDue();
}

int DisplayReader::selectedDigit() const {
  byte lowCount = 0;
  int lowIndex = -1;
  for (byte i = 0; i < 3; i++) {
    if (digitalRead(DIGIT_PINS[i]) == LOW) {
      lowCount++;
      lowIndex = i;
    }
  }
  return lowCount == 1 ? lowIndex : -1;
}

byte DisplayReader::readSegmentMask() const {
  byte mask = 0;
  // A=bit 6부터 G=bit 0까지이며 HIGH인 선을 켜진 세그먼트로 본다.
  for (byte i = 0; i < 7; i++) {
    if (digitalRead(SEGMENT_PINS[i]) == HIGH) {
      mask |= (1 << (6 - i));
    }
  }
  return mask;
}

void DisplayReader::sampleSelectedDigit() {
  // digit 전환 구간을 거르고 동일 결과가 세 번 반복된 슬롯만 확정한다.
  const int firstDigit = selectedDigit();
  if (firstDigit < 0) return;

  delayMicroseconds(25);
  if (selectedDigit() != firstDigit) return;
  const byte firstMask = readSegmentMask();
  const bool firstPoint = digitalRead(SEG_DP) == HIGH;

  delayMicroseconds(10);
  const byte secondMask = readSegmentMask();
  const bool secondPoint = digitalRead(SEG_DP) == HIGH;

  delayMicroseconds(10);
  const byte thirdMask = readSegmentMask();
  const bool thirdPoint = digitalRead(SEG_DP) == HIGH;
  if (selectedDigit() != firstDigit ||
      firstMask != secondMask ||
      secondMask != thirdMask ||
      firstPoint != secondPoint ||
      secondPoint != thirdPoint) {
    return;
  }

  if (candidateMasks_[firstDigit] == firstMask &&
      candidatePoints_[firstDigit] == firstPoint) {
    if (candidateCounts_[firstDigit] < 3) candidateCounts_[firstDigit]++;
  } else {
    candidateMasks_[firstDigit] = firstMask;
    candidatePoints_[firstDigit] = firstPoint;
    candidateCounts_[firstDigit] = 1;
  }
  if (candidateCounts_[firstDigit] >= 3) {
    digitMasks_[firstDigit] = firstMask;
    digitPoints_[firstDigit] = firstPoint;
    freshDigits_ |= (1 << firstDigit);
  }
}

void DisplayReader::reportIfDue() {
  if (millis() - lastReportMs_ < 50) return;
  lastReportMs_ = millis();

  Serial.print(F("{\"a\":")); Serial.print(digitalRead(SEG_A));
  Serial.print(F(",\"b\":")); Serial.print(digitalRead(SEG_B));
  Serial.print(F(",\"c\":")); Serial.print(digitalRead(SEG_C));
  Serial.print(F(",\"d\":")); Serial.print(digitalRead(SEG_D));
  Serial.print(F(",\"e\":")); Serial.print(digitalRead(SEG_E));
  Serial.print(F(",\"f\":")); Serial.print(digitalRead(SEG_F));
  Serial.print(F(",\"g\":")); Serial.print(digitalRead(SEG_G));
  Serial.print(F(",\"dp\":")); Serial.print(digitalRead(SEG_DP));
  Serial.print(F(",\"d10\":")); Serial.print(digitalRead(DIGIT1));
  Serial.print(F(",\"d9\":")); Serial.print(digitalRead(DIGIT2));
  Serial.print(F(",\"d8\":")); Serial.print(digitalRead(DIGIT3));
  Serial.print(F(",\"m10\":")); Serial.print(digitMasks_[0]);
  Serial.print(F(",\"p10\":")); Serial.print(digitPoints_[0] ? 1 : 0);
  Serial.print(F(",\"m9\":")); Serial.print(digitMasks_[1]);
  Serial.print(F(",\"p9\":")); Serial.print(digitPoints_[1] ? 1 : 0);
  Serial.print(F(",\"m8\":")); Serial.print(digitMasks_[2]);
  Serial.print(F(",\"p8\":")); Serial.print(digitPoints_[2] ? 1 : 0);
  Serial.print(F(",\"fresh\":")); Serial.print(freshDigits_);
  Serial.println('}');
  freshDigits_ = 0;
}
