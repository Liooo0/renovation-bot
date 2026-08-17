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
KB = open(os.path.join(APP_DIR, KB_FILE), encoding="utf-8").read()
MAX_TURNS = int(CONFIG.get("max_conversation_turns", 8))

# 老板面板会话:随机 token → 过期时间(内存)。重启即失效,需重新登录。
_owner_sessions = {}
_SESSION_TTL = 60 * 60 * 24 * 30  # 30 天
_COOKIE_NAME = "owner_session"

SYSTEM = f"""你是「{CONSULTANT}」,{BUSINESS}的线上装修顾问。客户来咨询装修,你要:
1. 只依据知识库回答报价/工期/案例/常见问题,库外信息说"这个我需要确认后答复"
2. 回答具体、有数字、可执行
3. 自然引导客户说出:面积、户型、预算、计划装修时间(别一次全问,顺其自然)
4. 客户表示有意向时,自然地引导留微信或电话:"我加你微信发你报价单和案例图?" — 不硬推销
5. 语气专业、亲切、简洁,用中文

===知识库===
{KB}"""

app = Flask(__name__)
DB_PATH = leadgen.init_db(DB_PATH)


def ask(history):
    """history: [{role, content}, ...] 截断到最近 MAX_TURNS 轮,避免上下文无限增长。"""
    window = history[-MAX_TURNS * 2:]
    data = json.dumps({
        "model": "deepseek-chat",
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
<title>__TITLE__</title><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{background:#101820;color:#e8e8e8;font-family:"PingFang SC",sans-serif;margin:0;display:flex;flex-direction:column;height:100vh}
header{padding:14px 20px;background:#1a2a3a;border-bottom:1px solid #2a3a4a}
header h1{margin:0;font-size:18px}header p{margin:2px 0 0;font-size:12px;color:#8aa}
#chat{flex:1;overflow-y:auto;padding:16px;max-width:760px;width:100%;margin:0 auto;box-sizing:border-box}
.msg{margin:10px 0;max-width:85%;padding:10px 14px;border-radius:12px;font-size:14px;line-height:1.7;white-space:pre-wrap}
.user{margin-left:auto;background:#1f5f8f;border-bottom-right-radius:2px}
.bot{background:#22303f;border-bottom-left-radius:2px}
#inputbar{max-width:760px;width:100%;margin:0 auto;box-sizing:border-box;padding:12px 16px;display:flex;gap:8px}
input{flex:1;padding:10px 12px;border-radius:8px;border:1px solid #3a4a5a;background:#182430;color:#eee;font-size:14px}
button{padding:10px 18px;border-radius:8px;border:0;background:#1f6faf;color:#fff;font-size:14px;cursor:pointer}
button:hover{background:#2a80c0}
.tip{font-size:12px;color:#6a8;text-align:center;padding:4px 0}
</style></head><body>
<header><h1>🏠 __TITLE__ <span style="font-size:12px;color:#8aa">__BUSINESS__</span></h1>
<p>报价 / 工期 / 案例 · 线上咨询</p></header>
<div id="chat"><div class="msg bot">你好,我是__CONSULTANT__。可以问我:半包全包多少钱?我家 89 平做下来多少?工期多久?有没有案例?要报价单留个微信就行。</div></div>
<div class="tip">示例:半包多少钱一平? / 89平全包大概多少? / 你们工期多久?</div>
<div id="inputbar"><input id="q" placeholder="输入你的装修问题…"><button onclick="ask()">发送</button></div>
<script>
var sid=localStorage.getItem('sid');if(!sid){sid=Math.random().toString(36).slice(2)+Date.now().toString(36);localStorage.setItem('sid',sid);}
function ask(){var q=document.getElementById('q');if(!q.value.trim())return;
var question=q.value;add('user',question);var b=add('bot','思考中…');q.value='';
fetch('/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({q:question,sid:sid})})
.then(r=>r.json()).then(d=>{b.textContent=d.a;scroll()}).catch(()=>{b.textContent='服务开小差了,稍后再试';scroll()});
}
function add(who,t){var c=document.getElementById('chat');var m=document.createElement('div');m.className='msg '+who;m.textContent=t;c.appendChild(m);scroll();return m}
function scroll(){document.getElementById('chat').scrollTop=99999}
document.getElementById('q').addEventListener('keydown',e=>{if(e.key==='Enter')ask()});
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
