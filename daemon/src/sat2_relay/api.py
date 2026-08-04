from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import HTMLResponse

from .config import LocalConfig
from .db import RelayDB
from .decisions import DecisionEngine, DecisionError
from .github import GitHubClient
from .models import DecisionSubmission, DeliveryResult, Heartbeat, HumanConfirmation, TaskAuthorizationRequest
from .service import RelayService

LOG = logging.getLogger(__name__)


DASHBOARD = r"""<!doctype html>
<html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>SAT2 Relay 2.2</title><style>
:root{font-family:Inter,system-ui,sans-serif;color:#18202a;background:#f4f6f8}body{margin:0}.wrap{max-width:1280px;margin:auto;padding:20px}.bar{display:flex;gap:8px;flex-wrap:wrap;align-items:center}.card{background:white;border:1px solid #dce2e8;border-radius:12px;padding:16px;margin:12px 0;box-shadow:0 1px 4px #0000000b}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}.ok{color:#08764f}.bad{color:#b42318}.warn{color:#b54708}.muted{color:#667085;font-size:13px}button,input{padding:8px 10px;border:1px solid #cdd5df;border-radius:7px;background:white}button{cursor:pointer}.primary{background:#1f6feb;color:white;border-color:#1f6feb}pre{white-space:pre-wrap;word-break:break-word;background:#101828;color:#e5e7eb;padding:12px;border-radius:8px;max-height:420px;overflow:auto}table{width:100%;border-collapse:collapse;font-size:13px}th,td{text-align:left;padding:7px;border-bottom:1px solid #eaecf0;vertical-align:top}.pill{padding:2px 7px;border-radius:99px;background:#eef2f6;font-size:12px}.error{background:#fff1f0;border-color:#fecdca}.success{background:#ecfdf3;border-color:#abefc6}
</style></head><body><div class='wrap'>
<h1>SAT2 Relay Control Center 2.2</h1><p class='muted'>文档引导、多 Session 双向自动推进、本地发布闸门与恢复。</p>
<div class='card'><div class='bar'><input id='token' type='password' size='48' placeholder='本地 API token'><button onclick='saveToken()'>保存</button><button class='primary' onclick='pollNow()'>立即轮询</button><button onclick='doctor()'>深度自检</button><button onclick='reloadCredentials()'>重载凭据</button><button onclick='diagnostics()'>导出诊断包</button><button onclick='refresh()'>刷新</button></div><div id='health'></div></div><div class='card'><h3>首次任务授权</h3><div class='bar'><input id='authTask' placeholder='task_id，例如 P0-B-WP-B'><input id='authSummary' size='64' value='Authorize the current source-only task within the reviewed task specification.'><button onclick='previewAuth()'>预览</button><button class='primary' onclick='authorizeTask()'>确认并发布授权</button></div><p class='muted'>Relay 自动读取 PR、head、task spec、角色与路径；无需手写 YAML。</p></div>
<div class='grid'><div class='card'><h3>任务</h3><div id='tasks'></div></div><div class='card'><h3>投递队列</h3><div id='deliveries'></div></div><div class='card'><h3>发布 Outbox</h3><div id='outbox'></div></div></div>
<div class='card'><h3>未解决警报</h3><div id='alerts'></div></div>
<div class='card'><h3>最近评论处理</h3><div id='comments'></div></div>
<div class='card'><h3>诊断输出</h3><pre id='out'>尚未连接</pre></div>
<script>
const token=()=>localStorage.getItem('sat2RelayToken')||document.getElementById('token').value;
function saveToken(){localStorage.setItem('sat2RelayToken',document.getElementById('token').value);refresh()}
async function api(path,opt={}){opt.headers={...(opt.headers||{}),'X-SAT2-Relay-Token':token()};if(opt.body&&typeof opt.body!=='string'){opt.headers['Content-Type']='application/json';opt.body=JSON.stringify(opt.body)}const r=await fetch(path,opt);const t=await r.text();if(!r.ok)throw new Error('HTTP '+r.status+': '+t);return t?JSON.parse(t):null}
function esc(x){return String(x??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}
function table(rows,cols){if(!rows.length)return '<p class=muted>无</p>';return '<table><thead><tr>'+cols.map(c=>'<th>'+esc(c[0])+'</th>').join('')+'</tr></thead><tbody>'+rows.map(r=>'<tr>'+cols.map(c=>'<td>'+esc(typeof c[1]==='function'?c[1](r):r[c[1]])+'</td>').join('')+'</tr>').join('')+'</tbody></table>'}
async function refresh(){try{const s=await api('/api/v2/status');const h=s.health;document.getElementById('health').innerHTML=`<p class=${h.ok?'ok':'bad'}>服务 ${h.ok?'在线':'异常'} · ${esc(h.mode)} · 最近轮询 ${esc(h.last_poll||'尚无')} · 待投递 ${h.pending_deliveries}</p>`;document.getElementById('tasks').innerHTML=table(s.tasks||[],[['Task','task_id'],['State','state'],['PR',r=>'#'+r.pr_number],['SHA','sha'],['Updated','updated_at']]);document.getElementById('deliveries').innerHTML=table((s.deliveries||[]).slice(0,30),[['ID','id'],['Event','event_id'],['Role','target_role'],['Status','status'],['Attempts','attempt_count'],['Last code','last_code']]);document.getElementById('outbox').innerHTML=(s.outbox||[]).slice(0,30).map(r=>`<div class='card ${r.status==='blocked'?'error':''}'><b>#${esc(r.id)} ${esc(r.task_id)} · ${esc(r.decision)}</b><div>${esc(r.status)} · comment ${esc(r.github_comment_id||'-')} · ${esc(r.last_error_code||'')}</div>${r.status==='waiting_for_human'?`<button onclick='confirmOutbox(${r.id})'>确认 Mentor Accepted</button>`:''}</div>`).join('')||'<p class=muted>无</p>';const alerts=(s.alerts||[]).filter(a=>!a.resolved_at);document.getElementById('alerts').innerHTML=alerts.length?alerts.map(a=>`<div class='card error'><b>${esc(a.code)}</b> · ${esc(a.task_id||'')} · PR ${esc(a.pr_number||'')}<pre>${esc(a.detail)}</pre><button onclick='resolveAlert(${a.id})'>标记已解决</button></div>`).join(''):'<p class=ok>无未解决警报</p>';document.getElementById('comments').innerHTML=table((s.comments||[]).slice(0,30),[['PR','pr_number'],['Comment','comment_id'],['Outcome','outcome'],['Retries','retry_count'],['Error','last_error']]);document.getElementById('out').textContent=JSON.stringify(s.health,null,2)}catch(e){document.getElementById('health').innerHTML='<p class=bad>'+esc(e)+'</p>'}}
async function pollNow(){document.getElementById('out').textContent=JSON.stringify(await api('/api/v2/control/poll',{method:'POST'}),null,2);refresh()}
async function doctor(){document.getElementById('out').textContent=JSON.stringify(await api('/api/v2/doctor?deep=true'),null,2);refresh()}
async function reloadCredentials(){document.getElementById('out').textContent=JSON.stringify(await api('/api/v2/control/reload-credentials',{method:'POST'}),null,2);refresh()}
async function diagnostics(){const value=await api('/api/v2/diagnostics/export');const text=JSON.stringify(value,null,2);document.getElementById('out').textContent=text;try{await navigator.clipboard.writeText(text)}catch(e){}}
async function previewAuth(){const task=document.getElementById('authTask').value.trim();const summary=document.getElementById('authSummary').value.trim();document.getElementById('out').textContent=JSON.stringify(await api('/api/v2/tasks/'+encodeURIComponent(task)+'/authorize/preview',{method:'POST',body:{summary,confirm:false}}),null,2)}
async function authorizeTask(){const task=document.getElementById('authTask').value.trim();const summary=document.getElementById('authSummary').value.trim();if(!confirm('确认由 Relay 发布任务授权？'))return;document.getElementById('out').textContent=JSON.stringify(await api('/api/v2/tasks/'+encodeURIComponent(task)+'/authorize',{method:'POST',body:{summary,confirm:true}}),null,2);refresh()}
async function confirmOutbox(id){if(!confirm('确认发布 Mentor Accepted？'))return;document.getElementById('out').textContent=JSON.stringify(await api('/api/v2/outbox/'+id+'/confirm',{method:'POST',body:{confirm:true}}),null,2);refresh()}
async function resolveAlert(id){await api('/api/v2/alerts/'+id+'/resolve',{method:'POST'});refresh()}
document.getElementById('token').value=localStorage.getItem('sat2RelayToken')||'';refresh();setInterval(refresh,10000);
</script></div></body></html>"""


