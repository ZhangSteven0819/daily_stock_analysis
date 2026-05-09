# OpenClaw VPS 集成方案

本文档用于新的部署路线：

- `daily_stock_analysis` 继续由 GitHub Actions 每天运行
- Actions 产出最新日报快照并提交回仓库
- VPS 上运行 `OpenClaw + openclaw-weixin`
- OpenClaw 通过 Skill 读取 GitHub 上的最新日报快照，并在微信中回答问题

## 一、当前日报快照位置

GitHub Actions 成功后，会把以下文件提交回仓库：

- `published_reports/latest_report_bundle.json`
- `published_reports/latest_report_bundle.md`

如果你的仓库是公开的，VPS 可直接读取 Raw 地址：

```text
https://raw.githubusercontent.com/<github-user>/daily_stock_analysis/main/published_reports/latest_report_bundle.json
```

例如：

```text
https://raw.githubusercontent.com/ZhangSteven0819/daily_stock_analysis/main/published_reports/latest_report_bundle.json
```

## 二、VPS 安装 OpenClaw

官方 Linux/VPS 快速路径：

```bash
curl -fsSL https://deb.nodesource.com/setup_24.x | sudo -E bash -
sudo apt-get install -y nodejs
npm install -g openclaw@latest
openclaw onboard --install-daemon
```

参考：

- OpenClaw Linux 文档：https://docs.openclaw.ai/linux
- Gateway CLI 文档：https://docs.openclaw.ai/cli/gateway

## 三、安装微信插件

```bash
npx -y @tencent-weixin/openclaw-weixin-cli install
openclaw config set plugins.entries.openclaw-weixin.enabled true
openclaw channels login --channel openclaw-weixin
openclaw gateway restart
```

说明：

- `openclaw channels login --channel openclaw-weixin` 会在终端显示二维码
- 用你的微信扫码确认后，微信消息就会路由进 OpenClaw

## 四、安装日报问答 Skill

把下方 Skill 目录放到 VPS 的 OpenClaw workspace：

```text
~/.openclaw/workspace/skills/github-daily-report-chat/
```

然后在 `~/.openclaw/openclaw.json` 中为 skill 注入日报地址：

```json
{
  "skills": {
    "entries": {
      "github-daily-report-chat": {
        "enabled": true,
        "env": {
          "DAILY_REPORT_URL": "https://raw.githubusercontent.com/ZhangSteven0819/daily_stock_analysis/main/published_reports/latest_report_bundle.json"
        }
      }
    }
  }
}
```

## 五、推荐的微信使用方式

父亲在微信里直接问：

- `今天报告是什么意思`
- `这只股票为什么是观望`
- `我已经持有，风险是什么`
- `用简单的话讲给我听`
- `给我一个明天开盘前检查清单`

## 六、限制说明

- 这条方案是“基于最新日报问答”，不是实时重新跑一次全量分析
- 如果想让 OpenClaw 直接实时分析单只股票，需要额外长期运行 DSA API
- 当前方案优先稳、便宜、适合老人使用
