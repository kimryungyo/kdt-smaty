# 운영 runbook

2026-08-19 Raspberry Pi 배포, AI 스피커/Vision 디버그 연결과 최신 Voice 장애 수정의
현재 인수인계는 [Raspberry Pi 서비스 인수인계](2026-08-19-service-handoff.md)를 먼저 본다.

이 문서는 현재 `main` 코드 기준의 안전한 시작·상태 확인·복구 범위를 정리한다. 자동
테스트 통과는 실물 장치 동작 승인이 아니다. 실제 이동은 [Task 09](../tasks/09-system-validation.md)의
안전 순서와 현장 책임자 승인 뒤에만 검증한다.

얼굴·camera·Voice를 실제 사용자 흐름으로 만드는 현재 gap과 구현 순서는
[Task 10](../tasks/10-operational-readiness.md)을 따른다.

## 운영 구성요소와 시작 순서

운영 relay 경로는 **ESP32 Wi-Fi/MQTT → EMQX → FastAPI `RelayClient`**다. MQTT→USB-serial
bridge는 운영 구성요소가 아니며 시작·복구 절차에 포함하지 않는다. `feature/serial-esp32`은
비운영 보존 브랜치다. Arduino USB serial은 ESP32 제어 transport가 아니라 높이 센서 입력이다.

1. EMQX를 시작하고, 사용할 경우 호스트 MediaMTX를 시작한다.
2. 설정·필수 파일·데이터 디렉터리를 확인한다.
3. camera publisher/receiver와 선택 Vision model을 필요한 역할만 켠다.
4. FastAPI를 worker 하나로 시작한다.
5. `/health/live`, `/health/ready`와 기능별 snapshot을 확인한다. Desk 이동은 마지막에 별도 안전 점검을 통과해야 한다.

```bash
.venv/bin/uvicorn smart_desk.main:app --host 0.0.0.0 --port 9090 --workers 1
curl http://127.0.0.1:9090/health/live
curl http://127.0.0.1:9090/health/ready
```

실제 책상이 연결된 프로세스에는 `--reload`나 worker 둘 이상을 사용하지 않는다.

## 설정과 필수 파일

`.env.example`을 기준으로 환경변수를 준비한다. `.env`보다 같은 이름의 환경변수가 우선한다.

| 범주 | 필수/선택 | 확인 대상 |
| --- | --- | --- |
| 시작 필수 | 필수 | `SMART_DESK_STORAGE__DATABASE_PATH`, SQLite 쓰기 권한, 유효한 기본 설정 |
| 이동 필수 | 실제 이동 때 필수 | 지속 중인 MQTT 연결, Arduino `SMART_DESK_SERIAL__PORT`, ESP32 Wi-Fi/MQTT 상태와 fresh height |
| media | 역할별 선택 | FFmpeg, MediaMTX RTSP path, 안정된 `/dev/v4l/by-id/...` camera path |
| 하단 Vision | 선택 | `SMART_DESK_VISION__LOWER_POSE_MODEL_PATH`와 ONNX 파일 |
| 얼굴 Vision | 선택 | local YuNet/SFace paths, OpenCV Zoo license review and operator SHA-256 record |
| WLED | 선택 | `SMART_DESK_WLED__ENABLED`, base URL |
| Voice/AI | 선택 | `SMART_DESK_VOICE__ENABLED`, OpenAI API key, wake-word/effect 파일, microphone/speaker 권한 |
| memory | 선택 | `SMART_DESK_PROFILE_MEMORY__ENABLED`, `data/mem0` 권한 |

하단 ONNX binary는 저장소에 포함되지 않는다. operator가 제공 경로, SHA-256, 모델 버전과
Ultralytics 라이선스 적합성을 기록해야 한다. production 상단 몸체/얼굴 detector와 embedding
model은 아직 선정·provisioning되지 않았다.

## 상태 확인

`/health/live`는 process 생존, `/health/ready`는 lifecycle 준비 상태다. 둘 중 하나가
정상이어도 실제 이동에는 height·MQTT·fresh ready relay가 각각 필요하다.

```bash
curl http://127.0.0.1:9090/api/status
curl http://127.0.0.1:9090/api/vision/status
curl http://127.0.0.1:9090/api/current-user
curl http://127.0.0.1:9090/api/automation/status
curl http://127.0.0.1:9090/api/voice/status
curl http://127.0.0.1:9090/api/assistant/latest
```

- Desk 이동 전에는 status의 height freshness, MQTT 연결과 ESP32 relay ready/fresh 상태를 함께 본다.
- QoS 1 publish의 내부 timeout보다 바깥 relay ack timeout이 먼저 끝나면 정상 WAKE도 취소된다.
  기본값은 MQTT 5초, relay ack 6초이며 운영 환경에서도 ack 값을 MQTT 값보다 크게 유지한다.
- 실제 자동 이동은 위 조건을 확인한 환경에서만
  `SMART_DESK_AUTOMATION__EXECUTE_AUTOMATIC_MOVEMENTS=true`로 활성화한다.
