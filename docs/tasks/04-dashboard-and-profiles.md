# 04. 대시보드와 프로필

## 목표

React에서 현재 책상 상태를 조회하고 수동 이동·목표 설정을 수행하며 사용자별
높이와 LED 설정을 저장한다.

## 선행 조건

- [책상 제어](03-desk-control.md) 완료

## 작업 목록

- [ ] `ProfileRepository`와 프로필 Pydantic 모델을 구현한다.
- [ ] 파일 쓰기 중 손상을 막는 임시 파일 교체 방식으로 저장한다.
- [ ] `DashboardService`가 Desk와 프로필 snapshot을 화면용 응답으로 조합한다.
- [ ] 상태 조회, 목표 설정, HOLD, STOP과 프로필 CRUD FastAPI route를 작성한다.
- [ ] route 내부에서 `get_desk()`·`get_dashboard()`를 조회하고 정책은 서비스에 둔다.
- [ ] React API client와 책상 상태·수동 제어·프로필 화면을 구현한다.
- [ ] 버튼을 누르는 동안 HOLD를 갱신하고 놓음·화면 이탈 때 STOP을 요청한다.

## 테스트

- [ ] HTTP 요청·응답 모델과 오류 상태를 계약 테스트한다.
- [ ] 프로필 저장·교체·손상 파일 처리를 검증한다.
- [ ] 브라우저 HOLD 요청이 끊겨도 서버 watchdog이 STOP하는지 확인한다.
- [ ] React production build와 FastAPI SPA 제공을 검증한다.

## 완료 조건

브라우저에서 실제 높이와 Desk 상태를 확인하고, 안전한 수동 조절·목표 설정과
프로필 저장을 끝까지 수행할 수 있다.
