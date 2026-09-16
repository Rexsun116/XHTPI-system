# V2 统一设计系统提案

基线：`a3e448b413f43a32cda7e4e3fa9c9e55537b4ddb`。本文件和静态原型是待确认方案，不是正式 UI 规范已落地的声明。

## 方向与不变量

沿用“订单控制中心 Dashboard V3.5”：浅灰蓝背景、浅色 Sidebar、青绿主色、白色细边卡片、紧凑的信息排版；不采用营销 hero、渐变大屏或另一套深蓝后台。技能检索中泛化的营销转化布局不适用于本系统，未采用。保留它的键盘、对比度和渐进展开建议。

订单阶段、任务状态、health 是不同维度。EXCEPTION 是健康/展示分组，不新增数据库 task status。色彩不决定权限、优先级、截止日或完成条件。正式实施复用已有 selector/presenter/owner 上下文，前端不 reconcile。

## Design tokens

|Token|值|用途|
|---|---|---|
|brand|`#14b8a6`|沿用 Dashboard 识别色、小面积强调|
|primary-text / primary-button|`#0f766e`|深青绿链接和白字按钮，提高小字对比；是品牌深色档，不改正式 Dashboard|
|ink / muted|`#172033` / `#526174`|正文 / 次级信息|
|page / surface / soft|`#f4f8fa` / `#ffffff` / `#f8fbfc`|页面 / 卡片 / 分区|
|border|`#e2e9ed`|卡片分隔；输入边框用 `#82929f` 以明确可操作边界|
|exception|`#c65b69` / `#fff5f6`|Dashboard 同源 marker / 背景；正文深色 `#963b4b`|
|action|`#b77728` / `#fff8ee`|marker / 背景；正文 `#875513`|
|waiting|`#3977ba` / `#f0f7ff`|marker / 背景；正文 `#285f99`|
|upcoming|`#7063ae` / `#f5f3ff`|marker / 背景；正文 `#5f519e`|
|done / success|`#368463` / `#effaf4`|marker / 背景；正文 `#256b4d`|
|warning / danger / info|action / exception / waiting|反馈语义；不能用 warning 代表普通主按钮|
|shadow|`0 3px 12px #0f172a08`|卡片轻微层次；dialog 另用 `0 20px 60px #17203330`|
|radius|8 / 12 / 16px|控件 / 内卡 / section|
|spacing|4 / 8 / 12 / 16 / 24 / 32 / 48px|模块内 12–16、模块间 24；不用随机 margin|
|font|`Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif`|原型离线使用本机 fallback，不下载字体；不是保证每台设备装有 Inter|
|type|12 辅助 / 14 正文 / 16 模块 / 28 页标题 / 26 关键金额|行高 1.5–1.65；窄屏输入至少 16px|
|number|`font-variant-numeric: tabular-nums`|金额、数量与日期对齐|
|layout|sidebar 245px；正文 max 1280px；桌面 padding 32px|延续 Dashboard 比例|
|breakpoints|1120 / 760px|中屏单列模块；窄屏 sidebar 抽屉和可横滚 section 导航|
|layers|sticky 10；scrim 30；drawer 40；dialog 浏览器 top layer|不让滚动导航遮住 focus|
|motion|150ms 颜色/透明度；滚动遵从 reduced-motion|不做数字滚动或任务自行跳动|

仅用色条表达语义不够，始终输出“待处理 / 等待 / 已完成”等文字。日期 `YYYY-MM-DD`；事件 `YYYY-MM-DD HH:mm`，标明 Asia/Shanghai；金额 `USD 24,800.00`，不跨币种求和；数量保留现有精度并显示单位。未知为“未确认 / —”，零为“0.00”。不改变 Decimal 或 document 格式政策。

## 组件契约

|组件|结构与交互|边界|
|---|---|---|
|Page header|面包屑/返回、PI、客户、副标题、stage/role、More|不在标题前堆表单；一个 h1|
|Toolbar|场景/筛选、次要按钮、状态|原型 Scenario Switcher 不进入正式页面|
|Summary card|只读 label/value、关键数字、单位|不把所有事实变成 input|
|Section card|标题、说明、summary、outstanding、编辑/历史、折叠|折叠不丢局部输入；编辑中说明保存边界|
|Status badge|浅色底+深色文字+圆点|状态有文字，不能只靠颜色|
|Order stage|有序只读阶段条、当前 aria-current=step|不能点击跳状态；出口流转旁注明原子发运后完成|
|Task card|标题、原因、事实、日期、唯一推荐动作、可展开历史|只呈现现有任务允许动作；RULE_DATA 无人工 Done|
|Data table|表头、数值右齐、适度行距、caption/空态|必要时仅表格内部横滚；不导致整页横滚|
|Form section|模块范围提示、label、必填标记、错误、保存/取消|不暗示全订单保存|
|Read-only fact|普通文本或定义列表|禁用 input 不作为默认只读呈现|
|Editable field|可见 label + helper + error关联|placeholder 只举例；保留用户输入|
|Empty state|说明为何空+已有合法下一步|不凭空提供创建或权限操作|
|Alert|语义文字、原因、可恢复路径|成功用 status；错误用 alert，不只 toast|
|Confirmation panel|订单身份、影响范围、前置检查、确认/取消|删除保留输入精确编号；完成受后端控制|
|More menu|低频入口、原生 details 或键盘菜单|危险操作与普通保存隔离；不展示无权限选项|
|Sticky action bar|仅编辑时：模块名、未保存、保存本模块、取消|不出现含糊的全局 Save|
|Section navigation|概览/运输/付款/运费/单据/任务/活动，锚点|scroll-margin；当前段可标记；窄屏独立横滚|
|Activity timeline|人类可读日期、行为、来源、备注|不渲染 JSON；完整审计保留原模型|
|Linked-order banner|自身角色、peer 编号、owner职责、跳转|缺 owner 明示错误，不能猜测|
|Completion checklist|客户付款/需付运费/交付要求、原因、更新路径|未通过无“强制完成”；不把所有任务变成门槛|

