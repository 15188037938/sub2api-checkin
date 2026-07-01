#!/usr/bin/env python3
"""Sub2API每日签到服务 - 单文件部署版"""
import sqlite3,datetime,json,os,sys
from http.server import HTTPServer,BaseHTTPRequestHandler
from urllib.parse import urlparse,parse_qs

DB_PATH=os.path.join(os.path.dirname(os.path.abspath(__file__)),"checkin.db")
PORT=int(os.environ.get("CHECKIN_PORT",18888))
SUB2API_URL=os.environ.get("SUB2API_URL","")
SUB2API_ADMIN_KEY=os.environ.get("SUB2API_ADMIN_KEY","")

def db_init():
    c=sqlite3.connect(DB_PATH)
    c.executescript("""CREATE TABLE IF NOT EXISTS checkins(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT NOT NULL,date TEXT NOT NULL,reward REAL NOT NULL,created_at TEXT NOT NULL,UNIQUE(user_id,date));
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
INSERT OR IGNORE INTO settings(key,value) VALUES('reward_amount','0.5');
INSERT OR IGNORE INTO settings(key,value) VALUES('reward_currency','USD');
INSERT OR IGNORE INTO settings(key,value) VALUES('enabled','true');""")
    c.commit();c.close()

def gs(k):
    c=sqlite3.connect(DB_PATH);r=c.execute("SELECT value FROM settings WHERE key=?",(k,)).fetchone();c.close()
    return r[0] if r else None

def ss(k,v):
    c=sqlite3.connect(DB_PATH);c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)",(k,v));c.commit();c.close()

def chk_today(uid):
    t=datetime.date.today().isoformat()
    c=sqlite3.connect(DB_PATH);r=c.execute("SELECT id,reward FROM checkins WHERE user_id=? AND date=?",(uid,t)).fetchone();c.close()
    return r

def do_ck(uid,reward):
    t=datetime.date.today().isoformat();n=datetime.datetime.now().isoformat()
    c=sqlite3.connect(DB_PATH)
    try:c.execute("INSERT INTO checkins(user_id,date,reward,created_at) VALUES(?,?,?,?)",(uid,t,reward,n));c.commit();return True
    except sqlite3.IntegrityError:return False
    finally:c.close()

def add_bal(uid,amount):
    if not SUB2API_URL or not SUB2API_ADMIN_KEY:return {"success":False,"error":"未配置API"}
    import urllib.request
    try:
        d=json.dumps({"amount":amount,"count":1,"note":"签到奖励"}).encode()
        r=urllib.request.Request(f"{SUB2API_URL}/api/v1/admin/redemption",data=d,headers={"Authorization":f"Bearer {SUB2API_ADMIN_KEY}","Content-Type":"application/json"},method="POST")
        x=json.loads(urllib.request.urlopen(r,timeout=10).read())
        if x.get("success")or x.get("data"):
            codes=x.get("data",[]);
            if codes:k=codes[0]if isinstance(codes[0],str)else codes[0].get("key",codes[0].get("code",""));return{"success":True,"code":k,"amount":amount,"method":"redemption"}
    except:pass
    return{"success":True,"amount":amount,"method":"manual","note":"需手动发放"}

def hist(uid,days=30):
    c=sqlite3.connect(DB_PATH);rows=c.execute("SELECT date,reward FROM checkins WHERE user_id=? ORDER BY date DESC LIMIT ?",(uid,days)).fetchall();c.close()
    return[{"date":r[0],"reward":r[1]}for r in rows]

def ts():
    t=datetime.date.today().isoformat()
    c=sqlite3.connect(DB_PATH);r=c.execute("SELECT COUNT(*),SUM(reward) FROM checkins WHERE date=?",(t,)).fetchone();c.close()
    return{"count":r[0]or 0,"total":r[1]or 0}

