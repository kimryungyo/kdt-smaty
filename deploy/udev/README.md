# SMART DESK udev 규칙

AKG Ara USB 마이크를 뺐다가 다시 연결했을 때 로그인 화면 사용자가 ALSA ACL을 가져가면
AI 스피커 프로세스에서 장치가 사라질 수 있다. 전용 장비에서는 다음 규칙을 설치해
`iot` 그룹에 안정적인 접근 권한을 준다.

```bash
sudo install -m 0644 \
  deploy/udev/99-smart-desk-akg-microphone.rules \
  /etc/udev/rules.d/99-smart-desk-akg-microphone.rules
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=sound
```

서비스 실행 사용자가 `iot` 그룹에 포함돼 있어야 한다. 규칙 설치 뒤 새 hot-plug부터
자동 적용되며, 이미 연결된 장치에는 위 `udevadm trigger`가 적용한다.

애플리케이션은 오디오 장치가 늦게 나타나거나 잠시 분리되면 2초 간격으로 다시 연다.
따라서 장치 권한이 복구되면 전체 서버를 재시작하지 않아도 AI 스피커가
`WAITING_WAKE` 상태로 돌아온다.
