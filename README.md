# 今日投递 · job-digest

给**商科港硕**（香港 + 大陆校招一起投）的每日投递助手：每天从你自己维护的岗位池里，挑出**今天该投的 N 家**推到手机（Telegram / 飞书）；电脑上有一个贴在屏幕顶边的面板，帮你一家一家投完。

秋招最累的往往不是投递本身，而是每天打开电脑先花半小时想今天投哪几家、哪家快截止了、简历放哪了、上次填的英文名怎么拼。这个工具就是来省掉这半小时的。

## 它做什么

- **岗位数据自动更新**：大陆岗位每天从公开岗位库导入（5000+ 条 2027 秋招）；香港岗位每天去 17 家关注公司的招聘系统自动查（带岗位直链，新挂出来的会单独提醒）；自己看到的岗位一条命令补录
- **每日简报**：按截止日排序（3 天内截止 → 7 天内截止 → 滚动招聘），香港、大陆各推 N 家；同时写一份带完整链接的 `今日投递清单.md`
- **按方向筛**：内置 21 个商科方向（管培、投行、风控合规、审计税务、咨询、市场品牌、人力资源、供应链……），选几个想投的就行，还能给某个方向固定每天的名额
- **投递面板**（Windows）：常驻屏幕顶边，鼠标碰一下就滑下来
  - 每家岗位一键打开申请页、复制简历路径、复制填表要用的字段
  - 每家岗位四种状态：**已投** / **稍后**（明天排最前）/ **屏蔽**（要选原因，当场从池子补一家）/ **撤销**
  - 统计今天投了几家、本周投了几家
  - 检测到在打游戏时整个藏起来
- **求职信**：按你的模板起草 md，写完一键转 PDF
- **链接体检**：`check_links.py` 找出已经 404 的岗位，以后不再推
- **手写提醒**：池子里没有、只有你知道的事（比如“东亚银行校招 11 月中才开”），到日子前在简报里提醒

## 它不做什么

- **不是全网岗位**。数据来自一个公开岗位库 + 17 家关注公司 + 你手动补录的，没覆盖到的公司要自己加（见「岗位数据从哪来」）。
- **不替你投**。它只负责告诉你今天投什么，并把材料准备好；网申表还是你自己填。
- **链接体检只认 404/410**。如果链接只指到校招首页、而不是具体岗位，它查不出来，所以录入时请直接填岗位详情页。

## 快速开始

需要 Python 3.9+。每日简报只用标准库，面板只能在 Windows 上用。

```bash
git clone https://github.com/cuizhuo2001/job-digest.git
cd job-digest
copy config.example.json config.json
copy profile.example.json profile.json
python tools/push.py --dry    # 第一次会下载岗位数据（约 1 分钟），然后预览今天的简报（只打印，不推送）
```

打开 `config.json`，把 `directions` 改成你想投的方向，`allowed_cities` 改成你想去的城市，再跑一次看看。只想离线试试筛选效果，可以把 `pool_path` 换成 `examples/pool_cn.example.csv`（虚构示例，故意放了几条会被规则剔除的岗位）。

## 岗位数据从哪来

三个来源，在 `config.json` 的 `pool_path`（大陆）/ `pool_path_hk`（香港）里按顺序列出，同一个岗位出现在多个来源时留前面那条：