CSS="*{margin:0;padding:0;box-sizing:border-box}body{font-family:-apple-system,sans-serif;background:#f5f7fa;min-height:100vh;display:flex;align-items:center;justify-content:center}.card{background:#fff;border-radius:16px;padding:40px;text-align:center;box-shadow:0 4px 24px rgba(0,0,0,0.08);max-width:400px;width:90%}.icon{font-size:64px;margin-bottom:16px}h1{font-size:24px;color:#1a1a2e;margin-bottom:8px}.desc{color:#666;font-size:14px;margin-bottom:24px}.rw{background:linear-gradient(135deg,#667eea,#764ba2);color:#fff;padding:20px;border-radius:12px;margin-bottom:24px}.ra{font-size:36px;font-weight:700}.rl{font-size:14px;opacity:0.9;margin-top:4px}.btn{display:inline-block;padding:14px 48px;border-radius:12px;font-size:16px;font-weight:600;cursor:pointer;border:none}.bp{background:#667eea;color:#fff}.bd{background:#e0e0e0;color:#999;cursor:not-allowed}.st{font-size:14px;margin-top:16px;color:#888}.ss{color:#4caf50}.se{color:#f44336}.hist{margin-top:24px;text-align:left;max-height:200px;overflow-y:auto}.hist h3{font-size:14px;color:#888;margin-bottom:8px}.hi{display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid #f0f0f0;font-size:13px;color:#555}.sp{display:inline-block;width:20px;height:20px;border:3px solid rgba(255,255,255,0.3);border-top-color:#fff;border-radius:50%;animation:spin 0.6s linear infinite;vertical-align:middle;margin-right:8px}@keyframes spin{to{transform:rotate(360deg)}}"

CHECKIN_HTML=f"""<!DOCTYPE html><html lang=zh><head><meta charset=UTF-8><meta name=viewport content="width=device-width,initial-scale=1"><title>每日签到</title><style>{CSS}</style></head><body><div class=card><div class=icon>🎁</div><h1>每日签到</h1><p class=desc>每天首次签到，自动领取额度奖励</p><div class=rw id=rb style=display:none><div class=rl>今日签到奖励</div><div class=ra id=ramt>---</div></div><button class="btn bp" id=cb onclick=doCheckin()>立即签到</button><div class=st id=st></div><div class=hist id=hist style=display:none><h3>签到记录</h3><div id=hl></div></div></div><script>
const API=location.origin;
let UID=localStorage.getItem('ckuid');
if(!UID){{UID=prompt('用户名/邮箱：');if(UID)localStorage.setItem('ckuid',UID)}}
async function load(){{const r=await fetch(API+'/api/checkin/status?user_id='+UID);const d=await r.json();
if(d.disabled){{st.textContent='签到未开放';return}}
rb.style.display='block';ramt.textContent=d.reward_available||d.today_reward||'---';
if(d.checked_in){{st.textContent='今日已签到';st.className='st ss';cb.textContent='今日已签到';cb.disabled=true;cb.className='btn bd'}}
const hr=await fetch(API+'/api/checkin/history?user_id='+UID);const hd=await hr.json();
if(hd.history&&hd.history.length){{hist.style.display='block';hl.innerHTML=hd.history.slice(0,7).map(h=>'<div class=hi><span>'+h.date+'</span><span>+'+h.reward+'</span></div>').join('')}}}}
async function doCheckin(){{cb.disabled=true;cb.innerHTML='<span class=sp></span>签到中...';
try{{const r=await fetch(API+'/api/checkin/do',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{user_id:UID}})}});const d=await r.json();
if(d.success){{st.textContent=d.message||'签到成功！';st.className='st ss';cb.textContent='今日已签到';cb.className='btn bd';ramt.textContent=d.reward}}
else{{st.textContent=d.error||'失败';st.className='st se';cb.textContent='立即签到';cb.className='btn bp';cb.disabled=false}}}}
catch(e){{st.textContent='错误:'+e.message;st.className='st se';cb.textContent='立即签到';cb.className='btn bp';cb.disabled=false}}}}
load();</script></body></html>"""

