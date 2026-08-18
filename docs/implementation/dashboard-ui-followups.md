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
| 자동 높이 조절 toggle·2초 유지 시간 | 비활성 UI | 2초는 전체 고정 자동화 설정이며 profile 입력 field로 만들지 않는다. |
| 작업 모드 | 앉은/선 버튼만 존재 | 기본·custom mode별 앉기/서기 높이와 LED를 합성하고 session 선택 계약이 필요하다. |
| WLED 색상 적용·자동 색상 새로고침 | 비활성 UI | `ledColor` 저장 외 WLED command API가 없다. |
| 서버 현재 사용자 | 화면에서 선택한 profile을 메인 사용자처럼 표시 | 현재 사용자는 Dashboard 선택이 아니라 서버의 안정화된 얼굴 식별로만 결정해야 한다. |
| 사용자 키 | 화면 입력만 존재 | 사용처가 없으므로 새 Dashboard에서 입력과 state를 제거한다. |
| Vision 디버그 카메라·상태 | placeholder | 카메라와 Vision 상태 공급자가 없다. |

## 현재 연결된 기능

- profile list/create/update/delete
- profile sitting/standing height 저장
- Desk snapshot polling
- manual HOLD/STOP 및 브라우저 종료 경로 STOP
- target SET/CANCEL

후속 Dashboard는 현재 화면을 점진 수정하지 않고 전면 개편한다. `/`에는 현재 사용자·상태,
제어 방식·작업 모드·직접 높이와 AI 응답을 두고, 우측 상단 설정 버튼에서 별도 profile 설정 route로
이동한다. 기존 API client와 HOLD/STOP 안전 동작은 재사용하되 첫 profile 선택 화면과
`selectedProfile` 기반 메인 제어는 제거한다.
