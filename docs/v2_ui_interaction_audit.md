# V2 全站 UI / 交互审计

审计基线：`a3e448b413f43a32cda7e4e3fa9c9e55537b4ddb`，分支 `feature/codex-rebuild`，原有 560 项测试。本文是源码审计和设计建议，不是正式业务页面的浏览器 UAT。未连接真实数据库。2026-09-16。

## 范围与方法

阅读 V2 base、Dashboard、详情、新建/编辑、关联出口创建、主数据、报价、确认、删除、Correction、登录、任务历史、文件需求、HS Code 及所有 `_*.html` partial；核对 `v2/web.py` 的路由、render context、写入范围和 redirect，`v2/app.py` 登录与入口，`v2/presenter.py` 的动作映射和历史格式，`v2/static/v2_batch1.js`。打印单据模板（`documents/invoice.html`、`contract.html`）仅检查下载上下文，不提出排版改造。CSS 主要内嵌于 base、Dashboard 和单据模板；V2 static 目前只有一个 JS 文件，没有独立统一 CSS。

角色说明：下面的“操作员”是登录使用者，不是新增 RBAC 权限；CUSTOMER_ORDER / EXPORT_ORDER 是订单业务角色。频率为设计估计，尚无实际使用统计。成熟度按功能覆盖和呈现一致性评估，不代表业务正确性评级。

## 页面清单

除登录外 URL 均以 `/v2` 开头；endpoint 默认 `v2.`。同一路由 GET 展示、POST 保存的页面在一行列出。

|页面|Route / endpoint|Template（v2/）|使用角色 / 频率|成熟度|主要问题 / 优先级|
|---|---|---|---|---|---|
|订单控制中心|`/` dashboard|dashboard.html|操作员 / 高|较成熟 V3.5|跨页样式断层；文字偏小 / P1|
|订单详情|`/orders/<id>` order_view|order_view.html|按订单阶段和角色 / 高|业务丰富，信息层级弱|长页面、多表单、历史混排 / P1|
|新建 PI|`/orders/new` order_new；`/check-pi-number` check_pi_number|order_form.html|操作员 / 高|结构化创建|长表单、异步校验反馈、错误定位 / P2|
|NEW 编辑|`/orders/<id>/edit` order_edit|order_edit.html|仅 NEW / 高|事务保存与付款选择明确|缺少变化摘要；与详情 facts 双入口 / P1|
|关联出口创建/编辑|`/orders/<id>/create-linked-export` create_linked_export；order_edit|create_linked_export_order.html|符合创建条件的客户单；NEW 出口编辑 / 中|角色限制清楚|大量原始字段名、缺少返回 / P2|
|实际发运确认|`/orders/<id>/enter-shipped` enter_shipped|enter_shipped.html|普通/客户订单 / 高|受控流程|就绪信息与错误帮助可改进 / P2|
|实际到港确认|`/orders/<id>/enter-arrived` enter_arrived|enter_arrived.html|SHIPPED 非出口 / 中|简单可用|标题文案、日期错误定位 / P2|
|完成确认|`/orders/<id>/enter-completed` enter_completed|enter_completed.html|ARRIVED 且通过检查 / 中|权威检查已存在|共享 partial 局部链接缺失 / P1|
|Correction|`/orders/<id>/corrections` correction_open；`/corrections/<id>/edit` correction_edit；`/close` correction_close|correction.html + 详情内表单|COMPLETED 按模块 / 低|规则存在，呈现分散|两套编辑界面、角色禁用项仍列出 / P1|
|客户|`/master/customers` master_list；`/<id>/edit` master_edit；`/<id>/toggle` master_toggle|master.html|操作员 / 中|基础 CRUD|无搜索/空态，原始字段名 / P2|
|出口商|`/master/exporters` 同上|master.html|操作员 / 中|基础 CRUD|同上 / P2|
|工厂|`/master/factories` 同上|master.html|操作员 / 中|基础 CRUD|同上 / P2|
|产品|`/master/products` 同上|master.html|操作员 / 高|基础 CRUD|长列表，品牌型号层级不足 / P2|
|货代|`/master/forwarders` 同上|master.html|操作员 / 中|基础 CRUD|启停按钮用当前状态而非动作 / P2|
|银行账户|`/master/banks` 同上|master.html|操作员 / 低|基础 CRUD|账户信息密集，缺少输入帮助 / P2|
|货代报价|`/quotes` quotes；`/quotes/<id>/edit` quote_edit|quotes.html|操作员 / 中|基础 CRUD|placeholder 代替 label，表格无表头 / P1|
|普通删除|`/orders/<id>/delete` order_delete|order_delete.html|独立 NEW / 低|安全确认完整|可沿用确认范式 / P3|
|关联删除|`/orders/<id>/delete-linked-trade` linked_trade_delete|linked_trade_delete.html|完整 NEW/NEW pair / 低|原子操作、双编号确认|应保留，不做单边删除入口 / P3|
|任务历史|`/tasks/<id>/history` task_history|task_history.html|操作员 / 中|人类可读事件|与详情的历史层次需协调 / P2|
|文件需求|`/orders/<id>/document-requirements` document_requirements_editor|document_requirements_editor.html|按现有生命周期校验 / 中|三态字段|原始字段名、无返回链接 / P2|
|HS Code|`/orders/<id>/booking-hs-codes` booking_hs_codes|booking_hs_codes.html|按服务权限 / 低|快照边界说明明确|进入路径不稳定 / P1|
|单据输出|`/orders/<id>/documents/<kind>` document|documents/invoice.html、contract.html；Booking Word|操作员 / 中|现有下载能力|详情入口散落，缺失字段报 HTTP 错误 / P2|
|登录|`/login` login（非 v2 前缀）|login.html|未登录用户 / 高频入口|最小可用|固定 360px、无 viewport/label / P1|

