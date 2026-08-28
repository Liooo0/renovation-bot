#!/usr/bin/env python3
"""装修获客 bot v1:客户问答 + 留资抽取 + 意向分级 + 老板面板 + 钉钉推送
客户页:  http://0.0.0.0:8765/
老板面板: http://0.0.0.0:8765/owner
换客户  = 换 config.json + kb_client.md
"""
import html
import json
import os
import secrets
import time
import urllib.request
from uuid import uuid4

import leadgen
from flask import Flask, jsonify, make_response, redirect, request

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(APP_DIR, "leads.db")
PORT = int(os.environ.get("PORT", "8765"))


def _load_config():
    """config.json 优先,缺失时回退 config.example.json(便于新环境直接跑)。"""
    for name in ("config.json", "config.example.json"):
        path = os.path.join(APP_DIR, name)
        if os.path.exists(path):
            return json.load(open(path, encoding="utf-8"))
    return {}


CONFIG = _load_config()
def _deepseek_key():
    """惰性加载 DeepSeek key:环境变量优先,缺失时读 ~/.hermes/.env。
    无 key 时返回空串(仅问答/抽取需要,老板面板与静态页面不依赖)。"""
    try:
        return leadgen.load_key()
    except (RuntimeError, OSError):
        return ""

BUSINESS = CONFIG.get("business_name", "")
CONSULTANT = CONFIG.get("consultant_name", "阿迪")
OWNER_PASS = str(CONFIG.get("owner_password", ""))
WEBHOOK = CONFIG.get("webhook") or CONFIG.get("dingtalk_webhook") or ""
DASH_URL = CONFIG.get("dashboard_url", "")
KB_FILE = CONFIG.get("kb_file", "kb_client.md")
MAX_TURNS = int(CONFIG.get("max_conversation_turns", 8))


def _load_kb():
    """行业库 kb.md(通用装修知识) + 公司库 kb_file(客户资料) 合并注入,双库分层。"""
    parts = []
    try:
        parts.append(open(os.path.join(APP_DIR, "kb.md"), encoding="utf-8").read())
    except OSError:
        pass
    try:
        parts.append(open(os.path.join(APP_DIR, KB_FILE), encoding="utf-8").read())
    except OSError:
        pass
    return "\n\n".join(parts)


KB = _load_kb()

# 老板面板会话:随机 token → 过期时间(内存)。重启即失效,需重新登录。
_owner_sessions = {}
_SESSION_TTL = 60 * 60 * 24 * 30  # 30 天
_COOKIE_NAME = "owner_session"

SYSTEM = f"""你是「{CONSULTANT}」,{BUSINESS}的线上装修顾问。客户来咨询装修,你要:
1. 行情/工艺/验收/避坑类问题优先依据《行业知识库》回答;本公司报价/案例/联系方式用《公司资料库》回答;库外信息说"这个我需要确认后答复"
2. 回答具体、有数字、可执行
3. 自然引导客户说出:面积、户型、预算、计划装修时间(别一次全问,顺其自然)
4. 客户表示有意向时,自然地引导留微信或电话:"我加你微信发你报价单和案例图?" — 不硬推销
5. 语气专业、亲切、简洁,用中文

===行业知识库===
{KB}"""

app = Flask(__name__)
DB_PATH = leadgen.init_db(DB_PATH)


def ask(history):
    """history: [{role, content}, ...] 截断到最近 MAX_TURNS 轮,避免上下文无限增长。"""
    window = history[-MAX_TURNS * 2:]
    data = json.dumps({
        "model": "deepseek-v4-flash",
        "messages": [{"role": "system", "content": SYSTEM}] + window,
        "max_tokens": 800, "temperature": 0.3,
    }).encode()
    req = urllib.request.Request("https://api.deepseek.com/chat/completions", data=data,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {_deepseek_key()}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())["choices"][0]["message"]["content"].strip()


