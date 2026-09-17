# V2 订单详情正式迁移：字段与交互映射审计

审计日期：2026-09-17

代码基线：`5e0a74444a059aa0f45936c3bec0582fd4faa32b`（`feature/codex-rebuild`）

原型基线：`prototypes/v2_order_detail_ui_prototype.html`（相关设计 checkpoint `c7781f0`）
范围：源码和静态原型审计；未启动应用、未连接任何真实数据库、未运行迁移或测试。本文件不是业务 UAT 结果。

## 执行摘要

正式页 `v2/templates/v2/order_view.html` 已有大部分原型所需的权威字段、受控生命周期入口、任务投影与关联订单所有权判断。迁移的主要工作是**重组既有展示和入口**，不能把原型模拟值、合并保存或阶段跳转直接带入正式页面。

建议迁移顺序：

1. 页面骨架、身份/阶段只读展示、订单概览、页面内导航和响应式基础；
2. 当前待处理面板（复用 selector/presenter，不在浏览器重算规则）；
3. 运输摘要和五个折叠组，保留既有独立保存 scope 与关联所有权；
4. 客户付款与运费模块；
5. 单证要求、系统生成文件、任务历史与活动；
6. Safari/窄屏 UAT 后的针对性修补。

`READY` 的核心包括 PI/PIItem 快照、运输/装柜/报价字段、任务和活动、文件生成、受控发运/到港入口。主要缺口是**正式页的模块化呈现、页面内导航、折叠与反馈**，以及少数原型数据没有可靠来源（国家/国旗、独立路线类型、真实“生成并下载”成功状态）。任何跨模块单一 Save、在 EXPORT_ORDER 编辑共享运输/财务、重设通用 `/status` 跳阶段，均不在本迁移范围内。

### 分类口径

|状态|含义|
|---|---|
|`READY`|已有可靠数据和业务逻辑，可复用。|
|`PRESENTATION_ONLY`|仅需调整结构、文案、格式或视觉层级。|
|`MISSING_DISPLAY`|权威数据已存在，但详情页当前没有合适展示。|
|`MISSING_DATA`|没有可靠字段/计算来源，不能用原型假值替代。|
|`BACKEND_REQUIRED`|新交互需要 route/service/context 支持。|
|`BUSINESS_DECISION`|实现前需确认业务口径。|
|`DO_NOT_IMPLEMENT`|原型演示内容，不应进入正式系统。|

### 证据入口与不变边界

- 页面 context：`v2/web.py:456-482` 的 `order_view`；它提供 `tasks`、`agreement`、`settlement`、金融/运输 owner、`completion`、`advance_payment`、`present_task` 和 `present_activity`。
- 任务读投影：`v2/selector.py:13-117`；任务文案与可用动作：`v2/presenter.py:50-186`。GET 不应 reconcile 或写入。
- 字段所有权：`v2/shipment_ownership.py` 和 `v2/linked_trade.py`。EXPORT_ORDER 的 physical shipment 与 customer payment/freight 均由关联 CUSTOMER_ORDER 管理。
- 生命周期：`v2/web.py:828-1015`。`PRE_SHIPMENT → SHIPPED` 必须经 `enter_shipped`；`SHIPPED → ARRIVED` 必须经 `enter_arrived`；不能恢复通用 `/orders/<id>/status` POST。
- 现有详情页的独立表单分别提交至 `order_facts`、`advance_receipt`、`freight_agreement_manual`、`update_eta`、`task_action` 等，字段白名单和阶段限制由 `v2/web.py:573-792` 强制执行。

## 1. 页面模块映射

