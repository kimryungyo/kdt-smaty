# SMART DESK FIN 7-segment 높이 리더

Arduino Uno 호환 CH340 보드가 모션데스크 표시기의 3자리 멀티플렉싱 신호를 읽어
115200 baud JSON-lines로 출력한다. 이 장치는 표시기를 구동하거나 모터를 제어하지
않는다.

## 안전과 핀맵

- D2~D12의 표시기 관련 핀은 모두 고임피던스 `INPUT`이다.
- `OUTPUT` 또는 `INPUT_PULLUP`으로 변경하지 않는다.
- 표시기 제어기와 Arduino GND를 공통으로 연결한다.
- 입력 신호는 Uno 허용 범위인 0~5V여야 한다.

| 신호 | Arduino |
| --- | --- |
| A, B, C, D, E, F, G | D7, D6, D5, D4, D3, D2, D12 |
| DP | D11 |
| 일의 자리, 십의 자리, 백의 자리 | D10, D9, D8 |

세그먼트는 active-HIGH, 자릿수 선택은 active-LOW다. A는 mask bit 6, G는 bit 0이다.

## 프레임 전환 안정화

자릿수 전환 중 서로 다른 자리의 신호가 섞이지 않도록 다음 순서로 확정한다.

1. D8~D10 중 정확히 한 자리만 active-LOW인 경우에만 샘플링한다.
2. 25us 뒤 같은 자리가 계속 선택됐는지 확인한다.
3. A~G mask와 DP를 10us 간격으로 세 번 읽고 모두 같은 경우만 후보로 둔다.
4. 같은 후보가 해당 자리에서 세 번 반복돼야 확정한다.
5. 50ms마다 이번 구간에 확정된 자리를 `fresh` bitset으로 함께 출력한다.

FIN의 `SegmentDecoder`는 세 자리가 모두 새로 확정된 `fresh=7` 프레임만 사용하며,
mask, 소수점과 73~118cm 물리 범위를 다시 검증한다.

## 표시기 자동 절전

물리 모션데스크 표시기는 마지막 제어 이후 약 30초가 지나면 자동 절전으로 꺼진다.
이는 Arduino나 USB serial 연결이 끊긴 것이 아니라 표시기 자체의 정상 동작이다.
표시기가 꺼지면 D8~D10의 자리 선택 신호가 더 이상 정상적으로 순환하지 않으므로
리더는 세 자리를 새로 확정한 `fresh=7` 프레임을 만들 수 없다.

현재 리더는 마지막으로 확정한 digit mask를 같은 실행 중 RAM에 남겨 두지만,
`fresh`는 50ms report마다 초기화한다. 서버는 `fresh=7`만 새 높이로 채택하므로 절전
중 출력은 높이 갱신으로 인정되지 않는다. 그 결과 다음 상태가 발생한다.

- 표시기가 켜진 동안 높이를 받은 서버는 기본 1초 후 높이를 `STALE`로 판정한다.
- 서버가 표시기 절전 중 시작되면 최초 높이가 없어 `WAITING` 상태를 유지한다.
- ESP32는 `height_waiting` 또는 `height_stale`로 이동을 무장하지 않으며 Dashboard의
  수동·목표 높이 제어도 `연결 확인 중` 상태로 잠긴다.

마지막 유효 높이를 절전 중 별도 상태로 전달하거나 재시작 뒤 복원하는 계약은 아직
구현하지 않았다. 캐시 높이를 새 실측값처럼 반복 발행하면 이동 중 height freshness
안전장치를 무력화하므로, 후속 구현에서는 live 측정과 cached 높이를 구분해야 한다.

## 시리얼 계약

시작 메시지:

```json
{"status":"reader_started","firmware":"smartdesk-fin-segment-reader-1.0.0","baudrate":115200}
```

높이 프레임 예시:

```json
{"m10":127,"p10":0,"m9":51,"p9":1,"m8":127,"p8":0,"fresh":7}
```

실제 프레임에는 배선 진단을 위한 A~G, DP와 D8~D10 순간 레벨도 포함된다.
표시기가 꺼진 동안 `fresh=0`이거나 mask 0인 프레임이 출력될 수 있지만 서버는 이를
새 높이로 채택하지 않는다.

## 빌드와 업로드

```bash
cd /srv/smart-desk-fin
python3 -m venv firmware/.venv
firmware/.venv/bin/python -m pip install -r firmware/requirements.txt
firmware/.venv/bin/pio run -d firmware/segment-reader
```

업로드 전 FIN 서버를 정상 종료해 Arduino 포트를 해제한다.

```bash
firmware/.venv/bin/pio run -d firmware/segment-reader -t upload
```

업로드 뒤 `reader_started`의 FIN 펌웨어 버전, 세그먼트 ON의 반복 유효 프레임과
`/api/status`의 같은 높이·`ONLINE`을 확인한다. 검증이 끝나면 FIN 서버를 다시
기동해 포트 재연결과 `/health/ready`를 확인한다.
