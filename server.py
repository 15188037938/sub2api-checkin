#!/usr/bin/env python3
"""
Sub2API 每日自动签到服务
- 用户每日首次登录自动签到，赠送额度
- 后台可配赠送金额
- 独立部署，通过 sub2api 的自定义菜单嵌入
"""

import sqlite3
import datetime
import json
import os
import time
import hashlib
import secrets
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import threading

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "checkin.db")
PORT = int(os.environ.get("CHECKIN_PORT", 18888))
# sub2api 站点地址和管理员 API Key，部署时配置
SUB2API_URL = os.environ.get("SUB2API_URL", "")
SUB2API_ADMIN_KEY = os.environ.get("SUB2API_ADMIN_KEY", "")


def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS checkins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            date TEXT NOT NULL,
            reward REAL NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(user_id, date)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    c.execute("""
        INSERT OR IGNORE INTO settings (key, value) VALUES ('reward_amount', '0.5')
    """)
    c.execute("""
        INSERT OR IGNORE INTO settings (key, value) VALUES ('reward_currency', 'USD')
    """)
    c.execute("""
        INSERT OR IGNORE INTO settings (key, value) VALUES ('enabled', 'true')
    """)
    conn.commit()
    conn.close()


def get_setting(key):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None


def set_setting(key, value):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()


def has_checked_in_today(user_id):
    today = datetime.date.today().isoformat()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, reward FROM checkins WHERE user_id = ? AND date = ?", (user_id, today))
    row = c.fetchone()
    conn.close()
    return row


