# 10. 로컬 서비스 운영 준비

## 목표

완료 상태는 자동 테스트가 많은 상태가 아니라, 한 대의 로컬 SMART DESK에서 다음 사용자
흐름을 실제로 수행할 수 있는 상태다.

1. Dashboard에서 profile을 만들고 user camera로 얼굴 표본을 등록한다. (현장 enrollment 검증은 미완료)
2. 등록 사용자를 다시 보면 current-user session이 시작되고, 이탈하면 session이 끝난다.
3. posture는 기존 MediaMTX `/bottom-cam`을 receive-only로 소비해 착석·기립을 관측한다.
4. user camera와 workspace camera는 이 애플리케이션이 FFmpeg publisher를 소유한다.
5. microphone을 연결하고 서버를 한 번 재기동하면 Wake Word, 한국어 Agent 응답과 TTS를
   사용할 수 있다.
6. 장치가 빠지거나 모델이 실패하면 profile·상태 조회는 유지하고 자동 이동만 fail-closed한다.

인터넷 공개 다중 사용자 서비스, 무중단 camera/audio hot-plug와 범용 플러그인 구조는 목표가
아니다. 운영 범위는 신뢰된 로컬 네트워크의 단일 프로세스·단일 책상이다.

## 2026-08-17 감사 기준선

| 영역 | 현재 확인된 상태 | 운영 차이 |
| --- | --- | --- |
| FastAPI | 18:15부터 worker 1개가 `:9090`에서 실행 중 | 현재 `main` 병합 전 process라 Voice API가 `404`; 계획된 재기동 필요 |
| EMQX | `:1883`에서 실행 중 | 실제 Wi-Fi 단절·복구와 ESP32 GPIO STOP 실측은 별도 |
| MediaMTX | RTSP `:8554`, WebRTC `:8889`에서 실행 중 | verification time에 RTSP `/bottom-cam` DESCRIBE는 `404`; 외부 publisher online 시 재확인 필요 |
| user camera | temporary live smoke: H264 1920x1080@15 verified | app FFmpeg publishes and consumes MediaMTX `/user-cam`; restart/freshness evidence remains |
| workspace camera | temporary live smoke: H264 2592x1944@15 verified | app FFmpeg publishes `/workspace-cam`; no server consumer is configured |
| posture | operator 제공 MediaMTX `/bottom-cam` | app does not publish; receives existing RTSP only (availability unverified) |
| 하단 pose | adapter와 sample 26/26 회귀, 기존 ONNX 경로 설정 | 재기동 뒤 `/bottom-cam` frame 실측 필요 |
| 얼굴 | 등록·식별·저장·session 엔진과 UI, YuNet/SFace adapter·모델 설정 | 재기동 뒤 user camera 현장 등록/재인식 검증 필요 |
| Voice | Agents SDK 경로, Wake Word 자산, API key와 speaker 장치가 있음 | 설정된 AKG Ara input은 오늘 미연결; 연결·재기동 뒤 실측 필요 |
| memory | `MemoryService` 경계 구현 | `mem0` package/storage 미준비; 기본 음성을 막지 않는 후순위 선택 기능 |
| Desk | MQTT relay heartbeat는 STOP, cached height 82.7cm | 현재 Desk `ERROR`; 실제 이동은 계속 비활성·별도 안전 검증 |

감사 중 API key가 한 read-only 하위 세션 입력에 노출됐다. 값은 문서와 로그에 기록하지 않으며
노출된 scratch transcript는 삭제했다. 현재 key 사용은 operator 결정에 따라 개발을 막지 않고,
교체는 후속 운영 정리로 남긴다.

## 이미 구현된 기반