def capture_lead(sid, q, a):
    """存对话轮次 + 抽取 → 合并落库 → 成熟则钉钉通知。任何失败都不影响回答。"""
    msgs = leadgen.get_raw_msgs(DB_PATH, sid)
    leadgen.append_turn(DB_PATH, sid, q, a)
    fields = leadgen.extract_lead(msgs + [q], _deepseek_key())
    lead = leadgen.upsert_lead(DB_PATH, sid, fields)
    leadgen.maybe_notify(DB_PATH, WEBHOOK, lead, DASH_URL, BUSINESS)


def authed(req):
    """老板面板鉴权:仅验证随机 session token,不接受 URL 传密码。

    空密码默认拒绝访问(除非 config.json 显式设 allow_empty_password=true)。
    """
    if not OWNER_PASS and not CONFIG.get("allow_empty_password"):
        return False
    token = req.cookies.get(_COOKIE_NAME) or ""
    exp = _owner_sessions.get(token)
    if not exp:
        return False
    if time.time() > exp:
        _owner_sessions.pop(token, None)
        return False
    return True


# ---------------- 客户页 ----------------
CUSTOMER_PAGE = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>__TITLE__</title><link rel="icon" href='data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 36 36"><rect width="36" height="36" rx="10" fill="%23c47a4a"/><path d="M7.5 17.8 L18 9.6 L28.5 17.8" stroke="white" stroke-width="2.2" fill="none" stroke-linecap="round" stroke-linejoin="round"/><path d="M10.8 16.8 V26.4 H25.2 V16.8" stroke="white" stroke-width="2.2" fill="none" stroke-linecap="round" stroke-linejoin="round"/><rect x="15.8" y="20.8" width="9.6" height="7" rx="3.5" fill="white"/></svg>'><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#f6f3ee;color:#2b2622;font-family:"PingFang SC","Helvetica Neue",sans-serif;display:flex;flex-direction:column;height:100vh;height:100dvh}
header{background:linear-gradient(135deg,#4a3728,#6b4f38);color:#fdfaf5;padding:16px 20px 14px;box-shadow:0 2px 12px rgba(0,0,0,.18);flex:0 0 auto}
.brand{display:flex;align-items:center;gap:10px}
.logo{width:36px;height:36px;display:flex;align-items:center;justify-content:center;flex:0 0 auto}
header h1{font-size:17px;font-weight:600;letter-spacing:.5px}
header p{font-size:12px;opacity:.78;margin-top:3px}
.chips{display:flex;gap:8px;overflow-x:auto;padding:4px 14px 10px;max-width:680px;width:100%;margin:0 auto;flex:0 0 auto;scrollbar-width:none}
.chips::-webkit-scrollbar{display:none}
.chips button{flex:0 0 auto;background:#fff;border:1px solid #e0d5c6;color:#6b4f38;border-radius:999px;padding:7px 14px;font-size:12.5px;cursor:pointer;transition:all .15s}
.chips button:hover{background:#8a6648;color:#fff;border-color:#8a6648}
#chat{flex:1;overflow-y:auto;padding:16px 14px;max-width:680px;width:100%;margin:0 auto;display:flex;flex-direction:column;gap:10px}
.msg{max-width:82%;animation:fadein .2s ease}
@keyframes fadein{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}
.bubble{padding:10px 14px;border-radius:14px;font-size:14.5px;line-height:1.65;white-space:pre-wrap;word-break:break-word;display:block}
.user{align-self:flex-end}.user .bubble{background:#8a6648;color:#fff;border-bottom-right-radius:4px}
.bot{align-self:flex-start}.bot .bubble{background:#fff;border:1px solid #e8e0d4;border-bottom-left-radius:4px;box-shadow:0 1px 3px rgba(74,55,40,.06)}
.time{display:block;font-size:10px;opacity:.55;margin-top:5px;text-align:right;padding:0 3px}
.typing{display:inline-flex;gap:4px;padding:3px 0}
.typing i{width:7px;height:7px;border-radius:50%;background:#b9a58f;animation:blink 1.2s infinite}
.typing i:nth-child(2){animation-delay:.2s}.typing i:nth-child(3){animation-delay:.4s}
@keyframes blink{0%,80%,100%{opacity:.25}40%{opacity:1}}
#inputbar{max-width:680px;width:100%;margin:0 auto;padding:10px 14px calc(14px + env(safe-area-inset-bottom));display:flex;gap:8px;flex:0 0 auto}
input{flex:1;padding:12px 14px;border-radius:12px;border:1px solid #ddd2c2;background:#fff;color:#2b2622;font-size:14.5px;outline:none;transition:border .15s}
input:focus{border-color:#8a6648;box-shadow:0 0 0 3px rgba(138,102,72,.12)}
#send{padding:12px 18px;border-radius:12px;border:0;background:#8a6648;color:#fff;font-size:14.5px;cursor:pointer;transition:background .15s}
#send:hover{background:#75563c}#send:disabled{opacity:.5;cursor:not-allowed}
</style></head><body>
<header><div class="brand"><div class="logo"><svg width="36" height="36" viewBox="0 0 36 36" fill="none" xmlns="http://www.w3.org/2000/svg"><defs><linearGradient id="lg" x1="0" y1="0" x2="36" y2="36" gradientUnits="userSpaceOnUse"><stop stop-color="#e09568"/><stop offset="1" stop-color="#a8623f"/></linearGradient></defs><rect width="36" height="36" rx="10" fill="url(#lg)"/><path d="M7.5 17.8 L18 9.6 L28.5 17.8" stroke="#fdfaf5" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/><path d="M10.8 16.8 V26.4 H25.2 V16.8" stroke="#fdfaf5" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/><rect x="15.8" y="20.8" width="9.6" height="7" rx="3.5" fill="#fdfaf5"/><circle cx="18.4" cy="24.3" r="1" fill="#c47a4a"/><circle cx="21.2" cy="24.3" r="1" fill="#c47a4a"/><circle cx="24" cy="24.3" r="1" fill="#c47a4a"/></svg></div><div><h1>__TITLE__</h1><p>__BUSINESS__ · 报价 / 工期 / 案例 · 线上咨询</p></div></div></header>
<div class="chips" id="chips">
<button onclick="ask(this.textContent)">半包和全包差多少钱？</button>
<button onclick="ask(this.textContent)">89平全包大概多少？</button>
<button onclick="ask(this.textContent)">装修工期要多久？</button>
<button onclick="ask(this.textContent)">水电验收要看什么？</button>
</div>
<div id="chat"><div class="msg bot"><span class="bubble">你好，我是__CONSULTANT__。可以问我：半包全包多少钱？我家 89 平做下来多少？工期多久？有没有案例？要报价单留个微信就行。</span><span class="time"></span></div></div>
<div id="inputbar"><input id="q" placeholder="输入你的装修问题…" enterkeyhint="send"><button id="send" onclick="send()">发送</button></div>
<script>
var sid=localStorage.getItem('sid');if(!sid){sid=Math.random().toString(36).slice(2)+Date.now().toString(36);localStorage.setItem('sid',sid);}
var busy=false;
function ts(){var d=new Date();return ('0'+d.getHours()).slice(-2)+':'+('0'+d.getMinutes()).slice(-2)}
function scroll(){var c=document.getElementById('chat');c.scrollTop=c.scrollHeight}
function add(who,t){var c=document.getElementById('chat');var m=document.createElement('div');m.className='msg '+who;var b=document.createElement('span');b.className='bubble';b.textContent=t;m.appendChild(b);var tm=document.createElement('span');tm.className='time';tm.textContent=ts();m.appendChild(tm);c.appendChild(m);scroll();return m}
function typing(){var c=document.getElementById('chat');var m=document.createElement('div');m.className='msg bot';var b=document.createElement('span');b.className='bubble typing';b.innerHTML='<i></i><i></i><i></i>';m.appendChild(b);c.appendChild(m);scroll();return m}
function ask(q){q=(q||'').trim();if(busy||!q)return;busy=true;document.getElementById('send').disabled=true;
add('user',q);var t=typing();
fetch('/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({q:q,sid:sid})})
.then(function(r){return r.json()}).then(function(d){t.remove();add('bot',d.a)})
.catch(function(){t.remove();add('bot','服务开小差了，稍后再试')})
.finally(function(){busy=false;document.getElementById('send').disabled=false;scroll()});}
function send(){var q=document.getElementById('q');ask(q.value);q.value=''}
document.getElementById('q').addEventListener('keydown',function(e){if(e.key==='Enter'&&!e.isComposing)send()});
</script></body></html>"""

# ---------------- 老板面板 ----------------
OWNER_LOGIN = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>老板面板</title><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{background:#101820;color:#e8e8e8;font-family:"PingFang SC",sans-serif;display:flex;justify-content:center;align-items:center;height:100vh}
.box{background:#1a2a3a;padding:32px;border-radius:12px;text-align:center}
input{padding:10px 12px;border-radius:8px;border:1px solid #3a4a5a;background:#182430;color:#eee;margin:12px 0;display:block}
button{padding:10px 24px;border-radius:8px;border:0;background:#1f6faf;color:#fff;cursor:pointer}
</style></head><body>
<form class="box" method="post" action="/owner/login">
<h2>老板面板</h2>
<input type="password" name="pass" placeholder="输入密码" required>
<button type="submit">进入</button>
</form></body></html>"""

OWNER_PAGE = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>老板面板 · __BUSINESS__</title><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{background:#101820;color:#e8e8e8;font-family:"PingFang SC",sans-serif;margin:0;padding:16px;max-width:1100px;margin:0 auto}
h1{font-size:18px}h1 span{font-size:12px;color:#8aa;font-weight:normal}
.stats{display:flex;gap:12px;margin:12px 0 20px}
.stat{background:#1a2a3a;border-radius:10px;padding:12px 20px;flex:1;text-align:center}
.stat b{display:block;font-size:22px}.stat span{font-size:12px;color:#8aa}
table{width:100%;border-collapse:collapse;background:#182430;border-radius:10px;overflow:hidden}
th,td{padding:10px 12px;text-align:left;font-size:13px;border-bottom:1px solid #22303f}
th{color:#8aa;font-weight:normal;font-size:12px}
button{padding:4px 10px;border-radius:6px;border:0;background:#182430;color:#8aa;font-size:12px;cursor:pointer}
button:hover{filter:brightness(1.3)}
details{font-size:12px}
a{color:#5aa0e0}
</style></head><body>
<h1>老板面板 <span>__BUSINESS__</span></h1>
<div class="stats">__STATS__</div>
<table>
<tr><th>时间</th><th>意向</th><th>需求</th><th>联系方式</th><th>状态</th><th>操作</th></tr>
__ROWS__
</table>
<script>
function setStatus(id,status,btn){
fetch('/owner/set_status',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:id,status:status})})
.then(r=>r.json()).then(d=>{if(d.ok)location.reload()});
}
</script></body></html>"""

BADGE = {"高": "#c0392b", "中": "#e67e22", "低": "#7f8c8d"}
STATUS_LABEL = {"new": "待跟进", "followed_up": "已跟进", "won": "已成交", "ignored": "忽略"}


def render_owner():
    esc = lambda s: html.escape(str(s or ""), quote=True)
    tot, high, pending = leadgen.stats(DB_PATH)
    stats = (f'<div class="stat"><b>{tot}</b><span>今日咨询</span></div>'
             f'<div class="stat"><b>{high}</b><span>今日高意向</span></div>'
             f'<div class="stat"><b>{pending}</b><span>待跟进</span></div>')
    rows = []
    for L in leadgen.list_leads(DB_PATH):
        badge = BADGE.get(L["intent_level"], "#7f8c8d")
        try:
            msgs = json.loads(L["raw_msgs"] or "[]")
        except json.JSONDecodeError:
            msgs = []
        quote = "<br>".join(f"<span style='color:#9aa'>{esc(m)}</span>" for m in msgs[-4:])
        btns = ""
        for s, lab in (("followed_up", "已跟进"), ("won", "已成交"), ("ignored", "忽略")):
            style = "background:#2a80c0;color:#fff" if s == L["status"] else ""
            btns += f"<button onclick=\"setStatus({L['id']},'{s}',this)\" style='{style}'>{lab}</button> "
        rows.append(
            f"<tr>"
            f"<td style='white-space:nowrap'>{esc(L['created_at'][5:])}</td>"
            f"<td><span style='background:{badge};color:#fff;padding:2px 8px;border-radius:8px'>{esc(L['intent_level'])}</span>"
            f"<span style='color:#8aa'> {esc(L['intent_score'])}</span></td>"
            f"<td>{esc(L['area'])}㎡ {esc(L['room_type'])} · {esc(L['budget'])} · {esc(L['start_time'])}</td>"
            f"<td><b>{esc(L['contact']) or '—'}</b></td>"
            f"<td>{STATUS_LABEL.get(L['status'], esc(L['status']))}</td>"
            f"<td>{btns}</td></tr>"
            f"<tr><td colspan='6' style='border:0;padding:0 12px 8px'><details>"
            f"<summary style='color:#6a8'>原话({len(msgs)}条)</summary>"
            f"<div style='font-size:12px;color:#9aa;line-height:1.6'>{quote}</div></details></td></tr>")
    return (OWNER_PAGE.replace("__BUSINESS__", esc(BUSINESS))
                     .replace("__STATS__", stats)
                     .replace("__ROWS__", "\n".join(rows) or "<tr><td colspan='6'>还没有咨询</td></tr>"))


@app.route("/")
def index():
    page = (CUSTOMER_PAGE.replace("__TITLE__", f"{BUSINESS} 装修咨询")
                         .replace("__BUSINESS__", html.escape(BUSINESS, quote=True))
                         .replace("__CONSULTANT__", html.escape(CONSULTANT, quote=True)))
    return page


@app.route("/ask", methods=["POST"])
def answer():
    body = request.json or {}
    q = (body.get("q") or "").strip()
    sid = (body.get("sid") or "").strip() or uuid4().hex
    if not q:
        return jsonify({"a": "请输入问题"})
    try:
        conv = leadgen.get_conversation(DB_PATH, sid)
        a = ask(conv + [{"role": "user", "content": q}])
    except Exception as e:
        a = f"服务开小差了: {e}"
    try:
        capture_lead(sid, q, a)
    except Exception:
        pass
    return jsonify({"a": a, "sid": sid})


@app.route("/owner")
def owner():
    if not authed(request):
        return OWNER_LOGIN
    return render_owner()


@app.route("/owner/login", methods=["POST"])
def owner_login():
    p = (request.form.get("pass") or "")
    if OWNER_PASS:
        if not secrets.compare_digest(p, OWNER_PASS):
            return OWNER_LOGIN, 403
    elif not CONFIG.get("allow_empty_password"):
        return OWNER_LOGIN, 403
    token = secrets.token_urlsafe(32)
    _owner_sessions[token] = time.time() + _SESSION_TTL
    resp = make_response(redirect("/owner"))
    resp.set_cookie(_COOKIE_NAME, token, max_age=_SESSION_TTL, httponly=True, samesite="Lax")
    return resp


@app.route("/owner/logout", methods=["POST"])
def owner_logout():
    token = request.cookies.get(_COOKIE_NAME) or ""
    _owner_sessions.pop(token, None)
    resp = make_response(redirect("/owner"))
    resp.delete_cookie(_COOKIE_NAME)
    return resp


@app.route("/owner/set_status", methods=["POST"])
def set_status():
    if not authed(request):
        return jsonify({"ok": False, "err": "auth"})
    b = request.json or {}
    ok = leadgen.set_status(DB_PATH, b.get("id"), b.get("status"))
    return jsonify({"ok": ok})


@app.route("/api/leads")
def api_leads():
    if not authed(request):
        return jsonify({"ok": False, "err": "auth"}), 403
    return jsonify({"ok": True, "leads": leadgen.list_leads(DB_PATH)})


if __name__ == "__main__":
    print(f"客户页: http://0.0.0.0:{PORT}/   老板面板: http://0.0.0.0:{PORT}/owner")
    app.run(host="0.0.0.0", port=PORT, debug=False)