def do_checkin(user_id, reward):
    today = datetime.date.today().isoformat()
    now = datetime.datetime.now().isoformat()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        c.execute(
            "INSERT INTO checkins (user_id, date, reward, created_at) VALUES (?, ?, ?, ?)",
            (user_id, today, reward, now)
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def add_balance_via_redemption(user_id, amount):
    """
    通过 sub2api 管理 API 生成兑换码并自动充值到用户账户。
    如果 sub2api 不支持直接充值 API，则生成兑换码返回给用户。
    """
    if not SUB2API_URL or not SUB2API_ADMIN_KEY:
        return {"success": False, "error": "未配置 SUB2API_URL 或 SUB2API_ADMIN_KEY"}

    headers = {
        "Authorization": f"Bearer {SUB2API_ADMIN_KEY}",
        "Content-Type": "application/json",
    }

    # 方式1：尝试调用管理后台的余额赠送 API
    # sub2api 的 New-API 分支常用 /api/v1/admin/topup
    try:
        import urllib.request
        import urllib.error

        # 生成兑换码
        code_data = json.dumps({
            "amount": amount,
            "count": 1,
            "note": f"每日签到奖励 - {datetime.date.today().isoformat()}",
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{SUB2API_URL}/api/v1/admin/redemption",
            data=code_data,
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            if result.get("success") or result.get("data"):
                codes = result.get("data", [])
                if codes:
                    code_str = codes[0] if isinstance(codes[0], str) else codes[0].get("key", codes[0].get("code", ""))
                    return {"success": True, "code": code_str, "amount": amount, "method": "redemption_code"}
    except Exception:
        pass

    # 方式2：尝试直接调用用户余额增加 API
    try:
        topup_data = json.dumps({
            "user_id": user_id,
            "amount": amount,
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{SUB2API_URL}/api/v1/admin/user/topup",
            data=topup_data,
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            if result.get("success") or result.get("data"):
                return {"success": True, "amount": amount, "method": "direct_topup"}
    except Exception:
        pass

    # 方式3：不依赖 sub2api 的 API，直接返回虚拟签到结果
    # 管理员可手动发放额度，或配置 webhook 通知
    return {
        "success": True,
        "amount": amount,
        "method": "manual",
        "note": "请管理员在 sub2api 后台手动发放额度，或配置 auto_topup.py 脚本",
    }


def get_checkin_history(user_id, days=30):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT date, reward FROM checkins WHERE user_id = ? ORDER BY date DESC LIMIT ?",
        (user_id, days)
    )
    rows = c.fetchall()
    conn.close()
    return [{"date": r[0], "reward": r[1]} for r in rows]


def get_today_stats():
    today = datetime.date.today().isoformat()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*), SUM(reward) FROM checkins WHERE date = ?", (today,))
    row = c.fetchone()
    conn.close()
    return {"count": row[0] or 0, "total_reward": row[1] or 0.0}


class CheckinHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # 禁用请求日志

    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def _send_html(self, html, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/api/checkin/status":
            # 查询签到状态
            qs = parse_qs(parsed.query)
            user_id = qs.get("user_id", [""])[0]
            if not user_id:
                self._send_json({"error": "缺少 user_id"}, 400)
                return

            enabled = get_setting("enabled") == "true"
            if not enabled:
                self._send_json({"checked_in": False, "disabled": True})
                return

            record = has_checked_in_today(user_id)
            reward = float(get_setting("reward_amount") or "0.5")

            if record:
                self._send_json({
                    "checked_in": True,
                    "today_reward": record[1],
                    "date": datetime.date.today().isoformat(),
                })
            else:
                self._send_json({
                    "checked_in": False,
                    "reward_available": reward,
                    "date": datetime.date.today().isoformat(),
                })

        elif path == "/api/checkin/history":
            qs = parse_qs(parsed.query)
            user_id = qs.get("user_id", [""])[0]
            history = get_checkin_history(user_id)
            self._send_json({"history": history})

        elif path == "/api/admin/settings":
            self._send_json({
                "reward_amount": float(get_setting("reward_amount") or "0.5"),
                "reward_currency": get_setting("reward_currency") or "USD",
                "enabled": get_setting("enabled") == "true",
                "sub2api_url": SUB2API_URL or "未配置",
                "today_stats": get_today_stats(),
            })

        elif path == "/admin":
            self._send_html(ADMIN_HTML)

        elif path == "/" or path == "":
            self._send_html(CHECKIN_HTML)

        else:
            self._send_json({"error": "Not Found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b"{}"

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            data = {}

        if path == "/api/checkin/do":
            user_id = data.get("user_id", "").strip()
            if not user_id:
                # 尝试从 Authorization header 中提取
                auth = self.headers.get("Authorization", "")
                if auth.startswith("Bearer "):
                    user_id = auth[7:].strip()

            if not user_id:
                self._send_json({"error": "缺少 user_id"}, 400)
                return

            enabled = get_setting("enabled") == "true"
            if not enabled:
                self._send_json({"success": False, "error": "签到功能已关闭"}, 400)
                return

            today = datetime.date.today().isoformat()
            existing = has_checked_in_today(user_id)
            if existing:
                self._send_json({
                    "success": False,
                    "error": "今日已签到",
                    "today_reward": existing[1],
                    "checked_in": True,
                })
                return

            reward = float(get_setting("reward_amount") or "0.5")

            # 尝试通过 sub2api API 发放额度
            result = add_balance_via_redemption(user_id, reward)

            if result.get("success"):
                do_checkin(user_id, reward)
                self._send_json({
                    "success": True,
                    "reward": reward,
                    "currency": get_setting("reward_currency") or "USD",
                    "method": result.get("method", "manual"),
                    "code": result.get("code", ""),
                    "message": f"签到成功！获得 {reward} 额度" + (
                        f"，兑换码：{result['code']}" if result.get("code") else ""
                    ),
                })
            else:
                self._send_json({
                    "success": False,
                    "error": result.get("error", "额度发放失败"),
                }, 500)

        elif path == "/api/admin/settings":
            if "reward_amount" in data:
                set_setting("reward_amount", str(float(data["reward_amount"])))
            if "reward_currency" in data:
                set_setting("reward_currency", data["reward_currency"])
            if "enabled" in data:
                set_setting("enabled", str(data["enabled"]).lower())

            self._send_json({
                "success": True,
                "settings": {
                    "reward_amount": float(get_setting("reward_amount") or "0.5"),
                    "reward_currency": get_setting("reward_currency") or "USD",
                    "enabled": get_setting("enabled") == "true",
                }
            })

        else:
            self._send_json({"error": "Not Found"}, 404)


def run_server():
    init_db()
    server = HTTPServer(("0.0.0.0", PORT), CheckinHandler)
    print(f"[签到服务] 启动成功: http://0.0.0.0:{PORT}")
    print(f"[签到服务] 管理后台: http://0.0.0.0:{PORT}/admin")
    print(f"[签到服务] 环境变量配置:")
    print(f"  SUB2API_URL={SUB2API_URL or '(未配置)'}")
    print(f"  SUB2API_ADMIN_KEY={'***' if SUB2API_ADMIN_KEY else '(未配置)'}")
    print(f"  CHECKIN_PORT={PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[签到服务] 已停止")
        server.shutdown()


# ============================================================
# 前端页面
# ============================================================

CHECKIN_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>每日签到</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:#f5f7fa;min-height:100vh;display:flex;align-items:center;justify-content:center}
.card{background:#fff;border-radius:16px;padding:40px;text-align:center;box-shadow:0 4px 24px rgba(0,0,0,0.08);max-width:400px;width:90%}
.icon{font-size:64px;margin-bottom:16px}
h1{font-size:24px;color:#1a1a2e;margin-bottom:8px}
.desc{color:#666;font-size:14px;margin-bottom:24px}
.reward{background:linear-gradient(135deg,#667eea,#764ba2);color:#fff;padding:20px;border-radius:12px;margin-bottom:24px}
.reward-amount{font-size:36px;font-weight:700}
.reward-label{font-size:14px;opacity:0.9;margin-top:4px}
.btn{display:inline-block;padding:14px 48px;border-radius:12px;font-size:16px;font-weight:600;cursor:pointer;border:none;transition:all 0.2s}
.btn-primary{background:#667eea;color:#fff}
.btn-primary:hover{background:#5a6fd6;transform:translateY(-1px)}
.btn-disabled{background:#e0e0e0;color:#999;cursor:not-allowed}
.status{font-size:14px;margin-top:16px;color:#888}
.status.success{color:#4caf50}
.status.error{color:#f44336}
.history{margin-top:24px;text-align:left;max-height:200px;overflow-y:auto}
.history h3{font-size:14px;color:#888;margin-bottom:8px}
.history-item{display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid #f0f0f0;font-size:13px;color:#555}
.spinner{display:inline-block;width:20px;height:20px;border:3px solid rgba(255,255,255,0.3);border-top-color:#fff;border-radius:50%;animation:spin 0.6s linear infinite;vertical-align:middle;margin-right:8px}
@keyframes spin{to{transform:rotate(360deg)}}
</style>
</head>
<body>
<div class="card">
  <div class="icon">🎁</div>
  <h1>每日签到</h1>
  <p class="desc">每天首次签到，自动领取额度奖励</p>

  <div class="reward" id="rewardBox" style="display:none">
    <div class="reward-label">今日签到奖励</div>
    <div class="reward-amount" id="rewardAmount">---</div>
    <div class="reward-label" id="rewardCurrency"></div>
  </div>

  <button class="btn btn-primary" id="checkinBtn" onclick="doCheckin()">立即签到</button>
  <div class="status" id="status"></div>

  <div class="history" id="history" style="display:none">
    <h3>签到记录</h3>
    <div id="historyList"></div>
  </div>
</div>

<script>
const API = window.location.origin;
const USER_ID = localStorage.getItem("checkin_user_id") || prompt("请输入你的 sub2api 用户名或邮箱：");
if (USER_ID) localStorage.setItem("checkin_user_id", USER_ID);

async function loadStatus() {
  if (!USER_ID) return;
  try {
    const resp = await fetch(`${API}/api/checkin/status?user_id=${encodeURIComponent(USER_ID)}`);
    const data = await resp.json();

    if (data.disabled) {
      document.getElementById("status").textContent = "签到功能暂未开放";
      document.getElementById("status").className = "status";
      document.getElementById("checkinBtn").disabled = true;
      document.getElementById("checkinBtn").className = "btn btn-disabled";
      return;
    }

    document.getElementById("rewardBox").style.display = "block";
    document.getElementById("rewardAmount").textContent = data.reward_available || data.today_reward || "---";

    if (data.checked_in) {
      document.getElementById("status").textContent = "今日已签到";
      document.getElementById("status").className = "status success";
      document.getElementById("checkinBtn").textContent = "今日已签到";
      document.getElementById("checkinBtn").disabled = true;
      document.getElementById("checkinBtn").className = "btn btn-disabled";
    } else {
      document.getElementById("status").textContent = "今日还未签到";
      document.getElementById("status").className = "status";
    }

    // 加载历史
    const histResp = await fetch(`${API}/api/checkin/history?user_id=${encodeURIComponent(USER_ID)}`);
    const histData = await histResp.json();
    if (histData.history && histData.history.length > 0) {
      document.getElementById("history").style.display = "block";
      document.getElementById("historyList").innerHTML = histData.history.slice(0, 7).map(h =>
        `<div class="history-item"><span>${h.date}</span><span>+${h.reward}</span></div>`
      ).join("");
    }
  } catch (e) {
    document.getElementById("status").textContent = "加载失败: " + e.message;
    document.getElementById("status").className = "status error";
  }
}

async function doCheckin() {
  if (!USER_ID) return;
  const btn = document.getElementById("checkinBtn");
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>签到中...';

  try {
    const resp = await fetch(`${API}/api/checkin/do`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({user_id: USER_ID})
    });
    const data = await resp.json();

    if (data.success) {
      document.getElementById("status").textContent = data.message || "签到成功！";
      document.getElementById("status").className = "status success";
      btn.textContent = "今日已签到";
      btn.className = "btn btn-disabled";
      document.getElementById("rewardAmount").textContent = data.reward;
      if (data.code) {
        document.getElementById("status").textContent += " 兑换码: " + data.code;
      }
    } else {
      document.getElementById("status").textContent = data.error || "签到失败";
      document.getElementById("status").className = "status error";
      btn.textContent = "立即签到";
      btn.disabled = false;
      btn.className = "btn btn-primary";
    }
  } catch (e) {
    document.getElementById("status").textContent = "网络错误: " + e.message;
    document.getElementById("status").className = "status error";
    btn.textContent = "立即签到";
    btn.disabled = false;
    btn.className = "btn btn-primary";
  }
}

loadStatus();
</script>
</body>
</html>"""

ADMIN_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>签到管理后台</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:#f5f7fa;min-height:100vh;padding:24px}
.header{max-width:600px;margin:0 auto 24px;display:flex;justify-content:space-between;align-items:center}
.header h1{font-size:24px;color:#1a1a2e}
.card{background:#fff;border-radius:16px;padding:32px;box-shadow:0 2px 16px rgba(0,0,0,0.06);max-width:600px;margin:0 auto 16px}
.card h2{font-size:18px;color:#1a1a2e;margin-bottom:20px;padding-bottom:12px;border-bottom:2px solid #f0f0f0}
.form-group{margin-bottom:20px}
.form-group label{display:block;font-size:14px;color:#666;margin-bottom:6px;font-weight:500}
.form-group input,.form-group select{width:100%;padding:10px 14px;border:1px solid #ddd;border-radius:8px;font-size:14px;outline:none;transition:border-color 0.2s}
.form-group input:focus,.form-group select:focus{border-color:#667eea}
.toggle{position:relative;display:inline-block;width:52px;height:28px}
.toggle input{display:none}
.slider{position:absolute;top:0;left:0;right:0;bottom:0;background:#ccc;border-radius:28px;cursor:pointer;transition:0.3s}
.slider:before{content:"";position:absolute;height:22px;width:22px;left:3px;bottom:3px;background:#fff;border-radius:50%;transition:0.3s}
input:checked+.slider{background:#667eea}
input:checked+.slider:before{transform:translateX(24px)}
.toggle-row{display:flex;align-items:center;gap:12px}
.btn{display:inline-block;padding:12px 32px;border-radius:10px;font-size:15px;font-weight:600;cursor:pointer;border:none;background:#667eea;color:#fff;transition:all 0.2s}
.btn:hover{background:#5a6fd6}
.btn:disabled{opacity:0.5;cursor:not-allowed}
.stats{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:20px}
.stat-item{background:#f8f9ff;padding:16px;border-radius:10px;text-align:center}
.stat-value{font-size:28px;font-weight:700;color:#667eea}
.stat-label{font-size:13px;color:#888;margin-top:4px}
.status-msg{font-size:14px;margin-top:12px;text-align:center}
.status-msg.success{color:#4caf50}
.status-msg.error{color:#f44336}
.env-notice{background:#fff3cd;border:1px solid #ffc107;color:#856404;padding:16px;border-radius:10px;font-size:14px;margin-top:12px}
.env-notice code{background:rgba(0,0,0,0.08);padding:2px 6px;border-radius:4px;font-size:13px}
</style>
</head>
<body>
<div class="header">
  <h1>签到管理后台</h1>
  <button class="btn" onclick="window.open('/','_blank')">预览签到页</button>
</div>

<div class="card">
  <h2>签到设置</h2>
  <div class="form-group">
    <label>每日签到赠送额度</label>
    <input type="number" id="rewardAmount" step="0.01" min="0" value="0.5" onchange="markDirty()">
  </div>
  <div class="form-group">
    <label>额度单位</label>
    <select id="rewardCurrency" onchange="markDirty()">
      <option value="USD">USD</option>
      <option value="CNY">CNY</option>
      <option value="Token">Token</option>
    </select>
  </div>
  <div class="form-group">
    <div class="toggle-row">
      <label class="toggle">
        <input type="checkbox" id="enabled" checked onchange="markDirty()">
        <span class="slider"></span>
      </label>
      <label>启用签到功能</label>
    </div>
  </div>
  <button class="btn" id="saveBtn" onclick="saveSettings()">保存设置</button>
  <div class="status-msg" id="saveStatus"></div>

  <div class="stats" id="statsContainer">
    <div class="stat-item">
      <div class="stat-value" id="todayCount">-</div>
      <div class="stat-label">今日签到人数</div>
    </div>
    <div class="stat-item">
      <div class="stat-value" id="todayTotal">-</div>
      <div class="stat-label">今日发放总额</div>
    </div>
  </div>

  <div class="env-notice" id="envNotice">
    <strong>环境配置提示</strong><br>
    如果签到发放的额度需要自动到账，请在启动服务时配置环境变量:<br>
    <code>SUB2API_URL</code> = 你的 sub2api 站点地址（如 <code>http://localhost:8080</code>）<br>
    <code>SUB2API_ADMIN_KEY</code> = 管理员 API Key<br>
    未配置时，签到仅记录日志，额度需手动发放。
  </div>
</div>

<script>
let dirty = false;
function markDirty() { dirty = true; }

async function loadSettings() {
  try {
    const resp = await fetch("/api/admin/settings");
    const data = await resp.json();
    document.getElementById("rewardAmount").value = data.reward_amount;
    document.getElementById("rewardCurrency").value = data.reward_currency;
    document.getElementById("enabled").checked = data.enabled;
    document.getElementById("todayCount").textContent = data.today_stats.count;
    document.getElementById("todayTotal").textContent = data.today_stats.total_reward.toFixed(2);
    if (data.sub2api_url && data.sub2api_url !== "未配置") {
      document.getElementById("envNotice").style.display = "none";
    }
    dirty = false;
  } catch (e) {
    document.getElementById("saveStatus").textContent = "加载设置失败";
    document.getElementById("saveStatus").className = "status-msg error";
  }
}

async function saveSettings() {
  const btn = document.getElementById("saveBtn");
  btn.disabled = true;
  btn.textContent = "保存中...";

  try {
    const resp = await fetch("/api/admin/settings", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        reward_amount: parseFloat(document.getElementById("rewardAmount").value),
        reward_currency: document.getElementById("rewardCurrency").value,
        enabled: document.getElementById("enabled").checked
      })
    });
    const data = await resp.json();
    if (data.success) {
      document.getElementById("saveStatus").textContent = "保存成功";
      document.getElementById("saveStatus").className = "status-msg success";
      dirty = false;
      loadSettings();
    }
  } catch (e) {
    document.getElementById("saveStatus").textContent = "保存失败: " + e.message;
    document.getElementById("saveStatus").className = "status-msg error";
  }
  btn.disabled = false;
  btn.textContent = "保存设置";
}

loadSettings();
// 每30秒刷新统计数据
setInterval(loadSettings, 30000);
</script>
</body>
</html>"""


if __name__ == "__main__":
    run_server()