|原型模块|原型展示|正式页现状与证据|可直接迁移？|缺口/风险|推荐批次与测试|
|---|---|---|---|---|---|
|标题/订单身份|PI、客户、订单类型/角色、日期、返回链接|`order_view.html` 仅 PI、客户、阶段；关联信息在 `#linked-trade`|部分|order type、PI 日期、身份层级和统一页头需补展示；不应显示原型固定客户/国家|1；`test_v2_*` 详情 GET/linked 视图|
|阶段只读展示|五阶段进度条及当前说明|仅蓝色 `pi.status` badge；底部有阶段受控入口|是，`PRESENTATION_ONLY`|阶段条不可变成可点击状态转换；EXPORT_ORDER 要显示角色限制|1；`test_v2_new_stage_workflow`、`test_v2_linked_shipment`|
|当前待处理|优先任务、Exception/Action/Waiting 数、查看全部、直接动作|详情逐条展示全部 task；`projected_details` + `present_task` 已为权威来源|部分|需要排序/聚合与 Done/Cancelled 折叠；不能按原型假日期或自写优先级|2；selector/presenter、Dashboard GET zero-write tests|
|订单概览|合同金额、逐项型号/数量/总量、路线、风险摘要|`Order Summary` 和 `Items` 分开；`PI.contract_total`、PIItem snapshots 已有|是|国旗无可靠来源；route 不得从港口文字猜国家；风险需使用已有明确事实而非新评分|1；NEW/linked/commission 页面测试|
|运输摘要|计划装柜、ETD/ETA、实际发运/到达四项|SHIPPED/ARRIVED 有 schedule card；COMPLETED snapshot 有；PRE 尚有编辑表单|部分|需统一所有阶段只读摘要，不改变日期编辑规则|3；`test_v2_schedule_enhancement`、`test_v2_batch3c`|
|运输五组折叠详情|装运计划、货物与集装箱、订舱与船务、司机执行、路线与单证资料|字段分散于 shipment-preparation、booking-cargo、schedule、post-shipment、shared shipment|部分|折叠是前端展示；必须保持各表单独立保存与字段 scope；部分字段到 SHIPPED 才允许编辑|3；`test_v2_new_stage_workflow`、linked shipment tests|
|客户付款|合同/预付/尾款及只读 owner 提示|NEW/PRE 有 `advance-payment-summary`；SHIPPED/ARRIVED 有 `_payment_receipts_form`；COMPLETED snapshot|是|付款汇总与 receipt 表单不可混为一项；EXPORT_ORDER 仅 owner 链接|4；advance presentation/audit、completion tests|
|运费|接受报价、USD/CNY 结算、付款提醒|PRE 有 agreement；SHIPPED/ARRIVED 有 settlement；completed snapshot 和 linked owner summary|是|报价接受、金额确认、invoice、付款是不同受控动作/条件|4；freight reference/currency/forwarder tests|
|单证要求与办理|按角色显示 required/状态|正式 `Document Requirements` 只显示三态；专门 editor；task 是办理状态来源|部分|应把字段显示和 task 状态一起呈现，不能凭字段值假定完成；术语统一为“单证”仅呈现调整|5；linked documents、document requirements tests|
|系统生成文件|PI、Invoice、Packing、Contract、Booking 各一个生成下载按钮|底部 documents 链接；`v2.web.document` 调用渲染并 `send_file(as_attachment=True)`；EXPORT_ORDER 排除 Booking|部分|不能承诺任意时刻可生成；失败须保留服务端缺失字段错误；“生成并下载”是现有 GET 下载，不新建文件状态|5；document/render tests，Safari 下载 UAT|
|任务与跟进|活动任务、Waiting On/Next Follow-up、完成历史|每个 `OrderTask` 已有 inline action/waiting/reopen + history；单独 history 页|是|视觉收纳、动作分组；不改 completion mode 或 task transition|5；task action/history tests|
|任务活动|人类可读活动流，含预付款审计|inline collapse 与 `task_history.html`；`present_activity` 格式化 payload|是|不是全字段审计总线；只显示现有 TaskActivity，不应制造事件|5；advance receipt audit、task history tests|
|关联 EXPORT_ORDER 边界|共享运输/财务只读、跳 owner；出口单保留自己的 documents|`_shared_shipment.html`、`_linked_financial_owner.html`、route 409 guards|是|必须 fail closed：owner 无效显示配置错误，不猜测；出口单不显示 Booking|贯穿 1-5；linked shipment/documents/batch tests|
|COMPLETED 边界|默认只读，Correction 明示|`_completed_snapshot.html`、Correction session/route guards|是|不能把模块编辑重开成普通页面编辑；Correction 仍需模块和原因|贯穿；completion/correction tests|
|导航、锚点、More|sticky tabs、锚点、折叠、More|无页内导航；More 只在 NEW delete；`v2_batch1.js` 修补少数锚点|部分|使用真实 element id；注意 G04 既有 `button[type=submit]` selector 与无 type button 不匹配|1/5；手工 URL fragment、delete tests|

## 2. 字段级映射

### 2.1 身份、概览与商品

