# -*- coding: utf-8 -*-
"""
需求接收网关 + Harness 仓库联动 + 实时 Pipeline 可视化

功能：
  1. /api/repos             — 列出 Harness 空间下的仓库
  2. /api/trigger            — 触发 pipeline
  3. /api/submit             — 手动提交需求
  4. /api/runs               — 查看运行状态
  5. /api/runs/{id}/events   — SSE 实时事件流
  6. /                       — 前端 UI（含流程进度图）
"""
import sys
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

import json
import os
import threading
import time
import uuid
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HARNESS_BASE = os.environ.get("HARNESS_BASE_URL", "http://localhost:3000")
HARNESS_TOKEN = os.environ.get("HARNESS_TOKEN", "")
HARNESS_SPACE = os.environ.get("HARNESS_SPACE", "test")

_runs: dict[str, dict] = {}
_daemon_callback = None


def set_daemon_callback(fn):
    global _daemon_callback
    _daemon_callback = fn


def _harness_headers():
    return {
        "Authorization": f"Bearer {HARNESS_TOKEN}",
        "Content-Type": "application/json",
    }


def _fetch_harness_repos() -> list[dict]:
    if not HARNESS_TOKEN:
        return []
    try:
        resp = requests.get(
            f"{HARNESS_BASE}/api/v1/spaces/{HARNESS_SPACE}/repos",
            headers=_harness_headers(),
            params={"limit": 50, "order": "desc"},
            timeout=5,
        )
        if resp.status_code == 200:
            return resp.json() if isinstance(resp.json(), list) else []
    except Exception as e:
        print(f"[Intake] 获取仓库失败: {e}", flush=True)
    return []


