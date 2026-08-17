# SMART DESK FIN

SMART DESK를 단일 FastAPI 프로세스와 `asyncio` 기반으로 재구성하는 프로젝트다.
설정, singleton container, task 관리와 애플리케이션 수명주기 위에 로컬 EMQX와
연결하는 비동기 MQTT 기반, Arduino 높이·ESP32 relay DeskIO 어댑터와 목표·수동
책상 제어기를 구현했다. FIN ESP32-C3 relay firmware는 clean build까지 완료했지만
upload와 실물 검증은 아직 수행하지 않았다. 영상은 카메라별 WebRTC publisher가
기존 호스트 MediaMTX에 WHIP로 발행하고 Python은 WHEP에서 최신 frame 하나를 읽는다.

현재 운영 transport는 서버와 ESP32가 EMQX를 통해 직접 통신하는 Wi-Fi/MQTT 경로다.
MQTT→USB-serial bridge는 실행하거나 배포하지 않는다. Arduino 높이 리더의 USB serial과
relay 분리 bench용 ESP32 serial 명령은 이 운영 transport와 별개의 입력·검증 경로다.

## 최종 제품 방향

이 프로젝트의 최종 목표는 단순한 책상 제어기나 음성 비서가 아니라, **책상 전체를
보는 카메라를 바탕으로 사용자의 현재 작업 맥락을 이해하고 음성·화면·책상 장치를
함께 사용하는 멀티모달 AI 스마트 데스크**를 만드는 것이다. 아래 내용은 현재 구현
완료 상태가 아니라 이후 기능과 구조를 판단할 때 유지해야 할 제품 방향이다.

책상 디스플레이에서는 React 웹 대시보드가 항상 실행된다. AI의 한 응답은 음성과
대시보드라는 두 표현 채널을 사용할 수 있으며, 두 채널은 서로 다른 답변이 아니라
하나의 응답을 상황에 맞게 나누어 표현한다.

- 음성은 확인, 진행 안내, 결론처럼 바로 이해할 수 있는 짧은 문장만 말한다.
- 장문 설명, 단계별 해설, 표, 수식, 카메라 캡처, 주석 이미지와 생성 이미지가
  필요한 답변은 대시보드에 표시한다.
- 상세 답변을 화면에 표시했을 때 음성은 내용을 전부 읽지 않고 화면을 보도록 짧게
  안내한다.
- 음성과 화면의 응답은 같은 대화 session과 작업 상태를 공유해 내용이 엇갈리지
  않게 한다.
- AI가 음성 응답을 마치면 제한된 시간 동안 후속 질문을 기다려, 사용자가 Wake Word를
  반복하지 않고 같은 문맥으로 대화를 이어갈 수 있게 한다.
- 사용자는 독서·공부 같은 작업 모드를 만들고 모드별 앉기 높이, 서기 높이와 LED 색상을
  저장한다. `AUTO`/`MANUAL` 제어 방식과 작업 모드는 서로 독립적으로 동작한다.

대표적인 최종 상호작용 목표는 다음과 같다. 이는 아직 구현 계약이나 component 설계가
아니다.

```text
사용자: "현재 풀고 있는 문제집의 1번 문제를 해설해 줘"
  ↓ microphone → STT
AI: "책상 위 문제를 확인해 해설해 드릴게요."                 (짧은 음성)
  ↓ 후속 camera context 연결 (미설계)
책상 frame에서 문제집·1번 문제를 확인하고 분석
  ↓ Assistant가 하나의 응답을 음성용 요약과 화면용 상세 콘텐츠로 구성
AI: 후속 Dashboard 연결을 통해 단계별 풀이와 필요한 자료 제공 (미설계)
  ↓
AI: "상세한 해설을 화면에 표시했습니다."                     (짧은 음성)
```

현재 구현은 Agents SDK `VoicePipeline` 단일 경로, 서버 current-user session 문맥,
제한된 public tool, Assistant turn 저장과 Dashboard의 최신 turn polling까지 포함한다.
자동 테스트는 이 코드 경계를 검증하지만 실제 microphone·speaker·OpenAI 계정은 opt-in
실물 검증 대상이다.

