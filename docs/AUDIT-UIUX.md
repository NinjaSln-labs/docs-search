# docs-search 文档站 UI/UX/UE 审计报告

**对象**：`docs/index.html`（GitHub Pages 文档站，含 landing + markdown viewer）
**方法**：Nielsen 10 启发式 + Krug 三定律/Trunk Test + WCAG 2.1 AA 要点（自动化 axe-core 不可用，纯静态 + 手动分析 + 对比度计算）
**日期**：2026-09-10

## 评分：5/10

| 分 | 依据 |
|---|---|
| -2 | 浏览器后退契约破坏（Major 3）——viewer 不写 history，后退离开站点 |
| -2 | 无 JS 降级缺失（Major 3）——全部文档导航依赖 `onclick`，href="#" 无真实目标 |
| -1 | 无站内搜索（Minor 2）——Trunk Test「Where's search?」答不上来 |
| -1 | 无加载态/焦点管理/标题不更新等 a11y 项（Minor 2 多项） |
| -1 | 对比度 hover 态 FAIL、表格窄屏溢出、错误信息不完整等（Minor 2 / Cosmetic 1） |

核心任务（浏览 7 份文档）**不阻断**；扣分集中在导航契约、降级、可访问性细节。

---

## 发现清单（按严重度）

### 🔴 Major（3）——优先修复

**M1. 浏览器后退/前进契约破坏**（Nielsen 3「User Control and Freedom」+ Krug Trunk Test）
- `loadDoc()` 不写 `history`/不响应 `popstate`；`#doc=` 深链接只在**初始加载**读取一次。
- 用户打开文档后按浏览器后退 → **离开站点**（或去上一外部页），不是回 landing。
- 修复：`loadDoc` 用 `history.pushState({doc:name})` + `popstate` 监听恢复；viewer 内前进/后退与浏览器同步。

**M2. 无 JS 环境文档完全不可达**（Krug「Don't Make Me Think」的降级要求 + 静态站本质）
- 全部「阅读 X →」与「返回首页」是 `<a href="#" onclick="...">`；JS 禁用/加载失败时点击无任何反应，站点只剩 landing 骨架。
- 技术文档站的用户常用 curl/无 JS 工具抓取，且这是 GitHub Pages 纯静态站——本可零 JS 可用。
- 修复：`href` 指向真实 `.md` 路径（如 `href="HUMAN-GUIDE.md"`），`onclick` 保留并 `return false`；无 JS 时点击直接打开原文。返回按钮同理（或 `href="index.html"`）。

### 🟠 Minor（2）——排期修复

**m3. 无加载/进行中状态**（Nielsen 1「Visibility of System Status」）
- `fetch` 无 loading 指示、无超时、无骨架屏；慢网下点击后页面看似无反应。
- 修复：加载中在 `md-body` 显示「加载中…」或 skeleton；`AbortController` 超时。

**m4. 无站内搜索**（Trunk Test「Where's search?」）
- 7 份文档、无搜索框；找特定 API 端点/错误码/参数只能逐个打开文档。
- 修复：加简单客户端搜索（如页面标题/文档内 `indexOf` 过滤列表），或至少提供 `#doc=` 深链接帮助。

**m5. 文档标题不更新**（Trunk Test「What page am I on?」+ 可识别性）
- `document.title` 恒为「docs-search 文档」；viewer 打开后标签页/书签无法识别当前文档，深链接分享无标题上下文。
- 修复：`loadDoc` 时 `document.title = 'docs-search · <文档名>'`。

**m6. 焦点管理缺失**（WCAG 2.4.7 Focus Visible + 2.4.3 Focus Order）
- 无 `:focus-visible` 自定义样式（卡片链接/按钮 hover 才变色，键盘 Tab 焦点仅靠浏览器默认 outline，视觉上不明显、与 hover 态不一致）。
- viewer 打开后焦点不移动（停留在被点击的卡片链接上，键盘用户需多次 Tab 才到内容）。
- 修复：统一 `:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }`；viewer 显示后 `focus()` 到「返回首页」按钮或内容区（加 `tabindex="-1"`）。

**m7. 表格窄屏溢出**（响应式）
- 表格 `width:100%` 但无 `overflow-x` 容器；API.md（48 行表格）/INSTALL.md 在 <400px 视口会横向溢出页面。
- 修复：`#viewer table` 外包 `overflow-x:auto`（或 `.md-body table { display:block; overflow-x:auto }`）。

