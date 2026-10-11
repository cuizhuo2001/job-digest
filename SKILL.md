---
name: job-digest
description: 每日投递简报 + 投递面板 —— 从岗位池排出「今天该投的 N 家」+ 截止雷达，按时推到 Telegram / 飞书。用户说「今天投什么」「投递简报」「岗位池还剩多少」「投完了记一下」「改一下每天推几家」「面板打不开」时使用；也用于排查为什么没推送。
---

# job-digest · 今日投递

把岗位池压成一条能扫的简报推到手机，电脑上的面板负责一家家投完。
**只读池子**：不投递、不改 CSV。完整说明见 `README.md`。

## 文件分工（改之前先看清改哪个）

| 文件 | 放什么 | 谁写 |
|---|---|---|
| `config.json` | 规则：池子路径、每天几家、排除词、推送渠道、定时 | 人 |
| `state.json` | 已投 `applied_ids`、稍后 `later`、屏蔽 `blocked`、屏蔽公司 `blocked_companies`、死链 `dead_ids`、求职信 `cover_letters` | 面板 / 脚本 |
| `profile.json` | 姓名邮箱、简历路径、速查卡、求职信模板 | 人 |
| `secrets.json` 或环境变量 `JD_*` | 推送密钥 | 人 |
| `history.jsonl` | 面板每次操作的流水（统计用） | 面板 |

⚠️ 已投、屏蔽这些记录在 `state.json`，**不在 config.json**。用户说「投了 XX」：在 `state.json` 的 `applied_ids` 加上它的 id（id 在 `今日投递清单.md` 每条的 `id=` 里）。面板开着时最好让用户直接在面板上点「已投」。

## 筛选顺序

已投 → 死链 → 屏蔽 → 岗位类型（香港池）→ 行业 / 公司排除 → 标题排除 → 城市 → 非金融（`require_fin`）→ 已过截止 → 重复（公司 + 去掉标点后的标题相同）。

排序：「稍后」过的排最前 → 3 天内截止 → 7 天内截止 → 滚动招聘（按 score）→ 更远的截止日。

## 常用命令

```bash
python tools/pool.py                  # 看筛选排序结果（调规则用）
python tools/push.py --dry            # 预览简报，不推
python tools/push.py                  # 真推
python tools/check_links.py           # 体检今天要推的链接；--all 查全部；--unmark <id> 撤销
python tools/register_task.py         # 注册 / 刷新任务计划；--show 看状态；--run 立刻推一次
pythonw tools/apply_popup.py          # 起面板；--nudge = 已经在跑就让它滑下来
```

## 排查

- **没推送**：先看 `push.log` 最后几行，再用 `python tools/register_task.py --show` 看任务状态。判断「今天到底跑没跑」看 `push.log` 的修改时间，别看任务计划的 Last Run Time（那只是「尝试过」）。
- **Telegram 报 URLError / 10060**：多半是网络不通，在 `secrets.json` 配 `telegram_proxy`。
- **飞书报 19021**：签名不对或本机时间偏差超过 1 小时；报 19024：关键词不匹配（关键词填「投递」）。
- **面板起不来**：同一时间只能开一个面板。先看有没有残留的 python / pythonw 进程在跑 `apply_popup.py`。
- **池子太旧**：脚本不联网，新岗位要用户自己补进 CSV；简报底部会显示快照日期。
