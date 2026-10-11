# 今日投递 · job-digest

给**港硕**（香港 + 大陆校招一起投）的每日投递助手：每天从你自己维护的岗位池里，挑出**今天该投的 N 家**推到手机（Telegram / 飞书）；电脑上有一个贴在屏幕顶边的面板，帮你一家一家投完。

秋招最累的往往不是投递本身，而是每天打开电脑先花半小时想今天投哪几家、哪家快截止了、简历放哪了、上次填的英文名怎么拼。这个工具就是来省掉这半小时的。

## 它做什么

- **每日简报**：按截止日排序（3 天内截止 → 7 天内截止 → 滚动招聘），香港、大陆各推 N 家；同时写一份带完整链接的 `今日投递清单.md`
- **投递面板**（Windows）：常驻屏幕顶边，鼠标碰一下就滑下来
  - 每家岗位一键打开申请页、复制简历路径、复制填表要用的字段
  - 每家岗位四种状态：**已投** / **稍后**（明天排最前）/ **屏蔽**（要选原因，当场从池子补一家）/ **撤销**
  - 统计今天投了几家、本周投了几家
  - 检测到在打游戏时整个藏起来
- **求职信**：按你的模板起草 md，写完一键转 PDF
- **链接体检**：`check_links.py` 找出已经 404 的岗位，以后不再推
- **手写提醒**：池子里没有、只有你知道的事（比如“东亚银行校招 11 月中才开”），到日子前在简报里提醒

## 它不做什么

- **不抓岗位**。岗位池是你自己维护的 CSV（官网、学校就业网、LinkedIn 搜到的都往里填），脚本只读不写，新开的岗位不会自己进来。
- **不替你投**。它只负责告诉你今天投什么，并把材料准备好；网申表还是你自己填。
- **链接体检只认 404/410**。如果链接只指到校招首页、而不是具体岗位，它查不出来，所以录入时请直接填岗位详情页。

## 快速开始

需要 Python 3.9+。每日简报只用标准库，面板只能在 Windows 上用。

```bash
git clone <仓库地址> job-digest
cd job-digest
copy config.example.json config.json
copy profile.example.json profile.json
python tools/pool.py          # 看看示例池子筛出来什么
python tools/push.py --dry    # 预览今天的简报（只打印，不推送）
```

默认配置下，大陆池用的是虚构示例（`examples/pool_cn.example.csv`，故意放了几条会被规则剔除的岗位，可以对照着看筛选效果），香港池直接用仓库自带的**真实名单**（见下一节）。确认能跑以后，把 `config.json` 里的 `pool_path` 换成你自己的 CSV。

## 自带的香港 2027 校招名单

`examples/pool_hk_2027.csv`：**30 个**香港 2027 年入职的 graduate programme / MT / 校招岗，覆盖中资券商、外资行、四大和中型会计师行、咨询、金管局。每一条都在 **2026-10-11** 打开官网核实过，`apply_url` 尽量直达具体岗位页；只能邮件投递或者没有单独岗位页的，在 `reasons` 列写了投递方式或点击路径。

- **会过期**：截止日过了会被自动剔除；下一年的项目要自己更新
- **不是完整名单**：偏金融 / 风控 / 审计 / 数据方向，没收录的公司不代表没在招
- `score` 是原作者按自己背景排的优先级，按你的情况改就行
- 发现链接失效或信息有误，欢迎提 issue / PR

**面板**：

```bash
pip install -r requirements.txt
pythonw tools/apply_popup.py
```

**定时**（Windows 任务计划：每天推简报、登录后自动启动面板、定时让面板滑下来提醒）：

```bash
python tools/register_task.py
```

时间在 `config.json` 的 `schedule` 里改，改完重跑一次。

## 岗位池 CSV

UTF-8 编码（带不带 BOM 都可以），第一行是表头，列的顺序随意。

