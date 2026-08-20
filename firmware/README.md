# SMART DESK FIN 펌웨어

FIN에서 사용하는 Arduino 높이 입력과 ESP32 릴레이 펌웨어를 관리한다.

## 개발 도구 설치

```bash
cd /srv/smart-desk-fin
python3 -m venv firmware/.venv
firmware/.venv/bin/python -m pip install -r firmware/requirements.txt
```

PlatformIO는 애플리케이션과 다른 버전의 웹 의존성을 사용하므로 앱 `.venv`에 설치하지
않는다. 모든 펌웨어 명령은 `firmware/.venv/bin/pio`로 실행한다.

## 프로젝트

| 경로 | 대상 | 역할 |
| --- | --- | --- |
| `segment-reader/` | Arduino Uno 호환 CH340 보드 | 7-segment 신호를 읽어 JSON-lines로 전송 |
| `desk-controller/` | ESP32-WROOM-32E | 높이 relay와 틸트를 한 보드에서 실행 |

높이와 틸트는 원래 ESP32-C3 두 대가 나눠 맡았으나, 한 대로 합치면서
`desk-controller/`로 통합했다. 서버가 쓰는 MQTT 토픽(`/desk_ctl`, `/tilt_ctl`)은
그대로라 서버 코드는 바뀌지 않는다.

각 펌웨어의 배선, 빌드, 업로드와 실물 검증 절차는 해당 디렉터리의 README를
따른다. 업로드 전에는 장치 포트를 점유한 프로세스를 정상 종료해야 한다.
