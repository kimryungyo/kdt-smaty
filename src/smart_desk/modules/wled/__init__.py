"""WLED client와 container accessor를 노출한다."""
from smart_desk.core.container import get_container
from smart_desk.modules.wled.client import WledClient, WledDisabledError, WledError, WledNotStartedError, WledProtocolError, WledUnavailableError, WledUnsupportedValueError, WledSessionMismatchError
from smart_desk.modules.wled.models import WledCapabilities, WledMode, WledSnapshot, WledStatus

def get_wled() -> WledClient:
    client = get_container().wled
    if client is None: raise WledDisabledError("WLED가 비활성화되어 있습니다.")
    return client

__all__ = ["WledCapabilities", "WledClient", "WledDisabledError", "WledError", "WledMode", "WledNotStartedError", "WledProtocolError", "WledSnapshot", "WledStatus", "WledUnavailableError", "WledUnsupportedValueError", "WledSessionMismatchError", "get_wled"]