ADMIN_HTML=f"""<!DOCTYPE html><html lang=zh><head><meta charset=UTF-8><meta name=viewport content="width=device-width,initial-scale=1"><title>签到管理</title><style>{CSS}
.card h2{{font-size:18px;color:#1a1a2e;margin-bottom:20px;padding-bottom:12px;border-bottom:2px solid #f0f0f0;text-align:left}}
.fg{{margin-bottom:20px;text-align:left}}.fg label{{display:block;font-size:14px;color:#666;margin-bottom:6px;font-weight:500}}
.fg input,.fg select{{width:100%;padding:10px 14px;border:1px solid #ddd;border-radius:8px;font-size:14px;outline:none}}
.fg input:focus{{border-color:#667eea}}
.tg{{position:relative;display:inline-block;width:52px;height:28px}}.tg input{{display:none}}
.sl{{position:absolute;top:0;left:0;right:0;bottom:0;background:#ccc;border-radius:28px;cursor:pointer;transition:0.3s}}
.sl:before{{content:"";position:absolute;height:22px;width:22px;left:3px;bottom:3px;background:#fff;border-radius:50%;transition:0.3s}}
input:checked+.sl{{background:#667eea}}input:checked+.sl:before{{transform:translateX(24px)}}
.tr{{display:flex;align-items:center;gap:12px;text-align:left}}
.hdr{{max-width:600px;margin:0 auto 24px;display:flex;justify-content:space-between;align-items:center}}
.hdr h1{{text-align:left}}.stats{{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:20px}}
.si{{background:#f8f9ff;padding:16px;border-radius:10px;text-align:center}}.sv{{font-size:28px;font-weight:700;color:#667eea}}.slb{{font-size:13px;color:#888;margin-top:4px}}
.env{{background:#fff3cd;border:1px solid #ffc107;color:#856404;padding:16px;border-radius:10px;font-size:14px;margin-top:12px;text-align:left}}
.env code{{background:rgba(0,0,0,0.08);padding:2px 6px;border-radius:4px;font-size:13px}}
</style></head><body><div class=hdr><h1>签到管理后台</h1><button class="btn bp" onclick="window.open('/')">预览签到页</button></div>
<div class=card style=max-width:600px;margin:0 auto 16px><h2>签到设置</h2>
<div class=fg><label>每日签到赠送额度</label><input type=number id=ra step=0.01 min=0 value=0.5 onchange=dirty=true></div>
<div class=fg><label>额度单位</label><select id=rc onchange=dirty=true><option value=USD>USD</option><option value=CNY>CNY</option><option value=Token>Token</option></select></div>
<div class=fg><div class=tr><label class=tg><input type=checkbox id=en checked onchange=dirty=true><span class=sl></span></label><label>启用签到</label></div></div>
<button class="btn bp" id=sb onclick=save()>保存设置</button><div class=st id=ss></div>
<div class=stats><div class=si><div class=sv id=tc>-</div><div class=slb>今日签到人数</div></div><div class=si><div class=sv id=tt>-</div><div class=slb>今日发放总额</div></div></div>
<div class=env id=enote><strong>配置提示</strong><br>如需额度自动到账，启动服务时设置环境变量:<br><code>SUB2API_URL</code>=站点地址 <code>SUB2API_ADMIN_KEY</code>=管理员Key<br>未配置则仅记录日志。</div></div>
<script>
let dirty=false;
async function load(){{const r=await fetch('/api/admin/settings');const d=await r.json();ra.value=d.reward_amount;rc.value=d.reward_currency;en.checked=d.enabled;tc.textContent=d.today_stats.count;tt.textContent=d.today_stats.total.toFixed(2);if(d.sub2api_url&&d.sub2api_url!=='未配置')enote.style.display='none';dirty=false}}
async function save(){{sb.disabled=true;sb.textContent='保存中...';
const r=await fetch('/api/admin/settings',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{reward_amount:parseFloat(ra.value),reward_currency:rc.value,enabled:en.checked}})}});
const d=await r.json();
if(d.success){{ss.textContent='保存成功';ss.className='st ss';dirty=false;load()}}
else{{ss.textContent='保存失败';ss.className='st se'}}
sb.disabled=false;sb.textContent='保存设置'}}
load();setInterval(load,30000);</script></body></html>"""

