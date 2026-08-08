# Dashboard UI 후속 검토 항목

기존 `/srv/smart-desk/dashboard/static/` 화면을 React로 재현하면서 확인한 항목이다.
이 문서는 UI 모양과 현재 구현된 API 연결을 분리해 후속 검토를 돕는다.

## 확정·반영된 높이 계약

- 사용자 제어와 목표 높이는 **75–115cm**다.
- 프로필의 앉은·선 높이와 Dashboard 목표 입력의 `min`/`max`, 안내 문구는 이 범위를
  사용한다.
- 기존 화면의 125cm 표기는 사용하지 않는다. 물리·측정 범위와 사용자 제어 범위는
  별도이므로 Dashboard가 115cm를 넘는 값을 전송하지 않는다.

## 현재 UI에서 기능 연결하지 않은 기존 영역

| 기존 UI 영역 | 현재 상태 | 후속 검토가 필요한 이유 |
| --- | --- | --- |
| Vision 자세·얼굴 등록·프로필 제안 | UI placeholder | Vision module/API가 아직 없다. |
| 자동 높이 조절 toggle·5초 유지 시간 | 비활성 UI | 자동화 정책과 profile field가 현재 없다. |
| WLED 색상 적용·자동 색상 새로고침 | 비활성 UI | `ledColor` 저장 외 WLED command API가 없다. |
| 서버 active profile | 화면 내부 선택 상태 | SQLite schema에 active profile field가 없다. |
| 사용자 키 | 화면 입력만 존재 | 현재 `Profile`에는 키 영속 field가 없다. |
| Vision 디버그 카메라·상태 | placeholder | 카메라와 Vision 상태 공급자가 없다. |

## 현재 연결된 기능

- profile list/create/update/delete
- profile sitting/standing height 저장
- Desk snapshot polling
- manual HOLD/STOP 및 브라우저 종료 경로 STOP
- target SET/CANCEL

후속 구현은 위 미연결 UI의 모양을 임의로 바꾸지 않고, 해당 도메인 모델·API·안전 정책이
확정된 뒤에만 click 동작과 실데이터를 연결한다.