|原型模块|原型标签|正式字段/计算来源|类型/格式|订单类型/阶段|可编辑性|空值策略|当前状态|建议|
|---|---|---|---|---|---|---|---|---|
|标题|PI 编号|`PI.pi_no`|字符串|全部/全部|不可编辑；NEW 整单编辑另有规则|不可为空|`READY`|标题主信息|
|标题|客户/出口商|`customer_name_snapshot` / `exporter_name_snapshot`|快照字符串|全部/全部|历史快照只读|`—`|`READY`|不要用主数据替代快照|
|标题|订单类型|`PI.order_type`|`SALES`/`COMMISSION`|全部/全部|创建/NEW 编辑规则既有|不可为空|`MISSING_DISPLAY`|显示翻译后的只读标签|
|标题|关联组/角色/peer|`trade_group.group_no`、`trade_role`、`linked_peer`|关系/字符串|linked|不可直接改|peer 缺失要明确显示|`READY`|保留 owner 跳转|
|概览|合同金额|`PI.contract_total`，逐项 `PIItem.line_total` 求和|Decimal，现有 `%.2f`/`presenter.money`|全部/全部|items 仅 NEW 整单编辑|无 items 为 0，但订单创建应有 item|`READY`|统一金额格式，不改 Decimal/舍入|
|概览|产品型号、逐项数量|`PIItem.product_model_snapshot`、`quantity`、`quantity_unit`|快照 + Decimal|全部/全部|仅 NEW item editor|逐项 `—`，不汇总掩盖缺失|`READY`|按 item 显示；保留多产品|
|概览|产品类别、品牌、包装、HS Code|PIItem `product_*_snapshot`|快照字符串|全部/全部|HS Code PRE 专门 editor|`—`|`READY`|详情表与运输单证资料复用同一 snapshot|
|概览|总数量|安全地按相同 `quantity_unit` 分组求和；不同单位不可单一相加|Decimal + unit|全部/全部|只读|单位混合时分组显示|`BUSINESS_DECISION`|原型的 `Total 200 MT` 仅样例；实施前定义混合单位呈现|
|概览|总重量|`gross_weight_kg` 是订舱 gross weight；商品净重可由 `quantity_to_kg` 仅对明确 KG/KGS/MT 推导|Decimal|按字段/单据|PRE 局部编辑 gross weight|空为 `—`|`READY`|不得把商品数量总和冒充货物毛重|
|概览|装运路线|`loading_port` → `destination_port`|字符串|全部/全部|NEW/Correction COMMERCIAL；PRE 只读|`—`|`READY`|原型可显示港口文字|
|概览|国家/国旗|只有 party snapshot `*_country_snapshot`，没有港口国家字段|字符串|全部|主数据快照|未知|`MISSING_DATA`|不能从港口推断；不用国旗，或明确按 party country（非港口）标示|
|概览|风险摘要|现有 task health/context 与 `completion_check`|投影|按 task/ARRIVED+|只读|无活动事实则不显示|`BACKEND_REQUIRED`|仅聚合已有明确问题；不增加“风险分数”|

### 2.2 运输、订舱与单证资料

|原型模块|原型标签|正式字段/计算来源|类型/格式|订单类型/阶段|可编辑性|空值策略|当前状态|建议|
|---|---|---|---|---|---|---|---|---|
|装运计划|Planned Shipment Date|`PI.planned_shipment_date`|Date|SALES；全部显示|仅 NEW；PRE+ 是历史合同快照|`—`|`READY`|不可作为 PRE 发运提醒时钟|
|装运计划|Container Loading Date / Period|`container_loading_date` / `container_loading_period`；legacy `container_loading_at` 仅读兼容|Date / `AM`/`PM`/`UNKNOWN`|physical owner|PRE 局部 shipment facts；linked 同步|`—`/未知|`READY`|在摘要中位于 ETD 前|
|装运计划|ETD / ETA|`PI.etd` / `PI.eta`|Date|physical owner|PRE schedule；SHIPPED `update_eta`；linked ETD 原子同步|`—`|`READY`|不把编辑混入其他 save|
|装运计划|Actual Departure|`actual_departure_date`|Date|customer/independent|仅 `enter_shipped` 受控动作，linked 原子 handoff|`—`|`READY`|不可由 facts generic flow 编辑|
|装运计划|Actual Arrival|`actual_arrival_date`|Date|non-export SHIPPED|仅 `enter_arrived`；COMPLETED correction ARRIVAL|`—`|`READY`|不可重设 generic stage POST|
|货物与集装箱|Package Count / Unit|`package_count` / `package_unit`|Integer/string|physical owner/PRE+|PRE booking-cargo|`—`；unit default BAGS 仅提交逻辑|`READY`|独立 booking cargo form|
|货物与集装箱|Gross Weight / Display Unit|`gross_weight_kg` + `gross_weight_display_unit`|Decimal canonical KGS；KGS/MT 显示|physical owner/PRE+|PRE booking-cargo|`—`|`READY`|通过 `normalize_weight_input`，不改变单位规则|
|货物与集装箱|Volume / Unit|`volume_cbm`，单位固定 CBM|Decimal|string|physical owner/PRE+|PRE booking-cargo|`—`|`READY`|正式页可将 unit 只读写为 CBM|
|货物与集装箱|Container Type / Count|`container_type` / `container_count`|string/int|physical owner/PRE+|PRE shipment preparation；linked copy|`—`|`READY`|详情内保留，不放在原型摘要|
|货物与集装箱|Container No. / Seal No.|`container_number` / `seal_number`|string|non-export SHIPPED/ARRIVED（completed correction另论）|post-shipment `order_facts`|`—`|`MISSING_DISPLAY`|有字段且在 completed snapshot，正式折叠详情应展示；需要按既有 stage form 接入|
|货物与集装箱|VGM|`vgm_kg` + `vgm_display_unit`|Decimal KGS/MT|non-export SHIPPED/ARRIVED|post-shipment facts|`—`|`MISSING_DISPLAY`|原型未列为必填，后续决定是否加到详情扩展项|
|订舱与船务|Vessel / Voyage|`vessel_info`|string|physical owner|PRE shipment prep；linked copy|`—`|`READY`|显示/编辑分离|
|订舱与船务|Booking Number|`booking_number`|string|physical owner|PRE shipment prep；SHIPPED/ARRIVED facts|`—`|`READY`|已存在两阶段写入边界|
|订舱与船务|Carrier / B/L No.|`shipping_company` / `bill_of_lading_number`|string|non-export post departure；linked departure command|受控 departure payload，SHIPPED/ARRIVED facts|`—`|`READY`|linked 由 atomic command，不能 export 自写|
|订舱与船务|Freight Forwarder|`freight_forwarder_id`/relationship；agreement snapshot 为权威接受报价名称|FK/snapshot|string|financial/physical owner|PRE shipment preparation；linked physical copy|`—`|`READY`|有“当前货代”和“接受报价快照”两种语义，分别标示|
|订舱与船务|Accepted Freight Quote / Agreement|`OrderFreightAgreement`：source quote、forwarder snapshot、amount/currency/date/note|关系 + Decimal|customer/non-linked|PRE 专门 agreement；COMPLETED correction FREIGHT|无 agreement = 未确认|`READY`|不要把报价主数据当已接受协议|
|司机执行|Loading Location、Driver、Phone、Vehicle|`container_location`、`driver_name`、`driver_phone`、`vehicle_number`|string|physical owner|PRE shipment prep；linked physical copy|`—`|`READY`|EXPORT_ORDER 只读 owner|
|路线与单证|Loading/Destination Port|`loading_port` / `destination_port`|string|全部|NEW；completed COMMERCIAL correction；physical owner shared|`—`|`READY`|linked export 应读 owner 的共享 physical 视图|
|路线与单证|Trade Term|`PIItem.trade_term`，逐 item，不是 PI 全局字段|string|全部|NEW item edit|`—`|`READY`|多产品可能不同，不能选一个假定全局 trade term|
|路线与单证|Freight Term / Clause / Waybill|`freight_term` / `freight_clause` / `waybill_option`|string/text|physical/commercial context|PRE shipment prep；completed COMMERCIAL correction|`—`|`READY`|保持现有字段范围|
|路线与单证|Shipping Mark|`shipping_mark`|string|physical owner / post shipment|PRE shipment prep；SHIPPED/ARRIVED facts|`—`|`READY`|详情与 documents 可复用|
|路线与单证|Same as Consignee / Notify Party|`notify_party_same_as_consignee` + notify snapshots|bool/snapshot|physical owner|PRE shipment prep|same = 不展示替代人；different 空值 `—`|`READY`|不能读取/显示非快照 master 资料|
|路线与单证|PI Item 类别/品牌/型号/数量/单位/包装/HS|PIItem snapshots + quantity/unit|快照/Decimal|全部|item fields NEW；HS Code PRE dedicated editor|逐项 `—`|`READY`|应逐项表格；保留 HS 专门入口|

