#pragma once

#include <Arduino.h>

// 외부 7-segment 신호를 구동하지 않고 안정된 멀티플렉싱 프레임만 읽는다.
class DisplayReader {
 public:
  void begin();
  void update();

 private:
  int selectedDigit() const;
  byte readSegmentMask() const;
  void sampleSelectedDigit();
  void reportIfDue();

  unsigned long lastReportMs_ = 0;
  byte digitMasks_[3] = {0, 0, 0};
  bool digitPoints_[3] = {false, false, false};
  byte freshDigits_ = 0;
  byte candidateMasks_[3] = {0, 0, 0};
  bool candidatePoints_[3] = {false, false, false};
  byte candidateCounts_[3] = {0, 0, 0};
};