이전 수동 STT → Responses → TTS 경로는 운영 경로가 아니다. model, VAD, 사용자 session과
장기 기억 경계는 [Agents SDK 음성 파이프라인 전환 결정](docs/architecture/agents-sdk-voice-pipeline.md)을
따르며, 실제 OpenAI·audio·Mem0 운영 검증은 아직 남아 있다.

```text
Microphone → VoiceService → AgentsVoiceRuntime (STT → Agent tools → TTS)
                                                ↓
                                             Speaker
```

Dashboard AI 응답은 현재 사용자 session의 최신 Assistant turn 하나를 HTTP polling으로
전달한다. SSE·WebSocket이나 범용 chat history 구조는 이번 범위에 추가하지 않는다.
workspace camera의 문제집·문서·화면 분석과 그 분석을 Assistant tool/camera context로
전달하는 기능은 아직 구현하지 않은 최종 제품 방향이다.

카메라는 현재 다음 구조를 사용한다.

```text
물리 camera → WebRtcCameraPublisher/WHIP → MediaMTX → WHEP/WebRtcFrameSource
                                                               ↓
                                           (image, captured_at) 최신값
```

향후 AI camera context는 요청마다 MediaMTX에 새로 연결하거나 물리 camera를 다시 여는
방식보다 기존 `WebRtcFrameSource.get_latest_frame()`을 재사용하는 것이 현재 구조에
적합하다. freshness 기준, crop·변환, AI 전송 방식과 MCP 사용 여부는 후속 설계에서
결정한다.

`VoiceService`는 microphone, STT와 wake word를 담당하고, `PlaybackCoordinator`는
TTS·효과음의 순서, 중지와 local speaker 출력을 관리한다. 로컬 microphone과 speaker는
MediaMTX를 경유하지 않는다.

추가 audio source가 필요해지면 `PlaybackCoordinator` 뒤에 adapter와 출력 정책을
추가할 수 있다. 구체적인 source, mixing 방식과 제어 tool은 요구가 확정된 뒤 별도
설계한다.

profile별 장기 기억 경계와 `profile:<profile_id>` namespace는 코드에 있으나, 실제 Mem0
운영 검증은 남아 있다. 최근 대화 문맥은 책상 `sessionId`에 연결된 Agents SDK 대화
session이 담당하고, 장기 기억에는 명시적으로 기억시킨 선호와 장기간 유효한 사실만
저장한다. raw 음성·camera 이미지·일시적인 행동 관측과 전체 transcript는 자동 저장하지
않는다. 기억 관리 UI는 후속 범위다.

## 개발 환경

백엔드:

```bash
cd /srv/smart-desk-fin
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

React 대시보드:

```bash
cd /srv/smart-desk-fin/frontend
npm ci
```

설정 이름과 기본값은 [`.env.example`](.env.example)을 참고한다. `.env`는
선택 사항이며, 같은 이름의 환경변수가 우선한다.

## 개발 실행

로컬 EMQX가 `127.0.0.1:1883`에서 실행 중인지 확인한 뒤 FastAPI를 실행한다.
최초 MQTT 연결과 구독을 완료하지 못하면 애플리케이션도 시작하지 않는다.

하드웨어 singleton 중복 생성을 막기 위해 Uvicorn worker는 반드시 하나만
사용한다.

worker 하나 안에서도 FastAPI 요청, MQTT, 높이 갱신과 책상 제어는 여러 async
task로 동시에 실행할 수 있다. 이 프로젝트에서는 별도 process lock이나 실행
관리자를 추가하지 않고 아래 명령을 표준으로 사용한다. 실제 책상을 연결한
상태에서는 프로세스를 자동 재시작하는 `--reload`를 사용하지 않는다.

```bash
.venv/bin/uvicorn smart_desk.main:app --host 0.0.0.0 --port 9090 --workers 1
```

다른 터미널에서 Vite 개발 서버를 실행한다. `/api`와 `/health` 요청은
`http://127.0.0.1:9090`으로 proxy한다.