| 列 | 必填 | 说明 |
|---|---|---|
| `id` | ✅ | 唯一且**以后不要改**：已投、屏蔽这些记录都靠它对应 |
| `company` | ✅ | 公司名 |
| `title` | ✅ | 岗位名 |
| `city` | ✅ | 城市，可以写多个（`上海 / 深圳`）；大陆池按 `allowed_cities` 过滤 |
| `apply_url` | ✅ | **岗位详情页**，不要填校招首页 |
| `deadline` |  | 截止日 `YYYY-MM-DD`；留空就当成滚动招聘 |
| `deadline_text` |  | 截止日的原文，比如“招满即止”，只用来显示 |
| `score` |  | 匹配分，数字越大越靠前（同为滚动招聘时按它排） |
| `fin` |  | `True` / `False`，是不是金融方向；`require_fin: true` 时只推 `True` |
| `type` |  | 校招 / 实习 / 社招 / 代理人招募……香港池按 `exclude_type_hk` 剔除 |
| `industry` |  | 行业，按 `exclude_industry` 剔除 |

两个池子的表头一样。不想用香港池，就把 `pool_path_hk` 留空。

> **找具体岗位链接的小技巧**：很多校招系统（北森 zhiye、hotjob、Moka）点岗位是在新窗口打开的，从地址栏复制就行。Workday 系的（`*.myworkdayjobs.com`）岗位页地址里带 `/job/`。实在只有邮箱投递的岗位，就把邮箱和要求写进 `reasons` 列备注。

## 推送

在 `config.json` 里选渠道，可以选多个，有一个发成功就算成功：

```json
"notify": {"channels": ["feishu", "telegram"]}
```

`console` 表示只打印，适合调试。密钥放在**环境变量**或 **`secrets.json`**（这个文件不会进 git）里：

```json
{
 "feishu_webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/xxxx",
 "feishu_secret": "签名校验的密钥（没开签名校验就删掉这行）",
 "telegram_token": "123456:ABC...",
 "telegram_chat": "你的 chat id",
 "telegram_proxy": "http://127.0.0.1:7890"
}
```

对应的环境变量：`JD_FEISHU_WEBHOOK`、`JD_FEISHU_SECRET`、`JD_TELEGRAM_TOKEN`、`JD_TELEGRAM_CHAT`、`JD_TELEGRAM_PROXY`。

**飞书**：自己建一个只有你的群 → 群设置 → 群机器人 → 添加机器人 → **自定义机器人**，复制 webhook 地址。安全设置建议选「签名校验」，把密钥填进 `feishu_secret`；如果选「自定义关键词」，就填 `投递`。

**Telegram**：找 @BotFather 建一个机器人拿到 token，给它发一条消息，再打开 `https://api.telegram.org/bot<token>/getUpdates` 找到你的 chat id。在内地直连不通的话，填上 `telegram_proxy`，直连失败时会自动改走代理。

## 文件说明

```
config.example.json   规则：池子在哪、每天几家、排除哪些词、推送渠道、定时
profile.example.json  个人资料：姓名邮箱、简历路径、速查卡、求职信模板
templates/            求职信模板、填表速查卡示例
examples/             示例岗位池 + 香港 2027 校招名单
tools/
  pool.py             读池子 → 过滤 → 排序（数据层）
  push.py             每日简报
  apply_popup.py/.html  投递面板（Windows）
  check_links.py      死链体检
  register_task.py    注册 Windows 任务计划
  jdlib.py            公共：路径、状态、个人资料、推送
  gaming_guard.py     检测游戏进程
  cl/                 求职信 md → docx → pdf
SKILL.md              给 Claude Code 当 skill 用的说明
```

**不会进 git 的**（见 `.gitignore`）：`config.json`、`profile.json`、`secrets.json`、`state.json`（已投、稍后、屏蔽、死链的记录）、`history.jsonl`（每次操作的日志）、`push.log`。

## 和 Claude Code 一起用

把仓库目录链接到 `~/.claude/skills/job-digest`，就可以直接说“今天投什么”“投完了记一下”“岗位池还剩多少”，Claude 会按 `SKILL.md` 操作。

## 已知局限

- 面板和定时任务只支持 Windows；每日简报本身跨平台，在 macOS / Linux 上可以用 cron 跑 `python tools/push.py`
- 池子要你自己补，简报会提醒“快照已 N 天没更新”
- 「生成 PDF」要装 Microsoft Word（通过 COM 转换）

## License

[MIT](LICENSE)
