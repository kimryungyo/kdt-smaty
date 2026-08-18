# Wake Word model

`hi_smarty_ko_mixed_v0_2_0.onnx`는 `하이 스마티` 호출어용
`livekit-wakeword==0.2.1` classifier다.

- model key: `hi_smarty_ko`
- SHA-256: `29bbe36b71105d9e4e3de24dcc368e7e1c681bde931085939224ec3e3cfe37ec`
- classifier input: float32 `(batch, 16, 96)` embeddings
- audio runtime input: 16kHz mono PCM16, rolling 32,000 samples (2 seconds)
- provisional threshold: `0.13`
- provisional consecutive evaluations: `2`
- source release: `/srv/wakeword/artifacts/releases/hi-smarty-ko-mixed-v0.2.0/`

현재 모델은 합성 기준선에 실제 화자 녹음을 더해 적응한 배포 후보이다. 실제 화자 테스트는
학습 화자와 같은 녹음 세션에서 이뤄졌고, 대상 장치의 연속 음성·생활 소음 검증은 아직
완료되지 않았다. 운영 임계값 변경 전 실제 장치에서 오탐률을 다시 검증해야 하며, 데이터와
재배포 조건은 원본 release의 `MODEL_CARD.md`를 함께 확인한다.
