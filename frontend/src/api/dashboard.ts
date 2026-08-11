export type Direction = "UP" | "DOWN";
export type HeightStatus = "STOPPED" | "WAITING" | "ONLINE" | "STALE" | "ERROR";
export type DeskState = "IDLE" | "MOVING" | "MANUAL" | "STOPPED" | "ERROR";

export type DeskStatus = {
  state: DeskState;
  height: { heightCm: number | null; observedAt: string | null; status: HeightStatus };
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

export type Profile = {
  id: string;
  name: string;
  sittingHeightCm: number;
  standingHeightCm: number;
  ledColor: string | null;
};

export type ProfileInput = Omit<Profile, "id">;
export type WledMode = "OFF" | "SOLID" | "EFFECT" | "MIXED";
export type WledStatus = "DISABLED" | "UNKNOWN" | "ONLINE" | "ERROR";
export type WledSnapshot = { status: WledStatus; on: boolean | null; mode: WledMode | null; color: string | null; effectId: number | null; effectName: string | null; paletteId: number | null; speed: number | null; intensity: number | null; observedAt: string | null; lastError: string | null };
export type WledCatalogItem = { id: number; name: string };
export type WledCapabilities = { deviceName: string; firmwareVersion: string; effects: WledCatalogItem[]; palettes: WledCatalogItem[]; observedAt: string };
export type WledControl = { action: "OFF" } | { action: "SOLID"; color: string } | { action: "EFFECT"; effectId: number; paletteId?: number; speed?: number; intensity?: number; color?: string };

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
    const detail = body && typeof body.detail === "string" ? body.detail : "요청을 처리하지 못했습니다.";
    throw new ApiError(detail, response.status);
  }
  return body as Response;
}

const json = (body: object): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const getDeskStatus = () => request<DeskStatus>("/api/status");
export const sendHold = (direction: Direction) => request<DeskStatus>("/api/control", json({ action: "HOLD", direction }));
export const sendStop = (keepalive = false) =>
  request<DeskStatus>("/api/control", { ...json({ action: "STOP" }), keepalive });
export const setTarget = (targetCm: number) => request<DeskStatus>("/api/target", json({ action: "SET", targetCm }));
export const cancelTarget = () => request<DeskStatus>("/api/target", json({ action: "CANCEL" }));

export const listProfiles = () => request<Profile[]>("/api/profiles");
export const createProfile = (profile: ProfileInput) => request<Profile>("/api/profiles", json(profile));
export const updateProfile = (id: string, profile: Partial<ProfileInput>) =>
  request<Profile>(`/api/profiles/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(profile),
  });
export const deleteProfile = (id: string) => request<void>(`/api/profiles/${encodeURIComponent(id)}`, { method: "DELETE" });
export const getWledStatus = () => request<WledSnapshot>("/api/wled/status");
export const getWledCapabilities = () => request<WledCapabilities>("/api/wled/capabilities");
export const controlWled = (command: WledControl) => request<WledSnapshot>("/api/wled/control", json(command));