| 来源 | 文件 | 怎么更新 |
|---|---|---|
| 🇨🇳 **大陆岗位库** | `data/cn_import.csv` | 每次推送前自动从开源项目 [job-autopilot](https://github.com/keii-2596/job-autopilot)（MIT）公开的 2027 届秋招岗位快照导入，5000+ 条，自动打方向标签 |
| 🇭🇰 **关注公司自动查岗** | `data/hk_watch.csv` | 每次推送前去 `watchlist_hk.json` 里 17 家公司的招聘系统搜 graduate / trainee / 2027 岗，只留工作地在香港的，全是岗位直链 |
| 🇭🇰 **自带校招名单** | `examples/pool_hk_2027.csv` | 人工核实的 30 条（见下一节），不自动更新 |
| ✍️ **手动补录** | `data/manual_hk.csv`、`data/manual_cn.csv` | `python tools/add_job.py` |

两个自动来源默认最多 20 小时刷一次（`sources.refresh_hours`），手动强刷：`python tools/refresh.py --force`。刷新失败时用上次的数据照常推送，简报底部会提示。

**大陆岗位库的局限**：上游维护者手动同步，不是每天更新（简报会显示数据日期，超过 5 天会提醒）；一条记录常是一家公司的整份校招公告，链接多是公司校招首页，投之前要在官网里找具体岗位。

**关注公司**：支持三种招聘系统 —— Workday（`*.myworkdayjobs.com`）、Oracle（`*.oraclecloud.com`）、北森（`*.zhiye.com`）。默认 17 家：德银、大华、三菱日联、花旗、摩根士丹利、贝莱德、道富、景顺、宏利、友邦、永明、万事达、耐克、摩根大通、施罗德、中金、中信建投国际。想加公司，打开它的招聘官网看网址属于哪种，照格式往 `watchlist_hk.json` 里加一条，然后：

```bash
python tools/watch_hk.py --only 公司名 --dry    # 只查这一家，看能不能查到
```

关注公司**第一次**挂出来的岗位，简报里会单独列在「🆕 关注公司新挂出 N 个岗位」。

**手动补录**（官网、群里、就业网看到的岗位）：

```bash
python tools/add_job.py                         # 交互式，一项项问
python tools/add_job.py --hk --company "示例银行" --title "Graduate Programme 2027" --url https://... --deadline 2026-11-30
python tools/add_job.py --cn --company "示例证券" --title "风控管培生" --city 深圳
python tools/add_job.py --list                  # 看加过的；--remove <id> 删掉
```

手动补录的岗位是你自己挑的，不受方向、城市、排除词这些规则限制，默认匹配分 90（排在滚动招聘前面）。

## 自带的香港 2027 校招名单

`examples/pool_hk_2027.csv`：**30 个**香港 2027 年入职的 graduate programme / MT / 校招岗，覆盖中资券商、外资行、四大和中型会计师行、咨询、金管局。每一条都在 **2026-10-11** 打开官网核实过，`apply_url` 尽量直达具体岗位页；只能邮件投递或者没有单独岗位页的，在 `reasons` 列写了投递方式或点击路径。

- **会过期**：截止日过了会被自动剔除；下一年的项目要自己更新
- **不是完整名单**：目前偏金融、风控、审计、咨询方向，市场、人力、供应链这些方向的公司还没收录；没收录不代表没在招
- 每条都打了方向标签（`tags` 列），按方向筛是准的
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
| `tags` |  | 方向标签，多个用 `;` 分隔，写方向名（如 `风控合规;审计税务`）。写了就按标签直接归类，比关键词猜得准 |
| `fin` |  | 旧字段，`True` 等于打了「金融」标签，新池子不用填 |
| `type` |  | 校招 / 实习 / 社招 / 代理人招募……香港池按 `exclude_type_hk` 剔除 |
| `industry` |  | 行业，按 `exclude_industry` 剔除 |

两个池子的表头一样。不想用香港池，就把 `pool_path_hk` 留空。

> **找具体岗位链接的小技巧**：很多校招系统（北森 zhiye、hotjob、Moka）点岗位是在新窗口打开的，从地址栏复制就行。Workday 系的（`*.myworkdayjobs.com`）岗位页地址里带 `/job/`。实在只有邮箱投递的岗位，就把邮箱和要求写进 `reasons` 列备注。

## 选方向

在 `config.json` 的 `directions` 里写你想投的方向，岗位**命中任何一个**就会推：

```json
"directions": ["管培", "风控合规", "审计税务", "咨询", "数据分析"]
```

可选的 21 个方向（关键词见 `presets/商科.json`）：

| 金融 | 会计与咨询 | 职能与业务 |
|---|---|---|
| 管培、投行、销售交易、研究、资管财富、风控合规、银行业务、保险精算、金融科技 | 审计税务、财务会计、咨询、战略投资 | 市场品牌、销售商务、运营、产品、数据分析、人力资源、供应链、行政综合 |

怎么判断一个岗位属于哪个方向：池子 `tags` 列写了方向名就直接算；没写的话，看标题、行业、公司名里有没有这个方向的关键词。英文不分大小写。

进阶写法：

```json
"directions": [
  "风控合规",
  {"name": "咨询", "quota_cn": 2},
  {"name": "四大", "company": ["德勤", "普华永道", "安永", "毕马威"], "quota_hk": 1}
]
```

- `quota_cn` / `quota_hk`：每天**固定**给这个方向几家（不多不少），剩下的名额给其他方向按截止日排
- 写对象可以覆盖预设（同名）或者自定义新方向（新名字），支持 `title` / `industry` / `company` / `tags` / `exclude_title` / `must_company` / `must_title`
- `ignore_global_exclude: true`：这个方向不受 `exclude_title` 约束
- 删掉 `directions` 这一项 = 不按方向筛，池子里的岗位全推

> ⚠️ 大陆数据很多是「一家公司的整份校招公告」，标题里列着「技术类 运营类 销售类 职能类」一长串，所以大陆池按方向只能粗筛：公告里提到这个方向就会命中。香港池自带名单已经逐条打好标签，按方向筛是准的。

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
presets/商科.json     21 个商科方向的关键词
watchlist_hk.json     香港关注公司清单
examples/             示例岗位池 + 香港 2027 校招名单
tools/
  pool.py             读池子 → 过滤 → 排序（数据层）
  push.py             每日简报（推送前先调 refresh.py）
  refresh.py          刷新数据源
  import_cn.py        大陆岗位库导入
  watch_hk.py         香港关注公司查岗
  add_job.py          手动补录
  apply_popup.py/.html  投递面板（Windows）
  check_links.py      死链体检
  register_task.py    注册 Windows 任务计划
  jdlib.py            公共：路径、状态、个人资料、推送
  gaming_guard.py     检测游戏进程
  cl/                 求职信 md → docx → pdf
SKILL.md              给 Claude Code 当 skill 用的说明
```

**不会进 git 的**（见 `.gitignore`）：`data/`（自动导入、查岗、手动补录的岗位）、`config.json`、`profile.json`、`secrets.json`、`state.json`（已投、稍后、屏蔽、死链的记录）、`history.jsonl`（每次操作的日志）、`push.log`。

## 和 Claude Code 一起用

把仓库目录链接到 `~/.claude/skills/job-digest`，就可以直接说“今天投什么”“投完了记一下”“岗位池还剩多少”，Claude 会按 `SKILL.md` 操作。

## 已知局限

- 面板和定时任务只支持 Windows；每日简报本身跨平台，在 macOS / Linux 上可以用 cron 跑 `python tools/push.py`
- 大陆岗位库更新频率取决于上游；香港自动查岗只覆盖 `watchlist_hk.json` 里的公司，而且只认 Workday / Oracle / 北森三种招聘系统（hotjob、Moka、SuccessFactors 等还没适配）
- 「生成 PDF」要装 Microsoft Word（通过 COM 转换）

## License

[MIT](LICENSE)
