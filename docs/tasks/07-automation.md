# 07. 자동화와 외부 장치

## 목표

Vision, 현재 사용자, 프로필과 Desk snapshot을 조합해 안전한 목표 높이를 결정한다.
서버가 `AUTO`/`MANUAL` 모드를 소유하고 사용자 preset과 직접 제어가 자동화보다 우선하도록
한다. WLED는 필수 서비스로 연결한다.

## 선행 조건

- [대시보드와 프로필](04-dashboard-and-profiles.md) 완료
- [Vision 파이프라인](06-vision-pipeline.md) 완료

## 작업 목록

- [ ] `AutomationService`의 `AUTO`/`MANUAL` mode, 상태와 전환 이유 snapshot을 정의한다.
- [ ] 현재 사용자가 없으면 mode를 적용하지 않고 책상을 STOP한다.
- [ ] 얼굴로 사용자가 확정되면 `AUTO`로 새 재실 session을 시작한다.
- [ ] 등록 사용자와 안정화된 앉음·섬 상태에 따른 profile 목표 선택을 구현한다.
- [ ] 미등록·불확실·다중 사용자·오래된 Vision에서는 자동 이동을 금지한다.
- [ ] preset, 직접 목표, HOLD와 STOP에서 먼저 `MANUAL`로 전환하고 자동 의도를 무효화한다.
- [ ] 같은 재실 session에서 `MANUAL`은 명시적 자동 모드 요청 전까지 유지한다.
- [ ] profile별 사용자 높이 preset CRUD와 현재 사용자 소유권 검증을 구현한다.
- [ ] profile의 앉은/선 높이는 복제 저장하지 않고 `POSTURE` preset으로 합성해 사용자
  preset과 한 목록으로 제공한다.
- [ ] preset 실행을 `MANUAL` 전환, 기존 이동 STOP, 새 목표 설정 순서로 직렬화한다.
- [ ] 같은 목표의 반복 설정을 억제하고 상태 변경 이력을 로그로 남긴다.
- [x] WLED HTTP 어댑터를 구현했다. 필수 lifecycle 전환과 색상 자동화 정책은 남아 있다.
- [ ] 자동화 시작·중지와 `get_automation()` singleton 접근을 연결한다.

## 테스트

- [ ] 사용자 인식, 자세 전환, 퇴장과 Vision 만료 시나리오를 검증한다.
- [ ] AUTO에서 앉음→섬과 섬→앉음 안정화 후 profile 높이를 한 번만 설정하는지 검증한다.
- [ ] preset·직접 목표·HOLD·STOP이 MANUAL로 전환되고 자동 목표가 덮어쓰지 않는지 확인한다.
- [ ] preset 실행 실패 후에도 MANUAL이 유지되는지 확인한다.
- [ ] 현재 사용자와 다른 profile의 preset ID를 거부하는지 확인한다.
- [ ] 앉은/선 높이 변경이 중복 row 없이 합성 preset에 즉시 반영되는지 확인한다.
- [ ] 명시적 AUTO 복귀가 기존 자세 후보를 버리고 안정화를 다시 시작하는지 확인한다.
- [ ] 모든 불확실 입력에서 책상이 움직이지 않거나 진행 중 이동을 STOP하는지 검증한다.

## 완료 조건

등록 사용자의 AUTO 자세 전환과 MANUAL preset 시나리오가 동작하고, 이탈·불확실성·수동
개입·센서 오류에서는 일관되게 안전 정지한다.
