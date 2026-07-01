#!/usr/bin/env python3
"""
自动充值脚本 - 配合签到服务使用
当签到服务的 method 为 "manual" 时，此脚本定期检查未处理的签到记录，
通过 sub2api 管理后台 API 批量发放额度。

用法：
  python auto_topup.py --sub2api-url http://localhost:8080 --admin-key sk-xxx --checkin-db checkin.db
"""

import sqlite3
import json
import argparse
import datetime
import sys
try:
    import urllib.request
    import urllib.error
except ImportError:
    pass


def get_unprocessed_checkins(db_path):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("""
        SELECT id, user_id, reward, date FROM checkins 
        WHERE id NOT IN (SELECT checkin_id FROM topup_log)
        ORDER BY date ASC
    """)
    rows = c.fetchall()
    conn.close()
    return rows


def ensure_topup_table(db_path):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS topup_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            checkin_id INTEGER UNIQUE,
            user_id TEXT,
            amount REAL,
            code TEXT,
            created_at TEXT
        )
    """)
    conn.commit()
    conn.close()


def create_redemption_code(sub2api_url, admin_key, amount, note):
    """通过 sub2api 管理 API 创建兑换码"""
    headers = {
        "Authorization": f"Bearer {admin_key}",
        "Content-Type": "application/json",
    }
    data = json.dumps({
        "amount": amount,
        "count": 1,
        "note": note,
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{sub2api_url}/api/v1/admin/redemption",
        data=data,
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        result = json.loads(resp.read().decode("utf-8"))
        if result.get("success") or result.get("data"):
            codes = result.get("data", [])
            if codes:
                code = codes[0]
                return isinstance(code, str) and code or code.get("key", code.get("code", ""))
    return None


def log_topup(db_path, checkin_id, user_id, amount, code):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute(
        "INSERT OR IGNORE INTO topup_log (checkin_id, user_id, amount, code, created_at) VALUES (?, ?, ?, ?, ?)",
        (checkin_id, user_id, amount, code, datetime.datetime.now().isoformat())
    )
    conn.commit()
    conn.close()


def main():
    parser = argparse.ArgumentParser(description="签到自动充值脚本")
    parser.add_argument("--sub2api-url", required=True, help="sub2api 站点地址")
    parser.add_argument("--admin-key", required=True, help="sub2api 管理员 API Key")
    parser.add_argument("--checkin-db", default="checkin.db", help="签到数据库路径")
    args = parser.parse_args()

    ensure_topup_table(args.checkin_db)
    checkins = get_unprocessed_checkins(args.checkin_db)

    if not checkins:
        print("没有待处理的签到记录")
        return

    for cid, user_id, reward, date_str in checkins:
        note = f"[自动] 每日签到 {date_str} - {user_id}"
        print(f"处理: user={user_id}, amount={reward}, date={date_str}")
        code = create_redemption_code(args.sub2api_url, args.admin_key, reward, note)
        if code:
            log_topup(args.checkin_db, cid, user_id, reward, code)
            print(f"  成功: 兑换码={code}")
        else:
            print(f"  失败: 无法生成兑换码")

    remaining = len(get_unprocessed_checkins(args.checkin_db))
    print(f"\n处理完成，剩余 {remaining} 条待处理")


if __name__ == "__main__":
    main()