- profile/activity-mode CRUD, Dashboard 설정·현재 사용자 분리
- 얼굴 등록 상태 API, 3개 embedding 표본의 원자적 교체·삭제
- open-set match, best-second margin, 익명/등록 session 전환과 stale 결과 경합 차단
- Agents SDK `VoicePipeline`, current-user context, session 취소, function tool, turn polling
- MQTT cold-start background 재연결과 이동 명령 fail-closed
- Arduino 높이 입력, Wi-Fi/MQTT ESP32 relay 계약, WLED adapter
- user/workspace/posture별 FFmpeg publisher와 RTSP latest-frame receiver lifecycle
- FFmpeg publisher는 warning/error만 journal에 남기고 progress stats를 억제하며, RTSP receiver는
  성공 frame 뒤 reconnect backoff를 base로 reset하고 source별 disconnect warning을 30초로 제한
- 하단 YOLO pose adapter, Vision freshness/count/skew/association과 Automation shadow 정책

## 우선순위별 차이

### P0. 실제 얼굴 등록·식별

- [ ] OpenCV YuNet 2023 face detector와 SFace 2021 embedding model을 version/hash/license와 함께
  로컬 provision한다. binary는 Git에 넣지 않는다.
- [ ] YuNet의 box·5 landmarks를 `FreshFaceObservation`에 한 번만 전달한다.
- [ ] SFace `alignCrop`·feature를 쓰는 extractor와 최소 face-size/blur/brightness 품질 gate를
  구현한다. 얼굴을 다시 검출하거나 crop을 저장하지 않는다.
- [ ] match, runner-up margin, enrollment consistency와 duplicate threshold를 명시적 설정으로
  연결한다. OpenCV 공식 cosine `0.363`은 시작 기준일 뿐 실제 user camera에서 same/different
  분포를 확인해 보수적으로 확정한다.
- [ ] 등록 성공, 재시작 뒤 식별, unknown/ambiguous, 다중 얼굴, 이탈 session 종료를 실카메라로
  확인한다.

선택 근거는 이미 사용하는 `opencv-python-headless` 안에서 detector·alignment·embedding을 모두
처리해 새 inference framework를 추가하지 않는 것이다.

- https://docs.opencv.org/4.x/d0/dd4/tutorial_dnn_face.html
- https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet
- https://github.com/opencv/opencv_zoo/tree/main/models/face_recognition_sface

### P0. 실제 camera topology

- [ ] user: Alcorlink V4L2 → app FFmpeg → MediaMTX `/user-cam` → app RTSP receiver
- [ ] workspace: ABKO V4L2 → app FFmpeg → MediaMTX `/workspace-cam` publish only; no server receiver yet
- [ ] posture: 외부 publisher → MediaMTX `/bottom-cam` → app RTSP receiver만 사용
- [ ] posture의 app-side publish와 `/dev/posture-cam` 의존은 production 설정에서 끈다.
- [ ] 세 source의 codec/크기/FPS, reconnect, freshness와 CPU를 실측한다.
- [ ] raw frame, face crop와 embedding을 DB·일반 API·로그에 남기지 않는다.

MediaMTX는 별도 사전 인프라로 유지한다. 애플리케이션 안에 WHEP server나 두 번째 media server를
추가하지 않는다. `/home/kimryungyo/sitting`에서는 하단 인식 알고리즘·검증 표본만 참고하고
webserver/WHEP/static 코드는 가져오지 않는다.

### P1. Voice와 microphone

- [ ] 현재 `.venv`에 `.[voice]` dependency가 실제로 모두 설치되는지 검증하고 누락 package를
  잠금 범위 안에서 설치한다.
- [ ] 무효한 legacy `.env` Voice key를 제거하고 현재 `VoiceSettings` 필드만 남긴다.
- [x] AKG Ara가 없는 오늘은 Voice가 기능별 `ERROR`로 격리되고 서버·Dashboard가 유지되는
  regression test로 검증한다. 실제 microphone 통과를 의미하지 않는다.
- [ ] 내일 AKG Ara 연결 후 PortAudio의 정확한 input 이름을 설정하고 서버를 한 번 재기동한
  뒤 `/api/voice/status`의 `WAITING_WAKE`를 확인한다.
  이번 단기 범위에는 무중단 hot-plug watcher를 추가하지 않는다.