### 2.3 付款、运费、单证、文件与任务

|原型模块|原型标签|正式字段/计算来源|类型/格式|订单类型/阶段|可编辑性|空值策略|当前状态|建议|
|---|---|---|---|---|---|---|---|---|
|客户付款|Payment Terms|`payment_terms`|string|全部|NEW；completed PAYMENT/COMMERCIAL correction按现有 scope|`—`|`READY`|只读概览 + 合法表单入口|
|客户付款|Advance planned/received/outstanding/date|`advance_payment_amount`、`advance_received_amount`、`advance_received_at`；`advance_payment_summary()` 计算 outstanding|Decimal/Date|SALES non-export|NEW planned；received 经 `advance_receipt`|无金额按 summary 语义|`READY`|现有摘要已覆盖 NEW/PRE；不杜撰流水表|
|客户付款|Balance planned/received/date|`balance_payment_amount`、`balance_received_amount`、`balance_received_at`|Decimal/Date|SALES non-export|post-shipment receipts / correction|`—`|`READY`|到货完成检查按总 received/outstanding|
|客户付款|确认预付款审计|`TaskActivity` `ADVANCE_RECEIPT_RECORDED` payload|append-only activity|SALES non-export|仅 `advance_receipt` 成功保存前写|历史无 backfill|`MISSING_DISPLAY`|任务活动中已有格式化，模块可链接相应 History|
|客户付款|独立 payment/receipt transaction 列表|不存在；PI 上是累计汇总字段|—|—|—|—|`MISSING_DATA`|不得将 TaskActivity 伪装为会计流水；如需独立流水是另一个领域设计|
|运费|USD/CNY 是否需要、金额、确认、invoice、付款|`FreightSettlement` currency-specific fields|bool/Decimal/status/DateTime|non-export owner；SHIPPED/ARRIVED等|金额 facts + manual task gates；export 只读 owner|`—`/not required|`READY`|按币种分区，不能跨币种比较|
|运费|Forwarder payment reminder|`FREIGHT_*_PAYMENT_CONFIRM` 和 `FORWARDER_PAYMENT` task 等，来自 reconcile|task projection|non-export、条件触发|task_action/settlement 所有约束|无 task = 不展示|`READY`|在当前待处理复用 presenter 动作|
|运费|报价与账单差异|`freight_agreement_difference` + `FREIGHT_BILL_DIFFERS_FROM_AGREED_QUOTE`|Decimal/task|non-export、条件触发|业务数据驱动|无差异不显示|`READY`|只呈现，不新增终态清理规则|
|单证|Required 三态及备注|`DOCUMENT_FACTS`（13 bool）+ `other_document_notes`|bool/text|NEW/PRE；linked role local documents|NEW facts/专门 `/document-requirements` PRE|未知=Not confirmed|`READY`|显示标签需翻译，不直接曝光 snake_case|
|单证|办理状态|`OrderTask` document codes 与 task status/activity|task projection|按规则/角色/阶段|manual task action 或 rule data|无 task ≠ 完成|`READY`|与 requirement 并排但不可混淆|
|单证|linked export documents|Export License/Customs/Settlement 等 export task/requirements|bool/task|EXPORT_ORDER|按已有 export document policies|缺失配置明确报错|`READY`|客户侧 document workflows 在 export 被 suppress|
|系统文件|PI/Invoice/Packing/Contract|`document(pi_id, kind)`；`documents.document_context` 用 snapshots|HTTP file response|已有支持的订单|下载，不改变领域事实|服务端验证缺字段会失败|`READY`|每个一个真实 GET 按钮；反馈应基于 response|
|系统文件|Booking DOCX|`document(..., 'booking')`；template 排除 EXPORT_ORDER|HTTP file response|non-export only|下载|export 不显示|`READY`|保持角色边界|
|系统文件|生成状态/最近时间|无持久化生成事件或可靠状态字段|—|—|—|—|`DO_NOT_IMPLEMENT`|删去原型模拟时间/成功状态|
|任务|状态、health、title、context|`OrderTask` + `selector.projected_details()`|enum/JSON projection|全部|由 rule/manual action|CANCELLED 隐藏或归档|`READY`|当前待处理必须用投影结果，不直读旧 context|
|任务|Waiting On/Next follow-up|`waiting_on`/`waiting_since`/`next_follow_up_at` + latest FOLLOW_UP|enum/DateTime|active tasks|`task_action(..., waiting/followup)`|空 = `—`|`READY`|使用现有 parse/时区语义|
|活动|事件、操作者、时间、payload|`TaskActivity` + `present_activity()`|append-only|全部|仅业务操作|空 activity 显示 No activity|`READY`|只显示 formatter 已允许的 payload|