附属写入端点不应伪装成可直接导航的页面：`order_facts`、`advance_receipt`、`update_eta`、`freight_agreement_manual`、`batches/batch_edit/batch_delete`、`task_action`、`enter_pre_shipment`。详情上下文已提供 `financial_owner_resolution`、`shipment_owner`、`completion`、`can_delete_linked_trade`、`can_create_linked_export`、task presentation；未来 UI 应复用这些判断，不在浏览器重造业务规则。

## 全局一致性与问题登记

P1：高频操作误解/入口失效/窄屏阻塞；P2：效率和可理解性；P3：完善。以下复现是按源码给出的验证步骤，未宣称正式页面已做浏览器验证。

|ID / 类型 / 程度|证据、页面与复现|影响|建议 / 可否仅 UI 修复|
|---|---|---|---|
|G01 UI P1|base `.side` 230px 深蓝，Dashboard `body:has(.v35-dashboard)` 覆盖为 245px 浅色。控制中心进入详情对比|视觉切换、内容横向跳动|将已确认 Dashboard tokens 提升为统一 layout；可以，另批次实施|
|G02 INTERACTION P1|base 固定 sidebar + main margin，无全局窄屏开关；窄屏打开主数据/详情|可用内容宽度不足|抽屉导航、遮罩、Esc、焦点返回；可以|
|G03 UI P2|页面多 h2/h5，几乎不覆盖 title block；base main 无统一 max-width|浏览器页签难区分，超宽信息难扫读|页名+PI号、统一 header 与宽度；可以|
|G04 BUG P1|详情多个 facts 表单；`v2_batch1.js` 用 `button[type=submit]`，Shipment Preparation 模板按钮为无 type 的 `<button>`。查询结果为 null|独立保存说明及脚本 HS 入口未插入；容易误以为整页保存|**BUG（确定的选择器不匹配）**，修正选择器或模板属性，提示直接进入模板；可以。出口 shared partial 另有入口，不等于该入口全站消失|
|G05 INTERACTION P1|`order_facts` 多数返回详情无 fragment/flash；保存页面中段表单|回到页首、丢失位置，无法确认保存了哪个模块|模块命名、局部反馈、定位恢复；可以但需单独 route 展示行为评审|
|G06 UI P2|bootstrap primary 蓝色与 Dashboard teal 混用；多个独立表单均 btn-primary|多个视觉主操作竞争|页级一个当前行动；编辑中的模块主按钮；可以|
|G07 INTERACTION P2|master/quotes 表单与列表同页，没有统一空态、搜索、筛选/分页|大列表难查、空表无说明|先做文案与空态；搜索/分页属 NEW REQUIREMENT，另行确认|
|G08 UI P1|quotes input 只有 placeholder；多数模板 label 没有 for/id；login 没 label、viewport、lang|输入后失去字段说明，键盘/读屏体验弱|可见 label、关联 id、错误描述、viewport；可以|
|G09 INTERACTION P2|一般 POST 无提交中状态/防重复点击；create PI 编号异步检查未处理请求返回乱序|用户无法判断处理中；编号可用提示可能滞后（服务端仍兜底）|呈现加载/错误、最新请求校验；不能据此认定重复订单可被保存；需专项验证|
|G10 UI P2|presenter.money 千分位两位；详情多处直接 Decimal、datetime；`value or ''` 隐藏零|空值和 0 混淆、数字难比较|统一只读格式；保留真实币种/单位和三态；可以，不改舍入规则|
|G11 UI P2|普通动作英文、业务提醒中文、字段 snake_case 混杂|增加理解成本|中文动词，保留 PI/ETD/ETA/角色代码；可以|
|G12 INTERACTION P2|base flash 没有统一 live region；abort(400/409) 常离开表单；NEW editor 已保留 data/error|错误反馈不一致、可能需重新录入|统一错误摘要+字段提示；涉及错误重绘需 route 专项确认，不改变验证规则|
|G13 UI P2|Dashboard 状态色完整；普通详情 badge 只有蓝色，任务标题直接输出 display_status|阶段与任务状态混淆|阶段中性色，任务状态带文字和语义色；可以|
|G14 INTERACTION P2|master_toggle 显示 Active/Inactive 但点击反转|状态和动作含义不清|状态 badge 与“停用/启用”分开；可以，保留历史快照|
|G15 UI P2|base Bootstrap CDN，Dashboard FontAwesome CDN，Inter 仅声明无加载|离线样式/图标不保证；非后端缺陷|后续评估本地资源打包；原型无外部依赖|
|G16 BUG P1|`_completion_check.html` 用 `#payment-receipts`、`#freight-settlement`、`#tasks-activity`，在 enter_completed 独立页这些节点不存在|未就绪时“更新”链接不定位|根据页面上下文使用完整详情 URL + anchor；可以，未修复|
|G17 BUG P1|详情 COMPLETED Correction 下拉列出 PAYMENT/FREIGHT；`correction_open` 明确拒绝出口单这两项|出口单可选择必然被拒绝的操作|按现有 owner 判断隐藏/说明；可以，不放宽后端|
|G18 NEW REQUIREMENT P2|全局未保存提醒/草稿恢复目前不存在|切换页面会丢未提交值|前端 dirty 提醒可独立提案；不引入自动保存或持久草稿，本轮仅演示|
|G19 BUSINESS RULE 待确认|何时将跨模块未完成事实聚合为一个“风险”评分，后台没有该评分能力|可能误导优先级|原型只用静态事实摘要，不提出新评分、截止日或提醒规则|