- [ ] debug server는 production에서 끄거나 `127.0.0.1`에만 bind하고 content-free local
  telemetry만 노출한다.
- [ ] 비제어 한국어 질문 1회, TTS drain, no-speech timeout, follow-up과 session 교대 취소를
  순서대로 검증한다.
- [ ] Mem0는 기본 음성이 성공한 뒤 선택적으로 설치·활성화하고 profile별 add/search/delete만
  검증한다.

Voice model은 `SMART_DESK_OPENAI__RESPONSE_MODEL`을 Agents SDK configuration에 전달한다.
실시간 latency를 위해 reasoning effort는 현재 low를 유지한다.

### P1. Dashboard와 상태 연결

- [ ] 얼굴 등록 화면이 camera/model/quality 실패 이유와 진행 표본 수를 이해 가능한 문구로
  표시하는지 실제 browser에서 확인한다.
- [ ] current-user, Vision, Automation, Voice와 Assistant latest polling이 재기동 뒤 현재 API와
  일치하는지 확인한다.
- [ ] Voice debug의 stale `assistant` JavaScript 참조를 제거하거나 현재 projection과 맞춘다.
- [ ] preview는 운영자 진단에서만 제공하고 얼굴 crop·embedding·similarity는 노출하지 않는다.

### P2. 장치 통합과 배포

- [ ] WLED 연결·복구를 실측하되 Desk/session rollback과 분리한다.
- [ ] Arduino live height와 ESP32 Wi-Fi/MQTT fresh STOP을 확인한다.
- [ ] relay 분리 bench에서 pulse timing·watchdog·broker 단절 STOP을 먼저 증명한다.
- [ ] 실제 책상 제한 이동은 독립 STOP 접근과 현장 승인 뒤 마지막에 수행한다.
- [ ] `main` 배포용 systemd unit과 environment file 권한, restart/rollback 절차를 Git 추적 가능한
  최소 자산으로 둔다. EMQX와 MediaMTX를 앱 process 안으로 합치지 않는다.

## 구현 순서

1. model provisioning/settings와 camera topology를 구현하고 자동 테스트한다.
2. upper detector + SFace extractor를 연결하고 fake/model fixture 회귀를 통과시킨다.
3. user/workspace publisher, bottom-cam receiver를 켠 shadow Vision으로 실측한다.
4. 얼굴 등록·식별·session start/end를 실카메라로 검증한다.
5. microphone 없이 Voice failure 격리와 배포 설정을 완성한다.
6. microphone 연결·재기동 후 실제 Voice/OpenAI를 opt-in 검증한다.
7. WLED와 relay 분리 bench, 마지막으로 제한 Desk 이동을 검증한다.

## 운영 완료 증거

- 전체 backend test, compileall, frontend production build와 firmware build
- model file별 SHA-256·출처·license 기록과 load failure fail-closed test
- user/workspace RTSP online, bottom-cam online/reconnect와 frame freshness/CPU 기록
- 얼굴 등록 3표본 성공, 재시작 persistence, matched/unknown/multiple/vacant session 전이
- microphone 연결 후 `WAITING_WAKE → RECORDING → PROCESSING → SPEAKING → WAITING_WAKE`
- session 교대 중 이전 run/TTS/tool/follow-up이 새 사용자에게 넘어가지 않음
- height/MQTT/relay/Vision 불확실 때 이동 차단과 가능한 STOP 접수
- 운영 restart 뒤 health와 기능별 snapshot, rollback 절차 확인

자동 이동 기본값과 배포 예시는 `SMART_DESK_AUTOMATION__EXECUTE_AUTOMATIC_MOVEMENTS=false`다.
2026-08-18 현재 운영 호스트는 사용자 지시에 따라 이를 `true`로 명시했고, Arduino live height,
ESP32 `ready`, 실제 목표 이동 및 Vision 불확실성 STOP을 확인했다. relay 분리 bench 잔여 항목은
완료된 것으로 오인하지 않고 별도로 유지한다.