## 按钮层级

- **Primary**：深青绿，当前 Next Action；模块进入编辑后其“保存本模块”可为 Primary。静态摘要按钮均次级。
- **Secondary**：浅色实体，取消、关闭、切换，不代表业务成功。
- **Outline**：边框按钮，编辑模块、查看来源、下载演示。
- **Success**：只用于受控确认完成且具备条件的确认页；不能把绿色通用 Done 变成订单完成。
- **Warning**：Correction 等需要额外注意的编辑模式；原因和范围必须同时显示。
- **Danger**：独立删除确认的最终动作，必须带影响说明；不常驻普通保存区。
- **Link action**：历史、模块定位、返回；不能用灰色链接掩盖不可逆删除。

悬停、focus-visible、禁用均有不同反馈；控件尽量 44px 点击区域。长文按钮允许换行。所有非提交按钮显式 type=button。

## 表单与反馈

1. 必填写“* 必填”，可选写“可选”；不要把现有三态 required 变成二态 checkbox。
2. 模块首次编辑显示“仅保存本模块；其他模块未提交内容不会保存”。变更后显示未保存，取消丢弃该模块草稿。
3. 保存成功在模块内提示并保持位置；失败保留输入，顶部错误摘要链接到字段，首个错误有焦点。
4. 独立模块不可通过一个跨模块保存按钮混合提交；正式 API payload 仍按现有 whitelist/`_form_scope`。
5. NEW 编辑付款驱动事实变化时，沿用显式 Recalculate / Keep & Edit，本设计不推断手工金额、不加模式字段。
6. 完成订单默认只读；Correction 先选模块、输入原因，突出“修订中”，允许范围由后端会话决定，完成修订后显式关闭会话。关联共享日期不能单单修订；出口不编辑 PAYMENT/FREIGHT。
7. 原型保存只显示“演示完成，未写入数据”，丢弃草稿，不改任何已显示业务事实、金额、状态或历史。场景切换只是换一组固定样例；刷新恢复默认。

## 文案

以中文操作为主：编辑运输、取消编辑、保存本模块、查看历史、确认实际发运、确认货物已到港、申请修订。保留 PI、ETD/ETA、B/L、PRE_SHIPMENT、CUSTOMER_ORDER/EXPORT_ORDER。将字段名翻译为业务标签，代码可作为次级信息，不直接显示 `usd_invoice_issued`。

Waiting 文案必须包含等谁、下一次跟进、已有记录；不把 waiting 自动理解为逾期。Next Action 的原因必须源于已有事实，不展示虚构风险分数。

## 原型评审

文件：`../prototypes/v2_order_detail_ui_prototype.html`，可直接 file:// 打开。无外部字体、库、图片、网络 API 或存储；模拟编号均以 DEMO 开头。六场景：普通 PRE_SHIPMENT、普通 SHIPPED、ARRIVED 有未收款/未付运费、COMPLETED、Linked CUSTOMER_ORDER、Linked EXPORT_ORDER。

原型的交互：段落滚动、场景选择、卡片折叠、局部编辑/取消/演示保存、任务历史和 Done History、More、窄屏侧栏、sticky 导航/编辑条、reduced-motion。下载和受控发运/到港以“入口说明”对话框演示，不产生真实文件或提交；完成条件不足只定位未完成事实。COMPLETED 的 More 提供显式 Correction 演示，只编辑备注草稿，订单状态不变。无 NEW 场景所以不显示删除入口。

统一 Activity 区仅展示本原型模拟的任务事件；正式实现不得宣称系统已有完整字段级审计或跨模块事件总线。任何新增聚合数据能力应先独立确认。

## 验证记录

- 单文件静态检查：静态 ID 唯一、无远程资源/Jinja；内联 JavaScript 通过 `node --check`；`git diff --check` 通过。
- 本机 Chrome headless 直接 file:// 打开；1440×1000 和 390×844 首屏截图已目视检查。六场景在两种宽度均自动检查无整页横向溢出；全页所有场景未逐像素人工验收。
- 自动交互检查：模块折叠、编辑/取消/保存（不改摘要事实）、任务 Activity、Done History、More、窄屏侧栏、Correction 对话框、reduced-motion（scrollBehavior=auto）。无捕获的 JS runtime exception；外部资源请求为零。未做完整读屏审计、Safari/Firefox 或真机触控验收。
- 回归：`PYTHONDONTWRITEBYTECODE=1 venv/bin/python -B -m unittest discover -s tests -p 'test*.py' -q`，560 tests passed（55.243s），未增加/修改 Python 测试。
- 既有 ResourceWarning / LegacyAPIWarning 保留。本轮没有创建业务数据库、备份或 Secret Key；测试仍会调用既有单据输出路径，忽略的测试生成物不纳入提交、不冒险清理用户目录。浏览器 profile、截图和检查脚本仅在系统临时目录。禁用 Python 字节码写入。
- 正式页面改造、真实应用启动与真实订单 Browser UAT 不在本轮；本报告不将原型检查当作业务 UAT 通过。
