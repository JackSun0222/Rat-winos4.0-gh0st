# Potato 积分机器人

一个面向 Potato 群的轻量积分机器人示例，提供后台控制面板接口、管理员 @ 机器人命令，以及积分兑换入口。服务使用 FastAPI + SQLite，便于快速部署。

## 特性
- **后台控制面板**：通过 `/admin/users/{user_id}/adjust` 接口直接增加/减少用户积分，并记录原因与操作者。
- **管理员 @ 机器人**：模拟 Potato 群里 @ 机器人发命令的方式，接口 `/bot/command` 解析 `@bot add|deduct|redeem` 等指令，只有管理员可执行。
- **积分兑换**：`/redeem` 接口支持按用途扣减积分，拒绝余额不足的请求，并在账本中留下用途与审批人。
- **账本与查询**：`/users/{id}` 查询用户余额及最近 50 条记录，`/ledger` 查看全局账本。
- **健康检查**：`/health` 返回服务状态，可用于探针。

## 快速开始
1. 准备依赖（建议虚拟环境）：
   ```bash
   pip install -r requirements.txt
   ```
2. 启动开发服务：
   ```bash
   uvicorn app:app --host 0.0.0.0 --port 8000 --reload
   ```
3. 示例调用：
   - 控制面板加积分：
     ```bash
     curl -X POST http://localhost:8000/admin/users/user123/adjust \
       -H "Content-Type: application/json" \
       -d '{"delta": 20, "reason": "签到奖励", "actor": "panel-admin"}'
     ```
   - 管理员 @ 机器人：
     ```bash
     curl -X POST http://localhost:8000/bot/command \
       -H "Content-Type: application/json" \
       -d '{"sender": "group-admin", "text": "@bot add user123 5 活跃奖励", "is_admin": true}'
     ```
   - 积分兑换：
     ```bash
     curl -X POST http://localhost:8000/redeem \
       -H "Content-Type: application/json" \
       -d '{"user_id": "user123", "amount": 10, "purpose": "礼品兑换", "actor": "shopkeeper"}'
     ```

数据库会自动创建在 `potato_points.db`，默认保存最近 50 条用户账单记录供查询。
