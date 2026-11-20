# 青溪 7+6 整批执行候选及下一步清单

时间：2026-11-20，Asia/Shanghai。此文描述隔离候选，不代表生产上线或真实通关。

## 本轮已执行

- [x] 修复原候选 `8ce73393` 的启动补偿漏项。结果文字已经是浏览器格式、但 operation_id 仍为 HTTP 值时，仍会修复；缺少 finished_at 不再使补偿跳过。相同局补偿不覆盖 previous_attempt，不重复归档或发送奖励通知。提交 `c7cf193b`。
- [x] 浏览器手动与自动单场路径固定第一重，拒绝高重、布尔值、浮点数和字符串参数；实际回执出现其它重数时保持 held。提交 `098597fa`。
- [x] 新增 `tools/qingxi_reward_batch.js`，顺序调用固定版本上游助手的每日副本和双试炼流程。复用原生导航、战斗、结算和真实 status，不构造战报或奖励。
- [x] 通过 `/api/qingxi-quests` 的显式 `action=rewards` 接入同一身份、共享洞府锁和持久化占用。现有 scheduler 不调用这个 action，因此新整批流程没有自动启用。
- [x] 新增 worker `--workflow rewards`；`--read-only` 只执行奖励状态检查。默认 workflow 仍为 quests。
- [x] 补充独立场地配额、跨日、未决局、未知发送、身份错误、重复批次、错误重数、伪胜利、通知去重和宿主释放条件回归。

## 已实现的约束

每日副本清单严格为：月殿血誓、血色禁地、南门守城、护民出关、血祭神坛、试傀场、剑冢。

麦田 longwind、竹林 bamboo 分别检查服务端 `used + remaining = limit = 3`。本地不把一个场地耗尽解释为两个场地全部完成。原生开局前重新读取对应资源状态；七个副本已领奖、未解锁，以及月殿已消耗失败奖励机会时不重复开局。试炼必须是第一重，不允许把残留 activeRun 当作开新局许可。

批次开始、每日副本结束及试炼结束后，都读取七个副本和两个试炼的原生状态。每日概览缺少 activeRun，候选会另外读取原生奖励接口，要求九项状态明确关闭，而非把字段缺失视为关闭。服务器日期变化时停止，批次日期同时约束 fetch 层的开局复核。

副本起始请求按资源限一次；试炼按场地至多三个起始请求。未知写入保留 localStorage journal 和宿主持久化占用。NPC/flight 的普通 refresh 不能单独释放奖励批次的 held；需要后续专门对账流程。

只有原生 verified/victory/rewarded、唯一 run_id 和对应配额减少才能记为本批试炼通关。次数消耗与实际胜利分开记录。缺少九项最终状态、worker 结果异常或飞行未确认关闭时，宿主仍保持 gameplay_uncertain。

奖励通知只汇总真实回执中的物品和修为；使用正常通知优先级。按 reward_id 去重，避免不同浏览器进程重复使用 fallback operation_id 时漏掉新局奖励或重放旧局。

## 验证证据

隔离 worktree：`/root/xiuxian-changfeng-browser-state-sync-20261120`。

执行：

```bash
/opt/xiuxian-main/.venv/bin/python -m pytest -q \
  tests/test_qingxi*.py tests/test_changfeng_trial_launcher.py \
  tests/test_trial*.py tests/test_fanren*.py
```

结果：**1247 passed in 31.04s**。Ruff、Python py_compile、`node --check tools/qingxi_reward_batch.js`、`git diff --check` 通过。pytest 由现有 conftest 定向至临时目录，未启用 `XIUXIAN_ALLOW_LIVE_TEST_DB`。

测试包含真实策略代码在 Node 中的隔离回放，以及宿主进程、持久化、原生接口夹具。它们不证明真实 Chromium 下七副本全部成功，也不证明真号已经取得 13 份奖励。

20:52 只读现场核对：生产 `/opt/xiuxian-main` 仍为 `eeefeea3`，服务 PID 1309046 active；watchdog ok。20:30 以来 journal warning 扫描为空。唯一生产未提交文件仍为 `data/quiz/quiz_bank.json`。本轮没有重启服务、修改生产状态库、启动真号或发送群消息。

吧唧 301299112、WA 8659059191 仍为 held/gameplay_uncertain；防御预检继续提示两个旧局待核对及长风今日缺记录。这不是已解决事项。

## 下一步计划清单

1. [ ] 在真实 Chromium 的隔离环境复核九个原生奖励接口的返回结构、第一重开局时序和收尾；与进程组清理候选联合审查，验证重复取消、重启、超时后无残留进程。保留已有其它 worktree 的未提交改动。
2. [ ] 补奖励批次的专门只读对账：九项状态、身份、日期、未决 journal 和飞行状态一起判定。吧唧/WA 的旧局只有取得新鲜关闭证据才能释放；补占用阻断原因记录及去重通报。
3. [ ] 接入每日自动调度及可视进度：七副本槽位加两个独立三次配额，按服务端状态断点续跑；与旧长风每日单场调度互斥，避免两套自动入口竞争名额。保持试炼第一重。
4. [ ] 选择没有旧局占用的可用本人账号，先做受限单场真号验收，再分阶段验证七副本和双试炼；换账号检查解锁、装备、次数和失败差异。核对真实奖励及群通知投递，不以本地胜利替代。
5. [ ] 联合验收通过后发布并重启；观察完整调度和跨日周期，证明不重复开局、不超配额、无奖不练习、未知不解锁、真实奖励只通报一次。

完整目标仍是稳定的 7+6 自动化。本轮完成的是实现候选和隔离回归，不能把它记为生产稳定验收完成。