## 订单详情页：信息架构与业务边界

当前顺序存在先于标题的内容：NEW 编辑入口、部分装运准备/运费内容及通过 DOM 移动的 Booking cargo；随后标题/角色信息、共享运输、阶段片段、items、facts、运输/付款/运费、完成检查、全部任务、下载、Correction。没有页面内 section 导航，浏览长订单需要反复滚动。

- **阶段与行动**：阶段是小 badge，Next Action 分散在任务和底部。近期 checkpoint 已修复 PRE_SHIPMENT/SHIPPED 的无效 `/status` 表单；本轮不能再把它列为未修复 BUG。应把受控入口提升到当前行动区，而非可点击跳阶段条。
- **只读/编辑**：PRE_SHIPMENT 多数事实默认呈输入框；用户难识别已确认事实。Overview 应使用只读值，编辑范围由模块明确说明。独立保存不能暗示其他模块也被提交。
- **多按钮**：Booking cargo、Shipment Preparation、Shipping Schedule、Freight Agreement 是不同保存范围；不能仅把它们合为一个 Save，尤其 `order_facts` 不同 field whitelist 与 `_form_scope` 有差异。
- **NEW**：整单编辑和详情 NEW facts 并存。全量编辑会影响 snapshots/items/payment plan，并有显式付款计划选择，不可简化为静默金额刷新。链接出口后的商品结构限制必须保持。
- **PRE_SHIPMENT**：Planned Shipment 只读；ETD/ETA 与装运准备按既有字段策略维护；实际发运必须走 enter_shipped。关联客户单发运涉及出口文件前置条件，成功后客户 SHIPPED、出口 COMPLETED，不能画成两单都 SHIPPED。
- **SHIPPED**：可更新 ETA，Actual Departure 不提供普通编辑/清空；实际到港走 enter_arrived。付款和运费采用不同保存模块。
- **ARRIVED**：优先展示权威 completion_check 的客户付款、必需运费及交付单据；未满足不得提供“强制完成”。一般操作性提醒不是新增完成门槛。
- **COMPLETED**：默认只读快照，无普通任务 Done/Reopen；显式 Correction 有模块、原因、会话关闭。详情内简版和完整 correction 页同时存在，需统一入口而不改授权边界。
- **Linked EXPORT_ORDER**：共享运输来自 shipment_owner；客户收款和运费来自 financial_owner，仅跳转所有者，不编辑；出口单有自己的商业/出口单据任务。关联不是复制一套客户流程。异常组应显示配置错误，不能猜 owner。
- **Linked CUSTOMER_ORDER**：保留自身客户付款、运费及共享运输编辑；显示出口单编号和作用。关联入口可清晰标注“查看出口执行单/返回客户订单”。
- **Tasks & Activity**：route 按 pi_id 读全部任务，详情默认逐条渲染，包括 DONE/CANCELLED；单条 activity 已折叠且经过 presenter 翻译，并非原始 JSON。应先显示活动任务，再折叠完成/取消历史；原型只示意排序，正式实现必须遵守 selector 现有优先级。
- **Downloads**：PI/Invoice/Packing/Contract/Booking 当前集中在底部一排，应归入 Documents / Downloads。生成失败需指出缺失事实，不得默认所有文件随时可用；出口单不显示 Booking。
- **Danger**：普通 NEW 单删与完整 NEW/NEW pair 双删已安全隔离于 More；原型六种场景均非 NEW，因此不提供删除按钮。完成不是普通保存；Correction 也不是把订单重新打开为业务状态。

