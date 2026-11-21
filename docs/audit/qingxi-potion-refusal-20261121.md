# 原生喝药拒绝不跳过防守

2026-11-21。用户分享包参考中提炼的窄修复；不是整包迁移。来源比较见 `qingxi-share-reference-review-20261121.md`，独立 bot 实验 `/root/xiuxian-potion-rejection-lab-20261121`，启动适配候选提交 `7308207ca9c26b5fedd9a2261a9cbdc582c006d5`。

## 缺陷与改动

原 m19 在血药或灵药阈值条件成立后，无论原生 `command` 成功与否都结束当前 tick。游戏可以因药力运化、原生锁或状态变化拒绝命令，这时退出会跳过该拍后面的闪避/格挡。真实 bot.tick 的离线反例已证明这种控制流问题，但不能证明它是今日任一战败的唯一原因。

仅在 `tools/changfeng_trial_browser.py` 的生成脚本适配层，要求原两条完整分支各匹配一次后替换为“原生命令严格返回 true 才结束本拍”，附加独立按药种的 wall-time 退避：拒绝500ms、接受2000ms。失败继续原有防守。锚点缺失/重复或 helper 冲突会拒绝生成，不重建整份 tick，也不回退到缺保护脚本。

不改变 HP/灵力归一单位、原70/45阈值、高伤修正、库存、冷却、动作条件、伤害、坐标、时钟、奖励或重试。携带回灵丹仍只补灵力。保留投射物适配、本人身份、原生加载/举盾预热、同 run 有奖入口及零重试。旧 m19 JS/PY 原文件及双哈希保持不变；有效脚本的这项变化由本适配提交和 helper 文件版本单独记录。

不包含跃斩、持续格挡、资源缓存、受击日志扩展或分享版百分比/最高重数策略；不恢复任何当日战败场地。当前两号自动开关仍关闭，0/26，三日观察未开始。

## 验证与待验边界

- 18 项定向测试通过，含从实际安装 m19 Python 函数生成完整 JS 后运行真实 bot.tick 的25个行为断言；撤掉本适配即恢复拒绝后跳过防守的失败。
- 覆盖 hp/mana独立退避、成功不重复、失败同拍闪避/格挡、carry-only灵药、陈旧快照、原生冷却/动作门槛、原成长单位，以及锚点漂移/冲突拒绝。
- 独立新页/运行中/停止后重注检查仅保留一个25ms计时器，原配置、identity guard和holdStart保留；原m19两哈希与生产pin一致。
- 主控制器在无实机浏览器窗口完成全量 **19243 passed, 1561 subtests passed in 577.68s**；全量Ruff、Python编译、Node语法、diff检查通过。四个候选文件与 `7308207` 字节一致。日志目录 `/root/xiuxian-qingxi-7plus6-release-20261121/potion-wrapper-validation/`。
- 一致性备份及源归档 `/root/xiuxian-qingxi-7plus6-release-20261121/potion-wrapper-2240/`，SQLite quick_check=ok；两主号任务无pending/gather/不确定占用，两自动开关false，WA两场地战败记录保留。适配helper SHA256=`64c05b9d18470bddb99b6637f8589604ee6812ef8757830405d9d07bd852d7ea`；待主服务重启与实态核验。

这些证明命令返回处理与围栏，不证明真实通关率改善。今日 WA 麦田/竹林已战败停跑，不为验证补发第二局；下个自然日依旧一重、有奖单场、零重试、服务端终局与配额/通知闭合。

## 22:41 发布核验

代码 `0cdba0e5` 已推 `xiuxian-mian/main`，与完整测试候选四文件字节一致。22:40:30 仅主服务重启，22:40:54 新MainPID=2345480、NRestarts=0；主服务、safety watchdog、health observer active，UI 200、24身份、queue=0。

两自动开关仍false、q21/q25选项未变、两主号无pending/gather/不确定占用；WA q19 rewarded/q20 active，吧唧q14 rewarded/q15 active，WA麦田与竹林两个confirmed_defeat均保留。旧m19两个原文件hash未变。没有启动任何新的试炼来证明本修复，接下来只继续主线并观察服务。
