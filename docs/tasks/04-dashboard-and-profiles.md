# 04. 대시보드와 프로필

## 목표

React에서 현재 책상 상태를 조회하고 수동 이동·목표 설정을 수행하며 사용자별
높이와 LED 설정을 저장한다.

## 선행 조건

- [책상 제어](03-desk-control.md) 완료

## 작업 목록

- [x] `StorageSettings`와 SQLite version 1 migration을 구현한다.
- [x] `profiles` 테이블과 프로필 Pydantic 모델을 구현한다.
- [x] `ProfileRepository` CRUD와 container·lifecycle 연결을 구현한다.
- [ ] `DashboardService`가 Desk와 프로필 snapshot을 화면용 응답으로 조합한다.
- [ ] 상태 조회, 목표 설정, HOLD, STOP과 프로필 CRUD FastAPI route를 작성한다.
- [ ] route 내부에서 `get_desk()`·`get_dashboard()`를 조회하고 정책은 서비스에 둔다.
- [ ] React API client와 책상 상태·수동 제어·프로필 화면을 구현한다.
- [ ] 버튼을 누르는 동안 HOLD를 갱신하고 놓음·화면 이탈 때 STOP을 요청한다.

## 테스트

- [x] SQLite migration·transaction·손상 보존과 프로필 CRUD를 검증한다.
- [x] SQLite 시작 실패와 lifecycle 순서를 통합 검증한다.
- [ ] HTTP 요청·응답 모델과 오류 상태를 계약 테스트한다.
- [ ] 브라우저 HOLD 요청이 끊겨도 서버 watchdog이 STOP하는지 확인한다.
- [ ] React production build와 FastAPI SPA 제공을 검증한다.

## 완료 조건

브라우저에서 실제 높이와 Desk 상태를 확인하고, 안전한 수동 조절·목표 설정과
프로필 저장을 끝까지 수행할 수 있다.

## 현재 진행 상태

Dashboard 구현에 앞서 `data/smart_desk.db`를 사용하는 SQLite 저장 기반과
`ProfileRepository`를 완료했다. DB 작업은 event loop 밖에서 직렬 실행하며,
손상·지원하지 않는 version·schema 불일치 DB를 자동 초기화하지 않는다.

`DashboardService`, FastAPI 프로필·제어 route와 React 화면은 아직 구현하지 않았으며
이 작업의 다음 단계로 남아 있다. AI 스피커 대화 저장은 별도 schema migration에서
설계한다.