- Vision `UNKNOWN`, 상단 `MULTIPLE` 또는 stale은 AUTO/PARK를 차단한다. 상단 detector와 실제 화각이
  없으므로 production 자동 이동의 근거로 사용하지 않는다.
- Voice/WLED가 `DISABLED` 또는 degraded여도 profile·상태 조회와 Desk 핵심 경로는 별도 상태로
  유지된다. Voice `enabled=true`의 static configuration/model 오류는 조용히 무시하지 않는다.
- Assistant latest는 현재 session의 최신 turn 하나만 표시하는 polling endpoint다.

## 단절·복구 확인

실제 이동 없이 먼저 할 수 있는 확인만 여기에 둔다.

1. WLED와 Voice를 끈 상태로 서버를 시작해 두 기능이 `DISABLED`이고 health/profile 조회가
   유지되는지 확인한다. 하단 model path가 없을 때 Vision은 `MODEL_UNAVAILABLE`로 AUTO를
   차단하며, 이를 `DISABLED` Voice/WLED와 혼동하지 않는다.
2. receive-only camera의 RTSP source가 없을 때 Vision snapshot이 stale/unknown으로 바뀌고
   AUTO가 blocked로 남는지 확인한다. 실제 camera 탈착은 Task 09 현장 항목이다.
3. EMQX가 늦게 시작되거나 runtime MQTT가 단절된 동안에도 profile과 상태 조회가 가능하고
   이동 요청은 blocked되는지 확인한다. MQTT runner는 연결·전체 구독 완료까지 재시도하며,
   실제 broker/Wi-Fi 단절에서 ESP32 GPIO STOP을 증명한 것은 아니다.
4. 선택 Voice를 활성화한 경우에만 audio/OpenAI 오류가 Voice snapshot에 격리되는지 확인한다.
   실제 microphone/speaker/OpenAI 계정 검증은 opt-in 환경에서 수행한다.

복구 뒤에는 `/health/ready`뿐 아니라 관련 snapshot의 freshness와 reason code를 다시 읽는다.
실제 책상이 연결된 상태에서 의도적인 broker/camera/power 단절이나 책상 이동 명령은 이
runbook의 실행 절차가 아니라 Task 09 안전 검증 계획으로 다룬다.

## 데이터와 복구 주의

SQLite DB(`SMART_DESK_STORAGE__DATABASE_PATH`)와 선택 Mem0 data path는 서버가 정상 종료된
상태에서 일관된 사본을 만든다. 실행 중 파일 복사, schema file 수동 편집, 임의의 DB/embedding/
memory 행 삭제는 피한다. migration 실패나 profile 삭제 중 memory 삭제 실패는 DB를 덮어쓰지
말고 로그와 기존 파일을 보존한 채 원인을 확인한다. 복원은 원본을 보존한 별도 환경에서 먼저
읽기 검증한 사본으로 수행하며, 복원 후 현재 사용자·자동화 state는 fresh 관측으로 다시 만든다.

## 검증 증거

| 범위 | 자동 증거 | 아직 필요한 증거 |
| --- | --- | --- |
| backend 전체 | 2026-08-17: `445 passed, 2 skipped in 5.62s`; compileall, diff check 통과 | 환경별 재실행 로그 |
| 하단 pose | fake adapter와 sample 26/26 (`sitting` 10, fullbody 6, standing 4, empty 6) | actual WebRTC/CPU, model provisioning/license record |
| lifecycle/API/automation | fake adapter, repository, API, session/turn tests | EMQX/Arduino/ESP32/WLED 실제 단절·복구와 제한 이동 |
| Voice/AI | Agents SDK/session/tool/turn contract tests | microphone/speaker/OpenAI/Mem0 live operation |
| frontend | 2026-08-17: `npm run build`로 TypeScript와 Vite production build 통과 | 실제 브라우저·장치 UX |
| firmware | relay native policy 6/6, ESP32-C3 Wi-Fi/MQTT firmware와 Arduino segment-reader build 통과 | upload, broker/배선/relay timing 실측 |

## 현재 제한과 실물 체크리스트

- [ ] production 상단 몸체/얼굴 detector와 embedding model을 선택·배포한다.
- [ ] user/posture WebRTC camera의 threshold와 CPU를 현장 보정한다.
- [ ] 실제 face enrollment와 등록/익명 session 전이를 검증한다.
- [ ] Arduino, Wi-Fi/MQTT ESP32, WLED와 제한된 Desk 이동·STOP·복구를 검증한다.
- [ ] logic analyzer로 relay timing과 independent timeout STOP을 측정한다.
- [ ] 실제 microphone/speaker/OpenAI/Mem0를 opt-in으로 검증한다.

`~/sitting`은 하단 인식 알고리즘·ONNX·sample 회귀의 참고 원본일 뿐이다. 그 프로젝트의
web server, WHEP, aiortc, static/app은 이 제품의 운영 구성요소가 아니며 복사·배포하지 않는다.