## 3. 交互与 endpoint 映射

|交互入口|原型行为|正式 endpoint/表单|权限与阶段限制|是否受控|缺口|迁移建议|
|---|---|---|---|---|---|---|
|当前待处理主操作|弹窗演示|`present_task().actions` → `enter_pre_shipment`、`enter_shipped`、`enter_arrived`、`#shipping-schedule`、`#shipment-preparation`、`#freight-agreement` 等|由 presenter + route 双重限制|是|部分 action（followup/advance receipt/task done）详情 inline 未完整映射为单一按钮族|以 `action.kind` 作为唯一映射，补齐 route 合法入口，不在模板复制 task-code 判断|
|查看全部/收起|前端显示更多任务|正式全部展示，无分组|无写入|是|需要 client-side `<details>` 或 server-rendered active/history group|纯 UI；保持所有任务和历史可键盘访问|
|阶段条|只读进度|无 endpoint|全部|是|需只读明确文案|不可点击；操作只从 task/受控按钮进入|
|进入待发运|按钮演示|POST `/v2/orders/<id>/enter-pre-shipment`|NEW、有 `STAGE_GATE_PRE_SHIPMENT ACTION`、export 拒绝|是|无|复用表单+CSRF|
|确认实际发运|按钮演示|GET/POST `/v2/orders/<id>/enter-shipped`|PRE、gate；linked 原子 command；export GET 转 customer owner|是|需将任务按钮和页面动作合并为一个一致入口|保留独立确认页和 required date/carrier/B/L 约束|
|确认货物已到港|按钮演示|GET/POST `/v2/orders/<id>/enter-arrived`|SHIPPED non-export|是|无|保持受控页|
|编辑运输/取消/保存|前端草稿|POST `/facts` 多个独立 form：booking cargo、shipment prep、schedule；`/update-eta`|字段白名单、stage、owner|是|原型“一组一保存”要对应多个现有 scope，而非一个 payload|每个折叠组标明实际保存范围；Cancel 可纯前端恢复表单，不写数据|
|展开/收起运输组|`<details>`|无 endpoint|无|是|无|纯 UI；保持内容未加载前后字段一致|
|登记预付款到账|静态入口|POST `/advance-receipt`|SALES、non-export、not completed；amount/date required|是|详情需从 task action 或 payment summary 提供同一路径|不要改累计覆盖语义；保留 TaskActivity 审计|
|客户收款|表单|POST `/facts`（SHIPPED/ARRIVED payment fields）|non-export、非 completed；关联 owner|是|预付款与尾款表单定位需明确|沿用 `_payment_receipts_form.html`，不要创建新 payment ledger|
|运费确认与付款|静态入口|`/facts`（允许字段）+ POST `/tasks/<id>/<action>`|币种、invoice/amount、task mode 条件|是|需将 action link/表单定位至对应币种|保持 amount/confirm/invoice/payment 分离|
|单证状态办理|表格演示|GET/POST `/document-requirements`；POST `/tasks/<id>/done|waiting|cancel|reopen`|PRE requirement editor；task completion mode|是|原型没有区分 requirement 与 task|两个子区分别呈现|
|文件生成下载|弹窗反馈|GET `/orders/<id>/documents/<kind>`|kind whitelist；Booking export 隐藏|是|浏览器下载成功不能由纯点击假定|使用 `<a>`；失败展示服务端错误，不加虚假生成记录|
|任务跟进/Done/Waiting|静态|POST `/tasks/<id>/<action>`|`task_service.py` 验证 mode/status/input|是|详情 UI 目前功能密集|延用 form/CSRF，视觉改造不改 transition|
|任务历史|静态时间线|GET `/tasks/<task_id>/history` 或详情 inline collapse|登录用户|是|无|保留 no-JS history page|
|More|示例|NEW standalone delete 或 NEW/NEW linked delete|`linked_delete_available()` 等 service guard|是|COMPLETED correction 不应塞入 Delete More|保留现有删除 confirmation；Correction 独立清晰入口|
|关联跳转|示例|`order_view(peer/owner)`|valid structured pair|是|owner 无效 fail closed|显示 owner/peer 与不可编辑原因|

