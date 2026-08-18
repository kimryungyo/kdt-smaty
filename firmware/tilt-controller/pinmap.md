# Tilt controller pin map

| ESP32-C3 GPIO | HW-039 연결 | 기본 level |
| --- | --- | --- |
| GPIO4 | R_EN | LOW (driver OFF) |
| GPIO10 | L_EN | LOW (driver OFF) |
| GPIO20 | RPWM (UP) | PWM 0 |
| GPIO21 | LPWM (DOWN) | PWM 0 |
| GND | 모터 드라이버 논리 GND | 공통 |

UP은 RPWM, DOWN은 LPWM에 20kHz PWM을 인가한다. hardware timer 만료 시에는
R_EN/L_EN을 LOW로 내려 PWM 상태와 무관하게 driver를 끈다.