```bash
cd /srv/smart-desk-fin/frontend
npm run dev
```

개발 대시보드는 `http://127.0.0.1:5173`에서 연다.

## AI 스피커 실행

Voice는 기본적으로 비활성화되어 기존 Desk·Dashboard 실행에 OpenAI SDK, PortAudio,
microphone와 speaker를 요구하지 않는다. Voice 장비에서는 optional dependency를 설치한다.

```bash
.venv/bin/python -m pip install -e '.[dev,voice]'
```

Debian/Ubuntu 계열에서는 PortAudio package `libportaudio2`, 개발 header가 필요한 환경은
`portaudio19-dev`를 확인한다. 운영 사용자가 PipeWire/PulseAudio session과 microphone
권한에 접근할 수 있어야 하며 root 실행을 기본 해법으로 사용하지 않는다.

최소 설정은 다음과 같다. 장치 이름을 비우면 PortAudio 기본 장치를 사용하고, 값을
지정하면 공백과 대소문자를 제외한 전체 이름이 유일하게 일치해야 한다.

기본 저부하 설정은 80ms microphone frame 5개마다 Wake Word를 추론하여 평상시 ONNX
실행 횟수를 400ms당 한 번으로 제한하고, 한 번 임계값을 넘으면 즉시 활성화한다.
`SMART_DESK_VOICE__WAKEWORD_CONSECUTIVE_FRAMES`를 2 이상으로 설정한 경우에는 첫
양성 뒤 다음 80ms frame에서 연속 여부를 확인한다. 더 빠른 반응이 필요하면
`SMART_DESK_VOICE__WAKEWORD_INFERENCE_INTERVAL_FRAMES`를 낮추고, CPU 사용량을 더
줄이려면 높인다.

```text
SMART_DESK_OPENAI__API_KEY=<secret>
SMART_DESK_VOICE__ENABLED=true
SMART_DESK_VOICE__INPUT_DEVICE_NAME=<stable full name>
SMART_DESK_VOICE__OUTPUT_DEVICE_NAME=<stable full name>
```

음성 흐름은 프로젝트의 `하이 스마티` 호출어와 local 확인음 뒤에 원본 24kHz PCM을
Agents SDK `VoicePipeline`에 streaming으로 전달하고, final transcript 뒤에 PCM TTS를
재생한다. follow-up은 Agent가 명시적으로 요청한 turn에서만 열린다. 사용자 테스트 전에
사용자에게 **“이 음성은 AI가 생성합니다”**를 고정 화면 문구, 물리 라벨 또는 온보딩으로
고지해야 한다.

오늘 AKG microphone이 없는 상태에서는 이 검증을 통과했다고 주장하지 않는다. 내일은
구성된 microphone을 연결하고 서버를 한 번 재기동한 뒤 `/api/voice/status`가
`WAITING_WAKE`인지 확인한다. 그 다음에만 원하면 아래 opt-in hardware test를 실행한다.
실제 장치 검증은 자동 테스트 증거가 아니며, 배포 환경에서 별도로 수행한다:

```bash
SMART_DESK_RUN_VOICE_HARDWARE=1 \
SMART_DESK_VOICE__ENABLED=true \
SMART_DESK_VOICE__INPUT_DEVICE_NAME='<stable full name>' \
SMART_DESK_VOICE__OUTPUT_DEVICE_NAME='<stable full name>' \
  .venv/bin/python -m pytest -m voice_hardware \
  # microphone, wake word, acknowledgement, speaker를 수동 점검
```

Wake Word model의 사용 조건과 wheel provenance는
[Voice third-party 문서](docs/third-party/voice.md)에 기록한다.

### 임시 AI 스피커 디버그 페이지

