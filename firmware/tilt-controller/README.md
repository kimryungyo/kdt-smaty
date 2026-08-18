# Tilt controller firmware

ESP32-C3가 틸트 모터 드라이버의 UP/DOWN 출력을 단독으로 소유한다. 서버는 USB
serial로 명령을 보내지만, firmware가 부팅 OFF·상호 배타 출력·최대 이동 시간을
독립적으로 강제한다.

## 안전 전제

- `include/config.h`의 핀과 `policy.h`의 물리 최대 위치는 bench 기본값이다. 실제
  배선·기구 한계·리미트 검증 전에는 업로드하거나 액추에이터를 연결하지 않는다.
- 부팅 뒤 위치는 `position_valid=false`다. 운영자가 안전한 기준 위치에 둔 뒤 local
  serial에서 `SET_POSITION <mm>`를 실행하기 전에는 `MOVE_TO`가 거부된다. 이 절차는
  FIN 서버가 해당 serial 포트를 열기 전에 수행한다.
- 모든 이동은 계산된 timer와 `ABSOLUTE_MAX_MOTION_MS` 안에서 종료한다. 서버·USB가
  끊겨도 timer가 출력을 OFF한다.
- 전원 차단 또는 모터 enable 차단 같은 물리 STOP 수단을 반드시 준비한다.

## Serial protocol

```text
STOP
STATUS
SET_POSITION <position_mm>
CALIBRATE <duty 1..100> <speed_mm_s> <UP|DOWN>
MOVE_TO <target_mm> <duty 1..100>
RUN <UP|DOWN> <duty 1..100> <duration_ms 50..16000>
```

응답은 한 줄 JSON이다. `ready`, `calibrated`, `moving`, `at_target`, `stopped`,
`rejected`, `fault` 이벤트를 사용한다. `CALIBRATE`는 서버가 동일 duty·direction의
`calibrated` ACK를 기다리는 계약이다.

`RUN`은 현장 원점 맞춤에만 쓰는 수동 시간 구동이다. 완료 후에도 위치는
`position_valid=false`로 남으므로, 이후 물리 기준점에서 `SET_POSITION`을 실행해야
한다.

## Server calibration files

`config/tilt_levels.example.json`과 `config/tilt_calibration.example.json`은 형식
예시일 뿐이며 실측값이 아니다. 장비별 실측 파일을 서버의
`data/tilt_levels.json`, `data/tilt_calibration.json`으로 배치하고, `.env`의
`SMART_DESK_TILT__*_FILE` 경로를 맞춘다. 서버는 단계 누락·비단조 목표·UP/DOWN
보정 누락을 발견하면 tilt를 활성화한 상태로 시작하지 않는다.

## Build and bench test

```bash
firmware/.venv/bin/pio test -d firmware/tilt-controller -e native
firmware/.venv/bin/pio run -d firmware/tilt-controller -e esp32-c3-devkitm-1
```

실물 시험은 모터 드라이버와 액추에이터를 분리한 상태에서 GPIO OFF, UP/DOWN 상호
배타, STOP과 timer OFF부터 확인한다. 상세 순서는
`.scratch/designs/tilt-hardware-mvp-design.md`를 따른다.
