---
name: github-daily-report-chat
description: 基于 GitHub 仓库中的最新股票日报快照回答问题。适用于“今天报告是什么意思”“为什么是观望”“我持有怎么办”“给我明天检查清单”等问题。
metadata: {"openclaw":{"requires":{"env":["DAILY_REPORT_URL"],"anyBins":["curl","python3","python"]},"primaryEnv":"DAILY_REPORT_URL","os":["linux"]}}
---

## 什么时候使用

当用户的问题是在解读当天股票日报，而不是要求实时重新分析时，优先使用本 Skill。

典型触发语句：

- 今天报告是什么意思
- 今天为什么是观望
- 我已经持有怎么办
- 用简单的话讲给我听
- 给我一个明天开盘前检查清单
- 今天 AAPL / 600519 / hk00700 怎么看

## 目标

从 `DAILY_REPORT_URL` 读取最新日报快照，只摘取和用户问题最相关的股票片段，再基于日报内容回答。

## 操作步骤

1. 用 `curl` 或 `python` 读取 `DAILY_REPORT_URL` 的 JSON 内容。
2. 先查看：
   - `report_date`
   - `elder_friendly_summary`
   - `stocks[]`
3. 如果用户提到了股票代码，只挑对应股票。
4. 如果用户没提股票，就从 `stocks[]` 中挑最相关的 1 到 3 只。
5. 回答时遵循下面格式：
   - 先给结论
   - 再讲原因
   - 再讲风险
   - 最后给一个简单行动建议

## 回答风格

- 优先使用中文
- 尽量少术语
- 像讲给老人听
- 不承诺收益
- 如果日报中没有足够信息，要明确说“今天这份日报里没有足够信息”

## 推荐命令

优先：

```bash
curl -fsSL "$DAILY_REPORT_URL"
```

如果 `curl` 不方便：

```bash
python3 - <<'PY'
import json, os, urllib.request
url = os.environ["DAILY_REPORT_URL"]
with urllib.request.urlopen(url, timeout=30) as r:
    data = json.load(r)
print(json.dumps(data, ensure_ascii=False))
PY
```

## 重要边界

- 本 Skill 是“基于日报问答”，不是实时行情终端
- 如果用户要求实时重新分析，应该明确说明当前依据是最近日报
- 如需实时重新分析，需要额外接入长期在线的 DSA API