Voice와 같은 프로세스에서 별도 HTTP 서버를 열어 Wake Word score, Voice 상태 전이와
microphone queue 통계를 확인할 수 있다. 사용자 발화, 음성 응답, session/turn 이력과
provider secret은 표시하지 않는다.

```text
SMART_DESK_VOICE_DEBUG__ENABLED=true
SMART_DESK_VOICE_DEBUG__HOST=0.0.0.0
SMART_DESK_VOICE_DEBUG__PORT=10000
```

기본 FastAPI 서버를 평소처럼 실행한 뒤 `http://<장비 IP>:10000`에서 확인한다. 디버그
서버는 Voice 다음에 시작되고 먼저 종료되므로 microphone, Wake Word model과 OpenAI
session을 중복 생성하지 않는다. 화면은 250ms(4Hz)마다 content-free read-only snapshot을
갱신한다. 임시 검증이 끝나면 `SMART_DESK_VOICE_DEBUG__ENABLED=false`로 닫고 신뢰하는
네트워크에서만 사용한다.

## 운영 실행

React를 빌드한 뒤 FastAPI만 실행한다. 빌드 결과는 `frontend/dist`에 생성되며,
FastAPI가 `/`에서 SPA로 제공한다.

```bash
cd /srv/smart-desk-fin/frontend
npm ci
npm run build

cd /srv/smart-desk-fin
.venv/bin/uvicorn smart_desk.main:app --host 0.0.0.0 --port 9090 --workers 1
```

`SMART_DESK_ENVIRONMENT=production`일 때 React 빌드가 없으면 FastAPI 시작을
실패 처리한다. 개발 환경에서는 빌드가 없어도 API만 실행할 수 있다.

상태 확인:

```bash
curl http://127.0.0.1:9090/health/live
curl http://127.0.0.1:9090/health/ready
```

## 카메라 실행 전제

카메라 WHIP publish와 WHEP frame 수신은 카메라별로 독립적으로 활성화한다. 모든 역할은
기본적으로 비활성화되어 있어 장치 없이 개발·테스트할 수 있다. 실제 실행 전에는
호스트에서 MediaMTX WebRTC listener `:8889`가 실행 중이어야 한다. FastAPI는 MediaMTX를
설치·시작·종료하지 않는다.

현재 호스트에서 확인한 역할과 기본 capture 설정은 다음과 같다. ABKO 장치는 제품명과
달리 V4L2에서 `2560x1440`이 아닌 `2592x1944`를 최대 해상도로 광고하므로 실제 광고값을
사용한다. posture 카메라는 아직 연결된 장치가 없어 `/dev/posture-cam`을 자리표시자로
두며, 해당 symlink 또는 환경변수 장치 경로를 구성하기 전에는 publish를 켜지 않는다.

| 역할 | 장치 | 기본 해상도 | MediaMTX path |
| --- | --- | --- | --- |
| `user` | Alcorlink USB 2.0 Camera | 1920x1080 | `user-cam` |
| `workspace` | ABKO APC930 QHD Webcam | 2592x1944 | `workspace-cam` |
| `posture` | 미지정 | 1280x720 후보 | `posture-cam` |

장치를 바꾸면 실제 capture index, input format, 해상도와 FPS를 먼저 확인한 뒤 `.env`에
안정적인 `/dev/v4l/by-id/...` 경로와 값을 설정한다. 현재 `workspace`는 application-owned
publish만 등록되어 있고 server-side receiver는 없다. 업무 영역 AI 분석과 receiver 연결은
별도 작업에서 다룬다.
`posture` 하단 Vision은 선택 ONNX 모델을 설정하면 full-frame 자세/인원수 adapter와
snapshot을 실행할 수 있지만, 실제 `/bottom-cam/whep`, ROI와 threshold/CPU 보정은 완료되지 않았다.

```bash
ls -l /dev/v4l/by-id/
ffmpeg -hide_banner -f v4l2 -list_formats all -i /dev/v4l/by-id/<camera-device>
ss -ltn | rg ':8889\b'
```

현재 topology에서 user는 publish+receive, workspace는 publish-only, posture는 external
`/bottom-cam` receive-only로 설정한다.

