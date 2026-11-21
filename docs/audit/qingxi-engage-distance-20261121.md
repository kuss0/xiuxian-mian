# 青溪原生接敌距离拒绝

2026-11-21，生产首轮主线恢复后发现，候选从 87edc120 建立。此文不证明主线或每日副本已通关。

WA 本轮 job `quest-4d64dbf7e35042419d5fe692977209d5`，09:19:56 启动，09:22:13 开始 qx_q11。7 次 field-defeat 均有 ok=true 结算，拾取进度为 arrows=2；没有再次出现 HTTP 200 正文被原生期限截断的问题。

09:26:54 原生野外控制器在御空途中为 `longwind:025` 发出 `field-engage-mwpq9hnk-m-jsm4jn`。服务器返回 HTTP 409、application/json、`{"error":"quest_too_far","ok":false}`，没有 engagement 或奖励。本地策略把这个确定拒绝当未知写入，留下一个 pending，导致最终清理等待超时并 held。原客户端 `dwelling-field-combat.js:555` 会在敌人接近时尝试 engage，拒绝时得到空 engagement；距离拒绝本身不表示开战成功。

修复仅接受上述动作、状态码、错误码及仅有 ok/error 两字段的完整正文，原样返回失败结果给客户端，记 rejected_noop、清该 operation 的 journal。同 operation 再次查询只返回缓冲的原拒绝，不重新发送。field-defeat、领奖、非 JSON、其它错误码/状态码、含额外字段的回包仍保持未决。

旧记录的放行单独校验：完整原始文件和正确身份/固定版本；唯一 pending 与磁盘 journal 相等；只有该条精确匹配的否定回执；其它写入及全部击杀均已确认，拒绝后没有其它业务写入。随后读新任务状态，要求目标仍存活且没有对应尸体，再要求服务端 flightLocked=false。符合才归档本条为已确认拒绝，通过原有逐字段 journal 清理入口交给下一 worker。没有使用通用 operator_release 扩大用户原来的一次性授权。

原始脱敏样本：`tests/fixtures/qingxi-engage-distance-20261121.json`。新鲜只读回包另存 `/root/xiuxian-tailnet-20261121.zSAVAf/post-distance-refusal/`：目标 hp=23056、generation=0、无该目标尸体，flightLocked=false。

定向回归 454 passed；Ruff、node --check、diff check 通过。将策略及宿主恢复为 HEAD 后，新浏览器策略测试和真实 held 放行测试分别失败（共 2 failed，20 passed）；恢复修复后通过。尚需生产完整回归、发布、API refresh 及下一局实际验证。

性能：真实主线战斗为 low/流畅，视距 200（客户端最小值），Mesa llvmpipe。5.165 秒观察到 8 个真实绘制帧，约 1.55 FPS。未改游戏时间或渲染时钟。该数据不能证明一重试炼能通关；双试炼 26 格验收和三日观察仍未开始。