## 4. 状态与角色矩阵

“显示”不等于“可编辑”；所有编辑仍由 server route 再校验。`CUSTOMER_ORDER` 为 linked pair 的 physical/financial owner，`EXPORT_ORDER` 不是第二套客户运输/付款流程。

|真实条件 / 原型 Scenario|当前待处理|概览/运输|客户付款与运费|单证|系统文件|允许操作 / 必须隐藏|
|---|---|---|---|---|---|---|
|独立 SALES `NEW`（原型未单列）|预付款/进入待发运 gate 等 selector 结果|概览、items、计划日期；无真实发运数据|advance summary 可见；无 post-shipment receipts|requirements 三态|PI/Invoice/Packing/Contract/Booking 受服务端校验|NEW 编辑、受 task 驱动 enter-pre-shipment、delete；无 enter-shipped/arrived|
|独立/客户 `PRE_SHIPMENT`（Scenario 1）|ETD、装柜、报价、预付款等 active projection|五组运输可展示；physical owner 可编辑既有 PRE modules|advance summary；agreement/edit 可用|需求编辑与 task|Booking 可见|enter-shipped 仅 route/task；planned date 只读；不显示 generic status|
|独立 SALES `SHIPPED`（Scenario 2）|ETA missing、arrival follow-up、document/payment/freight tasks|schedule/read-only departure；ETA 专门编辑；post-shipment refs|receipts+freight settlement，非 export|task-based办理|Booking 可见|enter-arrived；不允许 generic actual departure/ETD edit|
|独立 SALES `ARRIVED` 有未结清（Scenario 3）|customer balance/freight/document action|同上；actual arrival/ETA 只读 summary|receipt/settlement + `completion_check` 权威缺口|delivery completion tasks|Booking 可见|仅 completion_check overall complete 时 enter-completed；不得强制完成|
|`COMPLETED`（Scenario 4）|Done/history；无普通 active action|`_completed_snapshot` 只读|完成 snapshot，linked owner resolution|已存 requirements/task history|Booking 仅 non-export|只允许显式 Correction session 的范围，不能普通 facts 编辑|
|linked `CUSTOMER_ORDER` `PRE_SHIPMENT`（Scenario 5）|客户单承载 physical gate 与自身付款/货代 task；出口 documents 仍属 peer|客户单编辑 shared physical；原子同步到 export|customer owner 可编辑/查看|customer tasks 与 export documents 要区分|Booking 可见|可 create/enter shipped（若 gates），需展示 peer；不得把 export documents 伪装成客户付款任务|
|linked `EXPORT_ORDER` `PRE_SHIPMENT`（Scenario 6）|只显示出口自身 documents；physical/customer docs 由 selector suppress 或 history-only|`_shared_shipment` 从 owner read-only|`_linked_financial_owner` read-only/owner jump|export license/customs/settlement docs 可见|**无 Booking**|隐藏 shipment/payment/freight 普通编辑、generic stages 和 arrival；owner 无效报配置错误|

### 必须保持的角色/所有权条件