**m8. 无 `<main>` landmark**（WCAG 1.3.1 Info and Relationships）
- `body > div.container` 无 `main`/`article`；辅助技术无法直达内容区。
- 修复：`<div class="container" id="main">` → `<main class="container">`，viewer 内加 `role="region" aria-label="文档内容"`（或 `<article>`）。

**m9. 错误信息不完整**（Nielsen 9「Recognize, Diagnose, Recover」）
- 加载失败统一显示「加载失败，可直接访问 <a>」，不区分 404/网络错误、无重试按钮。
- 修复：catch 里区分 `r.status`（404 →「文档不存在」、其他 →「加载失败」）+「重试」按钮。

**m10. 链接文字与卡片标题不一致**（Nielsen 4「Consistency and Standards」）
- 卡片标题「人类使用手册」但链接文字「阅读 HUMAN-GUIDE →」；6 张卡片混用文件名/中文名。
- 修复：统一为「阅读人类使用手册 →」「阅读一键安装器 →」…（文件名保留在原文链接/JSON-LD）。

**m11. hover 态按钮对比度不足**（WCAG 1.4.3 Contrast）
- `.btn.primary:hover` `#fff/#2ea043` = **3.37:1，FAIL AA**（常态 4.63:1 达标）。
- 修复：hover 用更深的绿（如 `#2c8a3e`→实测 ≥4.5）或 hover 时文字保持 `#fff` 但背景加深。

### 🟡 Cosmetic（1）——顺手修

**c12. 无 favicon**：标签页/书签显示默认地球图标。加 `<link rel="icon">`（可内联 SVG/emoji data URI）。
**c13. badges 依赖第三方 shields.io**：断网/被墙时显示破碎图（有 alt 兜底）。可接受，或内联静态徽章。
**c14. 移动端按钮高度 ~42px**：低于 Apple 44px 触控建议（WCAG 2.5.8 的 24px 达标）。`padding: 11px 22px` 即可。
**c15. viewer 无「你在这里」指示**：顶部只有返回/原文按钮；可加当前文档名面包屑（与 m5 一起）。
**c16. 链接 href 无协议白名单**：渲染器 `[x](javascript:...)` 会生成可执行链接——内容源是仓库自身 .md（信任源）风险低，但可加 `javascript:` 过滤防未来上传内容。

---

## 做得好的（保持）

- ✅ **对比度整体优秀**：正文 16.02:1、muted 6.15:1、accent 7.49:1（GitHub 深色系配色达标）
- ✅ **单一主 CTA + 明确 hero**：pip install 主按钮、GitHub 次按钮；名称/tagline/徽章齐全
- ✅ **信息架构清晰**：6 卡片 = 6 文档，扫描即懂；agent-note 把机器索引入口放在显眼位置
- ✅ **移动端响应式基础**：grid `auto-fit minmax(270px,1fr)` 单列降级正常
- ✅ **XSS 防护正确**：`escHtml` 先转义后加标签
- ✅ **零外部依赖**（除 badges）：内联 CSS/JS，快

---

## 达到 10/10 的修复路径

1. **M1+M2**（后退契约 + 无 JS 降级）——`href` 真实化 + `history` API，约 15 行 JS + 6 处 HTML
2. **m5+m6**（标题 + 焦点）——`document.title` 一行 + `:focus-visible` CSS 4 行
3. **m7+m8**（表格溢出 + main landmark）——CSS 2 处 + HTML 标签改 2 处
4. **m9+m3**（错误 + 加载态）——catch 细化 + loading 文案，约 10 行
5. **m4**（搜索）——标题/关键字过滤列表，约 20 行（可选加分项）
6. **m10+m11+c12-c16**——文案统一、hover 色值、favicon 等小改

修复工作量约 **0.5-1 小时**；不改动任何功能逻辑（纯前端交互/样式/语义）。

---
*报告生成：静态分析 + WCAG 对比度计算；自动化 axe-core 扫描未执行（环境无 node 依赖，见 docs-search 仓库零依赖约束——建议后续在 CI 或本地临时环境补一次 axe 扫描验证 m6/m8 等结构项）*
