#!/usr/bin/env python3
"""
Bangumi 追番日历生成器
根据 Bangumi 用户的「想看 / 在看」动画条目，生成分集播出时间的 iCalendar (ICS) 文件。

数据来源：
- 收藏列表:  GET https://api.bgm.tv/v0/users/{uid}/collections
- 分集信息:  GET https://api.bgm.tv/v0/episodes
- 每日放送:  GET https://api.bgm.tv/calendar （用于补充分集的具体播出时刻）

仅使用 Python 标准库，无需安装依赖。
"""

import json
import os
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone

# 代理：本地调试可设 BANGUMI_PROXY=http://127.0.0.1:7897（GitHub Actions 上无需设置）
# 注意：显式 ProxyHandler 而非依赖 urllib 的 HTTPS_PROXY 解析，规避其行为不一致问题
BANGUMI_PROXY = os.environ.get("BANGUMI_PROXY", "")

# ────────── 配置 ──────────
BANGUMI_UID = os.environ.get("BANGUMI_UID", "463198")
OUTPUT_PATH = os.environ.get("OUTPUT_PATH", "bangumi.ics")
USER_AGENT = "bangumi-ical/1.0 (github actions; uid:463198)"
API_BASE = os.environ.get("BANGUMI_BASE_URL", "https://api.bgm.tv")

# 只保留播出日期在该窗口内的分集，避免老条目刷满日历
DAYS_BACK = int(os.environ.get("DAYS_BACK", "14"))      # 已播出的分集保留最近 N 天
DAYS_FORWARD = int(os.environ.get("DAYS_FORWARD", "180"))  # 最多展望未来 N 天

# 事件相对播出日期的偏移天数（默认 +1 = 设到第二天；设 0 可恢复原样）
# 适用场景：深夜档动画的播出日期为日本时间，实际观看日在次日
DAY_OFFSET = int(os.environ.get("DAY_OFFSET", "1"))

# 收藏类型: 1=想看, 3=在看（Bangumi API 定义，subject_type=2 为动画）
COLLECTION_TYPES = {"1": "想看", "3": "在看"}

# 是否扫描「看过」条目的续集/特别篇/剧场版等关联新番（1=开启，0=关闭）
INCLUDE_SEQUELS = os.environ.get("INCLUDE_SEQUELS", "1") == "1"
# 关联条目仅保留播出日期为空或在此天数内的（即即将播出/正在播出），避免老番关联刷屏
SEQUEL_WINDOW_DAYS = int(os.environ.get("SEQUEL_WINDOW_DAYS", "45"))


def http_get_json(path: str, retries: int = 3) -> object:
    """请求 Bangumi API 并返回 JSON，带简单重试。"""
    url = f"{API_BASE}{path}"
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": BANGUMI_PROXY, "https": BANGUMI_PROXY})
        if BANGUMI_PROXY else urllib.request.ProxyHandler({})
    )
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with opener.open(req, timeout=20) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"请求失败: {url} -> {last_err}")


def fetch_collections(uid: str, ctype: str) -> list:
    """分页拉取某用户某类收藏（动画）。"""
    result, offset, limit = [], 0, 50
    while True:
        data = http_get_json(
            f"/v0/users/{uid}/collections?subject_type=2&type={ctype}"
            f"&limit={limit}&offset={offset}"
        )
        batch = data.get("data", [])
        result.extend(batch)
        if len(batch) < limit:
            break
        offset += limit
        time.sleep(0.3)
    return result


def fetch_calendar() -> dict:
    """每日放送：返回 subject_id -> {"time": "HH:MM"} 映射。"""
    mapping = {}
    try:
        cal = http_get_json("/calendar")
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 每日放送接口失败，分集将按全天事件生成: {e}", file=sys.stderr)
        return mapping
    for day in cal:
        for item in day.get("items", []):
            sid = item.get("id")
            t = item.get("time") or item.get("air_time")
            if sid and isinstance(t, str) and ":" in t:
                mapping[sid] = t
    return mapping


