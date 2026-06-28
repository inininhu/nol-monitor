"""
NOL 票务监控 — Bark 通知版
依赖: pip install requests beautifulsoup4
"""
import os, sys, json, re
import requests
from bs4 import BeautifulSoup
from datetime import datetime

NOL_URL    = "https://world.nol.com/en/ticket"
STATE_FILE = "nol_state.json"
BARK_KEY   = os.environ.get("BARK_KEY", "")
KEYWORDS   = [k.strip() for k in os.environ.get("KEYWORDS", "").split(",") if k.strip()]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

# ── 策略 1：提取 Next.js __NEXT_DATA__ JSON ─────────────────────────────────
def extract_next_data(html):
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>([\s\S]*?)</script>', html)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []

    def walk(obj, depth=0):
        if depth > 10:
            return []
        if isinstance(obj, list) and len(obj) >= 3 and all(isinstance(i, dict) for i in obj):
            sample = obj[0]
            if any(k in sample for k in ["title","name","productName","goodsName","performName"]):
                return obj
        if isinstance(obj, dict):
            for v in obj.values():
                r = walk(v, depth + 1)
                if r:
                    return r
        return []

    items = walk(data.get("props", {}))
    events = []
    for item in items:
        title  = (item.get("title") or item.get("name") or item.get("productName")
                  or item.get("goodsName") or item.get("performName") or "")
        date   = str(item.get("startDate") or item.get("performDate") or
                     item.get("date") or item.get("startDt") or "")
        venue  = (item.get("venue") or item.get("venueName") or
                  item.get("place") or item.get("placeName") or "")
        status = (item.get("status") or item.get("saleStatus") or
                  item.get("ticketStatus") or item.get("saleState") or "Unknown")
        link   = item.get("path") or item.get("url") or item.get("link") or ""
        url    = ("https://world.nol.com" + link) if link and link.startswith("/") else str(link)
        if title:
            events.append({"title": str(title).strip(), "date": str(date)[:30],
                           "venue": str(venue)[:60], "status": str(status).strip(), "url": url})
    return events

# ── 策略 2：BeautifulSoup 解析可见 HTML ────────────────────────────────────
def extract_html(html):
    soup = BeautifulSoup(html, "html.parser")
    events = []
    selectors = [
        "[class*='TicketCard']", "[class*='ticket-card']", "[class*='ticket-item']",
        "[class*='ProductCard']", "[class*='EventCard']",  "[class*='GoodsItem']",
        "li[class*='item']",     "article",
    ]
    for sel in selectors:
        cards = soup.select(sel)
        if len(cards) < 2:
            continue
        for card in cards[:60]:
            title_el = (card.find(class_=re.compile(r'title|name|product|perform', re.I))
                        or card.find(["h2","h3","h4","strong"]))
            title = title_el.get_text(strip=True) if title_el else ""
            if not title:
                title = card.get_text(" ", strip=True)[:80]

            status_el = card.find(class_=re.compile(r'status|badge|state|sale|tag', re.I))
            status = status_el.get_text(strip=True) if status_el else ""

            date_el = card.find(class_=re.compile(r'date|period|term', re.I))
            date = date_el.get_text(strip=True)[:30] if date_el else ""

            venue_el = card.find(class_=re.compile(r'venue|place|location|hall', re.I))
            venue = venue_el.get_text(strip=True)[:50] if venue_el else ""

            link = card.find("a", href=True)
            href = link["href"] if link else ""
            url = ("https://world.nol.com" + href) if href.startswith("/") else href

            if title and len(title) > 5:
                events.append({"title": title, "date": date, "venue": venue,
                               "status": status or "Unknown", "url": url})
        if events:
            log(f"HTML 解析成功（选择器: {sel}），获取 {len(events)} 个演出")
            return events
    return events

# ── Bark 通知 ──────────────────────────────────────────────────────────────
def notify(title, body):
    if not BARK_KEY:
        log("未设置 BARK_KEY，跳过通知")
        return
    try:
        r = requests.post(
            "https://api.day.app/push",
            json={
                "title": title,
                "body": body,
                "device_key": BARK_KEY,
                "group": "NOL监控",
                "icon": "https://world.nol.com/favicon.ico",
                "url": "https://world.nol.com/en/ticket",
                "sound": "alarm",
            },
            timeout=10,
        )
        d = r.json()
        log("✅ Bark 通知已发送" if d.get("code") == 200 else f"❌ Bark 失败: {d.get('message')}")
    except Exception as e:
        log(f"❌ Bark 通知失败: {e}")

# ── 主逻辑 ─────────────────────────────────────────────────────────────────
def main():
    log(f"开始检查: {NOL_URL}")

    try:
        resp = requests.get(NOL_URL, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        html = resp.text
        log(f"页面获取成功 ({len(html)//1024} KB)")
    except Exception as e:
        log(f"❌ 页面获取失败: {e}")
        sys.exit(1)

    events = extract_next_data(html)
    if events:
        log(f"__NEXT_DATA__ 解析成功，获取 {len(events)} 个演出")
    else:
        log("__NEXT_DATA__ 未找到，尝试 HTML 解析...")
        events = extract_html(html)

    if not events:
        log("⚠️ 解析失败，打印页面片段供调试：")
        print(html[1500:3000])
        sys.exit(0)

    # 加载上次状态
    prev_map = {}
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            prev_map = {e["title"]: e for e in json.load(f)}

    # 对比变化
    changes = []
    for ev in events:
        t = ev["title"]
        if not t:
            continue
        if t not in prev_map:
            changes.append({"type": "new", "ev": ev,
                "msg": f"🆕 新演出上线\n{t}\n📅 {ev['date']}  📍 {ev['venue']}\n🎫 {ev['status']}"})
        elif prev_map[t].get("status", "") != ev["status"]:
            changes.append({"type": "change", "ev": ev,
                "msg": f"🔄 状态变更\n{t}\n{prev_map[t].get('status','')} → {ev['status']}"})

    # 关键词过滤（仅影响通知，不影响状态记录）
    notified = ([c for c in changes
                 if any(k.lower() in c["ev"]["title"].lower() for k in KEYWORDS)]
                if KEYWORDS else changes)

    if notified:
        # 每条变化单独发一条 Bark 通知，方便查看
        for c in notified:
            lines = c["msg"].split("\n")
            title_str = lines[0]          # e.g. "🆕 新演出上线"
            body_str  = "\n".join(lines[1:])  # rest of the message
            notify(title_str, body_str)
        log(f"🔔 已发送 {len(notified)} 条通知")
    elif changes:
        log(f"发现 {len(changes)} 个变化但不匹配关键词，跳过通知")
    else:
        log(f"✓ 无变化（共 {len(events)} 个演出）")

    for c in changes:
        log(c["msg"].replace("\n", "  |  "))

    # 保存状态
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(events, f, ensure_ascii=False, indent=2)
    log("完成")

if __name__ == "__main__":
    main()
