import { useCallback, useEffect, useState } from "react";

import {
  type ActivityMode,
  type ActivityModeInput,
  type Profile,
  type ProfileInput,
  ApiError,
  createActivityMode,
  createProfile,
  cancelFaceEnrollment,
  deleteFace,
  deleteActivityMode,
  deleteProfile,
  getDeskStatus,
  getFaceEnrollment,
  getProfile,
  listActivityModes,
  listProfiles,
  startFaceEnrollment,
  type Enrollment,
  updateActivityMode,
  updateProfile,
} from "../../api/dashboard";
import {
  DESK_CONTROL_MAX_CM,
  DESK_CONTROL_MIN_CM,
  MODE_DESCRIPTION_MAX_LENGTH,
  MODE_TILT_LEVEL_MAX,
  MODE_TILT_LEVEL_MIN,
} from "../../config";
import { navigate } from "../../routes";
import "./profile-settings.css";

type Props = { pathname: string };
type Draft = {
  name: string;
  sittingHeightCm: string;
  standingHeightCm: string;
  ledColor: string;
  tiltLevel: string;
  description: string;
};

const emptyDraft = (): Draft => ({
  name: "", sittingHeightCm: "75", standingHeightCm: "100", ledColor: "", tiltLevel: "", description: "",
});
const profileDraft = (profile: Profile): Draft => ({
  name: profile.name,
  sittingHeightCm: String(profile.sittingHeightCm),
  standingHeightCm: String(profile.standingHeightCm),
  ledColor: profile.ledColor ?? "",
  tiltLevel: profile.tiltLevel === null ? "" : String(profile.tiltLevel),
  description: profile.description ?? "",
});
const modeDraft = (mode?: ActivityMode): Draft => mode ? {
  name: mode.name,
  sittingHeightCm: String(mode.sittingHeightCm),
  standingHeightCm: String(mode.standingHeightCm),
  ledColor: mode.ledColor ?? "",
  tiltLevel: mode.tiltLevel === null ? "" : String(mode.tiltLevel),
  description: mode.description ?? "",
} : emptyDraft();

function toInput(draft: Draft): ProfileInput | ActivityModeInput | null {
  const sittingHeightCm = Number(draft.sittingHeightCm);
  const standingHeightCm = Number(draft.standingHeightCm);
  if (!draft.name.trim() || !Number.isFinite(sittingHeightCm) || !Number.isFinite(standingHeightCm)) return null;
  const tiltLevel = draft.tiltLevel.trim() === "" ? null : Number(draft.tiltLevel);
  if (tiltLevel !== null && (!Number.isInteger(tiltLevel) || tiltLevel < MODE_TILT_LEVEL_MIN || tiltLevel > MODE_TILT_LEVEL_MAX)) return null;
  return {
    name: draft.name.trim(), sittingHeightCm, standingHeightCm, ledColor: draft.ledColor || null,
    tiltLevel, description: draft.description.trim() || null,
  };
}

function Header() {
  return <header className="settings-header"><button type="button" className="settings-brand" onClick={() => navigate("/")} aria-label="SMART DESK 홈"><span aria-hidden="true" />SMART DESK</button><button type="button" className="settings-home" onClick={() => navigate("/")}>대시보드로</button></header>;
}

export function ProfileSettings({ pathname }: Props) {
  const match = /^\/settings\/profiles\/([^/]+)$/.exec(pathname);
  return <div className="settings-page"><Header />{pathname === "/settings/profiles" ? <ProfileList /> : pathname === "/settings/profiles/new" ? <ProfileEditor create /> : match ? <ProfileEditor profileId={decodeURIComponent(match[1])} /> : <main className="settings-main"><h1>페이지를 찾을 수 없습니다.</h1><button type="button" onClick={() => navigate("/settings/profiles")}>프로필 목록으로</button></main>}</div>;
}

