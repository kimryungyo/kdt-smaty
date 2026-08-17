# 하단 YOLO pose 모델 배치

권장 파일명은 `yolo26n-pose.onnx`이며 저장소에는 모델 바이너리를 넣지 않는다. 운영자는 모델을
별도로 provision한 뒤 `SMART_DESK_VISION__LOWER_POSE_MODEL_PATH`에 로컬 경로를 설정한다. 빈 경로는
의도적으로 detector를 비활성화하고 `MODEL_UNAVAILABLE`를 유지한다.

이 구현을 검증한 로컬 파일의 SHA-256은 다음과 같다.

```text
93fc5e1d6b7690f33b4e1d60d6e9aec1cea14bdbc361bfae11778969be662078
```

모델은 Ultralytics YOLO 계열이다. 배포·재배포 전에 Ultralytics AGPL-3.0 또는 Enterprise 라이선스
조건과 조직의 사용 형태를 반드시 확인한다.