- `shipment_owner_for(pi)` 只在严格 1 CUSTOMER_ORDER + 1 EXPORT_ORDER pair 时返回 owner；无效 group 不能猜测。
- `PHYSICAL_FORM_FIELDS` 在非完成 EXPORT_ORDER 的 `/facts` 会被 409 拒绝；`copy_physical_facts` 的 allowlist 不含 commercial/financial facts。
- `financial_owner_for(pi)` 令 EXPORT_ORDER 的付款与运费只读且链接客户单；route 对 financial fields 同样 409。
- linked departure 是 `record_linked_actual_departure()` 原子 handoff：客户 `PRE_SHIPMENT → SHIPPED`，出口 `PRE_SHIPMENT → COMPLETED`，不可在 UI 描绘为两者都 SHIPPED。

## 5. Safari 代码级兼容性审计

以下是源码风险评估，不是 Safari 实测结论。

|项目|证据|等级|实施/UAT 要点|
|---|---|---|---|
|`input[type=date]`|详情、发运/到港、ETA、编辑页广泛使用；值为 ISO calendar date|`VERIFY`|Safari 原生选择器外观/键盘与 Chromium 不同；确认显示、清空、必填错误。现有 date 语义不应转换为 datetime。|
|`datetime-local`|详情 Waiting form、Dashboard modal；`task_service.parse_datetime()` 用 `datetime.fromisoformat`|`VERIFY`|确认本地时间输入、空值、浏览器返回与服务器业务时间展示；不要自行附加时区。|
|select/file input|Bootstrap 原生 select；此详情没有 file input|`LOW`|确认长选项、disabled/export read-only 与键盘操作。|
|固定侧栏与窄屏|`v2/base.html` fixed `.side`/`.main`；详情仅 ≤575.98px 覆盖|`RISK`|390px 验证 sidebar、长任务、表单和表格不产生整页横向溢出；新增 flex/grid 容器必须 `min-width:0`。|
|sticky tab/锚点|原型有 sticky tabs；正式页目前无统一 tabs，`v2_batch1.js` 用 `scrollIntoView`|`VERIFY`|实施后检验 anchor 不被固定元素遮挡；为 section 设置 `scroll-margin-top`。|
|折叠动画/`details`|原型使用 `<details>`；正式页用 Bootstrap `.collapse`|`VERIFY`|`details` 在现代 Safari 可用；若正式迁移改用它，验证键盘、marker、reduced motion 和大内容重排。不要依赖纯 hover。|
|Bootstrap dropdown/collapse|base 从 Bootstrap CDN 加载 bundle；More/inline history 使用|`VERIFY`|验证 Popper dropdown、collapse 和网络缓存后的 bundle 版本一致；不把 CDN 缓存问题写成已知 bug。|
|JS language/API|`v2_batch1.js` 使用 optional chaining、`closest`、`dataset`、`scrollIntoView`|`VERIFY`|现代 macOS Safari 支持；旧 Safari 版本不明，UAT 先记录版本。必要时只为目标版本做兼容处理。|
|现有 selector bug|`v2_batch1.js` 查询 `button[type=submit]`，而 shipment form button 没有显式 `type`|`RISK`|与 Safari 无关但已知跨浏览器行为缺口：独立保存说明/HS link 可能不插入；迁移前修正选择器或 button 属性，单独测试。|
|生成下载|`send_file(path, as_attachment=True)`|`VERIFY`|Safari 可能将 PDF 交给预览而不是用户期待的直接落盘；确认 filename、download/popup 行为和失败信息，不承诺“已下载”。|
|浏览器返回/表单恢复|多个 POST redirect 到详情，内联表单/fragment|`VERIFY`|验证 Back 后不会误以为表单已保存；模块保存后最好 redirect 到真实 anchor（需单独评审）。|
|外部资源与缓存|Bootstrap CSS/JS CDN|`VERIFY`|开发时关注 Safari 缓存导致 CSS/JS 未更新；本轮不改资源策略。|
|dialog/popover|仅静态原型使用 `<dialog>`；正式详情无此依赖|`LOW`|不构成正式迁移 blocker；若引入 modal，使用 Bootstrap/modal 或提供可访问的 fallback。|
|打印/单据|HTML PDF + DOCX/Booking download，不是浏览器打印页|`VERIFY`|Safari 不作为服务端 PDF 渲染依据；验证下载后的预览/保存即可。|

## 6. 分批迁移计划

每批均须先阅读 `AGENTS.md`、涉及 route/model/template/service，使用 `ui-ux-pro-max` 做 UI 变更，且在隔离测试库运行相关 tests。建议不修改 schema/model/migration。

