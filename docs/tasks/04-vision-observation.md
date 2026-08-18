# 04. Vision 관측

## 사용자 결과

서버는 Dashboard가 열려 있지 않아도 두 카메라의 최신 프레임을 계속 처리한다. 상단 카메라는
책상 영역의 인원수와 재실을, 하단 카메라는 선택된 한 사람의 앉음·섬 상태만 제공한다. 카메라나 모델이 불확실하면 오래된 결과 대신
명확한 `UNKNOWN`과 차단 근거를 반환한다.

이 작업은 얼굴로 profile을 식별하지 않는다. 다만 상단 재실 count에 필요한 얼굴 검출 결과는
한 번만 만들고 공유한다. [얼굴 식별과 사용자 세션](05-face-identity-session.md)은 이 fresh
얼굴 box를 받아 정렬·품질 검사·embedding·profile 비교를 수행한다.

## 현재 상태

> 재실은 상단 단독, 자세는 하단 최고 score pose라는 정책으로 구현됐다. 하단의 다중 pose는
> 진단용 count로만 노출하며 재실·자동화 차단에 사용하지 않는다.

- 사용자·자세 카메라용 WebRTC 최신 frame source가 lifecycle에 등록된다.
- 각 source는 queue 없이 최신 `(frame, captured_at)` 하나를 유지하고 재연결한다.
- `VisionService`가 container의 user(상단)·posture(하단) source를 소유하고 최신 frame만
  소비한다. `workspace`는 Vision 자세 입력으로 대체하지 않는다.
- `/api/vision/status`는 fail-closed raw/stable snapshot과 camera freshness를 제공한다.
- 기본 `NoopVisionDetector`는 실제 관측인 척하지 않고 `MODEL_UNAVAILABLE`를 반환한다.
- YuNet이 provision되면 한 번의 상단 inference에서 box·5 landmarks를 만들며, face row 수를
  upper count로 사용한다. landmark와 confidence는 내부 face 경계에만 남고 일반 API에는 노출하지 않는다.
- 선택 하단 detector는 OpenCV DNN YOLO pose ONNX의 `(1,300,57)` end-to-end NMS 출력을 검증한다.
  상단 재실·얼굴과 하단 자세 모두 각 2Hz(0.5초 이상 간격)에서 최신 frame 하나만 사용한다.
  하단은 threshold를 통과한 사람 중 confidence가 가장 높은 한 명만 골라 자세를 계산하며, 하단의
  사람 수와 다중 검출은 재실·자동화 차단 근거로 사용하지 않는다.
- Vision은 두 카메라의 전체 화각을 그대로 추론한다. 상단에서 두 명 이상이 3초 안정화 창의
  다수결을 통과하면 `MULTIPLE`/`MULTIPLE_PEOPLE`로 AUTO를 차단한다. 하단의 주변 통행은 최고 점수 사람 선택에만
  영향을 줄 수 있으며 ROI crop·mask·좌표 필터는 구현하거나 적용하지 않는다.
  WHEP/aiortc, FastAPI/web UI, MJPEG/JPEG polling이나
  일반 preview endpoint는 구현하거나 복사하지 않았다.
- `/api/vision/debug`는 마지막 성공 추론의 box·face·pose 관절 geometry만,
  `/api/vision/debug/frame/{upper|lower}`는 같은 시점의 메모리 JPEG 한 장만 반환한다.
  Dashboard의 `/debug/vision`은 이를 2Hz로 갱신해 캔버스로 오버레이한다. 이는 스트리밍·녹화가
  아니며 raw frame을 DB나 일반 상태 API에 노출하지 않는다.

## 첫 구현의 관측 범위

첫 버전은 범용 다중 사람 추적이나 카메라 간 Re-ID를 목표로 하지 않는다. 상단 전체 화각의
사람 count가 재실의 유일한 기준이다. 상단 한 명과 fresh한 하단 최고 점수 pose가 있으면 정상
자동화 후보로 보며, 상단 얼굴은 신원 확인 전용이라 사람 count에 더하지 않는다.

- 상단 전체 화각에서 두 명 이상이 검출됨
- 상단 재실 또는 하단 최고 점수 자세의 관측 시각이 허용 범위를 벗어남
- 하단에 threshold를 통과한 pose가 없어 자세를 계산할 수 없음
- frame, detector 결과 또는 model 상태가 만료됨

실제 설치에서 두 카메라의 시야가 충분히 겹치지 않는다면 “동일 인물”을 추정하지 않고
자동화 차단 조건과 필요한 추가 추적 작업을 문서화한다.

## 공개 관측 모델

task 01에서 확정한 이름으로 Vision API는 최소한 다음을 분리해 제공한다.

| 정보 | 예시 내용 |
| --- | --- |
| 카메라 | 연결 상태, 마지막 frame 시각, frame age와 오류 |
| 인원수 | 상단의 raw count와 안정화 count; 하단 count는 진단용 검출 결과일 뿐 정책 입력이 아님 |
| 재실 | 상단 count만으로 만든 `PRESENT_SINGLE`, `VACANT`, `MULTIPLE`, `UNKNOWN`, 관측·만료 시각 |
| 자세 | `SITTING`, `STANDING`, `UNKNOWN`, 후보와 유지 시간 |
| 결합 상태 | 상단 재실·하단 자세의 시각 일치와 자동화 사용 가능 여부 |

내부 freshness 판단에는 monotonic clock을 사용하고, API에는 브라우저가 해석할 수 있는 wall
clock 시각 또는 age를 일관되게 제공한다. 프로세스 재시작 전의 monotonic 값을 저장하거나
API로 노출하지 않는다.