def _trigger_pipeline(repo_path: str, description: str) -> dict:
    run_id = str(uuid.uuid4())[:8]
    _runs[run_id] = {
        "id": run_id,
        "requirement": description,
        "repo_path": repo_path,
        "status": "queued",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "pr_url": None,
        "review_score": None,
        "review_text": "",
        "report_url": f"/api/runs/{run_id}",
    }

    if _daemon_callback:
        threading.Thread(
            target=_daemon_callback,
            args=(run_id, description, repo_path),
            daemon=True,
        ).start()

    print(f"[Intake] 触发 pipeline: run_id={run_id}, repo={repo_path}", flush=True)
    return {"run_id": run_id, "status": "queued"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/" or path == "/index.html":
            self._serve_ui()
        elif path == "/api/runs":
            self._serve_json(_runs)
        elif path == "/api/repos":
            self._handle_repos()
        elif "/events" in path and path.startswith("/api/runs/"):
            parts = path.split("/")
            run_id = parts[3] if len(parts) >= 5 else ""
            self._serve_sse(run_id)
        elif path.startswith("/api/runs/"):
            run_id = path.split("/")[-1]
            self._serve_run_detail(run_id)
        else:
            self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        body = json.loads(raw.decode("utf-8", errors="replace"))

        if path == "/api/submit":
            self._handle_submit(body)
        elif path == "/api/trigger":
            self._handle_trigger(body)
        elif "/abort" in path and path.startswith("/api/runs/"):
            parts = path.split("/")
            run_id = parts[3] if len(parts) >= 5 else ""
            self._handle_abort(run_id)
        else:
            self.send_error(404)

    def _handle_abort(self, run_id: str):
        if run_id not in _runs:
            self._serve_json({"error": "run not found"}, 404)
            return
        try:
            import ws_server
            ws_server.set_abort(run_id)
        except ImportError:
            pass
        try:
            import pipeline
            pipeline.set_abort(run_id)
        except ImportError:
            pass
        _runs[run_id]["status"] = "failed"
        self._serve_json({"ok": True, "run_id": run_id})

    def _handle_repos(self):
        repos = _fetch_harness_repos()
        result = []
        for r in repos:
            result.append({
                "identifier": r.get("identifier", ""),
                "path": r.get("path", ""),
                "description": r.get("description", ""),
                "is_empty": r.get("is_empty", True),
                "git_url": r.get("git_url", ""),
                "created": r.get("created", 0),
            })
        self._serve_json(result)

    def _handle_trigger(self, body):
        repo_path = body.get("repo_path", "").strip()
        if not repo_path:
            self._serve_json({"error": "repo_path is required"}, 400)
            return

        repos = _fetch_harness_repos()
        description = ""
        for r in repos:
            if r.get("path") == repo_path or r.get("identifier") == repo_path:
                description = (r.get("description") or "").strip()
                repo_path = r.get("path", repo_path)
                break

        if not description:
            description = body.get("requirement", "").strip()

        if not description:
            self._serve_json({"error": "repo has no description and no requirement provided"}, 400)
            return

        result = _trigger_pipeline(repo_path, description)
        self._serve_json(result)

    def _handle_submit(self, body):
        requirement = body.get("requirement", "").strip()
        repo_path = body.get("repo_path", "").strip()

        if not requirement:
            self._serve_json({"error": "requirement is empty"}, 400)
            return

        result = _trigger_pipeline(repo_path, requirement)
        self._serve_json(result)

    def _serve_json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False, indent=2,
                          default=lambda o: str(o)).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _serve_run_detail(self, run_id: str):
        bus_file = Path("runs") / run_id / "bus.json"
        if not bus_file.exists():
            self._serve_json({"error": "run not found"}, 404)
            return
        data = json.loads(bus_file.read_text(encoding="utf-8"))
        run_info = _runs.get(run_id, {})
        self._serve_json({
            "run": run_info,
            "messages": data,
            "review_score": run_info.get("review_score"),
            "review_text": run_info.get("review_text", ""),
        })

    def _serve_sse(self, run_id: str):
        """Server-Sent Events: poll bus.json and stream new messages."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        bus_file = Path("runs") / run_id / "bus.json"
        seen = 0
        try:
            for _ in range(600):
                if bus_file.exists():
                    try:
                        data = json.loads(bus_file.read_text(encoding="utf-8"))
                    except Exception:
                        data = []
                    if len(data) > seen:
                        for msg in data[seen:]:
                            payload = json.dumps(msg, ensure_ascii=False)
                            self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                        self.wfile.flush()
                        seen = len(data)

                run_info = _runs.get(run_id, {})
                if run_info.get("status") in ("done", "failed"):
                    status_msg = json.dumps({"event_type": "__status__", "status": run_info["status"],
                                             "review_score": run_info.get("review_score")}, ensure_ascii=False)
                    self.wfile.write(f"data: {status_msg}\n\n".encode("utf-8"))
                    self.wfile.flush()
                    if seen > 0:
                        break
                time.sleep(1)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _serve_ui(self):
        html = _build_html()
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)


def _build_html() -> str:
    return ("""<!DOCTYPE html>
