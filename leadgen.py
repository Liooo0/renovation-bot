#!/usr/bin/env python3
"""装修获客 bot · 留资抽取 + 意向分级 + SQLite 落库 + 钉钉推送

独立模块,可 CLI 单测:
    python leadgen.py --history '["89平三房,预算15万,十月装修","微信是138xxxx","半包多少钱"]'
"""
import argparse, json, os, sqlite3, time, urllib.request

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT UNIQUE NOT NULL,
  area TEXT DEFAULT '',
  room_type TEXT DEFAULT '',
  budget TEXT DEFAULT '',
  start_time TEXT DEFAULT '',
  contact TEXT DEFAULT '',
  notes TEXT DEFAULT '',
  intent_score INTEGER DEFAULT 0,
  intent_level TEXT DEFAULT '低',
  status TEXT DEFAULT 'new',
  raw_msgs TEXT DEFAULT '[]',
  created_at TEXT,
  updated_at TEXT,
  notified_at TEXT
);
"""
LEAD_COLS = ["id", "session_id", "area", "room_type", "budget", "start_time", "contact",
             "notes", "intent_score", "intent_level", "status", "raw_msgs",
             "created_at", "updated_at", "notified_at"]
VALID_STATUS = ("new", "followed_up", "won", "ignored")


def load_key():
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if key:
        return key
    with open(os.path.expanduser("~/.hermes/.env")) as f:
        for line in f:
            if line.startswith("DEEPSEEK_API_KEY="):
                return line.strip().split("=", 1)[1].strip()
    raise RuntimeError("DEEPSEEK_API_KEY 未找到(~/.hermes/.env 或环境变量)")


def _chat_json(system, user, key, max_tokens=400):
    data = json.dumps({
        "model": "deepseek-chat",
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "max_tokens": max_tokens, "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }).encode()
    req = urllib.request.Request("https://api.deepseek.com/chat/completions", data=data,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())["choices"][0]["message"]["content"].strip()


def extract_lead(history, key=None):
    """从客户对话历史抽取结构化留资信息。失败抛异常,调用方兜底。"""
    key = key or load_key()
    system = (
        "你是装修获客系统的信息抽取器。从客户的咨询对话中抽取装修意向信息。"
        "请只输出一个 json 对象,字段:\n"
        "{\n"
        '  "is_lead": 是否潜在装修客户(同行/推销/闲聊=false),\n'
        '  "area": 面积数字或空字符串(如"89"),\n'
        '  "room_type": 户型或空字符串(如"三房"),\n'
        '  "budget": 预算或空字符串(如"15万"),\n'
        '  "start_time": 计划装修时间或空字符串(如"10月"),\n'
        '  "contact": 联系方式或空字符串(如"微信xxx"/"138xxxx"),\n'
        '  "intent_score": 0-100整数,\n'
        '  "intent_level": "高"/"中"/"低",\n'
        '  "notes": 一句话客户需求摘要\n'
        "}\n"
        "打分规则:有预算+30,有面积或户型+25,有时间线+20,有联系方式+15,"
        "有急切需求词(尽快/着急/想早点/价格合适就定)+10,封顶100。"
        "没提到的字段一律空字符串。intent_level:>=70高,40-69中,<40低。"
    )
    content = _chat_json(system, "对话:\n" + "\n".join(f"- {m}" for m in history), key)
    try:
        out = json.loads(content)
    except json.JSONDecodeError:
        start, end = content.find("{"), content.rfind("}")
        out = json.loads(content[start:end + 1])
    for k in ("area", "room_type", "budget", "start_time", "contact", "notes"):
        out.setdefault(k, "")
        out[k] = str(out[k] or "").strip()
    out["is_lead"] = bool(out.get("is_lead", True))
    try:
        out["intent_score"] = min(100, max(0, int(out.get("intent_score", 0) or 0)))
    except (TypeError, ValueError):
        out["intent_score"] = 0
    if out.get("intent_level") not in ("高", "中", "低"):
        s = out["intent_score"]
        out["intent_level"] = "高" if s >= 70 else ("中" if s >= 40 else "低")
    return out


# ---- SQLite(每操作独立连接,线程安全;Flask 多线程) ----

def _connect(path):
    return sqlite3.connect(path, timeout=10)


def init_db(path):
    conn = _connect(path)
    try:
        conn.execute(SCHEMA)
        conn.commit()
    finally:
        conn.close()
    return path


def _get_lead_conn(conn, session_id):
    cur = conn.execute(f"SELECT {','.join(LEAD_COLS)} FROM leads WHERE session_id=?", (session_id,))
    row = cur.fetchone()
    return dict(zip(LEAD_COLS, row)) if row else None


def get_lead(path, session_id):
    conn = _connect(path)
    try:
        return _get_lead_conn(conn, session_id)
    finally:
        conn.close()


def get_raw_msgs(path, session_id):
    lead = get_lead(path, session_id)
    if not lead:
        return []
    try:
        return json.loads(lead["raw_msgs"] or "[]")
    except json.JSONDecodeError:
        return []


def _merge_fields(old, new):
    out = {}
    for k in ("area", "room_type", "budget", "start_time", "contact", "notes"):
        nv = (new.get(k) or "").strip()
        ov = (old.get(k) or "").strip()
        out[k] = nv if nv else ov
    out["intent_score"] = new.get("intent_score", old.get("intent_score", 0))
    out["intent_level"] = new.get("intent_level") or old.get("intent_level", "低")
    return out


def upsert_lead(path, session_id, fields, new_msg):
    """按会话累积合并成一张客户卡。返回最新 lead dict。"""
    conn = _connect(path)
    try:
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        old = _get_lead_conn(conn, session_id)
        if old:
            msgs = json.loads(old["raw_msgs"] or "[]")
            msgs.append(new_msg)
            m = _merge_fields(old, fields)
            conn.execute(
                "UPDATE leads SET area=?, room_type=?, budget=?, start_time=?, contact=?, notes=?,"
                "intent_score=?, intent_level=?, raw_msgs=?, updated_at=? WHERE session_id=?",
                (m["area"], m["room_type"], m["budget"], m["start_time"], m["contact"], m["notes"],
                 m["intent_score"], m["intent_level"], json.dumps(msgs, ensure_ascii=False), now, session_id))
        else:
            conn.execute(
                "INSERT INTO leads (session_id, area, room_type, budget, start_time, contact, notes,"
                "intent_score, intent_level, raw_msgs, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (session_id, fields.get("area", ""), fields.get("room_type", ""), fields.get("budget", ""),
                 fields.get("start_time", ""), fields.get("contact", ""), fields.get("notes", ""),
                 fields.get("intent_score", 0), fields.get("intent_level", "低"),
                 json.dumps([new_msg], ensure_ascii=False), now, now))
        conn.commit()
        return _get_lead_conn(conn, session_id)
    finally:
        conn.close()


def set_status(path, lead_id, status):
    if status not in VALID_STATUS:
        return False
    conn = _connect(path)
    try:
        conn.execute("UPDATE leads SET status=?, updated_at=? WHERE id=?",
                     (status, time.strftime("%Y-%m-%d %H:%M:%S"), lead_id))
        conn.commit()
        return True
    finally:
        conn.close()


def stats(path):
    today = time.strftime("%Y-%m-%d")
    conn = _connect(path)
    try:
        tot = conn.execute("SELECT COUNT(*) FROM leads WHERE created_at LIKE ?", (today + "%",)).fetchone()[0]
        high = conn.execute("SELECT COUNT(*) FROM leads WHERE created_at LIKE ? AND intent_level='高'",
                            (today + "%",)).fetchone()[0]
        pending = conn.execute("SELECT COUNT(*) FROM leads WHERE status='new'").fetchone()[0]
        return tot, high, pending
    finally:
        conn.close()


def list_leads(path, limit=100):
    conn = _connect(path)
    try:
        rows = conn.execute(
            f"SELECT {','.join(LEAD_COLS)} FROM leads ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(zip(LEAD_COLS, r)) for r in rows]
    finally:
        conn.close()


def maybe_notify(path, webhook, lead, dashboard_url, business_name):
    """关键事件:首次拿到联系方式且意向≥中 → 钉钉 markdown 推送,24h 内同 lead 去重。"""
    if not webhook:
        return False
    if not (lead.get("contact") or "").strip():
        return False
    if lead.get("intent_level") not in ("高", "中"):
        return False
    cur = get_lead(path, lead.get("session_id")) or lead
    notified_at = cur.get("notified_at")
    if notified_at:
        try:
            if time.time() - time.mktime(time.strptime(notified_at, "%Y-%m-%d %H:%M:%S")) < 24 * 3600:
                return False
        except ValueError:
            pass
    try:
        msgs = json.loads(lead.get("raw_msgs") or "[]")
    except json.JSONDecodeError:
        msgs = []
    quote = " / ".join(str(m) for m in msgs[-2:]) or "(无原话)"
    text = (
        f"### 🏠 新客户 · {business_name}\n\n"
        f"- **意向:** {lead.get('intent_level')} | **意向分:** {lead.get('intent_score')}\n"
        f"- **面积:** {lead.get('area')}㎡ | **户型:** {lead.get('room_type')}\n"
        f"- **预算:** {lead.get('budget')} | **计划:** {lead.get('start_time')}\n"
        f"- **联系方式:** {lead.get('contact')}\n"
        f"- **需求:** {lead.get('notes')}\n"
        f"\n[打开面板跟进]({dashboard_url})\n"
        f"> {quote}"
    )
    payload = json.dumps({"msgtype": "markdown",
                          "markdown": {"title": "新客户留资", "text": text}},
                         ensure_ascii=False).encode()
    req = urllib.request.Request(webhook, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            if json.loads(r.read()).get("errcode") == 0:
                conn = _connect(path)
                try:
                    conn.execute("UPDATE leads SET notified_at=? WHERE session_id=?",
                                 (time.strftime("%Y-%m-%d %H:%M:%S"), lead["session_id"]))
                    conn.commit()
                finally:
                    conn.close()
                return True
    except Exception:
        pass
    return False


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--history", required=True,
                   help='消息 JSON 数组,如 \'["89平三房,预算15万","微信是138xxxx"]\'')
    p.add_argument("--db", default="leads.db", help="SQLite 路径(默认 leads.db)")
    a = p.parse_args()
    print(json.dumps(extract_lead(json.loads(a.history)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