function ProfileList() {
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try { setProfiles(await listProfiles()); setError(""); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "프로필을 불러오지 못했습니다."); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  return <main className="settings-main"><section className="settings-heading"><div><p>PROFILE SETTINGS</p><h1>프로필 관리</h1><span>프로필을 선택해 설정을 확인하거나 수정합니다.</span></div><button type="button" className="settings-primary" onClick={() => navigate("/settings/profiles/new")}>새 프로필</button></section>{error && <p className="settings-error" role="alert">{error}</p>}<section className="settings-list" aria-label="프로필 목록">{profiles.length === 0 ? <p className="settings-empty">아직 등록된 프로필이 없습니다.</p> : profiles.map((profile) => <button type="button" className="settings-profile" key={profile.id} onClick={() => navigate(`/settings/profiles/${encodeURIComponent(profile.id)}`)}><strong>{profile.name}</strong><span>기본 · 앉기 {profile.sittingHeightCm.toFixed(1)}cm · 서기 {profile.standingHeightCm.toFixed(1)}cm</span></button>)}</section></main>;
}

function ProfileEditor({ create = false, profileId }: { create?: boolean; profileId?: string }) {
  const [editingProfile, setEditingProfile] = useState<Profile | null>(null);
  const [draft, setDraft] = useState<Draft>(emptyDraft);
  const [modes, setModes] = useState<ActivityMode[]>([]);
  const [loading, setLoading] = useState(!create);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [modeEditor, setModeEditor] = useState<ActivityMode | "new" | null>(null);
  const [modeValue, setModeValue] = useState<Draft>(emptyDraft);
  const [enrollment, setEnrollment] = useState<Enrollment | null>(null);

  const loadModes = useCallback(async (id: string) => {
    try { setModes(await listActivityModes(id)); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "작업 모드를 불러오지 못했습니다."); }
  }, []);
  useEffect(() => {
    if (create || !profileId) return;
    let alive = true;
    void (async () => {
      try {
        const profile = await getProfile(profileId);
        if (!alive) return;
        setEditingProfile(profile); setDraft(profileDraft(profile)); await loadModes(profile.id);
      } catch (cause) { if (alive) setError(cause instanceof Error ? cause.message : "프로필을 불러오지 못했습니다."); }
      finally { if (alive) setLoading(false); }
    })();
    return () => { alive = false; };
  }, [create, loadModes, profileId]);

  const updateDraft = (key: keyof Draft, value: string) => setDraft((current) => ({ ...current, [key]: value }));
  const useCurrentHeight = async (key: "sittingHeightCm" | "standingHeightCm") => {
    setMessage(""); setError("");
    try {
      const status = await getDeskStatus();
      if (status.height.status !== "ONLINE" || status.height.heightCm === null) {
        setError("현재 높이는 ONLINE 상태의 최신 센서 값에서만 사용할 수 있습니다."); return;
      }
      updateDraft(key, String(status.height.heightCm));
      setMessage("현재 높이를 draft에만 복사했습니다. 저장 전에는 책상 설정이 바뀌지 않습니다.");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "현재 높이를 확인하지 못했습니다."); }
  };
  const saveProfile = async (event: React.FormEvent) => {
    event.preventDefault(); setError(""); setMessage("");
    if (!create && !editingProfile) { setError("프로필을 찾을 수 없어 저장할 수 없습니다."); return; }
    const input = toInput(draft);
    if (!input) { setError("이름과 유효한 앉기·서기 높이를 입력해주세요."); return; }
    setSaving(true);
    try {
      let profile: Profile;
      if (create) profile = await createProfile(input);
      else {
        if (!editingProfile) { setError("프로필을 찾을 수 없어 저장할 수 없습니다."); return; }
        profile = await updateProfile(editingProfile.id, input);
      }
      setEditingProfile(profile); setDraft(profileDraft(profile));
      if (create) {
        try {
          setEnrollment(await startFaceEnrollment(profile.id));
          setMessage("프로필을 만들고 얼굴 등록을 시작했습니다. 이 작업은 현재 사용자를 변경하지 않습니다.");
        } catch (cause) {
          setError(cause instanceof ApiError && cause.status === 503 ? "프로필은 만들었지만 얼굴 모델 또는 단일 얼굴 관측을 사용할 수 없습니다. 아래에서 재시도하거나 건너뛸 수 있습니다." : cause instanceof Error ? `프로필은 만들었지만 얼굴 등록을 시작하지 못했습니다: ${cause.message}` : "프로필은 만들었지만 얼굴 등록을 시작하지 못했습니다.");
        }
        return;
      }
      setMessage("기본 작업 모드를 저장했습니다. 현재 사용자, 제어 방식, 작업 모드와 책상은 변경하지 않습니다.");
      await loadModes(profile.id);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "프로필을 저장하지 못했습니다."); }
    finally { setSaving(false); }
  };
  const removeProfile = async () => {
    if (!editingProfile) return;
    const confirmation = `‘${editingProfile.name}’ 프로필을 삭제할까요? 얼굴·작업 모드·향후 memory와 활성 session에 영향을 줄 수 있습니다. 서버 memory 삭제 실패 시 삭제가 실패할 수 있습니다.`;
    if (!window.confirm(confirmation)) return;
    setError("");
    try { await deleteProfile(editingProfile.id); navigate("/settings/profiles"); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "프로필을 삭제하지 못했습니다."); }
  };
  const saveMode = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!editingProfile) return;
    const input = toInput(modeValue);
    if (!input) { setError("작업 모드 이름과 유효한 앉기·서기 높이를 입력해주세요."); return; }
    setSaving(true); setError("");
    try {
      if (modeEditor === "new") await createActivityMode(editingProfile.id, input);
      else if (modeEditor) await updateActivityMode(modeEditor.key, input);
      setModeEditor(null); await loadModes(editingProfile.id);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "작업 모드를 저장하지 못했습니다."); }
    finally { setSaving(false); }
  };
  const removeMode = async (mode: ActivityMode) => {
    if (!window.confirm(`‘${mode.name}’ 작업 모드를 삭제할까요? 다음 작업 모드 선택 또는 다음 session부터 삭제 결과가 적용됩니다.`)) return;
    try { await deleteActivityMode(mode.key); if (editingProfile) await loadModes(editingProfile.id); }
    catch (cause) { setError(cause instanceof ApiError && cause.status === 409 ? "현재 활성 작업 모드는 삭제할 수 없습니다." : cause instanceof Error ? cause.message : "작업 모드를 삭제하지 못했습니다."); }
  };

  if (loading) return <main className="settings-main"><p>프로필을 불러오는 중입니다.</p></main>;
  if (!create && !editingProfile) return <main className="settings-main"><section className="settings-heading"><div><p>PROFILE SETTINGS</p><h1>프로필을 찾을 수 없습니다.</h1><span>{error || "프로필을 불러오지 못했습니다."}</span></div><button type="button" className="settings-secondary" onClick={() => navigate("/settings/profiles")}>목록으로</button></section></main>;
  if (create && editingProfile) return <main className="settings-main"><section className="settings-heading"><div><p>FACE ENROLLMENT</p><h1>{editingProfile.name} 얼굴 등록</h1><span>얼굴 등록은 profile을 현재 사용자로 지정하지 않습니다.</span></div></section>{error && <p className="settings-error" role="alert">{error}</p>}{message && <p className="settings-message" role="status">{message}</p>}<FaceEnrollment profileId={editingProfile.id} initial={enrollment} onError={setError} /><div className="settings-actions"><button type="button" className="settings-secondary" onClick={() => navigate(`/settings/profiles/${encodeURIComponent(editingProfile.id)}`)}>얼굴 등록 건너뛰고 설정으로</button><button type="button" className="settings-primary" onClick={() => navigate("/settings/profiles")}>목록으로</button></div></main>;
  return <main className="settings-main"><section className="settings-heading"><div><p>{create ? "NEW PROFILE" : "PROFILE SETTINGS"}</p><h1>{create ? "새 프로필" : editingProfile?.name ?? "프로필"}</h1><span>이 화면의 편집 내용은 설정 API에만 저장되며 현재 책상 실행 상태를 바꾸지 않습니다.</span></div><button type="button" className="settings-secondary" onClick={() => navigate("/settings/profiles")}>목록으로</button></section>{error && <p className="settings-error" role="alert">{error}</p>}{message && <p className="settings-message" role="status">{message}</p>}<form className="settings-card" onSubmit={(event) => void saveProfile(event)}><h2>기본 작업 모드 <small>이름: 기본</small></h2><p>기본 작업 모드의 이름은 바꿀 수 없습니다. 아래 값만 저장합니다.</p><ProfileFields draft={draft} onChange={updateDraft} onUseCurrent={useCurrentHeight} /><div className="settings-actions"><button type="submit" className="settings-primary" disabled={saving}>{create ? "프로필 만들기" : "기본값 저장"}</button></div></form>{!create && editingProfile && <><FaceEnrollment profileId={editingProfile.id} onError={setError} /><section className="settings-card settings-modes"><div className="settings-section-title"><div><h2>사용자 작업 모드</h2><p>작업 모드는 설정값입니다. 저장·삭제해도 현재 작업 모드나 LED, 책상은 즉시 바뀌지 않습니다.</p></div><button type="button" className="settings-primary" onClick={() => { setModeEditor("new"); setModeValue(emptyDraft()); }}>작업 모드 추가</button></div><div className="settings-mode-list">{modes.map((mode) => <div className="settings-mode" key={mode.key}><div><strong>{mode.name}</strong><span>{mode.kind === "DEFAULT" ? "기본 작업 모드" : "사용자 작업 모드"} · 앉기 {mode.sittingHeightCm.toFixed(1)}cm · 서기 {mode.standingHeightCm.toFixed(1)}cm · LED {mode.ledColor ? `#${mode.ledColor}` : "없음"} · 틸트 {mode.tiltLevel === null ? "미설정" : mode.tiltLevel}</span>{mode.description && <p className="settings-mode-description">{mode.description}</p>}</div>{mode.editable ? <div><button type="button" onClick={() => { setModeEditor(mode); setModeValue(modeDraft(mode)); }}>수정</button><button type="button" className="settings-danger-text" onClick={() => void removeMode(mode)}>삭제</button></div> : <em>이름 고정</em>}</div>)}</div></section></>}{modeEditor && <div className="settings-modal" role="dialog" aria-modal="true" aria-label="작업 모드 편집"><form className="settings-card settings-dialog" onSubmit={(event) => void saveMode(event)}><h2>{modeEditor === "new" ? "작업 모드 추가" : "작업 모드 수정"}</h2><ProfileFields draft={modeValue} onChange={(key, value) => setModeValue((current) => ({ ...current, [key]: value }))} /><div className="settings-actions"><button type="button" className="settings-secondary" onClick={() => setModeEditor(null)}>취소</button><button type="submit" className="settings-primary" disabled={saving}>저장</button></div></form></div>}{!create && <section className="settings-delete"><h2>프로필 삭제</h2><p>얼굴과 custom 작업 모드, 향후 memory 및 활성 session에 영향을 줄 수 있습니다. 서버가 항목 삭제에 실패하면 profile을 보존하고 삭제 요청이 실패할 수 있습니다.</p><button type="button" className="settings-danger" onClick={() => void removeProfile()}>프로필 삭제</button></section>}</main>;
}

