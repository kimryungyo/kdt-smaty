# 워크플로우 구현 계획

## 현재 구현과 목표 차이

| 영역 | 현재 | 목표 |
| --- | --- | --- |
| profile 선택 | React 값을 메인 사용자처럼 사용 | 설정 대상에만 사용 |
| 현재 사용자 | 서버 상태 없음 | 얼굴 기반 `CurrentUserSnapshot` |
| 사용자 키 | 입력 후 폐기 | profile 저장 |
| 얼굴 등록·식별 | placeholder·미구현 | background 식별과 등록 session |
| 자세·재실 | 미구현 | 안정화·freshness snapshot |
| 제어 모드 | 없음 | 서버 session `AUTO`/`MANUAL` |
| 자동 높이 | 비활성 placeholder | 자세별 profile 높이 적용 |
| 자세별 버튼 | 브라우저 선택 profile 사용 | 현재 사용자 합성 preset |
| 사용자 preset | 없음 | CRUD, 합성 조회와 MANUAL 실행 |
| Vision debug | placeholder | 실제 상태·preview 연결 |
| AI Dashboard 응답 | 없음 | 별도 화면 응답 연결 |
| WLED·Voice 시작 | 설정에 따른 조건부 생성 | 필수 lifecycle 서비스 |

현재 얼굴 감지로 특정 profile 화면을 자동으로 여는 코드는 없다. 목표에도 추가하지 않는다.

## 구현 순서

1. WLED와 Voice의 조건부 생성을 제거하고 필수 시작·오류 정책을 검증한다.
2. profile schema에 사용자 키와 자세 유지 시간을 추가한다.
3. `desk_presets` schema와 profile 설정 CRUD를 추가한다.
4. `CurrentUserSnapshot`과 read-only API를 추가한다.
5. 최신 frame 기반 재실·얼굴·자세 loop와 freshness를 구현한다.
6. 얼굴 임베딩 저장소, background 식별과 등록 session을 구현한다.
7. Dashboard profile 설정과 얼굴 등록 화면을 연결한다.
8. Vision과 현재 사용자를 Dashboard·debug 화면에 표시한다.
9. `AutomationService`에 `AUTO`/`MANUAL` mode와 상위 명령 직렬화를 구현한다.
10. 자세별 높이와 사용자 preset 합성 조회·실행을 연결한다.
11. 관측·차단과 mode 전이를 검증한 뒤 자세 기반 실제 자동 목표를 허용한다.
12. Voice 사용자 문맥과 AI Dashboard 응답을 연결한다.

## 필수 자동 검증

- 한 frame 얼굴 후보로 현재 사용자가 확정되지 않는다.
- 미등록·다중·오래된 frame·이탈에서 profile ID가 남지 않는다.
- AUTO에서 앉음→섬, 섬→앉음 안정화 후 목표를 한 번만 설정한다.
- preset·직접 목표·HOLD·STOP이 먼저 MANUAL로 전환한다.
- MANUAL은 명시적 AUTO 요청 전까지 유지된다.
- preset 실행 실패 후에도 MANUAL을 유지한다.
- 현재 사용자와 다른 profile의 preset을 거부한다.
- 앉은/선 높이 변경이 중복 row 없이 합성 목록에 반영된다.
- AUTO 복귀 시 이전 자세 후보를 버리고 다시 안정화한다.
- 이탈·Vision 만료·센서·릴레이 오류에서 진행 중 이동을 STOP한다.
- 추론 중에도 health와 STOP 응답이 지연되지 않는다.

## 실물 검증

자동 테스트 후 제한된 범위에서 다음을 검증한다.

- 사용자가 일어선 자세를 유지하면 선 높이로 이동한다.
- 다시 앉으면 앉은 높이로 이동한다.
- preset 클릭 시 MANUAL로 바뀌고 해당 높이로 이동한다.
- MANUAL에서 자세가 바뀌어도 자동 목표가 덮어쓰지 않는다.
- 사용자가 이탈하면 AUTO와 MANUAL 모두 정지한다.
- camera, 높이 센서, MQTT 또는 ESP32 단절에서 fail-closed STOP한다.