<html lang="zh"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>AI Agent Pipeline</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/css/xterm.min.css">
<script src="https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/lib/xterm.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/@xterm/addon-fit@0.10.0/lib/addon-fit.min.js"></script>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f172a;min-height:100vh;color:#e2e8f0}
.top-bar{background:#1e293b;border-bottom:1px solid #334155;padding:10px 24px;display:flex;align-items:center;justify-content:space-between}
.top-bar h1{font-size:18px;color:#f1f5f9;font-weight:600}
.top-bar .links a{color:#818cf8;font-size:13px;text-decoration:none;margin-left:16px}
.main{display:flex;height:calc(100vh - 45px)}
.sidebar{width:280px;background:#1e293b;border-right:1px solid #334155;display:flex;flex-direction:column;overflow:hidden;flex-shrink:0}
.sidebar h2{font-size:12px;text-transform:uppercase;letter-spacing:1px;color:#64748b;padding:12px 12px 6px;font-weight:600}
.repo-list{flex:1;overflow-y:auto;padding:0 6px 6px}
.repo-card{background:#0f172a;border:1px solid #334155;border-radius:6px;padding:10px;margin-bottom:6px;cursor:default;transition:border-color .15s}
.repo-card:hover{border-color:#4f46e5}
.repo-card .name{font-weight:600;font-size:13px;color:#f1f5f9}
.repo-card .path{font-size:10px;color:#64748b;margin-top:1px}
.repo-card .desc{font-size:11px;color:#94a3b8;margin-top:4px;line-height:1.3}
.repo-card .desc.empty{color:#475569;font-style:italic}
.repo-card .actions{margin-top:6px;display:flex;align-items:center;gap:6px}
.btn{padding:4px 12px;border-radius:5px;border:none;cursor:pointer;font-size:11px;font-weight:500;transition:all .15s}
.btn-trigger{background:#4f46e5;color:#fff}.btn-trigger:hover{background:#4338ca}.btn-trigger:disabled{background:#3730a3;color:#818cf8;cursor:not-allowed}
.btn-open{background:transparent;color:#818cf8;border:1px solid #4f46e5}.btn-open:hover{background:#1e1b4b}
.btn-danger{background:#dc2626;color:#fff}.btn-danger:hover{background:#b91c1c}
.btn-cmd{background:#334155;color:#e2e8f0;border:1px solid #475569}.btn-cmd:hover{background:#475569}
.content{flex:1;display:flex;flex-direction:column;overflow:hidden}
.pipeline-header{padding:10px 20px;background:#1e293b;border-bottom:1px solid #334155;display:flex;align-items:center;justify-content:space-between;flex-shrink:0}
.pipeline-header .info{font-size:13px;color:#94a3b8}.pipeline-header .info strong{color:#f1f5f9}
.pipeline-flow{padding:16px;display:flex;align-items:flex-start;gap:0;justify-content:center;flex-shrink:0}
.stage{display:flex;flex-direction:column;align-items:center;min-width:100px}
.stage-icon{width:48px;height:48px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:20px;border:3px solid #334155;background:#1e293b;transition:all .4s;position:relative}
.stage-icon .spin{display:none;position:absolute;inset:-4px;border-radius:50%;border:3px solid transparent;border-top-color:#818cf8;animation:spin 1s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.stage.active .stage-icon{border-color:#818cf8;box-shadow:0 0 16px rgba(129,140,248,.3)}.stage.active .stage-icon .spin{display:block}
.stage.done .stage-icon{border-color:#34d399;background:#064e3b}.stage.fail .stage-icon{border-color:#f87171;background:#7f1d1d}
.stage-label{margin-top:6px;font-size:11px;color:#64748b;font-weight:500}
.stage.active .stage-label{color:#818cf8}.stage.done .stage-label{color:#34d399}.stage.fail .stage-label{color:#f87171}
.stage-detail{font-size:10px;color:#475569;margin-top:2px;max-width:100px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;text-align:center}
.arrow{display:flex;align-items:center;padding-top:8px}.arrow svg{width:32px;height:16px}
.arrow svg line{stroke:#334155;stroke-width:2}.arrow svg polygon{fill:#334155}
.arrow.done svg line{stroke:#34d399}.arrow.done svg polygon{fill:#34d399}
.arrow.active svg line{stroke:#818cf8}.arrow.active svg polygon{fill:#818cf8}
.control-bar{display:flex;align-items:center;gap:8px;padding:8px 16px;background:#1e293b;border-top:1px solid #334155;border-bottom:1px solid #334155;flex-shrink:0}
.term-tabs{display:flex;gap:0;margin-left:auto}
.term-tab{padding:4px 14px;font-size:11px;cursor:pointer;background:#0f172a;color:#64748b;border:1px solid #334155;transition:all .15s}
.term-tab:first-child{border-radius:5px 0 0 5px}.term-tab:last-child{border-radius:0 5px 5px 0}
.term-tab.active{background:#334155;color:#e2e8f0;border-color:#4f46e5}
.terminal-area{flex:1;display:flex;overflow:hidden;position:relative}
.term-pane{position:absolute;inset:0;display:none;padding:4px}
.term-pane.active{display:block}
.term-pane .xterm{height:100%}
.review-bar{margin:0 16px;padding:10px 16px;background:#1e293b;border:1px solid #334155;border-radius:6px;flex-shrink:0;display:none;max-height:180px;overflow-y:auto}
.review-bar .hdr{display:flex;justify-content:space-between;align-items:center;margin-bottom:6px}
.review-bar .hdr span{font-size:13px;font-weight:600;color:#f1f5f9}
#reviewScoreBadge{font-size:11px;padding:2px 8px;border-radius:10px}
#reviewContent{font-size:12px;color:#94a3b8;line-height:1.5;white-space:pre-wrap}
.empty-state{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;color:#475569}
.empty-state svg{width:64px;height:64px;margin-bottom:12px;opacity:.3}
.empty-state p{font-size:13px;margin-top:6px}
.toast{position:fixed;top:12px;right:12px;padding:8px 16px;border-radius:6px;color:#fff;font-size:12px;z-index:999;display:none;animation:fadeIn .3s}
.toast.show{display:block}.toast.success{background:#059669}.toast.error{background:#dc2626}
@keyframes fadeIn{from{opacity:0;transform:translateY(-8px)}to{opacity:1}}
.btn-chat{background:#0e7490;color:#fff;border:none}.btn-chat:hover{background:#0891b2}
.btn-chat.active{background:#0891b2;box-shadow:0 0 0 2px #22d3ee}
.chat-bar{display:flex;align-items:center;gap:8px;padding:6px 16px;background:#0c1929;border-top:1px solid #164e63;flex-shrink:0}
.chat-input{flex:1;background:#0f172a;color:#e2e8f0;border:1px solid #164e63;border-radius:5px;padding:5px 10px;font-size:12px;outline:none;font-family:inherit}
.chat-input:focus{border-color:#22d3ee}
.btn-chat-send{background:#0e7490;color:#fff;border:none;padding:5px 14px;border-radius:5px;font-size:12px;cursor:pointer;white-space:nowrap}.btn-chat-send:hover{background:#0891b2}
</style></head>
<body>
<div class="top-bar">
  <h1>AI Agent Pipeline</h1>
  <div class="links"><a href="__HARNESS_BASE__" target="_blank">Harness</a><a href="__HARNESS_BASE__/spaces/__HARNESS_SPACE__/repos" target="_blank">Repositories</a></div>
</div>
<div class="main">
  <div class="sidebar">
    <h2>Repositories</h2>
    <div class="repo-list" id="repoList"></div>
  </div>
  <div class="content" id="contentArea">
    <div class="empty-state" id="emptyState">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M9 3H5a2 2 0 00-2 2v4m6-6h10a2 2 0 012 2v4M9 3v18m0 0h10a2 2 0 002-2V9M9 21H5a2 2 0 01-2-2V9m0 0h18"/></svg>
      <p>Select a repository and click <strong>Trigger</strong> to start</p>
    </div>
    <div id="pipelineView" style="display:none;flex-direction:column;flex:1;overflow:hidden">
      <div class="pipeline-header">
        <div class="info">Pipeline <strong id="pvRunId"></strong> <span id="pvRepo" style="color:#64748b"></span></div>
        <div class="info" id="pvStatus"></div>
      </div>
      <div class="pipeline-flow">
        <div class="stage" data-stage="plan"><div class="stage-icon"><div class="spin"></div>&#128221;</div><div class="stage-label">Plan</div><div class="stage-detail" id="detailPlan"></div></div>
        <div class="arrow" data-arrow="0"><svg viewBox="0 0 32 16"><line x1="0" y1="8" x2="24" y2="8"/><polygon points="24,4 32,8 24,12"/></svg></div>
        <div class="stage" data-stage="code"><div class="stage-icon"><div class="spin"></div>&#128187;</div><div class="stage-label">Code</div><div class="stage-detail" id="detailCode"></div></div>
        <div class="arrow" data-arrow="1"><svg viewBox="0 0 32 16"><line x1="0" y1="8" x2="24" y2="8"/><polygon points="24,4 32,8 24,12"/></svg></div>
        <div class="stage" data-stage="deliver"><div class="stage-icon"><div class="spin"></div>&#128640;</div><div class="stage-label">Deliver</div><div class="stage-detail" id="detailDeliver"></div></div>
        <div class="arrow" data-arrow="2"><svg viewBox="0 0 32 16"><line x1="0" y1="8" x2="24" y2="8"/><polygon points="24,4 32,8 24,12"/></svg></div>
        <div class="stage" data-stage="review"><div class="stage-icon"><div class="spin"></div>&#128270;</div><div class="stage-label">Review</div><div class="stage-detail" id="detailReview"></div></div>
      </div>
      <div class="review-bar" id="reviewPanel">
        <div class="hdr"><span>Code Review</span><span id="reviewScoreBadge"></span></div>
        <div id="reviewContent"></div>
      </div>
      <div class="control-bar" id="controlBar">
        <button class="btn btn-danger" onclick="doAbort()" title="Abort">Abort</button>
        <button class="btn btn-cmd" onclick="sendCmd('/plan')">Plan</button>
        <button class="btn btn-cmd" onclick="sendCmd('/code')">Code</button>
        <button class="btn btn-cmd" onclick="sendCmd('/review')">Review</button>
        <button class="btn btn-cmd" onclick="sendCmd('/status')">Status</button>
        <button class="btn btn-chat" id="chatBtn" onclick="toggleChatInput()" title="Chat with agent after review">Chat</button>
        <div class="term-tabs">
          <div class="term-tab active" onclick="switchTab('agent')">Agent</div>
          <div class="term-tab" onclick="switchTab('shell')">Shell</div>
        </div>
      </div>
      <div class="chat-bar" id="chatBar" style="display:none">
        <input id="chatInput" class="chat-input" type="text" placeholder="基于 Review 反馈，告诉 Agent 如何优化代码... (Enter 发送)" autocomplete="off"/>
        <button class="btn btn-chat-send" onclick="doSendChat()">发送</button>
      </div>
      <div class="terminal-area">
        <div class="term-pane active" id="paneAgent"></div>
        <div class="term-pane" id="paneShell"></div>
      </div>
    </div>
  </div>
</div>
<div id="toast" class="toast"></div>
<script>
const HB='__HARNESS_BASE__',HS='__HARNESS_SPACE__',WS_PORT=3002;
let activeRunId=null,activeES=null,seenMsgIds=new Set(),activeRepoPath='';
let agentTerm=null,shellTerm=null,agentWs=null,shellWs=null;
let agentFit=null,shellFit=null;
let agentInputBuf='';

function toast(m,t){const e=document.getElementById('toast');e.textContent=m;e.className='toast show '+t;setTimeout(()=>e.className='toast',3500)}

/* ── Terminal Init ── */
function initTerminals(){
  if(agentTerm)return;
  const fitAddon1=new FitAddon.FitAddon();
  agentTerm=new Terminal({cursorBlink:true,fontSize:13,fontFamily:"'Fira Code',monospace",theme:{background:'#0f172a',foreground:'#e2e8f0',cursor:'#818cf8',selectionBackground:'#334155'}});
  agentTerm.loadAddon(fitAddon1);agentFit=fitAddon1;
  agentTerm.open(document.getElementById('paneAgent'));
  fitAddon1.fit();
  agentTerm.writeln('\\x1b[36m[Agent Terminal]\\x1b[0m Type /help for commands');
  agentTerm.write('\\r\\n\\x1b[32m> \\x1b[0m');
  agentTerm.onData(data=>{
    if(data==='\\r'){
      agentTerm.write('\\r\\n');
      if(agentInputBuf.trim()){sendCmd(agentInputBuf.trim())}
      agentInputBuf='';
      agentTerm.write('\\x1b[32m> \\x1b[0m');
    }else if(data==='\\x7f'){
      if(agentInputBuf.length>0){agentInputBuf=agentInputBuf.slice(0,-1);agentTerm.write('\\b \\b')}
    }else{
      agentInputBuf+=data;agentTerm.write(data);
    }
  });

  const fitAddon2=new FitAddon.FitAddon();
  shellTerm=new Terminal({cursorBlink:true,fontSize:13,fontFamily:"'Fira Code',monospace",theme:{background:'#0f172a',foreground:'#e2e8f0',cursor:'#34d399',selectionBackground:'#334155'}});
  shellTerm.loadAddon(fitAddon2);shellFit=fitAddon2;
  shellTerm.open(document.getElementById('paneShell'));
  fitAddon2.fit();

  window.addEventListener('resize',()=>{agentFit.fit();shellFit.fit()});
  connectShellWs();
}

function connectAgentWs(runId){
  if(agentWs){agentWs.close();agentWs=null}
  const url='ws://'+location.hostname+':'+WS_PORT+'/ws/agent/'+runId;
  agentWs=new WebSocket(url);
  agentWs.onmessage=function(ev){
    try{
      const msg=JSON.parse(ev.data);
      if(msg.type==='chunk'){
        const c=msg.stage==='plan'?'\\x1b[96m':msg.stage==='code'?'\\x1b[93m':msg.stage==='review'?'\\x1b[95m':'\\x1b[37m';
        agentTerm.write(c+msg.text.replace(/\\n/g,'\\r\\n')+'\\x1b[0m');
      }else if(msg.type==='system'){
        agentTerm.write('\\x1b[33m'+msg.text.replace(/\\n/g,'\\r\\n')+'\\x1b[0m');
      }else if(msg.type==='event'){
        agentTerm.write('\\r\\n\\x1b[36m['+msg.event_type+']\\x1b[0m\\r\\n');
      }
    }catch(e){}
  };
  agentWs.onclose=function(){agentWs=null};
}

function connectShellWs(){
  if(shellWs){shellWs.close();shellWs=null}
  const url='ws://'+location.hostname+':'+WS_PORT+'/ws/shell';
  shellWs=new WebSocket(url);
  shellWs.onmessage=function(ev){
    try{
      const msg=JSON.parse(ev.data);
      if(msg.type==='shell_output'){shellTerm.write(msg.text)}
    }catch(e){shellTerm.write(ev.data)}
  };
  shellWs.onclose=function(){shellWs=null;shellTerm.writeln('\\r\\n\\x1b[31m[Disconnected]\\x1b[0m')};
  shellTerm.onData(data=>{
    if(shellWs&&shellWs.readyState===1){shellWs.send(JSON.stringify({type:'shell_input',text:data}))}
  });
}

function switchTab(name){
  document.querySelectorAll('.term-tab').forEach(t=>t.classList.remove('active'));
  document.querySelectorAll('.term-pane').forEach(p=>p.classList.remove('active'));
  if(name==='agent'){
    document.querySelector('.term-tab:first-child').classList.add('active');
    document.getElementById('paneAgent').classList.add('active');
    setTimeout(()=>agentFit&&agentFit.fit(),50);
  }else{
    document.querySelector('.term-tab:last-child').classList.add('active');
    document.getElementById('paneShell').classList.add('active');
    setTimeout(()=>shellFit&&shellFit.fit(),50);
  }
}

function sendCmd(cmd){
  if(!activeRunId){toast('No active pipeline','error');return}
  if(agentWs&&agentWs.readyState===1){
    agentWs.send(JSON.stringify({type:'cmd',cmd:cmd}));
    if(!cmd.startsWith('/')){return}
    agentTerm.write('\\r\\n\\x1b[2m> '+cmd+'\\x1b[0m\\r\\n');
  }else{toast('Agent terminal not connected','error')}
}

function doAbort(){
  if(!activeRunId)return;
  fetch('/api/runs/'+activeRunId+'/abort',{method:'POST'}).then(()=>toast('Abort sent','success'));
  sendCmd('/abort');
}

/* ── Repos ── */
async function loadRepos(){
  try{
    const repos=await fetch('/api/repos').then(r=>r.json());
    const runs=await fetch('/api/runs').then(r=>r.json());
    const runByRepo={};Object.values(runs).forEach(r=>{runByRepo[r.repo_path]=r});
    const el=document.getElementById('repoList');
    if(!repos.length){el.innerHTML='<div style="padding:12px;color:#475569;font-size:12px">No repos.</div>';return}
    repos.sort((a,b)=>{
      const ra=runByRepo[a.path],rb=runByRepo[b.path];
      const ta=ra?ra.created_at:'',tb=rb?rb.created_at:'';
      if(ta&&!tb)return -1;if(!ta&&tb)return 1;
      if(ta&&tb)return ta>tb?-1:ta<tb?1:0;
      return (b.created||0)-(a.created||0);
    });
    el.innerHTML=repos.map(r=>{
      const d=r.description&&r.description.trim();const run=runByRepo[r.path];
      const badge=run?`<span style="font-size:10px;padding:1px 5px;border-radius:3px;background:${run.status==='done'?'#064e3b':run.status==='running'?'#1e3a5f':run.status==='failed'?'#7f1d1d':'#78350f'};color:${run.status==='done'?'#34d399':run.status==='running'?'#60a5fa':run.status==='failed'?'#f87171':'#fbbf24'}">${run.status}</span>`:'';
      return `<div class="repo-card"><div style="display:flex;justify-content:space-between;align-items:center"><span class="name">${r.identifier}</span>${badge}</div>
        <div class="path">${r.path}</div><div class="desc${d?'':' empty'}">${d?d.substring(0,80):'No description'}</div>
        <div class="actions">${d?`<button class="btn btn-trigger" onclick="triggerRepo('${r.path}',this)">Trigger</button>`:`<button class="btn btn-trigger" disabled>No desc</button>`}
          <button class="btn btn-open" onclick="window.open('${HB}/${r.path}','_blank')">Open</button>
          ${run?`<button class="btn btn-open" onclick="showPipeline('${run.id}','${r.path}')">View Run</button>`:''}</div></div>`}).join('');
  }catch(e){console.error(e)}
}

async function triggerRepo(path,btn){
  btn.disabled=true;btn.textContent='...';
  try{
    const d=await fetch('/api/trigger',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({repo_path:path})}).then(r=>r.json());
    if(d.run_id){toast('Started: '+d.run_id,'success');showPipeline(d.run_id,path);loadRepos()}
    else{toast(d.error||'Failed','error');btn.disabled=false;btn.textContent='Trigger'}
  }catch(e){toast(e.message,'error');btn.disabled=false;btn.textContent='Trigger'}
}

/* ── Pipeline View ── */
function showPipeline(runId,repo){
  activeRunId=runId;activeRepoPath=repo||'';seenMsgIds.clear();
  document.getElementById('emptyState').style.display='none';
  document.getElementById('pipelineView').style.display='flex';
  document.getElementById('pvRunId').textContent=runId;
  document.getElementById('pvRepo').textContent=repo||'';
  document.getElementById('pvStatus').innerHTML='<span style="color:#fbbf24">Running...</span>';
  document.getElementById('reviewPanel').style.display='none';
  document.querySelectorAll('.stage').forEach(s=>{s.className='stage'});
  document.querySelectorAll('.arrow').forEach(a=>{a.className='arrow'});
  ['detailPlan','detailCode','detailReview','detailDeliver'].forEach(id=>{document.getElementById(id).textContent=''});

  initTerminals();
  if(agentTerm){agentTerm.clear();agentTerm.writeln('\\x1b[36m[Agent Terminal]\\x1b[0m Connected to Pipeline '+runId);agentTerm.write('\\x1b[32m> \\x1b[0m')}
  connectAgentWs(runId);

  if(activeES){activeES.close()}
  activeES=new EventSource('/api/runs/'+runId+'/events');
  activeES.onmessage=function(ev){try{handleEvent(JSON.parse(ev.data))}catch(e){}};
  activeES.onerror=function(){if(activeES){activeES.close();activeES=null}};
}

function handleEvent(msg){
  if(msg.event_type==='__status__'){
    const s=msg.status;
    document.getElementById('pvStatus').innerHTML=s==='done'?'<span style="color:#34d399">Done</span>':'<span style="color:#f87171">Failed</span>';
    if(s==='done')setStage('review','done');
    if(s==='failed')document.querySelectorAll('.stage.active').forEach(el=>{el.classList.remove('active');el.classList.add('fail')});
    loadRepos();return;
  }
  if(msg.id&&seenMsgIds.has(msg.id))return;
  if(msg.id)seenMsgIds.add(msg.id);
  const et=msg.event_type||'';
  if(et==='plan.started')setStage('plan','active');
  if(et==='plan.done'){setStage('plan','done');setArrow(0,'done')}
  if(et==='plan.failed')setStage('plan','fail');
  if(et==='code.started'){setStage('code','active');setArrow(0,'done')}
  if(et==='code.done'){setStage('code','done');setArrow(1,'done');document.getElementById('detailCode').textContent=msg.content||''}
  if(et==='code.failed')setStage('code','fail');
  if(et==='pr.created'){setStage('deliver','done');setArrow(1,'done');setArrow(2,'done');document.getElementById('detailDeliver').textContent='Done'}
  if(et==='review.started'){setStage('review','active');setArrow(2,'done')}
  if(et==='review.done'){
    setStage('review','done');
    const score=msg.data&&msg.data.score,rt=msg.data&&msg.data.review_text;
    document.getElementById('detailReview').textContent=score?score+'/100':'';
    if(rt){
      const rp=document.getElementById('reviewPanel');rp.style.display='block';
      document.getElementById('reviewContent').textContent=rt.substring(0,2000);
      const b=document.getElementById('reviewScoreBadge');b.textContent=score+'/100';
      b.style.background=score>=80?'#064e3b':score>=60?'#78350f':'#7f1d1d';
      b.style.color=score>=80?'#34d399':score>=60?'#fbbf24':'#f87171';
    }
  }
  if(et==='pipeline.done'){setStage('review','done');document.getElementById('pvStatus').innerHTML='<span style="color:#34d399">Done</span>';loadRepos()}
  if(et==='pipeline.failed'){document.querySelectorAll('.stage.active').forEach(el=>{el.classList.remove('active');el.classList.add('fail')});document.getElementById('pvStatus').innerHTML='<span style="color:#f87171">Failed</span>';loadRepos()}

  if(agentTerm&&msg.content){
    const ts=msg.timestamp?new Date(msg.timestamp*1000).toLocaleTimeString('en',{hour12:false}):'';
    const role=(msg.role||'SYS').toUpperCase().padEnd(8);
    const c=et.includes('failed')?'\\x1b[31m':et.includes('done')||et==='pr.created'?'\\x1b[32m':'\\x1b[37m';
    agentTerm.write('\\r\\n'+c+'['+ts+'] '+role+' '+escTermText(msg.content.substring(0,200))+'\\x1b[0m');
  }
}

function setStage(n,s){const el=document.querySelector('.stage[data-stage="'+n+'"]');if(el)el.className='stage '+s}
function setArrow(i,s){const el=document.querySelector('.arrow[data-arrow="'+i+'"]');if(el)el.className='arrow '+s}
function escTermText(s){return s.replace(/</g,'&lt;').replace(/>/g,'&gt;')}
function escHtml(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}

function toggleChatInput(){
  const bar=document.getElementById('chatBar');
  const btn=document.getElementById('chatBtn');
  const visible=bar.style.display!=='none'&&bar.style.display!=='';
  bar.style.display=visible?'none':'flex';
  btn.classList.toggle('active',!visible);
  if(!visible){document.getElementById('chatInput').focus()}
}

function doSendChat(){
  const inp=document.getElementById('chatInput');
  const msg=inp.value.trim();
  if(!msg){toast('请输入优化指令','error');return}
  if(!activeRunId){toast('No active pipeline','error');return}
  sendCmd('/chat '+msg);
  agentTerm.write('\\r\\n\\x1b[36m[Chat]\\x1b[0m \\x1b[2m'+escTermText(msg)+'\\x1b[0m\\r\\n');
  inp.value='';
}

document.addEventListener('keydown',function(e){
  if(e.key==='Enter'&&document.activeElement===document.getElementById('chatInput')){doSendChat()}
});

loadRepos();setInterval(loadRepos,10000);
</script>
</body></html>"""
    .replace('__HARNESS_BASE__', HARNESS_BASE)
    .replace('__HARNESS_SPACE__', HARNESS_SPACE))


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def start_server(host="0.0.0.0", port=3001):
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"[Intake] 服务启动: http://localhost:{port}", flush=True)
    server.serve_forever()