function ProfileFields({ draft, onChange, onUseCurrent }: { draft: Draft; onChange: (key: keyof Draft, value: string) => void; onUseCurrent?: (key: "sittingHeightCm" | "standingHeightCm") => void }) {
  return <div className="settings-fields"><label>이름<input value={draft.name} maxLength={100} onChange={(event) => onChange("name", event.target.value)} required /></label><label>기본 LED 색상<input type="color" value={`#${draft.ledColor || "000000"}`} onChange={(event) => onChange("ledColor", event.target.value.slice(1).toUpperCase())} /><button type="button" className="settings-link" onClick={() => onChange("ledColor", "")}>색상 없음</button></label>{(["sittingHeightCm", "standingHeightCm"] as const).map((key) => <label key={key}>{key === "sittingHeightCm" ? "앉기 높이" : "서기 높이"}<div className="settings-height"><input type="number" min={DESK_CONTROL_MIN_CM} max={DESK_CONTROL_MAX_CM} step="0.1" value={draft[key]} onChange={(event) => onChange(key, event.target.value)} required /><span>cm</span>{onUseCurrent && <button type="button" onClick={() => void onUseCurrent(key)}>현재 높이 사용</button>}</div></label>)}<label>틸트 단계<input type="number" min={MODE_TILT_LEVEL_MIN} max={MODE_TILT_LEVEL_MAX} step="1" value={draft.tiltLevel} placeholder="미설정" onChange={(event) => onChange("tiltLevel", event.target.value)} /><button type="button" className="settings-link" onClick={() => onChange("tiltLevel", "")}>단계 없음</button></label><label>설명<textarea value={draft.description} maxLength={MODE_DESCRIPTION_MAX_LENGTH} placeholder="이 모드를 사용하는 상황을 적어주세요." onChange={(event) => onChange("description", event.target.value)} /></label></div>;
}

