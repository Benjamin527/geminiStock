# Coinbase Dashboard Redesign

## Goal

把现有美股 AI 盯盘控制台从单文件内联页面，重构成符合 `DESIGN.md` 中 Coinbase design-md 约束的前端展示层，同时尽量不改动后端数据接口与业务逻辑。

## Design Direction

- 视觉气质采用 Coinbase 风格的 `trust-focused, institutional feel`
- 主画布以白色和浅灰分区为主，品牌蓝 `#0052ff` 仅用于主 CTA、关键状态和少量强调
- 数字信息采用更克制、更有秩序的展示方式，避免当前偏深色交易终端的噪音感
- 信息层级调整为“账户概览式摘要 > 主标的卡片 > 市场观察 > 活动时间线与报警”

## Scope

本次只重构 Dashboard 前端层：

- 把当前 `gemini_stock/web/app.py` 中的内联 HTML、CSS、JS 拆分
- 新增模板与静态资源目录
- 保留 `/api/status`、`/api/watchlist`、`/api/movement-alert-settings` 等现有接口
- 保留大部分服务端片段拼装逻辑，避免业务层联动改动

不在本次范围内：

- 不改动 AI 分析、规则引擎、数据库结构
- 不改动 Docker 服务拓扑
- 不新增复杂前端框架

## Page Architecture

### 1. Hero Summary

顶部改成更像机构级资产控制台的摘要区：

- 左侧为标题与说明
- 右侧为运行节奏、市场阶段、报警/错误、后台健康四个核心指标
- 使用浅色画布，蓝色只用于核心倒计时和主按钮

### 2. Primary Watchlist

主标的区域改成白底卡片栅格：

- 每张卡片优先展示 symbol、偏向、分数、置信度、建议动作
- 把“买入参考 / 止损位 / 卖出参考”整理成更简洁的三列交易计划条
- 细项指标放到次级信息区，不再使用重色块压满整卡

### 3. Market + Activity

- 大盘观察改成更轻的二级卡片
- 运行概览、最近 AI 时间线、最近报警、近期错误使用浅灰/白分区
- 表格改成低边框、强对齐、轻层级的 Coinbase 风格

### 4. Settings

- 设置区保留现有能力，但用更轻的配置抽屉和胶囊按钮表达
- 移动端保持可用，保留 tab 切换与抽屉逻辑

## Implementation Notes

- 使用服务端模板文件作为页面壳
- 使用独立 CSS 与 JS 文件承载样式和交互
- 服务端继续生成 HTML fragments，前端刷新逻辑继续复用 `/api/status`
- 通过测试更新验证核心文案、静态资源引用和关键区块输出

## Risks

- 现有页面测试对旧文案和类名有依赖，需要同步调整
- 单文件 `app.py` 仍然承担片段生成职责，本次先拆视图资源，不做大规模 presenter 重构

## Acceptance Criteria

- 页面不再内联大段 CSS/JS
- Dashboard 使用独立模板与静态资源
- 首页整体风格明显转为 Coinbase 风格：白底、蓝色强调、克制层级、机构感
- 现有交互能力保留：自动刷新、倒计时、watchlist 增删、阈值保存、移动端 tab
- 测试通过，Docker 镜像能重新构建并启动 Dashboard
