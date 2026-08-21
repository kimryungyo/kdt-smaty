export type Direction = "UP" | "DOWN";
export type HeightStatus = "STOPPED" | "WAITING" | "ONLINE" | "STALE" | "ERROR" | "SENSOR_SLEEPING";
export type HeightProvenance = "LIVE" | "CACHED";
export type DeskState = "IDLE" | "MOVING" | "MANUAL" | "STOPPED" | "ERROR" | "WAKING";

export type DeskStatus = {
  state: DeskState;
  height: { heightCm: number | null; observedAt: string | null; status: HeightStatus; provenance: HeightProvenance | null };
  relay: {
    event: string | null;
    state: "UP" | "DOWN" | "STOP" | null;
    firmware: string | null;
    code: string | null;
    detail: string | null;
    receivedAt: string | null;
    lastError: string | null;
  };
  targetHeightCm: number | null;
  direction: Direction | null;
  detail: string;
  lastError: string | null;
  updatedAt: string;
};

/** 시간에 따라 조명을 바꾸는 규칙. TIME_OF_DAY는 벽시계, ELAPSED는 모드를 켠 뒤 경과 분. */
export type LedScheduleStep = { at: number; color: string; brightness: number };
export type LedSchedule = { kind: "TIME_OF_DAY" | "ELAPSED"; steps: LedScheduleStep[] };

export type Profile = {
  id: string;
  name: string;
  sittingHeightCm: number;
  standingHeightCm: number;
  ledColor: string | null;
  /** 조명 밝기(0~255). null이면 이 설정은 밝기를 건드리지 않는다. */
  ledBrightness: number | null;
  ledSchedule: LedSchedule | null;
  /** PIN 잠금 여부. PIN 자체는 서버가 해시로만 보관해 노출하지 않는다. */
  hasPin: boolean;
  tiltLevel: number | null;
  description: string | null;
};

export type ProfileInput = Omit<Profile, "id" | "hasPin">;
export type ActivityMode = {
  key: string;
  kind: "DEFAULT" | "CUSTOM";
  name: string;
  sittingHeightCm: number;
  standingHeightCm: number;
  ledColor: string | null;
  ledBrightness: number | null;
  ledSchedule: LedSchedule | null;
  tiltLevel: number | null;
  description: string | null;
  editable: boolean;
};

export type ActivityModeInput = {
  name: string;
  // 높이는 프로필이 소유한다. 작업 모드는 조명과 틸트만 정한다.
  sittingHeightCm?: number;
  standingHeightCm?: number;
  ledColor: string | null;
  ledBrightness: number | null;
  ledSchedule: LedSchedule | null;
  tiltLevel: number | null;
  description: string | null;
};
export type WledMode = "OFF" | "SOLID" | "EFFECT" | "MIXED";
export type WledStatus = "DISABLED" | "UNKNOWN" | "ONLINE" | "ERROR";
export type WledSnapshot = { status: WledStatus; on: boolean | null; brightness: number | null; mode: WledMode | null; color: string | null; effectId: number | null; effectName: string | null; paletteId: number | null; speed: number | null; intensity: number | null; observedAt: string | null; lastError: string | null };
export type WledCatalogItem = { id: number; name: string };
export type WledCapabilities = { deviceName: string; firmwareVersion: string; effects: WledCatalogItem[]; palettes: WledCatalogItem[]; observedAt: string };
type WledExpectedSession = { expectedSessionId?: string };
export type WledControl = WledExpectedSession & ({ action: "OFF" } | { action: "BRIGHTNESS"; brightness: number } | { action: "SOLID"; color: string } | { action: "EFFECT"; effectId: number; paletteId?: number; speed?: number; intensity?: number; color?: string });

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

async function request<Response>(path: string, init?: RequestInit): Promise<Response> {
  const response = await fetch(path, {
    ...init,
    headers: { Accept: "application/json", ...init?.headers },
  });
  const body = response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok) {
    // WLED route는 detail을 {code, ...} 객체로 준다. 그 code를 잃으면 호출자가
    // 실제 사유를 구분할 수 없으므로 문자열 detail과 같은 자리로 꺼낸다.
    const raw = body?.detail;
    const detail =
      typeof raw === "string"
        ? raw
        : typeof raw?.code === "string"
          ? raw.code
          : "요청을 처리하지 못했습니다.";
    throw new ApiError(detail, response.status);
  }
  return body as Response;
}