|批次|目标与预计文件|后端/业务规则|风险与依赖|必测 Scenario|模型/推理|建议 commit|
|---|---|---|---|---|---|---|
|OD-1 页面骨架与概览|`v2/templates/v2/base.html`、`order_view.html`，必要时 V2 CSS/JS；标题、只读阶段、overview、真实锚点|无后端；不得改变 context/endpoint|base 与 Dashboard DOM/CSS 兼容，窄屏 sidebar|NEW、PRE、COMPLETED、linked export|`gpt-5.6-terra` / high；需 skill|`design: restructure v2 order detail shell`|
|OD-2 当前待处理|`order_view.html`，必要时最小 presenter 仅为安全显示数据|原则上 presentation only；严格复用 selector/presenter|排序/Done history 不可改变 task 状态或 GET 写入|PRE exception/action/waiting、SHIPPED ETA、ARRIVED、export suppress|`gpt-5.6-terra` / high；需 skill|`design: surface v2 order actions`|
|OD-3 运输|`order_view.html`、相关 partial、`v2_batch1.js`；只有证据证明需要时触碰 presenter|保存继续到既有 endpoint，不新增合并 payload|linked owner、ETD 原子性、actual departure/arrival controlled flows|independent PRE/SHIPPED/ARRIVED、linked customer/export|`gpt-5.6-terra` / high；需 skill|`design: organize v2 shipment details`|
|OD-4 付款与运费|`order_view.html`、`_payment_receipts_form.html`、`_freight_*` partial，最小 presenter format|不变更 aggregation、invoice gates 或 task rules|新增 advance audit 要显示但不能伪造成流水；export owner read-only|NEW/PRE summary、SHIPPED receipts、ARRIVED completion、export|`gpt-5.6-terra` / high；需 skill|`design: clarify v2 payment and freight panels`|
|OD-5 单证、文件、任务活动|`order_view.html`、document/task partial；可能 `v2_batch1.js`|纯呈现和 anchor；下载 route 不变|requirements 与 task completion 不混淆；file generation failures|PRE, linked customer/export, COMPLETED|`gpt-5.6-terra` / high；需 skill|`design: consolidate v2 documents and task history`|
|OD-6 Safari/响应式 UAT 修补|仅 UAT 实测涉及文件|通常 UI；若暴露 route feedback 问题另提案|先记录 Safari 版本和复现；不要顺带业务重构|六种情景、1440px/390px、Safari|`gpt-5.6-terra` / high；需 skill|`fix: polish v2 order detail safari uat`|

## 7. 需要用户确认的问题

### 正式迁移前必须确认

1. **总数量的混合单位口径**：多 PI Item 单位不同时，是否按单位分组展示，还是只显示逐项而不显示单一总计？当前代码没有通用单位换算规则。
2. **港口国旗**：正式页面是否完全不显示港口国旗，还是另有经确认的港口→国家主数据？现有字段不能安全提供该映射。
3. **“当前待处理”范围**：详情页是否只默认显示 active（Exception/Action/Waiting/Upcoming）任务，并将 DONE/CANCELLED 归档；还是要保留当前全部 task 平铺？这只影响呈现和可见历史，不改状态。
4. **模块保存后的定位反馈**：是否允许各现有 POST redirect 回同一详情页的真实 anchor 并带 flash/成功提示？这会触及 route redirect 表现，需单独确认，不能由前端假定成功。

### 可在本地 Safari UAT 后确认

1. 5 个运输折叠组中每组的默认展开状态，以及很长 PI Item 表格在 390px 的横向滚动/卡片化策略。
2. Safari 的 PDF 下载是否接受“浏览器预览后由用户保存”，以及是否需改下载文案。
3. 详情页 Sidebar 在 390px 是否采用静态顶部导航、抽屉，还是沿用当前 ≤575px 的静态排列。

### 无需用户确认，可沿用既有逻辑

- `enter_pre_shipment`、`enter_shipped`、`enter_arrived` 和 completed correction 的服务端边界；
- linked CUSTOMER_ORDER 的 physical/financial owner 与 EXPORT_ORDER 只读规则；
- 付款 aggregate、预付款审计活动、freight 的 USD/CNY 分离和 completion check；
- 文件 kinds 与 EXPORT_ORDER 隐藏 Booking；
- `DOCUMENT_FACTS` 三态、task completion mode、TaskActivity append-only 历史。

## 8. 建议的第一实施批次

先实施 **OD-1：页面骨架、身份/阶段只读展示、订单概览、真实页面内导航与窄屏基础**。

范围应限于正式详情的视觉层级和安全只读投影：

- 使用 `order_view` 现有 `pi`、`pi.items`、linked metadata，不添加 query 或模型字段；
- 将标题、关联身份、只读五阶段、合同金额、逐项型号/数量、路线迁移到清晰的概览；
- 不显示从端口推断的国家/国旗；多单位总量按用户确认前不做错误合计；
- 仅加入指向**已有元素**的 anchors；阶段条不提交状态；
- 不移动或改变现有 form action/name/CSRF/field scope，不合并保存；
- 用 1440px、390px、Safari 验证无页面横向溢出，且保留 NEW、COMPLETED、linked EXPORT_ORDER 边界。

完成 OD-1 后应独立提交、运行模板/路由相关的隔离测试及 `git diff --check`，再进入 action panel。这样可在不触碰付款、运输写入或 Reminder 规则的前提下，先获得可用于真实订单 UAT 的安全页面骨架。
