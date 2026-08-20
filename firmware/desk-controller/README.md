# desk-controller

높이 relay와 틸트(BTS7960)를 ESP32-WROOM-32E 한 대에서 실행하는 펌웨어다.

원래 ESP32-C3 두 대가 나눠 맡았으나 배선 길이 문제로 한 대에 합쳤다. 서버가 쓰는
MQTT 계약은 그대로라 서버 코드는 바뀌지 않는다.

## MQTT 계약

| 토픽 | 방향 | 용도 |
| --- | --- | --- |
| `/desk_ctl` | 구독 | 높이 relay 명령(UP/DOWN/STOP/WAKE) |
| `/desk_ctl_status` | 발행 | relay 상태와 heartbeat |
| `/tilt_ctl` | 구독 | 틸트 명령(MOVE_TO/RUN/STOP/CALIBRATE/SET_POSITION) |
| `/tilt_ctl_status` | 발행 | 틸트 이벤트와 heartbeat |
| `/smartdesk/desk/height` | 구독 | 서버가 보내는 높이 lease |

두 장치가 Wi-Fi 연결 하나와 MQTT client 하나를 공유한다. client id는
`smartdesk-fin-desk-<MAC>`이다.

## 배선

`pinmap.md`를 따른다. 핀을 옮길 때의 제약도 그 문서에 있다.

## 빌드와 업로드

```bash
cd /srv/smart-desk-fin
firmware/.venv/bin/pio test -d firmware/desk-controller -e native
firmware/.venv/bin/pio run -d firmware/desk-controller -e esp32dev
firmware/.venv/bin/pio run -d firmware/desk-controller -e esp32dev -t upload
```

WROOM-32E는 C3와 달리 native USB가 없다. USB-UART 어댑터로 업로드하며, 포트를
점유한 프로세스(Main 컨테이너 등)를 먼저 정상 종료해야 한다.

`include/secrets.h`는 Git에 올리지 않는다. `secrets.h.example`을 복사해 실제 Wi-Fi
자격증명을 채운다.

## 안전 설계

이동의 최후 차단은 hardware timer ISR이 GPIO 레지스터를 직접 끄는 것으로
보장한다. 소프트웨어가 멈춰도 relay는 `MAX_HOLD_MS`(500ms), 틸트는
`ABSOLUTE_MAX_MOTION_MS`(16s) 안에 반드시 꺼진다.

- relay는 timer 0, 틸트는 timer 1을 쓴다. 같은 번호를 쓰면 한쪽 차단이 사라진다.
- Wi-Fi나 broker가 끊기면 두 장치를 모두 세운다. 명령을 받을 수 없는 동안
  움직이면 정지 명령이 도달할 방법이 없기 때문이다.
- 이동 중에도 접속을 시도한다. 접속을 미루면 서버의 재발행 명령과 맞물려 영구
  미접속에 빠진다.
