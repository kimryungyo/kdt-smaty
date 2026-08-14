#include <Arduino.h>

#include "display_reader.h"

namespace {
DisplayReader reader;
}

void setup() {
  reader.begin();
}

void loop() {
  reader.update();
}