const json = (body: object): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const getDeskStatus = (signal?: AbortSignal) => request<DeskStatus>("/api/status", { signal });
export const sendHold = (direction: Direction) => request<DeskStatus>("/api/control", json({ action: "HOLD", direction }));
export const sendStop = (keepalive = false) =>
  request<DeskStatus>("/api/control", { ...json({ action: "STOP" }), keepalive });
export const setTarget = (targetCm: number) => request<DeskStatus>("/api/target", json({ action: "SET", targetCm }));
export const cancelTarget = () => request<DeskStatus>("/api/target", json({ action: "CANCEL" }));

export const listProfiles = () => request<Profile[]>("/api/profiles");
export const getProfile = (id: string, signal?: AbortSignal) => request<Profile>(`/api/profiles/${encodeURIComponent(id)}`, { signal });
export const createProfile = (profile: ProfileInput) => request<Profile>("/api/profiles", json(profile));
/** 이름 변경과 삭제는 PIN이 걸린 프로필이면 서버가 이 header를 요구한다. */
const pinHeader = (pin?: string): Record<string, string> => (pin ? { "X-Profile-Pin": pin } : {});
export const updateProfile = (id: string, profile: Partial<ProfileInput>, pin?: string) =>
  request<Profile>(`/api/profiles/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", ...pinHeader(pin) },
    body: JSON.stringify(profile),
  });
export const deleteProfile = (id: string, pin?: string) =>
  request<void>(`/api/profiles/${encodeURIComponent(id)}`, { method: "DELETE", headers: pinHeader(pin) });
export const setProfilePin = (id: string, pin: string, currentPin?: string) =>
  request<void>(`/api/profiles/${encodeURIComponent(id)}/pin`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", ...pinHeader(currentPin) },
    body: JSON.stringify({ pin }),
  });
export const verifyProfilePin = (id: string, pin: string) =>
  request<void>(`/api/profiles/${encodeURIComponent(id)}/pin/verify`, json({ pin }));
export const listActivityModes = (profileId: string, signal?: AbortSignal) =>
  request<ActivityMode[]>(`/api/profiles/${encodeURIComponent(profileId)}/activity-modes`, { signal });
export const createActivityMode = (profileId: string, mode: ActivityModeInput) =>
  request<ActivityMode>(`/api/profiles/${encodeURIComponent(profileId)}/activity-modes`, json(mode));
export const updateActivityMode = (modeId: string, mode: Partial<ActivityModeInput>) =>
  request<ActivityMode>(`/api/activity-modes/${encodeURIComponent(modeId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(mode),
  });
export const deleteActivityMode = (modeId: string) =>
  request<void>(`/api/activity-modes/${encodeURIComponent(modeId)}`, { method: "DELETE" });
export const getWledStatus = (signal?: AbortSignal) => request<WledSnapshot>("/api/wled/status", { signal });
export const getWledCapabilities = () => request<WledCapabilities>("/api/wled/capabilities");
export const controlWled = (command: WledControl) => request<WledSnapshot>("/api/wled/control", json(command));

export type AssistantPhase = "LISTENING" | "PROCESSING" | "TOOL" | "FINAL";
export type AssistantTurnStatus = "RUNNING" | "SUCCEEDED" | "CANCELLED" | "FAILED";
export type AssistantTurn = {
  turnId: string; sessionId: string | null; profileId: string | null; phase: AssistantPhase; sequence: number;
  status: AssistantTurnStatus; title: string; summary: string | null; detail: string | null;
  startedAt: string; updatedAt: string; completedAt: string | null; errorCode: string | null;
};
export type AssistantLatest = { turn: AssistantTurn | null };
export const getAssistantLatest = (signal?: AbortSignal) => request<AssistantLatest>("/api/assistant/latest", { signal });

export type VoiceState = "DISABLED" | "WAITING_WAKE" | "WAITING_FOLLOWUP" | "ACKNOWLEDGING" | "RECORDING" | "PROCESSING" | "SPEAKING" | "ERROR";
export type VoiceStatus = { state: VoiceState; lastTransitionAt: string | null; followupExpiresAt: string | null; lastError: string | null };
export const getVoiceStatus = (signal?: AbortSignal) => request<VoiceStatus>("/api/voice/status", { signal });

export type CurrentUser = { session: { sessionId: string; kind: "REGISTERED" | "ANONYMOUS"; profileId: string | null; startedAt: string; changedAt: string } | null };
export type VisionStatus = { cameras: Record<string, { status: "OFFLINE" | "ONLINE" | "STALE" | "ERROR"; observedAt: string | null; expiresAt: string | null; ageSeconds: number | null; error: string | null }>; identity: { status: string; profileId: string | null; observedAt: string | null; expiresAt: string | null }; presence: { rawStatus: string; status: string; upperCount: number | null; lowerCount: number | null; observedAt: string | null; expiresAt: string | null }; posture: { rawStatus: string; status: string; candidateSince: string | null; observedAt: string | null; expiresAt: string | null }; association: { usable: boolean; reasonCodes: string[] } };
export type VisionDebugBox = { x: number; y: number; width: number; height: number; confidence: number | null };
export type VisionDebugPose = { box: VisionDebugBox; keypoints: { x: number; y: number; confidence: number }[] };
export type VisionDebugCamera = { observedAt: string | null; frameWidth: number | null; frameHeight: number | null; personBoxes: VisionDebugBox[]; faceBoxes: VisionDebugBox[]; poseDetections: VisionDebugPose[]; detectorError: boolean; error: string | null; frameAvailable: boolean };
export type VisionDebug = { cameras: Record<"upper" | "lower", VisionDebugCamera> };
export type AutomationStatus = { sessionId: string | null; controlMode: "AUTO" | "MANUAL" | null; activityMode: ActivityMode | null; state: string; heightPolicy: string | null; postureCandidate: string | null; candidateSince: string | null; targetHeightCm: number | null; intentSource: string | null; blockedReasonCodes: string[]; initialMoveDueAt: string | null; parkDueAt: string | null; generation: number; revision: number; lastTransitionReason: string; lastTransitionSource: string; lastTransitionAt: string; updatedAt: string };
export type Enrollment = { enrollmentId: string; profileId: string; state: "WAITING_FACE" | "CAPTURING" | "PROCESSING" | "SUCCEEDED" | "CANCELLED" | "FAILED"; requiredSamples: number; acceptedSamples: number; startedAt: string; changedAt: string; failureCode: string | null };

export type TiltStatus = "UNAVAILABLE" | "IDLE" | "MOVING" | "AT_TARGET" | "STOPPED" | "ERROR";
export type TiltSnapshot = {
  status: TiltStatus; level: number | null; targetLevel: number | null;
  positionMm: number | null; positionValid: boolean;
  minLevel: number; maxLevel: number; detail: string; lastError: string | null; updatedAt: string;
};

export const getCurrentUser = (signal?: AbortSignal) => request<CurrentUser>("/api/current-user", { signal });
export const getVisionStatus = (signal?: AbortSignal) => request<VisionStatus>("/api/vision/status", { signal });
export const getVisionDebug = (signal?: AbortSignal) => request<VisionDebug>("/api/vision/debug", { signal });
export const getAutomationStatus = (signal?: AbortSignal) => request<AutomationStatus>("/api/automation/status", { signal });
export const setControlMode = (controlMode: "AUTO" | "MANUAL", expectedSessionId: string) => request<AutomationStatus>("/api/desk/control-mode", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ controlMode, expectedSessionId }) });
export const setActivityMode = (activityModeKey: string, expectedSessionId: string) => request<AutomationStatus>("/api/desk/activity-mode", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ activityModeKey, expectedSessionId }) });
export const startFaceEnrollment = (profileId: string) => request<Enrollment>(`/api/profiles/${encodeURIComponent(profileId)}/face-enrollments`, json({}));
export const getFaceEnrollment = (enrollmentId: string, signal?: AbortSignal) => request<Enrollment>(`/api/face-enrollments/${encodeURIComponent(enrollmentId)}`, { signal });
export const cancelFaceEnrollment = (enrollmentId: string) => request<void>(`/api/face-enrollments/${encodeURIComponent(enrollmentId)}`, { method: "DELETE" });
export const deleteFace = (profileId: string) => request<void>(`/api/profiles/${encodeURIComponent(profileId)}/face`, { method: "DELETE" });
export const getTiltStatus = (signal?: AbortSignal) => request<TiltSnapshot>("/api/tilt/status", { signal });
export const setTiltTarget = (level: number) => request<TiltSnapshot>("/api/tilt/target", {
  method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ level }),
});
export const stopTilt = () => request<TiltSnapshot>("/api/tilt/stop", { method: "POST" });
