# 워크플로우 구현 계획

## 현재 구현과 남은 차이

| 영역 | 현재 코드·자동 검증 | 남은 목표/제한 |
| --- | --- | --- |
| Dashboard/profile | `/` 메인과 설정 route, 편집 profile/current user 분리 | Vision preview·일부 debug 근거 |
| 현재 사용자·얼굴 | fake-driven identity/session, v4 embedding repository와 API | production detector/alignment/embedding model, 실제 등록 |
| Vision | 하단 ONNX pose·freshness/stabilization/API, sample 회귀 | 실제 user/posture WebRTC·threshold/CPU 보정 |
| mode·자동화 | activity mode CRUD, `AUTO`/`MANUAL`, generation/blocked policy와 shadow AUTO | 실제 Vision·WLED·Desk가 연결된 안전한 end-to-end 이동 |
| AI·Voice | Agents SDK `VoicePipeline` 단일 경로, session context/tool/turn store와 Dashboard polling | 실제 microphone/speaker/OpenAI·Mem0 운영 검증, 일부 preview/debug UX |
| service 상태 | 이동 필수 조건과 선택 WLED/Voice degraded 분리 | 실제 장치 단절·복구 측정 |

현재 얼굴 감지로 특정 profile 설정 화면을 자동으로 여는 코드는 없고 추가하지 않는다.

## 잔여 구현·검증 순서

1. production 상단 몸체/얼굴 detector와 embedding model을 선정하고 model별 binary
   provisioning, SHA-256와 라이선스를 검토한다. 현재 하단 Ultralytics ONNX는 별도로
   AGPL-3.0/Enterprise 조건을 확인한다.
2. 실제 user/posture WebRTC camera를 연결해 count, freshness, threshold와 Pi CPU를 보정한다.
3. 이 Vision 입력으로 face enrollment와 등록/익명 session 전이를 실측한다.
4. 자동화는 shadow 상태 검증 후 제한된 범위에서 WLED, Arduino, Wi-Fi/MQTT ESP32와 Desk를
   연결해 STOP·복구를 먼저 확인하고 실제 이동을 검증한다.
5. 실제 microphone/speaker/OpenAI 계정과 Mem0를 opt-in으로 검증하고 Voice/turn UX의 남은
   preview/debug 항목을 정리한다.
6. Task 09 evidence matrix에 환경, firmware/model hash, 측정과 미완료 항목을 누적한다.

## 필수 자동 검증

- 얼굴 없이도 단일 재실·자세 3초 뒤 익명 session이 생긴다.
- stable 자세 뒤 2초 지연 뒤 익명 앉음 75cm·섬 110cm 목표를 한 번만 만든다.
- 한 frame 얼굴 후보나 unknown으로 등록 사용자가 전환되지 않는다.
- 고품질 unknown 3초, A→B와 VACANT 전이가 새 session ID와 결정표를 따른다.
- AUTO에서 앉음→섬, 섬→앉음 fresh 자세 2초 뒤 목표를 한 번만 설정한다.
- 직접 목표·HOLD·사용자 STOP이 먼저 MANUAL로 전환하고 active 작업 모드는 유지한다.
- MANUAL은 명시적 AUTO 요청 전까지 유지된다.
- 작업 모드 선택은 control mode를 유지하고 다른 profile의 mode를 거부한다.
- AUTO mode 선택은 fresh 자세로 목표를 재평가하고 MANUAL에서는 책상을 움직이지 않는다.
- 설정값 수정은 active session에 즉시 반영되지 않고 다음 선택부터 적용된다.
- 같은 session에서 AUTO를 다시 선택하면 이전 자세 후보를 버리고 fresh 자세를 2초 확인한다.
- 상단 다중·Vision 만료는 AUTO만, 센서·릴레이 오류는 모든 이동을 STOP한다.
- fresh VACANT 30초 뒤에만 75cm park하고 사람 후보·수동 명령에서 즉시 취소한다.
- stale session control/activity mode 요청은 `409`, HOLD·직접 높이·STOP은 session 없이 처리한다.
- 추론 중에도 health와 STOP 응답이 지연되지 않는다.
- Wake Word 16kHz와 VoicePipeline 24kHz 경로, 명시적 VAD, 조건부 follow-up을 검증한다.
- 사용자 교대가 이전 Agent run·미실행 tool·TTS·follow-up을 취소하고 SDK session을 폐기한다.
- session 교대·종료 시 이전 AI 상세 응답을 즉시 숨기고 늦은 turn도 다시 표시하지 않는다.
- profile 삭제 전에 장기 기억을 삭제하며 실패 시 profile DB를 보존한다.

## 실물 검증

자동 테스트 후 제한된 범위에서 다음을 검증한다.

- 사용자가 일어선 자세를 유지하면 선 높이로 이동한다.
- 다시 앉으면 앉은 높이로 이동한다.
- AUTO에서 작업 모드를 바꾸면 자세에 맞는 새 높이를 안전하게 평가한다.
- MANUAL에서 작업 모드를 바꾸면 LED만 바뀌고 책상은 이동하지 않는다.
- MANUAL에서 자세가 바뀌어도 자동 목표가 덮어쓰지 않는다.
- 사용자가 이탈하면 session을 끝내고 30초 fresh VACANT 뒤 75cm park한다.
- 익명 사용자가 앉고 서면 75/110cm로 이동하고, 얼굴 식별 뒤 profile 목표로 교체한다.
- camera, 높이 센서, MQTT 또는 ESP32 단절에서 fail-closed STOP한다.