```text
SMART_DESK_MEDIA__USER__PUBLISH_ENABLED=true
SMART_DESK_MEDIA__USER__RECEIVE_ENABLED=true
SMART_DESK_MEDIA__WORKSPACE__PUBLISH_ENABLED=true
SMART_DESK_MEDIA__WORKSPACE__RECEIVE_ENABLED=false
SMART_DESK_MEDIA__POSTURE__PUBLISH_ENABLED=false
SMART_DESK_MEDIA__POSTURE__RECEIVE_ENABLED=true
```

publish가 활성화된 카메라는 FFmpeg 자식 process를 시작하며 장치 또는 FFmpeg가 없으면
애플리케이션 시작을 실패 처리한다. receive만 활성화한 카메라는 로컬 장치를 열지 않고
설정된 `RECEIVE_URL`에 연결하며, stream이 아직 없어도 background에서 재연결한다. 종료
시 reader를 먼저 닫고 자신이 시작한 FFmpeg만 종료한다. MediaMTX는 계속 실행된다.

### 원격 카메라 송출

원격 개발 컴퓨터에서 전체 Desk 서버를 실행하지 않고 `fin`의 publisher 전용 진입점을
사용할 수 있다. 원격 컴퓨터의 `.env`에서 해당 카메라의 `PUBLISH_ENABLED=true`,
`PUBLISH_URL=http://<MediaMTX-host>:8889/<path>/whip`와 로컬 장치 경로를 설정한다.

```bash
.venv/bin/python -m smart_desk.media_publish --camera workspace
```

MediaMTX 호스트에서는 같은 카메라의 publish를 끄고 receive만 켠다. 하나의 path에는
publisher 하나만 연결하며, 원격 publish를 허용할 때는 MediaMTX 인증 또는 IP 제한을
함께 적용한다.

## 테스트

```bash
.venv/bin/python -m pytest
.venv/bin/python -m compileall -q src tests
cd frontend && npm run build
```

펌웨어 도구 설치:

```bash
python3 -m venv firmware/.venv
firmware/.venv/bin/python -m pip install -r firmware/requirements.txt
```

Arduino 세그먼트 리더 build와 FIN relay firmware의 native 계약 test·ESP32-C3 build:

```bash
firmware/.venv/bin/pio run -d firmware/segment-reader
firmware/.venv/bin/pio test -d firmware/relay-controller -e native
firmware/.venv/bin/pio run -d firmware/relay-controller -e esp32-c3-devkitm-1
```

전용 환경은 애플리케이션 `.venv`와 PlatformIO의 웹 의존성 충돌을 막는다. build는
장치를 변경하지 않는다. Arduino upload 전에는 서버를 종료해 시리얼 포트를
해제한다. ESP32 firmware upload와 실제 UP/DOWN은 relay 분리 검증과 사용자 승인 뒤에만
수행한다. 세부 절차는 [펌웨어 안내](firmware/README.md)를 따른다.

기본 테스트는 MQTT broker 없이 실행된다. 로컬 EMQX와 실제 QoS 1 발행·구독 및
재연결·재구독까지 확인하려면 다음 명령을 추가로 실행한다.

```bash
SMART_DESK_RUN_MQTT_INTEGRATION=1 \
  .venv/bin/python -m pytest -m mqtt_integration
```

전체 폴더와 파일 책임은 [프로젝트 구조](docs/PROJECT_STRUCTURE.md), 설계와 구현
순서는 [설계 문서](docs/README.md), 프런트엔드 실행 방식은
[React 대시보드](docs/architecture/frontend.md)에서 확인한다. 실제 구현은
[번호순 작업 목록](docs/tasks/README.md)을 따른다. 새 기능이나 구조를 계획할
때는 [계획 및 설계 가이드](docs/guides/README.md)를 먼저 확인한다.
운영 시작·상태·복구와 현재 제한은 [운영 runbook](docs/operations/README.md)에 정리한다.
