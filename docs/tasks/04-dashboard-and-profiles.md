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
- [x] `DashboardService`가 Desk 상태를 화면용 응답으로 변환하고 ProfileRepository CRUD를 위임한다.
- [x] 상태 조회, 목표 설정, HOLD, STOP과 프로필 CRUD FastAPI route를 작성한다.
- [x] route는 `get_dashboard()`로 조립된 service만 조회하고 정책은 service·DeskController에 둔다.
- [x] React API client와 책상 상태·수동 제어·프로필 화면을 구현한다.
- [x] 버튼을 누르는 동안 HOLD를 갱신하고 놓음·화면 이탈 때 STOP을 요청한다.

## 테스트

- [x] SQLite migration·transaction·손상 보존과 프로필 CRUD를 검증한다.
- [x] SQLite 시작 실패와 lifecycle 순서를 통합 검증한다.
- [x] HTTP 요청·응답 모델과 오류 상태를 계약 테스트한다.
- [ ] 브라우저 HOLD 요청이 끊겨도 서버 watchdog이 STOP하는지 확인한다.
- [x] React production build와 FastAPI SPA 제공을 검증한다.

## 완료 조건

브라우저에서 실제 높이와 Desk 상태를 확인하고, 안전한 수동 조절·목표 설정과
프로필 저장을 끝까지 수행할 수 있다.

## 현재 진행 상태

`DashboardService`, FastAPI `/api` route와 React 페이지 흐름을 구현했다. 상태 polling은
SQLite profile 목록과 분리하며, 수동 제어의 안전 판단과 watchdog은 계속
`DeskController`가 단독 소유한다. 실제 relay를 사용한 브라우저 HOLD 단절 검증만
실물 연결 시 별도로 수행한다.
