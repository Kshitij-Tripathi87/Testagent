"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import type { Session } from "@supabase/supabase-js";
import {
  Activity, AlertTriangle, ArrowRight, Bot, CheckCircle2, ChevronRight,
  CircleDashed, Cloud, Database, Download, GitBranch, Layers3, Loader2,
  LogOut, Play, Plus, RefreshCw, Save, ShieldCheck, Upload, XCircle,
} from "lucide-react";
import { apiRequest, downloadArtifact } from "../lib/api";
import { getSupabase } from "../lib/supabase";

type Workspace = { id: string; name: string; role: string };
type Dataset = {
  id: string; urn: string; name: string; column_count: number;
  upstream_count: number; contract_version: number; synced_at?: string;
};
type UploadInfo = { id: string; file_name: string; size_bytes: number; row_count: number; columns: string[] };
type DatasetDetail = {
  id: string; urn: string; name: string; metadata: { columns?: Array<{name:string;type:string;nullable:boolean;description?:string}>; upstream_urns?: string[]; owners?: string[] };
  contract: { constraints?: Array<{kind:string;column?:string;ref_urn?:string;max_staleness_hours?:number}> };
  contract_version: number; uploads: UploadInfo[];
};
type Run = {
  id: string; dataset_id: string; status: string; passed: boolean | null; created_at?: string;
  summary: {total_checks?:number;passed_checks?:number;failed_checks?:number;incidents?:number;row_count?:number};
  error_message?: string; checks?: Array<{check_id:string;kind:string;column?:string;passed:boolean;detail:string}>;
  incidents?: Array<{id:string;title:string;status:string;details:{origin_urn?:string;summary?:string;trace?:Array<{urn:string;passed:boolean|null;detail:string;checked:boolean}>}}>;
  artifacts?: Array<{id:string;kind:string;file_name:string}>;
};
type Connection = { id:string; name:string; gms_url:string; status:string };

const cx = (...parts: Array<string | false | null | undefined>) => parts.filter(Boolean).join(" ");