def fetch_episodes(subject_id: int) -> list:
    """拉取条目的正片分集（type=0），自动分页。"""
    result, offset, limit = [], 0, 100
    while True:
        data = http_get_json(
            f"/v0/episodes?subject_id={subject_id}&type=0"
            f"&limit={limit}&offset={offset}"
        )
        batch = data.get("data", [])
        result.extend(batch)
        total = data.get("total")
        if len(batch) < limit or (total is not None and offset + len(batch) >= total):
            break
        offset += limit
        time.sleep(0.3)
    return result


def parse_airdate(s: str):
    """解析播出日期，容忍异常格式；返回 date 或 None。"""
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError, AttributeError):
        return None


def ics_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def fold_line(line: str) -> str:
    """RFC 5545 规定单行不超过 75 字节，超长需折叠。"""
    out, cur = [], ""
    for ch in line:
        if len((cur + ch).encode("utf-8")) > 73:
            out.append(cur)
            cur = " " + ch
        else:
            cur += ch
    out.append(cur)
    return "\r\n".join(out)


def format_ep_ranges(nums: list) -> str:
    """把集号列表格式化为范围：[1,2,3,4] -> 'EP1～4'；[1,3,5] -> 'EP1、EP3、EP5'。"""
    nums = sorted(set(nums))
    parts, i = [], 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        if j - i >= 1:
            parts.append(f"EP{nums[i]}～{nums[j]}")
        else:
            parts.append(f"EP{nums[i]}")
        i = j + 1
    return "、".join(parts)


def build_events(subjects: list, air_times: dict, today: date) -> list:
    """生成 [(dtstart, all_day, summary, description, uid)] 列表。

    同一部动画同一天播出的多集合并为一个事件。
    """
    lo, hi = today - timedelta(days=DAYS_BACK), today + timedelta(days=DAYS_FORWARD)
    events = []
    # (subject_id, 事件日期) -> [分集]
    grouped = {}

    for subj in subjects:
        sid = subj["subject_id"]
        info = subj.get("subject") or {}
        title = info.get("name_cn") or info.get("name") or f"subject {sid}"
        orig = info.get("name", "")
        air_time = air_times.get(sid)  # "HH:MM" 或 None

        try:
            eps = fetch_episodes(sid)
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 条目 {sid}《{title}》分集获取失败，跳过: {e}", file=sys.stderr)
            continue

        for ep in eps:
            ad = parse_airdate(ep.get("airdate", ""))
            if ad is None or not (lo <= ad <= hi):
                continue
            event_date = ad + timedelta(days=DAY_OFFSET)  # 偏移到第二天（或指定天数）
            src = info.get("_source", "")
            grouped.setdefault((sid, event_date, title, orig, src, air_time), []).append(ep)

        time.sleep(0.3)

    for (sid, event_date, title, orig, src, air_time), day_eps in grouped.items():
        day_eps.sort(key=lambda e: e.get("sort") or e.get("ep") or 0)
        nums = [e.get("sort") or e.get("ep") or 0 for e in day_eps]
        summary = f"{title} {format_ep_ranges(nums)}".strip()

        desc_parts = []
        if orig and orig != title:
            desc_parts.append(orig)
        if src:
            desc_parts.append(src)
        for ep in day_eps:
            ep_no = ep.get("sort") or ep.get("ep") or "?"
            ep_title = ep.get("name_cn") or ep.get("name") or ""
            if ep_title:
                desc_parts.append(f"EP{ep_no} {ep_title}")
        desc_parts.append(f"https://bgm.tv/subject/{sid}")
        desc = " | ".join(desc_parts)
        uid = f"bgm-{sid}-{event_date.isoformat()}@bangumi-ical"

        if air_time:
            hh, mm = (int(x) for x in air_time.split(":")[:2])
            dtstart = datetime(event_date.year, event_date.month, event_date.day, hh, mm)
            events.append((dtstart, False, summary, desc, uid))
        else:
            events.append((event_date, True, summary, desc, uid))

    return events


