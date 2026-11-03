"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import type { Session } from "@supabase/supabase-js";
import {
  Activity, ArrowLeft, Bot, CheckCircle2, CircleDashed, GitBranch,
  Github, Loader2, MessageSquare, Plus, RefreshCw, ShieldCheck,
  Ticket, Trash2, XCircle,
} from "lucide-react";
import { apiRequest } from "../../lib/api";
import { getSupabase } from "../../lib/supabase";

type Workspace = { id: string; name: string; role: string };
type Integration = {
  id: string; provider: "github" | "slack" | "jira"; name: string;
  config: Record<string, string>; status: string; last_tested_at?: string;
};
type Incident = {
  id: string; title: string; status: string; dataset_id: string;
  details: { summary?: string; origin_urn?: string };
  created_at?: string;
};
type AgentStep = {
  id: string; position: number; step_key: string; provider: string;
  title: string; description: string; status: string;
  result: Record<string, string>; error?: string;
};
type AgentWorkflow = {
  id: string; incident_id: string; incident_title?: string; status: string;
  plan: { summary?: string; planner?: string; requires_approval?: boolean };
  result: Record<string, string | number | null>;
  steps: AgentStep[];
  created_at?: string;
};

const cx = (...parts: Array<string | false | null | undefined>) => parts.filter(Boolean).join(" ");