export default function Home() {
  const supabase = useMemo(() => getSupabase(), []);
  const devUser = !supabase ? (process.env.NEXT_PUBLIC_DEV_USER || "") : "";
  const [session, setSession] = useState<Session | null>(null);
  const [email, setEmail] = useState("");
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState("");
  const [workspaceName, setWorkspaceName] = useState("My data workspace");
  const [connections, setConnections] = useState<Connection[]>([]);
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [selectedDataset, setSelectedDataset] = useState("");
  const [detail, setDetail] = useState<DatasetDetail | null>(null);
  const [editingContract, setEditingContract] = useState(false);
  const [contractDraft, setContractDraft] = useState("");
  const [runs, setRuns] = useState<Run[]>([]);
  const [selectedRun, setSelectedRun] = useState<Run | null>(null);
  const [connectionName, setConnectionName] = useState("Production DataHub");
  const [gmsUrl, setGmsUrl] = useState("http://localhost:8080");
  const [token, setToken] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const credentials = useMemo(() => ({
    accessToken: session?.access_token || null,
    devUser: devUser || null,
  }), [session?.access_token, devUser]);

  useEffect(() => {
    if (!supabase) return;
    let active = true;
    supabase.auth.getSession().then(({ data }) => {
      if (active) setSession(data.session);
    });
    const { data: { subscription } } = supabase.auth.onAuthStateChange((_event, next) => {
      setSession(next);
    });
    return () => { active = false; subscription.unsubscribe(); };
  }, [supabase]);

  const signedIn = Boolean(session?.access_token || devUser);

  const loadWorkspace = useCallback(async (id: string) => {
    if (!id) return;
    const [connectionRows, datasetRows, runRows] = await Promise.all([
      apiRequest<Connection[]>("/api/v1/workspaces/" + id + "/connections", credentials),
      apiRequest<Dataset[]>("/api/v1/workspaces/" + id + "/datasets", credentials),
      apiRequest<Run[]>("/api/v1/workspaces/" + id + "/runs", credentials),
    ]);
    setConnections(connectionRows);
    setDatasets(datasetRows);
    setRuns(runRows);
  }, [credentials]);

  const loadWorkspaces = useCallback(async () => {
    const rows = await apiRequest<Workspace[]>("/api/v1/workspaces", credentials);
    setWorkspaces(rows);
    if (!workspaceId && rows.length) setWorkspaceId(rows[0].id);
  }, [credentials, workspaceId]);

  useEffect(() => {
    if (!signedIn) return;
    loadWorkspaces().catch((reason) => setError(reason.message));
  }, [signedIn, loadWorkspaces]);

  useEffect(() => {
    if (!workspaceId) return;
    loadWorkspace(workspaceId).catch((reason) => setError(reason.message));
  }, [workspaceId, loadWorkspace]);

  useEffect(() => {
    if (!workspaceId || !selectedDataset) { setDetail(null); return; }
    apiRequest<DatasetDetail>("/api/v1/workspaces/" + workspaceId + "/datasets/" + selectedDataset, credentials)
      .then((result) => {
        setDetail(result);
        setContractDraft(JSON.stringify(result.contract, null, 2));
        setEditingContract(false);
      }).catch((reason) => setError(reason.message));
  }, [workspaceId, selectedDataset, credentials]);

  const refreshRun = useCallback(async (runId: string) => {
    if (!workspaceId) return;
    const result = await apiRequest<Run>("/api/v1/workspaces/" + workspaceId + "/runs/" + runId, credentials);
    setSelectedRun(result);
    setRuns((current) => [result, ...current.filter((item) => item.id !== result.id)]);
    if (["completed", "failed", "cancelled"].includes(result.status)) {
      await loadWorkspace(workspaceId);
    }
  }, [workspaceId, credentials, loadWorkspace]);

  useEffect(() => {
    if (!selectedRun || !["queued", "running"].includes(selectedRun.status)) return;
    const timer = window.setInterval(() => {
      refreshRun(selectedRun.id).catch((reason) => setError(reason.message));
    }, 2500);
    return () => window.clearInterval(timer);
  }, [selectedRun?.id, selectedRun?.status, refreshRun]);

  async function signIn(event: FormEvent) {
    event.preventDefault();
    if (!supabase) return;
    setBusy("auth"); setError(""); setMessage("");
    const { error: authError } = await supabase.auth.signInWithOtp({
      email,
      options: { emailRedirectTo: window.location.origin },
    });
    setBusy("");
    if (authError) setError(authError.message);
    else setMessage("Check your email for the secure sign-in link.");
  }

  async function createWorkspace(event: React.FormEvent) {
    event.preventDefault(); setBusy("workspace"); setError("");
    try {
      const result = await apiRequest<Workspace>("/api/v1/workspaces", {
        ...credentials, method: "POST", body: JSON.stringify({ name: workspaceName }),
      });
      setWorkspaces((items) => [result, ...items]);
      setWorkspaceId(result.id);
      setMessage("Workspace created.");
    } catch (reason) { setError((reason as Error).message); }
    finally { setBusy(""); }
  }

  async function connectDataHub(event: React.FormEvent) {
    event.preventDefault();
    if (!workspaceId) return;
    setBusy("connect"); setError(""); setMessage("");
    try {
      const created = await apiRequest<Connection>("/api/v1/workspaces/" + workspaceId + "/connections", {
        ...credentials, method: "POST",
        body: JSON.stringify({ name: connectionName, gms_url: gmsUrl, token }),
      });
      await apiRequest("/api/v1/workspaces/" + workspaceId + "/connections/" + created.id + "/test", {
        ...credentials, method: "POST",
      });
      const sync = await apiRequest<{synced:number}>("/api/v1/workspaces/" + workspaceId + "/connections/" + created.id + "/sync", {
        ...credentials, method: "POST",
      });
      setToken("");
      await loadWorkspace(workspaceId);
      setMessage("Connected to DataHub. Synced " + sync.synced + " dataset(s).");
    } catch (reason) { setError((reason as Error).message); }
    finally { setBusy(""); }
  }

  async function uploadAndRun(event: React.FormEvent) {
    event.preventDefault();
    if (!workspaceId || !selectedDataset || !file) return;
    setBusy("run"); setError(""); setMessage("");
    try {
      const form = new FormData();
      form.append("file", file);
      const uploaded = await apiRequest<UploadInfo>(
        "/api/v1/workspaces/" + workspaceId + "/datasets/" + selectedDataset + "/uploads",
        { ...credentials, method: "POST", body: form },
      );
      const queued = await apiRequest<Run>("/api/v1/workspaces/" + workspaceId + "/runs", {
        ...credentials, method: "POST",
        body: JSON.stringify({ dataset_id: selectedDataset, upload_id: uploaded.id }),
      });
      setSelectedRun(queued);
      setFile(null);
      setMessage("Quality run queued. Results will appear here automatically.");
    } catch (reason) { setError((reason as Error).message); }
    finally { setBusy(""); }
  }

  async function syncExisting(connectionId: string) {
    setBusy("sync"); setError("");
    try {
      const result = await apiRequest<{synced:number}>(
        "/api/v1/workspaces/" + workspaceId + "/connections/" + connectionId + "/sync",
        { ...credentials, method: "POST" },
      );
      await loadWorkspace(workspaceId);
      setMessage("Catalog refreshed. " + result.synced + " dataset(s) synced.");
    } catch (reason) { setError((reason as Error).message); }
    finally { setBusy(""); }
  }

  async function signOut() {
    if (supabase) await supabase.auth.signOut();
    setSession(null); setWorkspaceId(""); setWorkspaces([]);
  }

  if (!signedIn) {
    return (
      <main className="auth-shell">
        <div className="auth-card">
          <div className="brand-mark"><Layers3 size={22}/></div>
          <p className="eyebrow">DATA QUALITY ENGINEERING</p>
          <h1>Welcome to Testagent</h1>
          <p className="muted">Connect your data catalog, define trustworthy contracts and investigate quality failures.</p>
          {supabase ? (
            <form onSubmit={signIn} className="stack">
              <label>Email address<input value={email} onChange={(e)=>setEmail(e.target.value)} type="email" required placeholder="you@company.com"/></label>
              <button className="primary-button" disabled={busy==="auth"}>{busy==="auth"?"Sending link…":"Send secure sign-in link"} <ArrowRight size={16}/></button>
            </form>
          ) : (
            <div className="notice">Authentication is not configured. Set Supabase URL and anon key, or provide NEXT_PUBLIC_DEV_USER for local-only development.</div>
          )}
          {message && <p className="success-text">{message}</p>}
          {error && <p className="error-text">{error}</p>}
          <p className="fine-print">Beta preview · Workspace-isolated data quality operations</p>
        </div>
      </main>
    );
  }

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="brand"><div className="brand-mark"><Layers3 size={20}/></div><span>testagent</span><span className="beta-tag">BETA</span></div>
        <div className="workspace-switch">
          <span className="eyebrow">WORKSPACE</span>
          <select value={workspaceId} onChange={(e)=>setWorkspaceId(e.target.value)}>
            <option value="">Select workspace</option>
            {workspaces.map((workspace)=><option key={workspace.id} value={workspace.id}>{workspace.name}</option>)}
          </select>
        </div>
        <nav className="side-nav">
          <a className="active"><Activity size={17}/> Overview</a>
          <a><Database size={17}/> Data catalog</a>
          <a><ShieldCheck size={17}/> Contracts</a>
          <a><Play size={17}/> Quality runs</a>
          <a><GitBranch size={17}/> Lineage & incidents</a>
          <a><Bot size={17}/> Agent artifacts</a>
        </nav>
        <div className="sidebar-bottom">
          <div className="connection-state"><span className="status-dot"/> API workspace</div>
          <button className="ghost-button" onClick={signOut}><LogOut size={16}/> Sign out</button>
        </div>
      </aside>

      <section className="main-area">
        <header className="topbar">
          <div><span className="eyebrow">DATA PLATFORM / WORKSPACE</span><h2>Quality overview</h2></div>
          <div className="top-actions"><span className="live-pill"><span className="status-dot"/> Connected session</span><button className="icon-button" title="Refresh" onClick={()=>workspaceId&&loadWorkspace(workspaceId)}><RefreshCw size={17}/></button></div>
        </header>

        <div className="content">
          {error && <div className="alert error"><XCircle size={17}/><span>{error}</span><button onClick={()=>setError("")}>Dismiss</button></div>}
          {message && <div className="alert success"><CheckCircle2 size={17}/><span>{message}</span><button onClick={()=>setMessage("")}>Dismiss</button></div>}

          {!workspaceId ? (
            <section className="panel setup-panel">
              <div className="section-icon"><Layers3/></div><h3>Create your first workspace</h3>
              <p className="muted">Workspaces keep catalog connections, uploaded data and quality results separated.</p>
              <form className="inline-form" onSubmit={createWorkspace}>
                <input value={workspaceName} onChange={(e)=>setWorkspaceName(e.target.value)} aria-label="Workspace name"/>
                <button className="primary-button" disabled={busy==="workspace"}><Plus size={16}/> Create workspace</button>
              </form>
            </section>
          ) : (
            <>
              <div className="welcome-row"><div><h1>Make data quality observable.</h1><p className="muted">A single place to validate contracts, trace failures and ship better data.</p></div><div className="workspace-label"><span className="eyebrow">ACTIVE WORKSPACE</span><strong>{workspaces.find((w)=>w.id===workspaceId)?.name}</strong></div></div>
              <div className="metric-grid">
                <Metric icon={<Database size={18}/>} label="Catalog datasets" value={String(datasets.length)} hint="Synced from DataHub"/>
                <Metric icon={<Play size={18}/>} label="Quality runs" value={String(runs.length)} hint="Saved execution history"/>
                <Metric icon={<AlertTriangle size={18}/>} label="Open incidents" value={String(runs.reduce((sum,r)=>sum+(r.summary?.incidents||0),0))} hint="Across recent runs"/>
                <Metric icon={<ShieldCheck size={18}/>} label="Contracts" value={String(datasets.length)} hint="One per synced dataset"/>
              </div>

              <div className="two-column">
                <section className="panel">
                  <div className="panel-heading"><div><span className="eyebrow">INTEGRATION</span><h3>DataHub connection</h3></div><Cloud size={20}/></div>
                  {connections.length ? (
                    <div className="connection-list">
                      {connections.map((connection)=><div className="connection-row" key={connection.id}>
                        <div className="connection-icon"><Database size={18}/></div><div className="grow"><strong>{connection.name}</strong><small>{connection.gms_url}</small></div>
                        <span className={cx("state-tag",connection.status==="connected"?"good":"pending")}>{connection.status}</span>
                        <button className="small-button" onClick={()=>syncExisting(connection.id)} disabled={busy==="sync"}>Sync</button>
                      </div>)}
                    </div>
                  ) : <p className="muted small-copy">No catalog connection has been added yet.</p>}
                  <form className="stack compact-form" onSubmit={connectDataHub}>
                    <div className="form-row"><label>Connection name<input value={connectionName} onChange={(e)=>setConnectionName(e.target.value)} required/></label><label>GMS URL<input value={gmsUrl} onChange={(e)=>setGmsUrl(e.target.value)} required placeholder="https://datahub.company.com"/></label></div>
                    <label>DataHub personal access token <span className="muted">(stored encrypted)</span><input type="password" value={token} onChange={(e)=>setToken(e.target.value)} placeholder="Optional for local quickstart"/></label>
                    <button className="secondary-button" disabled={busy==="connect"}>{busy==="connect"?<Loader2 className="spin" size={16}/>:<Plus size={16}/>} Add, test & sync catalog</button>
                  </form>
                </section>

                <section className="panel">
                  <div className="panel-heading"><div><span className="eyebrow">EXECUTION</span><h3>Run a quality check</h3></div><Activity size={20}/></div>
                  {datasets.length ? (
                    <form className="stack compact-form" onSubmit={uploadAndRun}>
                      <label>Dataset<select value={selectedDataset} onChange={(e)=>setSelectedDataset(e.target.value)} required>
                        <option value="">Choose a dataset</option>
                        {datasets.map((dataset)=><option key={dataset.id} value={dataset.id}>{dataset.name}</option>)}
                      </select></label>
                      <label className="upload-zone"><Upload size={20}/><strong>{file ? file.name : "Choose a CSV file"}</strong><span>{file ? (file.size/1024).toFixed(1)+" KB" : "Up to 25 MB · dataset rows used for this run"}</span><input type="file" accept=".csv,text/csv" onChange={(e)=>setFile(e.target.files?.[0]||null)}/></label>
                      <button className="primary-button" disabled={!selectedDataset||!file||busy==="run"||Boolean(selectedRun&&["queued","running"].includes(selectedRun.status))}>{busy==="run"?<Loader2 className="spin" size={16}/>:<Play size={16}/>} Upload & execute checks</button>
                    </form>
                  ) : <div className="empty-inline"><CircleDashed size={22}/><p>Sync a DataHub catalog to select a dataset for quality checks.</p></div>}
                </section>
              </div>

              <section className="panel">
                <div className="panel-heading"><div><span className="eyebrow">DISCOVER</span><h3>Data catalog</h3></div><span className="count-tag">{datasets.length} datasets</span></div>
                {datasets.length ? <div className="dataset-table">
                  <div className="table-head"><span>DATASET</span><span>COLUMNS</span><span>UPSTREAM</span><span>CONTRACT</span><span/></div>
                  {datasets.map((dataset)=><button className={cx("dataset-row",selectedDataset===dataset.id&&"selected")} key={dataset.id} onClick={()=>setSelectedDataset(dataset.id)}>
                    <span><strong>{dataset.name}</strong><small>{dataset.urn}</small></span><span>{dataset.column_count}</span><span>{dataset.upstream_count}</span><span>v{dataset.contract_version}</span><ChevronRight size={16}/>
                  </button>)}
                </div> : <div className="empty-state"><Database size={28}/><strong>Your catalog is empty</strong><p>Connect DataHub and sync datasets to get started.</p></div>}
              </section>

              {detail && <section className="panel">
                <div className="panel-heading"><div><span className="eyebrow">DATASET CONTRACT</span><h3>{detail.name}</h3><p className="muted mono">{detail.urn}</p></div><div className="top-actions"><span className="count-tag">Version {detail.contract_version}</span><button className="small-button" onClick={()=>setEditingContract((value)=>!value)}>{editingContract?"Cancel":"Edit contract"}</button></div></div>
                {editingContract ? (
                  <div className="stack">
                    <p className="muted small-copy">Edit the contract JSON. Allowed constraint kinds: not_null, unique, referential and freshness. Referential rules must target a declared upstream URN.</p>
                    <textarea className="contract-editor" value={contractDraft} onChange={(event)=>setContractDraft(event.target.value)} rows={12} spellCheck={false}/>
                    <div><button className="primary-button" onClick={saveContract} disabled={busy==="contract"}>{busy==="contract"?<Loader2 className="spin" size={16}/>:<Save size={16}/>} Save new contract version</button></div>
                  </div>
                ) : <>
                  <div className="contract-grid">
                    {(detail.metadata.columns||[]).map((column)=><div className="column-chip" key={column.name}><span>{column.name}</span><small>{column.type} · {column.nullable?"nullable":"required"}</small></div>)}
                  </div>
                  <div className="contract-summary"><strong>{detail.contract.constraints?.length||0} constraints</strong><span>{detail.metadata.upstream_urns?.length||0} upstream dependencies</span><span>{detail.uploads.length} data uploads</span></div>
                  {(detail.metadata.upstream_urns||[]).length>0 && <div className="upstream-list"><strong>Declared upstream datasets</strong>{detail.metadata.upstream_urns?.map((urn)=><span key={urn}><GitBranch size={13}/>{urn}</span>)}</div>}
                </>}
              </section>}

              {selectedRun && <section className="panel">
                <div className="panel-heading"><div><span className="eyebrow">LATEST EXECUTION</span><h3>Quality run results</h3></div><RunStatus status={selectedRun.status} passed={selectedRun.passed}/></div>
                {["queued","running"].includes(selectedRun.status) ? <div className="running-state"><Loader2 className="spin" size={22}/><div><strong>{selectedRun.status==="queued"?"Waiting for worker":"Executing contract checks"}</strong><p className="muted">This view refreshes automatically.</p></div></div> : selectedRun.status==="failed" ? <div className="alert error"><XCircle size={17}/>{selectedRun.error_message||"Run failed. Check worker logs."}</div> : (
                  <>
                    <div className="result-metrics"><Metric label="Passed" value={String(selectedRun.summary?.passed_checks??0)} hint="Checks passed"/><Metric label="Failed" value={String(selectedRun.summary?.failed_checks??0)} hint="Checks failed"/><Metric label="Rows" value={String(selectedRun.summary?.row_count??0)} hint="Rows inspected"/><Metric label="Incidents" value={String(selectedRun.summary?.incidents??0)} hint="Investigations created"/></div>
                    <div className="check-list">{(selectedRun.checks||[]).map((check)=><div className="check-row" key={check.check_id}><span className={cx("check-icon",check.passed?"pass":"fail")}>{check.passed?<CheckCircle2 size={16}/>:<XCircle size={16}/>}</span><div className="grow"><strong>{check.kind}{check.column?" · "+check.column:""}</strong><small>{check.detail}</small></div><span className={cx("state-tag",check.passed?"good":"bad")}>{check.passed?"Passed":"Failed"}</span></div>)}</div>
                    {(selectedRun.incidents||[]).map((incident)=><div className="incident-card" key={incident.id}><div className="incident-title"><AlertTriangle size={17}/><strong>{incident.title}</strong><span className="state-tag pending">{incident.status}</span></div><p>{incident.details.summary}</p><small>Deepest confirmed origin: {incident.details.origin_urn}</small></div>)}
                    <div className="artifact-list"><strong>Generated engineering artifacts</strong>{(selectedRun.artifacts||[]).map((artifact)=><button className="artifact-link" key={artifact.id} onClick={()=>downloadArtifact("/api/v1/workspaces/"+workspaceId+"/runs/"+selectedRun.id+"/artifacts/"+artifact.id,credentials.accessToken,credentials.devUser).catch((reason)=>setError(reason.message))}><Download size={15}/>{artifact.file_name}</button>)}</div>
                  </>
                )}
              </section>}

              <section className="panel">
                <div className="panel-heading"><div><span className="eyebrow">HISTORY</span><h3>Recent quality runs</h3></div><span className="count-tag">{runs.length} saved</span></div>
                {runs.length ? <div className="run-list">{runs.slice(0,8).map((run)=><button className="run-row" key={run.id} onClick={()=>refreshRun(run.id)}><div className="run-symbol"><Activity size={16}/></div><div className="grow"><strong>{datasets.find((d)=>d.id===run.dataset_id)?.name||"Dataset run"}</strong><small>{new Date(run.created_at||Date.now()).toLocaleString()}</small></div><RunStatus status={run.status} passed={run.passed}/><ChevronRight size={15}/></button>)}</div> : <p className="muted small-copy">Completed and queued runs will appear here.</p>}
              </section>
            </>
          )}
          <footer className="footer"><span>TESTAGENT · DATA QUALITY ENGINEERING</span><span>Beta preview · Protecting data contracts</span></footer>
        </div>
      </section>
    </main>
  );
}

function Metric({icon,label,value,hint}:{icon?:ReactNode;label:string;value:string;hint:string}) {
  return <div className="metric-card"><div className="metric-top">{icon&&<span className="metric-icon">{icon}</span>}<span>{label}</span></div><strong>{value}</strong><small>{hint}</small></div>;
}
function RunStatus({status,passed}:{status:string;passed:boolean|null}) {
  const good = status==="completed" && passed;
  const bad = status==="failed" || (status==="completed" && passed===false);
  return <span className={cx("state-tag",good?"good":bad?"bad":"pending")}>{status==="completed"?(passed?"Passed":"Failed"):status}</span>;
}