### Partial 对照

|Partial|上下文/责任|未来呈现原则|
|---|---|---|
|`_shared_shipment`|shipment_owner，出口单只读|展示来源和跳转，不加共享字段编辑|
|`_linked_financial_owner`|financial_owner_resolution + financial agreement/settlement|owner 缺失明确错误，不显示本单财务编辑|
|`_freight_agreement_form`|PRE_SHIPMENT 最终接受报价|金额/币种独立保存，不与后续结算混淆|
|`_freight_settlement_form`|USD/CNY required、invoice、payment 条件|同意运费只读参考；付款控件保留 invoice gate|
|`_payment_receipts_form`|预付/尾款实际到账|与计划金额分开，不以任务 Done 代替收款事实|
|`_completion_check`|completion_check 权威结果|逐项来源与更新入口，未完成不展示提交完成|
|`_completed_snapshot`|终态事实、owner 财务|只读，不把仍未确认的值伪装为零|

## 推荐实施顺序（本轮不执行）

1. **UI-1 全局设计系统与 Layout**：先提取 Dashboard tokens、标题、sidebar、宽度、按钮和焦点样式；保持 Dashboard search/reveal/collapse 及业务 DOM contracts。验收：控制中心/详情/主数据连续导航、窄屏菜单。
2. **UI-2 订单详情**：只读 Overview、当前行动、section 导航、分模块独立编辑、历史折叠；先修 G04/G16/G17 入口不匹配。验收矩阵覆盖所有阶段和 linked owner，比较提交字段与既有 route 完全一致。
3. **UI-3 新建/编辑**：布局、中文说明、付款选择清晰、错误定位；不改变 Decimal、事务、snapshot 和 item identity 规则。
4. **UI-4 主数据**：表格、空态、输入说明、启停表达；搜索/分页作为单独确认需求。
5. **UI-5 确认、删除、Correction、低频页**：统一风险面板，保留双编号删除确认，明确 correction 模块/关闭会话；下载失败反馈。
6. **UI-6 移动端与可访问性完善**：全流程键盘、读屏、缩放、对比度、44px 触控、sticky 遮挡与 reduced-motion。基础可访问性应从 UI-1 开始，不等到最后补救。

每阶段独立 diff / 测试 / 浏览器 UAT 后才进入下一阶段。老板经营看板是独立项目，等待真实订单数据积累与统计口径确认；不混入本轮方案。

## 本轮交付与验收边界

原型：`../prototypes/v2_order_detail_ui_prototype.html`；设计规范：`v2_ui_design_system.md`。模拟数据不对应真实订单；前端演示不调用 API、不保存业务事实。浏览器检查记录见设计系统末尾；正式业务页尚未进行本轮浏览器 UAT。