def _new_clients(local: LocalConfig) -> tuple[GitHubClient, GitHubClient]:
    primary = local.github_secret
    alert = local.github_alert_secret
    github = GitHubClient(primary.value, token_source=primary.source, token_fingerprint=primary.fingerprint)
    if alert.value == primary.value:
        return github, github
    return github, GitHubClient(alert.value, token_source=alert.source, token_fingerprint=alert.fingerprint)


def create_app(local: LocalConfig, db: RelayDB, service: RelayService, poll_enabled: bool = True) -> FastAPI:
    stop_event = asyncio.Event()
    poll_lock = asyncio.Lock()

    async def run_poll() -> dict:
        async with poll_lock:
            return await asyncio.to_thread(service.poll_once)

    async def poll_loop() -> None:
        interval = 10
        while not stop_event.is_set():
            try:
                result = await run_poll()
                config = service.repo_config
                interval = config.poll_interval_seconds if config else 15
                LOG.info("poll complete %s", result, extra={"stage": "poll"})
            except Exception as exc:  # noqa: BLE001
                LOG.exception("poll loop failed: %s", exc, extra={"stage": "poll"})
                db.set_meta("last_poll_error", str(exc))
                config = service.repo_config
                interval = config.error_retry_seconds if config else 10
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
            except TimeoutError:
                pass

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        task = asyncio.create_task(poll_loop()) if poll_enabled else None
        yield
        stop_event.set()
        if task:
            await task

    app = FastAPI(title="SAT2 Relay", version="2.2.0", lifespan=lifespan)
    decision_engine = DecisionEngine(service, db)

    def auth(x_sat2_relay_token: str | None = Header(default=None)) -> None:
        if not x_sat2_relay_token or not hmac.compare_digest(x_sat2_relay_token, local.api_token):
            raise HTTPException(status_code=401, detail="invalid local relay token")

    def health_payload() -> dict:
        heartbeat = db.latest_heartbeat()
        return {
            "ok": True,
            "version": "2.2.0",
            "mode": service.effective_mode().value,
            "repository": local.github_repository,
            "config_ref": local.repository_config_ref,
            "last_poll": db.get_meta("last_poll"),
            "last_poll_error": db.get_meta("last_poll_error"),
            "pending_deliveries": db.pending_delivery_count(),
            "pending_outbox": db.pending_outbox_count(),
            "open_alerts": db.open_alert_count(),
            "latest_open_alert": db.latest_open_alert(),
            "extension_last_seen": heartbeat["last_seen"] if heartbeat else None,
            "role_endpoints": db.fresh_role_endpoints(service.repo_config.extension_stale_seconds) if service.repo_config else [],
            "github_token_source": getattr(service.github, "token_source", "unknown"),
            "github_token_fingerprint": getattr(service.github, "token_fingerprint", None),
            "github_rate_limit": getattr(service.github, "rate_limit", {}),
            "time": datetime.now(UTC).isoformat(),
        }

    def diagnostic_payload() -> dict:
        snapshot = db.status_snapshot()
        deliveries = []
        for row in snapshot.get("deliveries", []):
            clean = dict(row)
            body = str(clean.pop("body", "") or "")
            clean["body_sha256"] = hashlib.sha256(body.encode()).hexdigest() if body else None
            clean["body_length"] = len(body)
            clean.pop("required_apps_json", None)
            clean.pop("delivery_token", None)
            deliveries.append(clean)
        heartbeats = []
        for row in snapshot.get("heartbeats", []):
            clean = dict(row)
            try:
                payload = json.loads(str(clean.pop("payload_json", "{}") or "{}"))
            except json.JSONDecodeError:
                payload = {"parse_error": True}
            clean["payload"] = payload
            heartbeats.append(clean)
        log_tail: list[str] = []
        try:
            if local.log_path.exists():
                log_tail = local.log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-250:]
        except OSError as exc:
            log_tail = [f"log read failed: {exc}"]
        outbox = []
        for row in snapshot.get("outbox", []):
            clean = dict(row)
            body = str(clean.pop("comment_body", "") or "")
            payload = str(clean.pop("event_payload_json", "") or "")
            clean["comment_body_sha256"] = hashlib.sha256(body.encode()).hexdigest() if body else None
            clean["event_payload_sha256"] = hashlib.sha256(payload.encode()).hexdigest() if payload else None
            outbox.append(clean)
        return {
            "generated_at": datetime.now(UTC).isoformat(),
            "version": "2.2.0",
            "health": health_payload(),
            "doctor": service.doctor(deep=True),
            "config": {
                "path": str(local.path),
                "repository": local.github_repository,
                "repository_config_ref": local.repository_config_ref,
                "repository_config_path": local.repository_config_path,
                "database": str(local.database_path),
                "log": str(local.log_path),
                "allow_github_writes": local.allow_github_writes,
            },
            "tasks": snapshot.get("tasks", []),
            "deliveries": deliveries,
            "outbox": outbox,
            "heartbeats": heartbeats,
            "alerts": snapshot.get("alerts", []),
            "comments": snapshot.get("comments", []),
            "meta": snapshot.get("meta", {}),
            "log_tail": log_tail,
            "redaction": {
                "github_tokens": "never included",
                "local_api_token": "never included",
                "delivery_bodies": "replaced with SHA-256 and length",
            },
        }

    @app.get("/", response_class=HTMLResponse)
    def dashboard() -> str:
        return DASHBOARD

    @app.get("/api/v2/health", dependencies=[Depends(auth)])
    @app.get("/api/v1/health", dependencies=[Depends(auth)])
    def health() -> dict:
        return health_payload()

    @app.get("/api/v2/status", dependencies=[Depends(auth)])
    @app.get("/api/v1/status", dependencies=[Depends(auth)])
    def status() -> dict:
        monitors = [monitor.model_dump(mode="json") for monitor in (service.repo_config.monitors if service.repo_config else [])]
        return {"health": health_payload(), "monitors": monitors, **db.status_snapshot()}

    @app.post("/api/v2/control/poll", dependencies=[Depends(auth)])
    @app.post("/api/v1/control/poll", dependencies=[Depends(auth)])
    async def poll() -> dict:
        return await run_poll()

    @app.post("/api/v2/control/reload-credentials", dependencies=[Depends(auth)])
    def reload_credentials() -> dict:
        github, alert = _new_clients(local)
        service.replace_github_clients(github, alert)
        doctor = service.doctor(deep=False)
        return {"ok": doctor["ok"], "github_token": doctor["github_token"], "doctor": doctor}

    @app.get("/api/v2/diagnostics/export", dependencies=[Depends(auth)])
    def diagnostics_export() -> dict:
        return diagnostic_payload()

    @app.get("/api/v2/doctor", dependencies=[Depends(auth)])
    def doctor(deep: bool = Query(default=True)) -> dict:
        return service.doctor(deep=deep)

    @app.post("/api/v2/extension/heartbeat", dependencies=[Depends(auth)])
    @app.post("/api/v1/extension/heartbeat", dependencies=[Depends(auth)])
    def heartbeat(payload: Heartbeat) -> dict:
        db.record_heartbeat(payload.installation_id, payload.extension_version, payload.model_dump_json())
        return {
            "ok": True,
            "server_time": datetime.now(UTC).isoformat(),
            "mode": service.effective_mode().value,
            "queue_pending": db.pending_delivery_count(),
            "open_alerts": db.open_alert_count(),
            "latest_open_alert": db.latest_open_alert(),
            "last_poll": db.get_meta("last_poll"),
            "last_poll_error": db.get_meta("last_poll_error"),
        }

    @app.get("/api/v2/deliveries/next", dependencies=[Depends(auth)])
    @app.get("/api/v1/deliveries/next", dependencies=[Depends(auth)])
    def next_delivery(installation_id: str = Query(min_length=8)) -> dict:
        config = service.repo_config
        if not config:
            raise HTTPException(status_code=503, detail="repository config unavailable")
        eligible_roles = db.bound_roles_for_installation(installation_id, config.extension_stale_seconds)
        delivery = db.lease_next(
            installation_id,
            config.delivery_lease_seconds,
            eligible_roles=eligible_roles,
        )
        return {
            "delivery": delivery.model_dump(mode="json") if delivery else None,
            "eligible_roles": sorted(eligible_roles),
        }

    @app.post("/api/v2/deliveries/{delivery_id}/result", dependencies=[Depends(auth)])
    @app.post("/api/v1/deliveries/{delivery_id}/result", dependencies=[Depends(auth)])
    def delivery_result(delivery_id: int, payload: DeliveryResult) -> dict:
        config = service.repo_config
        if not config:
            raise HTTPException(status_code=503, detail="repository config unavailable")
        try:
            status_value = db.complete_delivery(
                delivery_id,
                payload.success,
                payload.code,
                payload.detail,
                config.maximum_delivery_attempts,
                config.retry_delays_seconds,
                retryable=payload.retryable,
                observed_url=payload.observed_url,
                observed_message_marker=payload.observed_message_marker,
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="delivery not found") from None
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        if status_value == "failed":
            service._alert("error", "DELIVERY_FAILED", f"Delivery {delivery_id} failed: {payload.code} {payload.detail or ''}")
        return {"ok": True, "status": status_value}

    @app.post("/api/v2/deliveries/{delivery_id}/approve", dependencies=[Depends(auth)])
    @app.post("/api/v1/deliveries/{delivery_id}/approve", dependencies=[Depends(auth)])
    def approve(delivery_id: int) -> dict:
        if not db.approve_delivery(delivery_id):
            raise HTTPException(status_code=409, detail="delivery is not awaiting approval")
        return {"ok": True}

    @app.post("/api/v2/deliveries/{delivery_id}/cancel", dependencies=[Depends(auth)])
    @app.post("/api/v1/deliveries/{delivery_id}/cancel", dependencies=[Depends(auth)])
    def cancel(delivery_id: int) -> dict:
        if not db.cancel_delivery(delivery_id):
            raise HTTPException(status_code=409, detail="delivery cannot be cancelled in its current state")
        return {"ok": True}

    @app.post("/api/v2/decisions/submit", dependencies=[Depends(auth)])
    def submit_decision(payload: DecisionSubmission) -> dict:
        try:
            return decision_engine.submit(payload)
        except DecisionError as exc:
            status = 409 if exc.code not in {"ENDPOINT_NOT_BOUND", "NO_ACTIVE_DELIVERY"} else 404
            raise HTTPException(status_code=status, detail={"code": exc.code, "detail": exc.detail}) from None

    @app.post("/api/v2/outbox/{outbox_id}/confirm", dependencies=[Depends(auth)])
    def confirm_outbox(outbox_id: int, payload: HumanConfirmation) -> dict:
        if not payload.confirm:
            raise HTTPException(status_code=400, detail="confirmation must be true")
        try:
            return {"ok": True, "outbox": decision_engine.confirm(outbox_id)}
        except DecisionError as exc:
            raise HTTPException(status_code=409, detail={"code": exc.code, "detail": exc.detail}) from None

    @app.post("/api/v2/outbox/recover", dependencies=[Depends(auth)])
    def recover_outbox() -> dict:
        return {"ok": True, "result": decision_engine.recover()}

    @app.post("/api/v2/tasks/{task_id}/authorize/preview", dependencies=[Depends(auth)])
    def preview_authorization(task_id: str, payload: TaskAuthorizationRequest) -> dict:
        try:
            return {"ok": True, "preview": decision_engine.preview_authorization(task_id, payload.summary)}
        except DecisionError as exc:
            raise HTTPException(status_code=409, detail={"code": exc.code, "detail": exc.detail}) from None

    @app.post("/api/v2/tasks/{task_id}/authorize", dependencies=[Depends(auth)])
    def authorize_task(task_id: str, payload: TaskAuthorizationRequest) -> dict:
        if not payload.confirm:
            raise HTTPException(status_code=409, detail={"code": "HUMAN_CONFIRMATION_REQUIRED", "detail": "Set confirm=true after reviewing the preview"})
        try:
            return decision_engine.authorize(task_id, payload.summary)
        except DecisionError as exc:
            raise HTTPException(status_code=409, detail={"code": exc.code, "detail": exc.detail}) from None

    @app.post("/api/v2/comments/{pr_number}/{comment_id}/replay", dependencies=[Depends(auth)])
    def replay_comment(pr_number: int, comment_id: int) -> dict:
        if not db.mark_comment_for_replay(local.github_repository, pr_number, comment_id):
            raise HTTPException(status_code=404, detail="comment processing record not found")
        return {"ok": True}

    @app.post("/api/v2/alerts/{alert_id}/resolve", dependencies=[Depends(auth)])
    def resolve_alert(alert_id: int) -> dict:
        if not db.resolve_alert(alert_id):
            raise HTTPException(status_code=404, detail="open alert not found")
        return {"ok": True}

    return app