def render_ics(events: list) -> str:
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//bangumi-ical//Bangumi Episode Calendar//CN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Bangumi 追番日历",
        "X-WR-TIMEZONE:Asia/Shanghai",
        # Asia/Shanghai 无夏令时，固定 +08:00
        "BEGIN:VTIMEZONE",
        "TZID:Asia/Shanghai",
        "BEGIN:STANDARD",
        "DTSTART:19700101T000000",
        "TZOFFSETFROM:+0800",
        "TZOFFSETTO:+0800",
        "TZNAME:CST",
        "END:STANDARD",
        "END:VTIMEZONE",
    ]
    for dtstart, all_day, summary, desc, uid in events:
        lines.append("BEGIN:VEVENT")
        lines.append(f"UID:{uid}")
        lines.append(f"DTSTAMP:{now}")
        if all_day:
            lines.append(f"DTSTART;VALUE=DATE:{dtstart.strftime('%Y%m%d')}")
        else:
            lines.append(f"DTSTART;TZID=Asia/Shanghai:{dtstart.strftime('%Y%m%dT%H%M%S')}")
            end = dtstart + timedelta(minutes=30)
            lines.append(f"DTEND;TZID=Asia/Shanghai:{end.strftime('%Y%m%dT%H%M%S')}")
        lines.append(f"SUMMARY:{ics_escape(summary)}")
        lines.append(f"DESCRIPTION:{ics_escape(desc)}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold_line(x) for x in lines) + "\r\n"


def main():
    uid = BANGUMI_UID
    today = date.today()

    subjects, type_names = [], []
    for ctype, cname in COLLECTION_TYPES.items():
        coll = fetch_collections(uid, ctype)
        type_names.append(f"{cname} {len(coll)} 部")
        subjects.extend(coll)
        time.sleep(0.3)

    print(f"用户 {uid}: {'、'.join(type_names)}，共 {len(subjects)} 个条目")

    # 扫描「看过」条目的关联新番（续集/特别篇/剧场版等）
    if INCLUDE_SEQUELS:
        watched = fetch_collections(uid, "2")
        known_ids = {s["subject_id"] for s in subjects} | {c["subject_id"] for c in watched}
        seen_rel, added = set(), 0
        for c in watched:
            sid = c["subject_id"]
            winfo = c.get("subject") or {}
            wtitle = winfo.get("name_cn") or winfo.get("name") or f"subject {sid}"
            try:
                rels = http_get_json(f"/v0/subjects/{sid}/subjects")
            except Exception as e:  # noqa: BLE001
                print(f"[warn] 条目 {sid}《{wtitle}》关联获取失败，跳过: {e}", file=sys.stderr)
                continue
            for rel in rels:
                rid = rel.get("id")
                if not rid or rid in known_ids or rid in seen_rel:
                    continue
                if rel.get("type") != 2:  # 仅动画
                    continue
                d = parse_airdate(rel.get("date") or "")
                if d is None or d >= today - timedelta(days=SEQUEL_WINDOW_DAYS):
                    seen_rel.add(rid)
                    subjects.append({
                        "subject_id": rid,
                        "subject": {
                            "name": rel.get("name", ""),
                            "name_cn": rel.get("name_cn", ""),
                            "_source": f"承接《{wtitle}》（{rel.get('relation', '')}）",
                        },
                    })
                    added += 1
            time.sleep(0.25)
        print(f"从「看过」{len(watched)} 部中找出 {added} 个续集/特别篇等新条目")

    air_times = fetch_calendar()
    print(f"每日放送匹配到 {len(air_times)} 个条目的具体播出时刻")

    events = build_events(subjects, air_times, today)
    events.sort(key=lambda e: (e[0].isoformat() if isinstance(e[0], date) else "", e[2]))

    ics = render_ics(events)
    with open(OUTPUT_PATH, "w", encoding="utf-8", newline="") as f:
        f.write(ics)
    print(f"已生成 {OUTPUT_PATH}，共 {len(events)} 个分集事件")


if __name__ == "__main__":
    main()