class H(BaseHTTPRequestHandler):
    def log_message(self,*a):pass
    def _j(self,d,s=200):
        self.send_response(s);self.send_header("Content-Type","application/json; charset=utf-8");self.send_header("Access-Control-Allow-Origin","*");self.send_header("Access-Control-Allow-Headers","Content-Type,Authorization");self.send_header("Access-Control-Allow-Methods","GET,POST,OPTIONS");self.end_headers()
        self.wfile.write(json.dumps(d,ensure_ascii=False).encode())
    def _h(self,html,s=200):
        self.send_response(s);self.send_header("Content-Type","text/html; charset=utf-8");self.end_headers();self.wfile.write(html.encode())
    def do_OPTIONS(self):
        self.send_response(200);self.send_header("Access-Control-Allow-Origin","*");self.send_header("Access-Control-Allow-Headers","Content-Type,Authorization");self.send_header("Access-Control-Allow-Methods","GET,POST,OPTIONS");self.end_headers()
    def do_GET(self):
        p=urlparse(self.path).path.rstrip("/")
        q=parse_qs(urlparse(self.path).query)
        if p=="/api/checkin/status":
            uid=q.get("user_id",[""])[0]
            if not uid:self._j({"error":"need user_id"},400);return
            if gs("enabled")!="true":self._j({"checked_in":False,"disabled":True});return
            r=chk_today(uid);rw=float(gs("reward_amount")or"0.5")
            if r:self._j({"checked_in":True,"today_reward":r[1],"date":datetime.date.today().isoformat()})
            else:self._j({"checked_in":False,"reward_available":rw,"date":datetime.date.today().isoformat()})
        elif p=="/api/checkin/history":
            uid=q.get("user_id",[""])[0];self._j({"history":hist(uid)})
        elif p=="/api/admin/settings":
            self._j({"reward_amount":float(gs("reward_amount")or"0.5"),"reward_currency":gs("reward_currency")or"USD","enabled":gs("enabled")=="true","sub2api_url":SUB2API_URL or"未配置","today_stats":ts()})
        elif p=="/admin":self._h(ADMIN_HTML)
        elif p in("/",""):self._h(CHECKIN_HTML)
        else:self._j({"error":"Not Found"},404)
    def do_POST(self):
        p=urlparse(self.path).path.rstrip("/")
        cl=int(self.headers.get("Content-Length",0));body=self.rfile.read(cl)if cl>0 else b"{}"
        try:d=json.loads(body)
        except:d={}
        if p=="/api/checkin/do":
            uid=d.get("user_id","").strip()
            if not uid:
                a=self.headers.get("Authorization","")
                if a.startswith("Bearer "):uid=a[7:].strip()
            if not uid:self._j({"error":"need user_id"},400);return
            if gs("enabled")!="true":self._j({"success":False,"error":"签到已关闭"},400);return
            ex=chk_today(uid)
            if ex:self._j({"success":False,"error":"今日已签到","today_reward":ex[1],"checked_in":True});return
            rw=float(gs("reward_amount")or"0.5");r=add_bal(uid,rw)
            if r.get("success"):
                do_ck(uid,rw);msg=f"签到成功！获得 {rw} 额度"
                if r.get("code"):msg+=f"，兑换码：{r['code']}"
                self._j({"success":True,"reward":rw,"currency":gs("reward_currency")or"USD","method":r.get("method","manual"),"code":r.get("code",""),"message":msg})
            else:self._j({"success":False,"error":r.get("error","发放失败")},500)
        elif p=="/api/admin/settings":
            if"reward_amount"in d:ss("reward_amount",str(float(d["reward_amount"])))
            if"reward_currency"in d:ss("reward_currency",d["reward_currency"])
            if"enabled"in d:ss("enabled",str(d["enabled"]).lower())
            self._j({"success":True,"settings":{"reward_amount":float(gs("reward_amount")or"0.5"),"reward_currency":gs("reward_currency")or"USD","enabled":gs("enabled")=="true"}})
        else:self._j({"error":"Not Found"},404)

if __name__=="__main__":
    db_init()
    s=HTTPServer(("0.0.0.0",PORT),H)
    print(f"[签到] http://0.0.0.0:{PORT} 管理:{PORT}/admin")
    s.serve_forever()
