"""Assistant public surface (legacy exports remain until Task 02B cutover)."""

from smart_desk.modules.assistant.agents_runtime import (
    AgentsVoiceConfig,
    AgentsVoiceRuntime,
    SmartDeskVoiceWorkflow,
    VoiceRuntimeEvent,
    VoiceRuntimeEventType,
    VoiceTurnError,
)
from smart_desk.modules.assistant.models import (
    AssistantDecisionReason,
    AssistantNextAction,
    AssistantReply,
    HistoryItem,
    OpenAiTurn,
)
from smart_desk.modules.assistant.openai import OpenAiGatewayPort, OpenAiTurnError
from smart_desk.modules.assistant.service import AssistantService

__all__ = [
    "AgentsVoiceConfig", "AgentsVoiceRuntime", "SmartDeskVoiceWorkflow",
    "VoiceRuntimeEvent", "VoiceRuntimeEventType", "VoiceTurnError",
    "AssistantReply", "AssistantNextAction", "AssistantDecisionReason", "AssistantService",
    "HistoryItem", "OpenAiGatewayPort", "OpenAiTurn", "OpenAiTurnError",
]
