# bangumi-ical

把 Bangumi「想看 / 在看」动画的分集播出时间生成 iCalendar 订阅链接，供 Apple 日历（macOS / iOS）订阅。零成本：GitHub Actions 定时生成，GitHub Pages 托管。

参考项目：[trim21/bangumi-episode-calendar](https://github.com/trim21/bangumi-episode-calendar)（其公共服务已停运，本项目为其免费替代方案）。

## 部署步骤（一次性）

1. 在 GitHub 新建仓库（如 `bangumi-ical`），把本目录内容推上去：
   ```bash
   cd bangumi-ical
   git init && git add -A && git commit -m "init"
   git remote add origin git@github.com:<你的用户名>/bangumi-ical.git
   git push -u origin master
   ```
2. 仓库设置 → Settings → Pages → Source 选 **GitHub Actions**。
3. 如需修改 Bangumi UID，改 `bangumi_calendar.py` 顶部默认值和 `.github/workflows/deploy.yml` 里的 `BANGUMI_UID`。
4. 到 Actions 页面手动触发一次 "Update and deploy calendar"（或等定时触发）。

## 订阅

在 macOS 日历：**文件 → 新建日历订阅**，粘贴：

```
https://<你的用户名>.github.io/bangumi-ical/bangumi.ics
```

iOS：设置 → 日历 → 账户 → 添加账户 → 其他 → 添加已订阅的日历。

> Apple 日历对订阅日历的自动刷新间隔由系统决定（通常数小时），无法手动强制刷新。

## 本地运行

```bash
python3 bangumi_calendar.py                    # 需要 uid=463198 的收藏为公开
# 如需代理：
BANGUMI_PROXY=http://127.0.0.1:7897 python3 bangumi_calendar.py
```

## 工作原理

- 拉取用户动画收藏：`type=1 想看` + `type=3 在看`
- 另扫描「看过」条目的关联条目（续集/番外篇/特别篇/剧场版/OVA 等），凡为动画且播出日期为空或近 `SEQUEL_WINDOW_DAYS` 天内的（即将/正在播出）一并纳入；已在想看/在看/看过中的条目自动去重
- 逐条目拉取正片分集（type=0）的 `airdate`
- 已播出超过 `DAYS_BACK`（默认 14 天）或日期未知的分集不生成事件
- 事件为**全天事件**（Bangumi API 的分集 `airdate` 只有日期；旧版每日放送接口的 `time` 字段已不再返回数据，故无法给出具体时刻）
- 仅用 Python 标准库，无第三方依赖

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `BANGUMI_UID` | `463198` | Bangumi 用户 UID |
| `OUTPUT_PATH` | `bangumi.ics` | 输出路径 |
| `DAYS_BACK` | `14` | 已播出分集保留天数 |
| `DAYS_FORWARD` | `180` | 未来展望天数 |
| `DAY_OFFSET` | `1` | 事件相对播出日期偏移天数（+1 = 设到第二天，深夜档实际观看日在次日；设 `0` 恢复原样） |
| `INCLUDE_SEQUELS` | `1` | 是否把「看过」条目的续集/特别篇等关联新番纳入日历（`0` 关闭） |
| `SEQUEL_WINDOW_DAYS` | `45` | 关联新番的播出日期须在未来、或已开播不超过此天数 |

## 更新频率

Actions 定时：北京时间每天 6:00 与 18:00 各更新一次，可按需修改 `deploy.yml` 中的 cron。
