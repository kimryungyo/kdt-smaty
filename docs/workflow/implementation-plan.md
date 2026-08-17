# 워크플로우 구현 계획

## 현재 구현과 목표 차이

| 영역 | 현재 | 목표 |
| --- | --- | --- |
| profile 선택 | React 값을 메인 사용자처럼 사용 | 첫 선택 화면 제거, 별도 설정 route에서만 사용 |
| Dashboard route | page enum으로 목록·설정·메인을 전환 | `/` 메인, `/settings/profiles...` 설정 분리 |
| 현재 사용자 | 서버 상태 없음 | 재실·얼굴 기반 등록/익명 `CurrentUserSnapshot` |
| 사용자 키 | 입력 후 폐기 | 사용처 없는 입력과 state 제거 |
| 얼굴 등록·식별 | placeholder·미구현 | background 식별과 등록 session |
| 자세·재실 | 미구현 | 안정화·freshness snapshot |
| 제어 방식 | 없음 | 서버 session `controlMode=AUTO/MANUAL` |
| 작업 모드 | profile 기본 높이·LED만 존재 | 기본+custom 활동별 앉기·서기 높이와 LED |
| 자동 높이 | 비활성 placeholder | profile 또는 익명 기본 75/110cm 적용과 빈자리 park |
| 자세별 버튼 | 브라우저 선택 profile 사용 | active 작업 모드와 현재 자세로 서버가 목표 선택 |
| 사용자 작업 모드 | 없음 | CRUD, session 선택과 LED·높이 적용 |
| Vision debug | placeholder | 실제 상태·preview 연결 |
| AI·Voice | 수동 STT→Responses/tool loop→TTS, 화면 응답 없음 | Agents SDK VoicePipeline과 동일 turn의 화면 응답 |
| 서비스 상태 | 전역 readiness와 선택 기능 조건부 생성 혼재 | Desk 이동 필수 조건과 WLED·Voice degraded 분리 |

현재 얼굴 감지로 특정 profile 화면을 자동으로 여는 코드는 없다. 목표에도 추가하지 않는다.

## 구현 순서

1. Voice를 Agents SDK VoicePipeline으로 교체하고 legacy gateway·수동 tool loop를 제거한다.
2. 서비스 lifecycle을 시작 필수·이동 필수·선택 기능으로 분류하고 전역 readiness 일괄 차단을 제거한다.
3. 사용자 키·profile별 자세 시간 입력을 제거하고 전체 자세 전환 확인 시간을 5초로 고정한다.
4. SQLite v3 `profile_modes` schema와 기본+custom 작업 모드 설정 CRUD를 추가한다.
5. 분리된 Vision snapshot과 등록·익명 `CurrentUserSnapshot` read-only API를 추가한다.
6. 상단 몸체/얼굴과 하단 하체를 결합하는 재실·자세 loop와 freshness를 구현한다.
7. 얼굴 임베딩 저장소, background 식별과 등록·익명 session 전이를 구현한다.
8. Dashboard를 `/` 메인과 `/settings/profiles...` 설정 route로 전면 개편하고 얼굴 등록을 연결한다.
9. Vision과 현재 사용자를 메인 Dashboard·debug 화면에 표시한다.
10. `AutomationService`에 `AUTO`/`MANUAL`, 최초 2초 지연, 빈자리 park와 명령 직렬화를 구현한다.
11. 등록 active 작업 모드·익명 기본 높이와 mode 선택·LED 적용을 연결한다.
12. 관측·차단과 control/activity mode 전이를 검증한 뒤 자세 기반 실제 자동 목표를 허용한다.
13. SDK 대화 session·Mem0·Desk function tool과 AI Dashboard 응답을 사용자 session에 연결한다.

## 필수 자동 검증

- 얼굴 없이도 단일 재실·자세 3초 뒤 익명 session이 생긴다.
- 최초 2초 지연 뒤 익명 앉음 75cm·섬 110cm 목표를 한 번만 만든다.
- 한 frame 얼굴 후보나 unknown으로 등록 사용자가 전환되지 않는다.
- 고품질 unknown 3초, A→B와 VACANT 전이가 새 session ID와 결정표를 따른다.
- AUTO에서 앉음→섬, 섬→앉음 fresh 자세 5초 뒤 목표를 한 번만 설정한다.
- 직접 목표·HOLD·사용자 STOP이 먼저 MANUAL로 전환하고 active 작업 모드는 유지한다.
- MANUAL은 명시적 AUTO 요청 전까지 유지된다.
- 작업 모드 선택은 control mode를 유지하고 다른 profile의 mode를 거부한다.
- AUTO mode 선택은 fresh 자세로 목표를 재평가하고 MANUAL에서는 책상을 움직이지 않는다.
- 설정값 수정은 active session에 즉시 반영되지 않고 다음 선택부터 적용된다.
- 같은 session에서 AUTO를 다시 선택하면 이전 자세 후보를 버리고 fresh 자세를 5초 확인한다.
- 다중·count 불일치·Vision 만료는 AUTO만, 센서·릴레이 오류는 모든 이동을 STOP한다.
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
