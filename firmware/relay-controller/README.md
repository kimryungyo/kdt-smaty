# SMART DESK FIN ESP32-C3 relay firmware

GPIO 3 UP, GPIO 4 DOWN의 active-high relay를 MQTT 명령으로 실행하는 FIN 전용
firmware다. 한 번의 UP/DOWN은 최대 500ms hardware timer deadline을 가지며, 같은
방향 갱신은 GPIO를 재접점하지 않고 timer만 다시 설정한다.

## Build

실제 네트워크 연결 전 `include/secrets.h.example`을 `include/secrets.h`로 복사해 값을
설정한다. `secrets.h`는 Git에서 제외된다. secret이 없어도 비이동 clean build는 할 수
있지만 Wi-Fi에는 연결되지 않는다.

```bash
pio run -d firmware/relay-controller -e esp32-c3-devkitm-1
```

기본 upload 경로는 ESP32-C3 내장 USB JTAG(`esp-builtin`)이다. Linux에서
`303a:1001` 장치에 접근할 수 있도록 udev 권한이 필요하다.

현재 PlatformIO `espressif32 7.0.1`의 Arduino core 2.0.17 hardware timer ISR을
`ESP_INTR_FLAG_IRAM`으로 등록한다. ISR은 ESP32-C3 GPIO clear register만 기록하며
MQTT, JSON, logging, allocation과 `digitalWrite()`를 호출하지 않는다.

## Safety gate

- build 성공은 timer 안전 검증 완료를 뜻하지 않는다.
- upload 전 relay를 책상 접점에서 분리한다.
- logic analyzer로 50/500ms OFF deadline과 network-loop stall 중 OFF를 확인한다.
- firmware upload와 실제 `/desk_ctl` UP/DOWN은 사용자 승인 뒤에만 수행한다.
- 실물 확정 전 후보값은 `HEIGHT_LEASE_MS=1500`, `CONTROL_ARM_DELAY_MS=500`이다.

## Relay-disconnected bench mode

책상 패널의 모터 연결선을 분리한 상태에서만 `esp32-c3-bench` 환경을 사용한다.
이 환경은 MQTT와 height lease 대신 USB serial의 `u/U`(UP 50/500ms),
`d/D`(DOWN 50/500ms), `b`(UP 500ms 뒤 main loop 1초 정지), `s`(STOP),
`p`(상태) 명령을 허용한다. serial 로그의 GPIO 값은 내부 확인값이므로 30ms
break-before-make의 외부 실측은 logic analyzer로 별도 수행한다. 시험이 끝나면 반드시
`esp32-c3-devkitm-1` production 환경을 다시 업로드한다.