## 구현 단계

실물 하단 카메라가 없어도 snapshot 모델, detector adapter, fake frame 기반 안정화·freshness,
lifecycle과 API를 먼저 구현한다. 모델 선택의 최종 확인과 threshold 보정은
카메라 연결 뒤 완료하며, 이 실물 항목 때문에 task 전체 착수를 막지 않는다.

### 입력과 전처리

- [x] 두 `WebRtcFrameSource`를 container에서 Vision service에 주입할 수 있게 보관한다.
- [ ] 설정 기반 카메라 경로·방향·해상도·FPS를 실물 연결 뒤 확인한다. ROI는 요구사항이 생기기 전까지 추가하지 않는다.
- [x] 상단 재실과 하단 자세의 2Hz 처리 상한(0.5초보다 빠른 설정 거부), frame/result freshness와 detector threshold를 정의한다.
- [ ] 상단 인원수와 하단 최고 점수 pose가 같은 책상 사용자를 충분히 대표하는지 실측한다.
- [ ] 상단 얼굴 detector를 한 번 load·실행하고 fresh box/count snapshot을 task 05가 재사용할
  수 있게 한다. task 05가 별도 얼굴 detector loop를 만들지 않게 한다.
- [x] 같은 frame을 중복 추론하지 않도록 captured time을 추적한다.
- [x] 하단 full-frame letterbox 640, RGB blob 전처리와 COCO hip/knee/ankle 기하 판정을 구현한다.

### detector와 안정화

- [x] 하단 자세에 OpenCV DNN YOLO pose ONNX를 적용한다. 모든 threshold 이상 row 중 가장 높은
  confidence 한 명으로 stateless raw 자세를 계산한다. 상단 detector는 재실 count와 얼굴 검출을 맡는다.
- [x] detector 추론을 event loop 밖에서 수행한다.
- [x] 같은 detector/model의 upper/lower 호출은 작은 lock으로 직렬화한다.
- [x] raw 결과와 안정화 결과를 구분하고 앉음·섬 후보 유지 timer를 구현한다.
- [x] frame·결과 만료, model 오류와 task 종료에서 `UNKNOWN`으로 전환한다.
- [x] 상단 재실과 하단 최고 점수 자세의 freshness·허용 시각 차이를 계산한다. 카메라 간 count 비교는 하지 않는다.
- [x] 재실과 자세의 안정화는 각각 자기 카메라의 monotonic distinct frame에서 전진한다.

### lifecycle과 API

- [x] Vision loop를 container singleton과 lifecycle resource로 등록한다.
- [x] 시작 전에는 안전한 초기 snapshot, 종료 시에는 만료된 snapshot을 제공한다.
- [x] `/api/vision/status`에 raw·안정화 상태와 차단 이유를 camelCase로 노출한다.
- [x] Task 04의 `identity`는 `UNKNOWN` placeholder이며 raw frame·face box·vector는 API에
  노출하지 않는다. Task 05는 `get_fresh_face_observation()`의 내부 frame+box만 소비한다.
- [x] 일반 Dashboard에는 최소 Vision snapshot을 연결했고, `/debug/vision`에는 같은 추론 frame의
  재실 person box·face box·pose 관절/자세 오버레이를 연결했다.

## 제외 범위

- 얼굴 정렬·품질 검사·임베딩, 등록 profile 비교와 현재 사용자 session
- 카메라 간 범용 사람 Re-ID 또는 장기 trajectory 저장
- 영상 녹화, raw frame DB 저장과 일반 사용자용 preview API
- 자세에 따른 실제 책상 이동

## 검증

- fake 최신 frame으로 재실·자세 후보와 안정화 전이를 결정적으로 재현한다.
- 같은 frame을 여러 loop에서 새 관측으로 반복 사용하지 않는다.
- 오래된 frame, RTSP 단절과 model 예외가 새 관측이나 PRESENT로 남지 않는다.
- 상단 다중 사용자와 상단/하단 timestamp 불일치가 자동화 불가 상태가 된다. 하단 다중 검출은 최고 점수 pose 선택을 확인한다.
- 처리 속도가 입력 FPS보다 느려도 frame queue와 메모리가 지속 증가하지 않는다.
- 추론 부하 중 health, Dashboard 조회와 STOP 응답 시간이 허용 기준을 넘지 않는다.
- 두 카메라를 동시에 장시간 실행해 독립 재연결과 최신 frame 교체를 확인한다.

## 실물 확인 항목

- 하단 ONNX adapter의 fake output과 제공 sample 자동 회귀는 완료했지만, 실제 WebRTC camera, CPU 지연,
  threshold는 계속 실측한다. 상단 detector/얼굴 embedding/전체 association의 현장 정확도도
  별도 확인한다.
- 앉음·섬·이탈과 카메라 전체 화각의 주변 통행을 촬영해 오검출 패턴을 기록한다.
- 조명, 안경·모자, 의자 위치와 책상 높이 변화가 사람 수·자세 판정에 미치는 영향을 본다.
- camera 한 대 제거, MediaMTX 중단과 복귀 후 snapshot과 memory 사용량을 확인한다.

## 완료 조건

- 재실·자세·인원수와 각 관측 freshness를 API로 독립 조회할 수 있다.
- 불확실·다중·불일치·오래된 결과가 자동화 가능 상태로 표현되지 않는다.
- Dashboard·STOP event loop와 frame memory가 추론 지연의 영향을 받지 않는다.
- 얼굴 task가 source나 얼굴 detector loop를 다시 만들지 않고 fresh 검출 결과를 입력으로 사용할 수 있다.
