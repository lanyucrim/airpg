# 灰幕网页端

这是 AI-TRPG 的玩家界面。页面只展示和提交意图，背包、关系、时间、线索与最终判定全部来自 Python 权威服务。

当前已完成地图层级、当前位置与可达地点、同处 NPC、场景叙述、背包操作、公开时钟/线索、日常机会和开发追踪的首个玩家界面切片。后端已经返回余额和当前位置交易报价，但网页尚未实现余额/报价展示、购买按钮、售罄/买不起结果面板和完整背包容量视图，因此不能把后端交易切片视为网页交易闭环。

## 前置条件

- Node.js `>=22.13.0`
- pnpm
- 已启动的 Python 服务（默认 `http://127.0.0.1:8000`）

## 启动

```powershell
pnpm install
pnpm dev
```

如需改变后端地址，复制 `.env.example` 为 `.env.local` 并修改 `NEXT_PUBLIC_API_BASE_URL`。

## 验证

```powershell
pnpm lint
pnpm build
pnpm test
```