function FaceEnrollment({ profileId, initial = null, onError }: { profileId: string; initial?: Enrollment | null; onError: (message: string) => void }) {
  const [enrollment, setEnrollment] = useState<Enrollment | null>(initial);
  const [busy, setBusy] = useState(false);
  const active = enrollment?.state === "WAITING_FACE" || enrollment?.state === "CAPTURING" || enrollment?.state === "PROCESSING";

  useEffect(() => { setEnrollment(initial); }, [initial]);
  useEffect(() => {
    if (!enrollment || !active) return;
    const controller = new AbortController();
    const poll = async () => {
      try { setEnrollment(await getFaceEnrollment(enrollment.enrollmentId, controller.signal)); }
      catch (cause) { if (!controller.signal.aborted) onError(cause instanceof Error ? `얼굴 등록 상태를 읽지 못했습니다: ${cause.message}` : "얼굴 등록 상태를 읽지 못했습니다."); }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 1000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [active, enrollment?.enrollmentId, onError]);

  const start = async () => {
    setBusy(true); onError("");
    try { setEnrollment(await startFaceEnrollment(profileId)); }
    catch (cause) {
      if (cause instanceof ApiError && cause.status === 409) onError("다른 얼굴 등록이 진행 중입니다. 현재 등록이 끝나거나 취소된 뒤 다시 시도하세요.");
      else if (cause instanceof ApiError && cause.status === 503) onError("얼굴 모델 또는 최신 단일 얼굴 관측이 준비되지 않았습니다. 카메라 상태를 확인한 뒤 재시도하세요.");
      else onError(cause instanceof Error ? `얼굴 등록을 시작하지 못했습니다: ${cause.message}` : "얼굴 등록을 시작하지 못했습니다.");
    } finally { setBusy(false); }
  };
  const cancel = async () => {
    if (!enrollment) return;
    setBusy(true); onError("");
    try { await cancelFaceEnrollment(enrollment.enrollmentId); setEnrollment((current) => current ? { ...current, state: "CANCELLED" } : current); }
    catch (cause) {
      if (cause instanceof ApiError && cause.status === 409) onError("PROCESSING 단계의 등록은 취소할 수 없습니다. 완료 또는 실패 상태를 기다린 뒤 재시도하세요.");
      else onError(cause instanceof Error ? `얼굴 등록을 취소하지 못했습니다: ${cause.message}` : "얼굴 등록을 취소하지 못했습니다.");
    } finally { setBusy(false); }
  };
  const remove = async () => {
    if (!window.confirm("저장된 얼굴 표본을 모두 삭제할까요? 이 작업은 현재 session과 자동화를 종료할 수 있으며 되돌릴 수 없습니다.")) return;
    setBusy(true); onError("");
    try { await deleteFace(profileId); setEnrollment(null); }
    catch (cause) { onError(cause instanceof ApiError && cause.status === 409 ? "진행 중인 얼굴 등록이 있어 삭제할 수 없습니다." : cause instanceof Error ? `얼굴을 삭제하지 못했습니다: ${cause.message}` : "얼굴을 삭제하지 못했습니다."); }
    finally { setBusy(false); }
  };
  const stateLabel = enrollment ? `${enrollment.state} · ${enrollment.acceptedSamples}/${enrollment.requiredSamples} 표본` : "등록된 얼굴 상태는 서버가 일반 API로 공개하지 않습니다.";

  return <section className="settings-card face-enrollment"><div className="settings-section-title"><div><h2>얼굴 등록</h2><p>등록·재등록·삭제는 현재 session을 끝내고 자동화를 멈출 수 있지만, 이 profile을 현재 사용자로 지정하지는 않습니다.</p></div><strong>{stateLabel}</strong></div>{enrollment?.failureCode && <p className="inline-error">실패 코드: {enrollment.failureCode}. 카메라와 단일 얼굴 상태를 확인한 뒤 재시도하세요.</p>}<div className="settings-actions"><button type="button" className="settings-secondary" disabled={busy || active} onClick={() => void start()}>{enrollment?.state === "FAILED" || enrollment?.state === "CANCELLED" ? "다시 시도" : "얼굴 등록/재등록 시작"}</button>{enrollment && enrollment.state !== "SUCCEEDED" && enrollment.state !== "CANCELLED" && enrollment.state !== "FAILED" && <button type="button" className="settings-secondary" disabled={busy || enrollment.state === "PROCESSING"} onClick={() => void cancel()}>등록 취소</button>}<button type="button" className="settings-danger" disabled={busy || active} onClick={() => void remove()}>저장된 얼굴 삭제</button></div></section>;
}