export default function AgentPage() {
  const supabase = useMemo(() => getSupabase(), []);
  const devUser = !supabase ? (process.env.NEXT_PUBLIC_DEV_USER || "") : "";
  const [session, setSession] = useState<Session | null>(null);
  const [email, setEmail] = useState("");
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState("");
  const [workspaceName, setWorkspaceName] = useState("My data workspace");
  const [integrations, setIntegrations] = useState<Integration[]>([]);
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [workflows, setWorkflows] = useState<AgentWorkflow[]>([]);
  const [activeWorkflow, setActiveWorkflow] = useState<AgentWorkflow | null>(null);
  const [incidentId, setIncidentId] = useState("");
  const [selectedIntegrationIds, setSelectedIntegrationIds] = useState<string[]>([]);
  const [provider, setProvider] = useState<"github" | "jira" | "slack">("github");
  const [connectionName, setConnectionName] = useState("Engineering GitHub");
  const [repository, setRepository] = useState("");
  const [channelId, setChannelId] = useState("");
  const [jiraUrl, setJiraUrl] = useState("https://");
  const [jiraEmail, setJiraEmail] = useState("");
  const [jiraProject, setJiraProject] = useState("");
  const [jiraIssueType, setJiraIssueType] = useState("Task");
  const [secret, setSecret] = useState("");
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const credentials = useMemo(() => ({
    accessToken: session?.access_token || null,
    devUser: devUser || null,
  }), [session?.access_token, devUser]);
  const signedIn = Boolean(session?.access_token || devUser);

  useEffect(() => {
    if (!supabase) return;
    let active = true;
    supabase.auth.getSession().then(({ data }) => {
      if (active) setSession(data.session);
    });
    const { data: { subscription } } = supabase.auth.onAuthStateChange((_event, next) => setSession(next));
    return () => { active = false; subscription.unsubscribe(); };
  }, [supabase]);

  const loadWorkspaceData = useCallback(async (id: string) => {
    if (!id) return;
    const [connectorRows, incidentRows, workflowRows] = await Promise.all([
      apiRequest<Integration[]>("/api/v1/workspaces/" + id + "/integrations", credentials),
      apiRequest<Incident[]>("/api/v1/workspaces/" + id + "/incidents", credentials),
      apiRequest<AgentWorkflow[]>("/api/v1/workspaces/" + id + "/agent/runs", credentials),
    ]);
    setIntegrations(connectorRows);
    setIncidents(incidentRows);
    setWorkflows(workflowRows);
    setSelectedIntegrationIds((current) => current.filter((id) => connectorRows.some((row) => row.id === id)));
    setActiveWorkflow((current) => current ? workflowRows.find((row) => row.id === current.id) || current : null);
    if (!incidentId && incidentRows.length) {
      const open = incidentRows.find((row) => row.status !== "resolved");
      if (open) setIncidentId(open.id);
    }
  }, [credentials, incidentId]);

  const loadWorkspaces = useCallback(async () => {
    const rows = await apiRequest<Workspace[]>("/api/v1/workspaces", credentials);
    setWorkspaces(rows);
    if (!workspaceId && rows.length) setWorkspaceId(rows[0].id);
  }, [credentials, workspaceId]);

  useEffect(() => {
    if (signedIn) loadWorkspaces().catch((reason) => setError(reason.message));
  }, [signedIn, loadWorkspaces]);

  useEffect(() => {
    if (workspaceId) loadWorkspaceData(workspaceId).catch((reason) => setError(reason.message));
  }, [workspaceId, loadWorkspaceData]);

  const refreshWorkflow = useCallback(async (id: string) => {
    if (!workspaceId) return;
    const row = await apiRequest<AgentWorkflow>(
      "/api/v1/workspaces/" + workspaceId + "/agent/runs/" + id, credentials,
    );
    setActiveWorkflow(row);
    setWorkflows((current) => [row, ...current.filter((item) => item.id !== row.id)]);
    if (!["queued", "running"].includes(row.status)) {
      await loadWorkspaceData(workspaceId);
    }
  }, [workspaceId, credentials, loadWorkspaceData]);

  useEffect(() => {
    if (!activeWorkflow || !["queued", "running"].includes(activeWorkflow.status)) return;
    const timer = window.setInterval(() => {
      refreshWorkflow(activeWorkflow.id).catch((reason) => setError(reason.message));
    }, 2500);
    return () => window.clearInterval(timer);
  }, [activeWorkflow?.id, activeWorkflow?.status, refreshWorkflow]);

  async function signIn(event: FormEvent) {
    event.preventDefault();
    if (!supabase) return;
    setBusy("auth"); setError(""); setMessage("");
    const { error: authError } = await supabase.auth.signInWithOtp({
      email, options: { emailRedirectTo: window.location.origin + "/agent" },
    });
    setBusy("");
    if (authError) setError(authError.message);
    else setMessage("Check your email for the secure sign-in link.");
  }

  async function createWorkspace(event: FormEvent) {
    event.preventDefault(); setBusy("workspace"); setError("");
    try {
      const row = await apiRequest<Workspace>("/api/v1/workspaces", {
        ...credentials, method: "POST", body: JSON.stringify({ name: workspaceName }),
      });
      setWorkspaces((current) => [row, ...current]);
      setWorkspaceId(row.id);
      setMessage("Workspace created.");
    } catch (reason) { setError((reason as Error).message); }
    finally { setBusy(""); }
  }

  async function createIntegration(event: FormEvent) {
    event.preventDefault();
    if (!workspaceId || !secret.trim()) return;
    setBusy("integration"); setError(""); setMessage("");
    let config: Record<string, string> = {};
    let integrationCredentials: Record<string, string> = {};
    if (provider === "github") {
      config = { repository };
      integrationCredentials = { access_token: secret };
    } else if (provider === "slack") {
      config = { channel_id: channelId };
      integrationCredentials = { bot_token: secret };
    } else {
      config = { base_url: jiraUrl, email: jiraEmail, project_key: jiraProject, issue_type: jiraIssueType };
      integrationCredentials = { api_token: secret };
    }
    try {
      const created = await apiRequest<Integration>("/api/v1/workspaces/" + workspaceId + "/integrations", {
        ...credentials, method: "POST",
        body: JSON.stringify({ provider, name: connectionName, config, credentials: integrationCredentials }),
      });
      setSecret("");
      await apiRequest("/api/v1/workspaces/" + workspaceId + "/integrations/" + created.id + "/test", {
        ...credentials, method: "POST",
      });
      setSecret("");
      setMessage(provider.toUpperCase() + " connected and tested.");
      await loadWorkspaceData(workspaceId);
    } catch (reason) {
      setError((reason as Error).message);
      await loadWorkspaceData(workspaceId).catch(() => undefined);
    } finally { setBusy(""); }
  }

  async function testIntegration(id: string) {
    setBusy("test:" + id); setError(""); setMessage("");
    try {
      const result = await apiRequest<{status:string}>(
        "/api/v1/workspaces/" + workspaceId + "/integrations/" + id + "/test",
        { ...credentials, method: "POST" },
      );
      await loadWorkspaceData(workspaceId);
      setMessage("Connector test passed (" + result.status + ").");
    } catch (reason) {
      setError((reason as Error).message);
      await loadWorkspaceData(workspaceId).catch(() => undefined);
    } finally {
      setBusy("");
    }
  }

  async function deleteIntegration(id: string) {
    setBusy("delete:" + id); setError("");
    try {
      await apiRequest("/api/v1/workspaces/" + workspaceId + "/integrations/" + id, {
        ...credentials, method: "DELETE",
      });
      setSelectedIntegrationIds((current) => current.filter((item) => item !== id));
      await loadWorkspaceData(workspaceId);
      setMessage("Connector removed.");
    } catch (reason) { setError((reason as Error).message); }
    finally { setBusy(""); }
  }

  function toggleIntegration(id: string, checked: boolean) {
    setSelectedIntegrationIds((current) => checked
      ? [...current.filter((item) => item !== id), id]
      : current.filter((item) => item !== id));
  }

  async function preparePlan(event: FormEvent) {
    event.preventDefault();
    if (!workspaceId || !incidentId || !selectedIntegrationIds.length) return;
    setBusy("plan"); setError(""); setMessage("");
    try {
      const row = await apiRequest<AgentWorkflow>(
        "/api/v1/workspaces/" + workspaceId + "/agent/incident-response",
        { ...credentials, method: "POST",
          body: JSON.stringify({ incident_id: incidentId, integration_ids: selectedIntegrationIds }) },
      );
      setActiveWorkflow(row);
      setWorkflows((current) => [row, ...current.filter((item) => item.id !== row.id)]);
      setMessage("Execution plan prepared. Review every destination before approving.");
    } catch (reason) { setError((reason as Error).message); }
    finally { setBusy(""); }
  }

  async function approvePlan() {
    if (!workspaceId || !activeWorkflow) return;
    setBusy("approve"); setError(""); setMessage("");
    try {
      const row = await apiRequest<AgentWorkflow>(
        "/api/v1/workspaces/" + workspaceId + "/agent/runs/" + activeWorkflow.id + "/approve",
        { ...credentials, method: "POST" },
      );
      setActiveWorkflow(row);
      setMessage("Plan approved. The worker is executing the steps in order.");
    } catch (reason) { setError((reason as Error).message); }
    finally { setBusy(""); }
  }

  async function signOut() {
    if (supabase) await supabase.auth.signOut();
    setSession(null); setWorkspaceId(""); setWorkspaces([]);
  }

  const openIncidents = incidents.filter((row) => row.status !== "resolved");
  const githubCount = integrations.filter((row) => row.provider === "github").length;
  const jiraCount = integrations.filter((row) => row.provider === "jira").length;
  const slackCount = integrations.filter((row) => row.provider === "slack").length;

  if (!signedIn) {
    return (
      <main className="auth-shell">
        <div className="auth-card">
          <div className="brand-mark"><Bot size={22}/></div>
          <p className="eyebrow">TESTAGENT · MULTI-STEP AGENT</p>
          <h1>Connect your tools</h1>
          <p className="muted">Sign in to manage workspace connectors and review agent workflows.</p>
          {supabase ? (
            <form onSubmit={signIn} className="stack">
              <label>Email address<input value={email} onChange={(event)=>setEmail(event.target.value)} type="email" required placeholder="you@company.com"/></label>
              <button className="primary-button" disabled={busy==="auth"}>{busy==="auth"?"Sending link…":"Send secure sign-in link"}</button>
            </form>
          ) : <div className="notice">Set Supabase authentication or NEXT_PUBLIC_DEV_USER for local-only development.</div>}
          {message && <p className="success-text">{message}</p>}
          {error && <p className="error-text">{error}</p>}
          <a href="/" className="agent-back-link"><ArrowLeft size={15}/> Back to overview</a>
        </div>
      </main>
    );
  }

  return (
    <main className="agent-shell">
      <aside className="agent-sidebar">
        <a className="brand agent-brand" href="/"><span className="brand-mark"><Bot size={20}/></span><span>testagent</span><span className="beta-tag">BETA</span></a>
        <span className="eyebrow">WORKSPACE</span>
        <select className="agent-workspace-select" value={workspaceId} onChange={(event)=>setWorkspaceId(event.target.value)}>
          <option value="">Select workspace</option>
          {workspaces.map((item)=><option key={item.id} value={item.id}>{item.name}</option>)}
        </select>
        <nav className="side-nav">
          <a href="/"><Activity size={17}/> Overview</a>
          <a className="active" href="/agent"><Bot size={17}/> Agent & integrations</a>
        </nav>
        <div className="sidebar-bottom">
          <div className="connection-state"><span className="status-dot"/> API workspace</div>
          <button className="ghost-button" onClick={signOut}>Sign out</button>
        </div>
      </aside>

      <section className="main-area">
        <header className="topbar">
          <div><span className="eyebrow">AUTOMATION / WORKSPACE</span><h2>Agent & integrations</h2></div>
          <div className="top-actions"><span className="live-pill"><span className="status-dot"/> Connected session</span><button className="icon-button" title="Refresh" onClick={()=>workspaceId&&loadWorkspaceData(workspaceId)}><RefreshCw size={17}/></button></div>
        </header>

        <div className="content">
          {error && <div className="alert error"><XCircle size={17}/><span>{error}</span><button onClick={()=>setError("")}>Dismiss</button></div>}
          {message && <div className="alert success"><CheckCircle2 size={17}/><span>{message}</span><button onClick={()=>setMessage("")}>Dismiss</button></div>}

          {!workspaceId ? (
            <section className="panel setup-panel">
              <div className="section-icon"><Bot/></div><h3>Create a workspace</h3>
              <p className="muted">Connect external tools and keep tokens, incidents and agent runs isolated inside this workspace.</p>
              <form className="inline-form" onSubmit={createWorkspace}>
                <input value={workspaceName} onChange={(event)=>setWorkspaceName(event.target.value)} aria-label="Workspace name"/>
                <button className="primary-button" disabled={busy==="workspace"}><Plus size={16}/> Create workspace</button>
              </form>
            </section>
          ) : (
            <>
              <div className="welcome-row">
                <div><h1>Connect the tools your team works in.</h1><p className="muted">Configure scoped connectors, then turn a quality incident into an approved multi-step response.</p></div>
                <div className="workspace-label"><span className="eyebrow">ACTIVE WORKSPACE</span><strong>{workspaces.find((row)=>row.id===workspaceId)?.name}</strong></div>
              </div>

              <div className="metric-grid agent-metrics">
                <Metric icon={<Github size={18}/>} label="GitHub" value={String(githubCount)} hint="Repository connections"/>
                <Metric icon={<Ticket size={18}/>} label="Jira" value={String(jiraCount)} hint="Project connections"/>
                <Metric icon={<MessageSquare size={18}/>} label="Slack" value={String(slackCount)} hint="Channel connections"/>
                <Metric icon={<GitBranch size={18}/>} label="Open incidents" value={String(openIncidents.length)} hint="Ready for response"/>
              </div>

              <section className="panel">
                <div className="panel-heading"><div><span className="eyebrow">CONNECTORS</span><h3>External tools</h3><p className="muted small-copy">Tokens are encrypted at rest. Create a connection and run a read-only test before using it in a workflow.</p></div><ShieldCheck size={20}/></div>
                <div className="agent-connector-grid">
                  {integrations.map((item)=>(
                    <article className="agent-connector-card" key={item.id}>
                      <div className="agent-connector-title">
                        {item.provider==="github"?<Github size={20}/>:item.provider==="jira"?<Ticket size={20}/>:<MessageSquare size={20}/>}
                        <div className="grow"><strong>{item.name}</strong><small>{item.provider.toUpperCase()} · {Object.values(item.config).filter(Boolean).join(" · ")}</small></div>
                        <span className={cx("state-tag",item.status==="connected"?"good":item.status==="error"?"bad":"pending")}>{item.status}</span>
                      </div>
                      <div className="agent-connector-actions">
                        <span className="muted small-copy">{item.last_tested_at ? "Tested " + new Date(item.last_tested_at).toLocaleString() : "Not tested"}</span>
                        <div className="row-gap-actions">
                          <button className="small-button" onClick={()=>testIntegration(item.id)} disabled={busy==="test:"+item.id}>{busy==="test:"+item.id?<Loader2 className="spin" size={13}/>:<RefreshCw size={13}/>} Test again</button>
                          <button className="small-button" onClick={()=>deleteIntegration(item.id)} disabled={busy==="delete:"+item.id}><Trash2 size={13}/> Remove</button>
                        </div>
                      </div>
                    </article>
                  ))}
                  {!integrations.length && <div className="empty-state agent-empty"><CircleDashed size={24}/><strong>No connectors yet</strong><p>Add GitHub, Jira or Slack below.</p></div>}
                </div>

                <form className="agent-form" onSubmit={createIntegration}>
                  <div className="form-row">
                    <label>Provider
                      <select value={provider} onChange={(event)=>setProvider(event.target.value as "github"|"jira"|"slack")}>
                        <option value="github">GitHub</option><option value="jira">Jira Cloud</option><option value="slack">Slack</option>
                      </select>
                    </label>
                    <label>Connection name<input value={connectionName} onChange={(event)=>setConnectionName(event.target.value)} required maxLength={100}/></label>
                  </div>
                  {provider==="github" && <label>Repository<input value={repository} onChange={(event)=>setRepository(event.target.value)} required placeholder="owner/repository"/></label>}
                  {provider==="slack" && <label>Slack channel ID<input value={channelId} onChange={(event)=>setChannelId(event.target.value)} required placeholder="C0123456789"/></label>}
                  {provider==="jira" && <>
                    <div className="form-row"><label>Jira Cloud site URL<input value={jiraUrl} onChange={(event)=>setJiraUrl(event.target.value)} required placeholder="https://company.atlassian.net"/></label><label>Account email<input type="email" value={jiraEmail} onChange={(event)=>setJiraEmail(event.target.value)} required placeholder="you@company.com"/></label></div>
                    <div className="form-row"><label>Project key<input value={jiraProject} onChange={(event)=>setJiraProject(event.target.value)} required placeholder="DATA"/></label><label>Issue type<input value={jiraIssueType} onChange={(event)=>setJiraIssueType(event.target.value)} required placeholder="Task"/></label></div>
                  </>}
                  <label>{provider==="github"?"Fine-grained personal access token":provider==="jira"?"Atlassian API token":"Slack bot token"}
                    <input type="password" value={secret} onChange={(event)=>setSecret(event.target.value)} required autoComplete="new-password" placeholder="Paste token; it will not be shown again"/>
                  </label>
                  <div className="agent-form-footer">
                    <p className="muted small-copy">
                      {provider==="github"?"Requires Issues: write on the selected repository.":
                       provider==="jira"?"The account must be allowed to create issues in the selected project.":
                       "Requires chat:write. Invite the bot into the target channel."}
                    </p>
                    <button className="primary-button" disabled={busy==="integration"||!workspaceId||!secret.trim()}>{busy==="integration"?<Loader2 className="spin" size={16}/>:<Plus size={16}/>} Save & test connector</button>
                  </div>
                </form>
              </section>

              <section className="panel">
                <div className="panel-heading"><div><span className="eyebrow">MULTI-STEP AGENT</span><h3>Prepare an incident response</h3><p className="muted small-copy">The agent reads evidence, creates engineering tickets, then notifies your team. Nothing is posted until you approve the plan.</p></div><Bot size={20}/></div>
                {openIncidents.length ? (
                  <form className="agent-form" onSubmit={preparePlan}>
                    <label>Incident
                      <select value={incidentId} onChange={(event)=>setIncidentId(event.target.value)} required>
                        <option value="">Select an open incident</option>
                        {openIncidents.map((item)=><option key={item.id} value={item.id}>{item.title}</option>)}
                      </select>
                    </label>
                    <div className="agent-destination-choices">
                      <strong>Destinations to include</strong>
                      {integrations.map((item)=>(
                        <label className="agent-check" key={item.id}>
                          <input type="checkbox" checked={selectedIntegrationIds.includes(item.id)} onChange={(event)=>toggleIntegration(item.id,event.target.checked)} disabled={item.status!=="connected"}/>
                          <span><b>{item.provider.toUpperCase()}</b> · {item.name} <small>{item.status!=="connected"?"Test this connector first":Object.values(item.config).filter(Boolean).join(" · ")}</small></span>
                        </label>
                      ))}
                      {!integrations.length && <p className="muted small-copy">Connect at least one external tool above.</p>}
                    </div>
                    <div><button className="primary-button" disabled={busy==="plan"||!incidentId||!selectedIntegrationIds.length}>{busy==="plan"?<Loader2 className="spin" size={16}/>:<Bot size={16}/>} Prepare workflow plan</button></div>
                  </form>
                ) : <div className="empty-state"><ShieldCheck size={25}/><strong>No open incidents available</strong><p>Create a local CSV dataset, add a failing contract and run quality checks to generate one. DataHub is optional.</p></div>}
              </section>

              {activeWorkflow && <section className="panel">
                <div className="panel-heading"><div><span className="eyebrow">PLAN REVIEW</span><h3>Incident response workflow</h3><p className="muted small-copy">{activeWorkflow.plan.summary || activeWorkflow.incident_title}</p></div><WorkflowStatus status={activeWorkflow.status}/></div>
                <div className="agent-plan-meta"><span>Planner: {activeWorkflow.plan.planner || "evidence-based"}</span><span>{activeWorkflow.steps.length} ordered steps</span><span>{activeWorkflow.plan.requires_approval?"Approval required":"No approval required"}</span></div>
                <div className="agent-steps">
                  {activeWorkflow.steps.map((step)=>(
                    <article className="agent-step" key={step.id}>
                      <div className="agent-step-number">{step.position+1}</div>
                      <div className="grow"><div className="agent-step-heading"><strong>{step.title}</strong><WorkflowStatus status={step.status}/></div><p>{step.description}</p>
                        {step.error && <div className="agent-step-error">{step.error}</div>}
                        {step.result?.url && <a href={step.result.url} target="_blank" rel="noreferrer" className="agent-result-link">Open created item ↗</a>}
                        {step.result?.key && <span className="agent-result-key">{step.result.key}</span>}
                      </div>
                    </article>
                  ))}
                </div>
                {activeWorkflow.status==="awaiting_approval" && <div className="agent-approve-row"><p className="muted small-copy">Review the evidence and exact connector destinations above. Approval permits the worker to create tickets and post to Slack.</p><button className="primary-button" onClick={approvePlan} disabled={busy==="approve"}>{busy==="approve"?<Loader2 className="spin" size={16}/>:<CheckCircle2 size={16}/>} Approve & execute</button></div>}
                {["queued","running"].includes(activeWorkflow.status) && <div className="running-state"><Loader2 className="spin" size={20}/><div><strong>Executing approved workflow</strong><p className="muted">This view refreshes automatically.</p></div></div>}
              </section>}

              <section className="panel">
                <div className="panel-heading"><div><span className="eyebrow">HISTORY</span><h3>Agent runs</h3></div><span className="count-tag">{workflows.length} saved</span></div>
                {workflows.length ? <div className="run-list">{workflows.map((row)=>(
                  <button className="run-row" key={row.id} onClick={()=>refreshWorkflow(row.id)}>
                    <div className="run-symbol"><Bot size={16}/></div>
                    <div className="grow"><strong>{row.incident_title || "Incident response"}</strong><small>{row.created_at?new Date(row.created_at).toLocaleString():""}</small></div>
                    <WorkflowStatus status={row.status}/>
                  </button>
                ))}</div> : <p className="muted small-copy">Approved and prepared workflows will appear here.</p>}
              </section>
            </>
          )}
          <footer className="footer"><span>TESTAGENT · WORKSPACE AUTOMATION</span><span>External writes require explicit approval</span></footer>
        </div>
      </section>
    </main>
  );
}

function Metric({icon,label,value,hint}:{icon:ReactNode;label:string;value:string;hint:string}) {
  return <div className="metric-card"><div className="metric-top"><span className="metric-icon">{icon}</span><span>{label}</span></div><strong>{value}</strong><small>{hint}</small></div>;
}
function WorkflowStatus({status}:{status:string}) {
  const good = status==="completed";
  const bad = status==="failed" || status==="partial_failed";
  return <span className={cx("state-tag",good?"good":bad?"bad":"pending")}>{status.replace(/_/g," ")}</span>;
}
