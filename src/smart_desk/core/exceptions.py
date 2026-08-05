"""애플리케이션 골조에서 공통으로 사용하는 예외."""


class ContainerNotInitializedError(RuntimeError):
    """AppContainer를 설치하기 전에 조회했음을 나타낸다."""


class ContainerAlreadyInitializedError(RuntimeError):
    """AppContainer를 한 프로세스에 중복 설치했음을 나타낸다."""


class DuplicateTaskError(RuntimeError):
    """같은 이름의 실행 중인 비동기 작업이 이미 있음을 나타낸다."""

