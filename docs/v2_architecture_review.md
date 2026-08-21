# XHTPI-system V2 Schema, Lifecycle and Form Architecture Review

本文档是 V2 设计审计，不授权删除字段、压缩 migration、建立 V2 生产库或迁移真实业务数据。

## 1. 审计口径与缩写

- 写入路径：`CS` 创建销售单，`CC` 创建 Commission 单，`ES` 编辑销售单，`EC` 编辑 Commission 单，`SS` 销售状态更新，`SC` Commission 状态更新。
- 页面：`C` Create，`E` Edit，`S` Status，`V` View，`D` Dashboard。
- 单据依赖：`PI` Proforma Invoice，`CI` Commercial Invoice，`PL` Packing List，`BK` Booking DOCX。
- Reminder：`DOC` Document，`SHIP` Shipping，`PAY` Payment，`FRT` Freight。
- 生命周期：`N` NEW，`P` PRE_SHIPMENT，`S` SHIPPED，`A` ARRIVED，`C` COMPLETED，`ALL` 全阶段。
- UI：`H` HIDDEN，`E` EDITABLE，`R` READ_ONLY。下表的 `阶段/UI` 表示首次相关阶段及建议；进入 COMPLETED 后原则上一律 R。

“依赖”列只列实际业务消费者；普通 ORM 查询、调试输出和字段名出现不算业务依赖。快照字段由创建/编辑时复制，目的是冻结单据抬头，不应被当前主数据静默覆盖。

## 2. PI 109 字段逐项审计矩阵

| # | 字段 / 当前类型 | 当前业务含义 | 写入路径；页面 | 单据 / Reminder / Dashboard 依赖 | V2 分类与建议来源 | 阶段 / UI |
|---:|---|---|---|---|---|---|
|1|`id` INTEGER|内部主键|ORM；V/D|关联 PIItem、Task 等|KEEP；内部 ID 可在 V2 重启|ALL/R|
|2|`pi_no` VARCHAR(50)|业务 PI 编号|CS/CC/ES/EC；C/E/V/D|PI/CI/PL/BK；Task 卡片|KEEP；唯一且允许重录原编号|N/E，后续 R（受控更正）|
|3|`pi_date` DATE|订单/PI 日期|CS/CC/ES/EC；C/E/V|PI/CI/PL|KEEP|N/E，P+/R|
|4|`customer_id` INTEGER|当前客户主数据关联|CS/CC/ES/EC；C/E/V/D|统计、导航；Task presenter|KEEP；FK + 快照|N/E，P+/R|
|5|`exporter_id` INTEGER nullable|销售订单出口商；Commission 可空|CS/ES；C/E/V|PI/CI/PL；部分列表|KEEP nullable；由明确 `order_type` 控制必填性|N/E，P+/R|
|6|`payment_terms` VARCHAR(100)|合同付款条款自由文本|六路径部分写；C/E/S/V|PI/CI；不应被 PAY 解析|KEEP；展示性合同文本，结构化付款事实另存|N/E，后续 R/受控更正|
|7|`loading_port` VARCHAR(100)|起运港|CS/CC/ES/EC；C/E/V|PI/CI/PL/BK|KEEP|N/E|
|8|`destination_port` VARCHAR(100)|目的港|CS/CC/ES/EC；C/E/V|PI/CI/PL/BK；订单筛选|KEEP|N/E|
|9|`bank` TEXT|收款账户完整文本|CS/CC/ES/EC；C/E/V|PI/CI|KEEP（V2 后续可改 `bank_account_id` + document snapshot）|N/E，S+/R|
|10|`shipment_date` DATE|计划发运日期|CS/CC/ES/EC；C/E/V|PI 文本；当前 Reminder 未用|KEEP；明确命名 `planned_shipment_date`|N/E|
|11|`note` VARCHAR(200)|PI/订单备注|CS/CC/ES/EC；C/E/V|PI|KEEP；不替代 Follow-up/Activity|N/E，后续可编辑|
|12|`status` VARCHAR(20)|订单生命周期状态|SS/SC；S/V/D|全部阶段页面；DOC/PAY；Dashboard|KEEP；集中枚举与 lifecycle policy|ALL；按状态流转|
|13|`pi_type` VARCHAR(50)|历史类型字段，当前业务写入不稳定|未见正式表单写；少量兼容|无可靠规则依赖|REPLACE；V2 `order_type=SALES/COMMISSION` 非空|N/E，后续 R|
|14|`customer_name_snapshot` VARCHAR(100)|客户名称快照|CS/CC/ES/EC；V|PI/CI/PL；Task fallback|KEEP；文档历史真相|N/E（随明确换客户刷新），P+/R|
|15|`customer_address_snapshot` VARCHAR(200)|客户地址快照|同上|PI/CI/PL|KEEP|N/E，P+/R|
|16|`customer_tax_code_snapshot` VARCHAR(100)|客户税号快照|同上|商业单据|KEEP|N/E，P+/R|
|17|`customer_country_snapshot` VARCHAR(50)|客户国家快照|同上|商业单据/展示|KEEP|N/E，P+/R|
|18|`customer_contact_snapshot` VARCHAR(100)|客户联系人快照|同上|PI/业务展示|KEEP|N/E，P+/R|
|19|`customer_phone_snapshot` VARCHAR(50)|客户电话快照|同上|PI/展示|KEEP|N/E，P+/R|
|20|`customer_email_snapshot` VARCHAR(100)|客户邮箱快照|同上|PI/展示；付款邮件上下文可用|KEEP|N/E，P+/R|
|21|`exporter_name_snapshot` VARCHAR(100)|出口商名称快照|CS/CC/ES/EC；V|PI/CI/PL|KEEP；Commission 取佣金收取方的现有行为需确认|N/E，P+/R|
|22|`exporter_address_snapshot` VARCHAR(200)|出口商地址快照|同上|PI/CI/PL|KEEP|N/E，P+/R|
|23|`exporter_tax_code_snapshot` VARCHAR(100)|出口商税号快照|同上|商业单据|KEEP|N/E，P+/R|
|24|`exporter_country_snapshot` VARCHAR(50)|出口商国家快照|同上|商业单据|KEEP|N/E，P+/R|
|25|`exporter_contact_snapshot` VARCHAR(100)|出口商联系人快照|同上|PI/展示|KEEP|N/E，P+/R|
|26|`exporter_phone_snapshot` VARCHAR(50)|出口商电话快照|同上|PI/展示|KEEP|N/E，P+/R|
|27|`exporter_email_snapshot` VARCHAR(100)|出口商邮箱快照|同上|PI/展示|KEEP|N/E，P+/R|
|28|`freight_forwarder_id` INTEGER|货代关联|ES/EC/SS/SC；E/S/V|BK；FRT presenter 安全 fallback|KEEP nullable；V2 启用 FK 前清洁主数据|P/E，C/R|
|29|`ocean_freight` FLOAT|旧页面“海运费”，报价/币种/实际账单语义不清|ES/EC/SS/SC；E/S/V|旧列表/表单，非新 FRT authoritative|UNCERTAIN；确认是报价还是合同运费后改 Numeric + currency 或移至 FreightQuote|P/E|
|30|`container_date` DATE|旧装柜日期（无时间）|ES/EC/SS/SC；E/S/V|DOC/SHIP fallback；BK|LEGACY_ONLY；V2 用 `container_loading_at`，导入时转换|P/H（导入层）|
|31|`container_loading_at` DATETIME|确认装柜日期时间|ES/EC/SS/SC；E/S/V|DOC/SHIP Driver|KEEP；唯一装柜时间真相|P/E，S+/R|
|32|`container_location` VARCHAR(100)|装柜地址|ES/EC/SS/SC；E/S/V|BK/操作上下文|KEEP|P/E，S+/R|
|33|`driver_name` VARCHAR(100)|司机姓名|ES/EC/SS/SC；E/S/V|SHIP Driver completeness|KEEP|P/E，S+/R|
|34|`driver_phone` VARCHAR(50)|司机电话|同上|SHIP Driver completeness|KEEP|P/E，S+/R|
|35|`vehicle_number` VARCHAR(50)|车牌|同上|SHIP Driver completeness|KEEP|P/E，S+/R|
|36|`etd` DATE|预计开航日期|ES/EC/SS/SC；E/S/V/D|SHIP Actual Departure；DOC BL/保险；Timeline|KEEP|P/E，S 可因改船期受控编辑|
|37|`eta` DATE|预计到港日期|ES/EC/SS/SC；E/S/V/D|SHIP Actual Arrival；Timeline|KEEP|P/E，运输中可更新|
|38|`coo_required` VARCHAR(10)|旧 COO “需要/不需要/已完成”混合事实|ES/EC/SS/SC；E/S/V|DOC adapter|REPLACE；V2 Boolean nullable `coo_required`，完成仅 Task|N/P E，S+/R|
|39|`apta_required` VARCHAR(10)|旧 APTA 混合事实|同上|DOC adapter|REPLACE；Boolean nullable|N/P E，S+/R|
|40|`export_license_required` VARCHAR(10)|旧出口许可证混合事实|同上|DOC adapter|REPLACE；Boolean nullable|N/P E，S+/R|
|41|`customs_docs_required` VARCHAR(10)|旧报关文件混合事实|同上|DOC adapter|REPLACE；Boolean nullable|N/P E，S+/R|
|42|`coc_required` BOOLEAN|是否需要 COC|六 mutation helper；E/S/V|DOC COC|KEEP|N/P E，S+/R|
|43|`coa_required` BOOLEAN|是否需要 COA|六 mutation helper；E/S/V|DOC COA（优先于 legacy）|KEEP|N/P E，S+/R|
|44|`original_bl_required` BOOLEAN|是否需要提单原件|六 mutation helper；E/S/V|DOC BL、原件邮寄前置|KEEP|N/P E，S+/R|
|45|`obd_electronic_required` BOOLEAN|是否需要 OBD 电子提单|同上|DOC OBD|KEEP|N/P E，S+/R|
|46|`insurance_original_required` BOOLEAN|是否需要保单原件|同上|DOC 保险、邮寄前置|KEEP|N/P E，S+/R|
|47|`insurance_electronic_required` BOOLEAN|是否需要保单电子版|同上|DOC 保险|KEEP|N/P E，S+/R|
|48|`original_documents_mail_required` BOOLEAN|是否需要邮寄原件|同上|DOC 邮寄 REQUIRED_INPUT|KEEP|N/P E，S+/R|
|49|`telex_release_required` BOOLEAN|是否需要电放件|同上|PAY/DOC 电放|KEEP|N/P E，S+/R|
|50|`other_documents` VARCHAR(200)|其他文件自由文本|ES/EC/SS/SC；E/S/V|BK/展示；无结构化 Task|UNCERTAIN；若需提醒应拆 Manual Task/DocumentRequirement，否则保留备注|P/E|
|51|`bill_of_lading` VARCHAR(100)|提单号或提单文本（命名不够明确）|ES/EC/SS/SC；E/S/V|CI/PL/展示|REPLACE；V2 明确 `bill_of_lading_number`|S/E，A+/R|
|52|`shipping_company` VARCHAR(100)|船公司|ES/EC/SS/SC；E/S/V|CI/PL/展示|KEEP（可未来主数据化）|P/S E|
|53|`coa_status` VARCHAR(20)|旧 COA 需要/完成混合状态|ES/EC/SS/SC；E/S/V|DOC legacy adapter|LEGACY_ONLY；V1 import，V2 `coa_required` + Task|导入/H|
|54|`insurance_status` VARCHAR(20)|旧保险混合且不能区分原件/电子版|ES/EC/SS/SC；E/S/V|adapter 有意不猜两类|LEGACY_ONLY；V1 import note，V2 两个 Boolean + Tasks|导入/H|
|55|`document_shipping_status` VARCHAR(20)|旧原件邮寄完成状态|ES/EC/SS/SC；E/S/V|DOC legacy adapter|LEGACY_ONLY；V2 mail required + Task|导入/H|
|56|`tracking_number` VARCHAR(100)|旧邮寄运单号|ES/EC/SS/SC；E/S/V|旧展示；新完成值在 Activity payload|REPLACE；V2 Task completion payload（可选 DocumentShipment 实体后置）|S/R（Task history）|
|57|`batch_no` VARCHAR(200)|产品批号|ES/EC/SS/SC；E/S/V|CI/PL 或业务展示|KEEP；若多 PIItem 不同批号应下沉 PIItem（待确认）|S/E|
|58|`actual_departure_date` DATE|实际发运/开航日期|ES/EC/SS/SC；E/S/V/D|SHIP Rule；FRT +7；Timeline|KEEP|S/E，A+/R/受控更正|
|59|`vessel_info` VARCHAR(100)|船名/航次信息|ES/EC/SS/SC；E/S/V|BK|KEEP；可拆 vessel/voyage 后置|P/E|
|60|`booking_number` VARCHAR(100)|订舱号|ES/EC/SS/SC；E/S/V|BK|KEEP|P/E，S+/R|
|61|`container_type_quantity` VARCHAR(20)|托书中的箱型箱量组合文本|ES/EC/SS/SC；E/S/V|BK|UNCERTAIN；与 container_type/数量字段重复，需确认模板真实用途|P/E|
|62|`shipping_mark` VARCHAR(50)|唛头|ES/EC/SS/SC；E/S/V|BK/PL|KEEP|N/P E|
|63|`freight_term` VARCHAR(50)|托书运费条款|ES/EC/SS/SC；E/S/V|BK|KEEP；与 PIItem.trade_term 不同语义需命名澄清|P/E|
|64|`contract_number` VARCHAR(100)|合同号|ES/EC/SS/SC；E/S/V|BK/商业文件|KEEP|N/P E|
|65|`freight_clause` VARCHAR(200)|托书运费附加条款|ES/EC/SS/SC；E/S/V|BK|KEEP|P/E|
|66|`waybill_option` VARCHAR(50)|运单/提单选项|ES/EC/SS/SC；E/S/V|BK|KEEP；枚举化|P/E|
|67|`container_number` VARCHAR(50)|箱号|ES/EC/SS/SC；E/S/V|CI/PL/业务展示|KEEP|S/E，A+/R|
|68|`seal_number` VARCHAR(50)|封号|同上|CI/PL/展示|KEEP|S/E，A+/R|
|69|`container_type` VARCHAR(20)|箱型|ES/EC/SS/SC；E/S/V|CI/PL/BK|KEEP；若多箱型未来拆 ShipmentContainer|P/E|
|70|`quantity_units` FLOAT|当前单箱/托书数量数值，语义不明确|ES/EC/SS/SC；E/S/V|BK/PL 可能使用|UNCERTAIN；核对 Word/PDF 标签后决定下沉 PIItem 或 Shipment|P/E|
|71|`gross_weight` FLOAT|毛重|ES/EC/SS/SC；E/S/V|PL/BK|REPLACE；Numeric(18,3) + unit|P/E|
|72|`volume` FLOAT|体积|ES/EC/SS/SC；E/S/V|PL/BK|REPLACE；Numeric(18,3) + CBM 语义|P/E|
|73|`vgm` VARCHAR(50)|核实总重 VGM 文本|ES/EC/SS/SC；E/S/V|BK|REPLACE；Numeric + unit，除非确需自由文本|P/E|
|74|`total_quantity` FLOAT|托书总数量|ES/EC/SS/SC；E/S/V|BK/PL|DERIVED 优先从 PIItem；允许 snapshot override 需确认|P/R 或受控 E|
|75|`total_quantity_unit` VARCHAR(20)|总数量单位|同上|BK/PL|KEEP 或由 PIItem 单位派生；当前 PIItem 无单位结构|P/E|
|76|`total_weight` FLOAT|总重量|同上|BK/PL|REPLACE；Numeric，可能 derived/snapshot|P/E|
|77|`total_weight_unit` VARCHAR(20)|重量单位|同上|BK/PL|KEEP；枚举|P/E|
|78|`total_volume` FLOAT|总体积|同上|BK/PL|REPLACE；Numeric，可能 derived/snapshot|P/E|
|79|`total_volume_unit` VARCHAR(20)|体积单位|同上|BK/PL|KEEP；枚举|P/E|
|80|`total_vgm` VARCHAR(50)|总 VGM 文本|同上|BK|REPLACE；Numeric + unit/derived|P/E|
|81|`actual_arrival_date` DATE|实际到港日期|ES/EC/SS/SC；E/S/V/D|SHIP Arrival|KEEP|A/E，C/R|
|82|`payment_received` VARCHAR(10)|旧“已收齐/未收齐”总状态|ES/EC/SS/SC；E/S/V|PAY legacy fallback；完成校验|DERIVED；V2 由结构化金额计算，V1 import-only fallback|S+/R（派生展示）|
|83|`currency` VARCHAR(10)|订单主币种|六 mutation helper；C/E/S/V/D|PAY 金额展示/计算|KEEP；V2 必填（一个 PI 一个主币种）|N/E，P+/R|
|84|`advance_payment_percent` NUMERIC(5,2)|预付款合同比例|六 helper；C/E/S/V|PAY 推导应收|KEEP|N/E，P+/R|
|85|`advance_payment_amount` NUMERIC(18,2)|预付款应收金额，可覆盖建议值|六 helper；C/E/S/V|PAY Advance task/settlement|KEEP|N/E，P+/R/受控更正|
|86|`advance_received_amount` NUMERIC(18,2)|预付款累计实收|六 helper；C/E/S/V/D|PAY resolve/settlement|KEEP；若分批到账频繁再 PaymentRecord|N+/E|
|87|`advance_received_at` DATETIME|预付款确认到账时间|六 helper；C/E/S/V|PAY settlement trigger|KEEP|N+/E|
|88|`balance_payment_amount` NUMERIC(18,2)|尾款应收金额|六 helper；C/E/S/V|PAY Follow-up/settlement|KEEP；可由总额-预付款建议但允许覆盖|N/E，P+/R|
|89|`balance_received_amount` NUMERIC(18,2)|尾款累计实收|六 helper；C/E/S/V/D|PAY resolve/settlement|KEEP|S+/E|
|90|`balance_received_at` DATETIME|尾款确认到账时间|六 helper；C/E/S/V|PAY settlement trigger|KEEP|S+/E|
|91|`freight_invoice_amount` FLOAT|旧单金额货代账单，币种无法确定|ES/EC/SS/SC；E/S/V|legacy display only|LEGACY_ONLY；绝不映射 USD/CNY authoritative|V1 import/H|
|92|`freight_invoice_confirmed` VARCHAR(10)|旧总账单确认状态，不能区分 USD/CNY|ES/EC/SS/SC；E/S/V|FRT legacy adapter|LEGACY_ONLY；V1 import note/task|V1 import/H|
|93|`freight_invoice_issued` VARCHAR(10)|货代发票是否开具的旧字符串事实|ES/EC/SS/SC + Freight service；E/S/V|FRT invoice/payment|REPLACE；V2 Boolean `freight_invoice_issued` + optional issued_at|S/A E|
|94|`freight_usd_bill_required` BOOLEAN|是否有 USD 海运费账单|六 helper；E/S/V|FRT USD workflow|KEEP|P/E，C/R|
|95|`freight_usd_amount` NUMERIC(18,2)|USD 海运费账单金额|六 helper/Freight service；E/S/V/D|FRT capture/confirm|KEEP|S/E|
|96|`freight_usd_confirmed` BOOLEAN|USD 金额业务确认事实|Freight service；E/S/V|FRT downstream；Activity snapshot 校验|KEEP；只通过确认 service 写|S/E（动作）|
|97|`freight_cny_bill_required` BOOLEAN|是否有 CNY 本地费用账单|六 helper；E/S/V|FRT CNY workflow|KEEP|P/E，C/R|
|98|`freight_cny_amount` NUMERIC(18,2)|CNY 本地费用账单金额|六 helper/Freight service；E/S/V/D|FRT capture/confirm|KEEP|S/E|
|99|`freight_cny_confirmed` BOOLEAN|CNY 金额业务确认事实|Freight service；E/S/V|FRT downstream；Activity snapshot 校验|KEEP；只通过确认 service 写|S/E（动作）|
|100|`freight_paid_at` DATETIME|实际支付货代时间|六 helper/Freight service；E/S/V|FRT warning/context|KEEP|S/A E|
|101|`telex_release` VARCHAR(10)|旧电放处理/完成状态|ES/EC/SS/SC；E/S/V|PAY legacy adapter|LEGACY_ONLY；V2 required fact + Task completion|V1 import/H|
|102|`settlement_documents_required` VARCHAR(10)|旧结汇文件需要/完成混合状态|ES/EC/SS/SC；E/S/V|PAY settlement adapter|REPLACE；V2 Boolean required；advance/balance 各自 Task|N/E，后续 R|
|103|`freight_payment_status` VARCHAR(10)|货代付款状态|ES/EC/SS/SC/Freight service；E/S/V|FRT RULE_DATA；订单完成校验|REPLACE；V2 明确 enum/Boolean + paid_at|S/A E，C/R|
|104|`commission_factory_id` INTEGER|Commission 订单实际厂家|CC/EC；C/E/V|Commission 列表/单据|KEEP；由 `order_type=COMMISSION` 控制|N/E，P+/R|
|105|`commission_exporter_id` INTEGER|Commission 收佣/对外主体关联|CC/EC；C/E/V|Commission 展示/快照|UNCERTAIN；当前命名与 exporter snapshot 关系需业务确认|N/E，P+/R|
|106|`commission_amount` FLOAT|佣金金额|CC/EC；C/E/V/D|Commission 统计|REPLACE；Numeric(18,2) + currency（是否同 PI 币种待确认）|N/E，S+/R|
|107|`commission_rate` FLOAT|佣金比例|CC/EC；C/E/V|Commission 计算/展示|REPLACE；Numeric(7,4)，明确计算基数|N/E，S+/R|
|108|`commission_status` VARCHAR(20)|佣金结算状态|CC/EC/SC；C/E/S/V|Commission 完成校验|KEEP/REPLACE 为 enum；未来可由 Task/支付事实驱动|S/A E，C/R|
|109|`factory_sale_amount` FLOAT|工厂实际销售金额|CC/EC；C/E/V/D|Commission 统计|REPLACE；Numeric(18,2) + currency/含税口径待确认|N/E，S+/R|

## 3. 分类汇总

- KEEP：76 项；核心订单、快照、结构化 Document/Shipping/Payment/Freight facts。
- REPLACE：19 项；混合“Required + Completed”的旧字符串字段、Float 金额/重量、含糊命名和旧总状态。
- DERIVED：2 项；`payment_received` 与 `total_quantity`，不应成为第二写入真相。
- LEGACY_ONLY：7 项；只供 V1 导入解释，权威值留在只读 archive。
- UNCERTAIN：5 项；`ocean_freight`、`other_documents`、`container_type_quantity`、`quantity_units`、`commission_exporter_id`，在业务语义确认前不删除、不自动迁移。

## 4. 订单生命周期与集中策略

现有状态集合与建议 V2 stage 一一对应：

| 现有 `PI.status` | V2 stage | 含义 |
|---|---|---|
|新建|NEW|商业条款已建立，物流执行尚未启动|
|待发运|PRE_SHIPMENT|订舱、装柜、单证准备|
|已发运|SHIPPED|货物已开航，后段文件、收款、货代结算|
|已到港|ARRIVED|到港后收尾、未结款/未结算/异常|
|已完成|COMPLETED|业务封存，默认只读|

建议建立单一 `order_lifecycle.py`（本阶段不实施）：

```python
class UIState(StrEnum):
    HIDDEN = "HIDDEN"
    EDITABLE = "EDITABLE"
    READ_ONLY = "READ_ONLY"

MODULE_POLICIES = {
    "COMMERCIAL_CORE":        {NEW: EDITABLE, PRE_SHIPMENT: READ_ONLY, SHIPPED: READ_ONLY, ARRIVED: READ_ONLY, COMPLETED: READ_ONLY},
    "PAYMENT_PLAN":           {NEW: EDITABLE, PRE_SHIPMENT: EDITABLE, SHIPPED: READ_ONLY, ARRIVED: READ_ONLY, COMPLETED: READ_ONLY},
    "PAYMENT_RECEIPTS":       {NEW: EDITABLE, PRE_SHIPMENT: EDITABLE, SHIPPED: EDITABLE, ARRIVED: EDITABLE, COMPLETED: READ_ONLY},
    "SHIPPING_PREPARATION":   {NEW: HIDDEN, PRE_SHIPMENT: EDITABLE, SHIPPED: READ_ONLY, ARRIVED: READ_ONLY, COMPLETED: READ_ONLY},
    "DRIVER_INFO":            {NEW: HIDDEN, PRE_SHIPMENT: EDITABLE, SHIPPED: READ_ONLY, ARRIVED: READ_ONLY, COMPLETED: READ_ONLY},
    "DOCUMENT_REQUIREMENTS":  {NEW: EDITABLE, PRE_SHIPMENT: EDITABLE, SHIPPED: READ_ONLY, ARRIVED: READ_ONLY, COMPLETED: READ_ONLY},
    "POST_SHIPMENT_DOCUMENTS":{NEW: HIDDEN, PRE_SHIPMENT: HIDDEN, SHIPPED: EDITABLE, ARRIVED: EDITABLE, COMPLETED: READ_ONLY},
    "FREIGHT_REQUIREMENTS":   {NEW: HIDDEN, PRE_SHIPMENT: EDITABLE, SHIPPED: READ_ONLY, ARRIVED: READ_ONLY, COMPLETED: READ_ONLY},
    "FREIGHT_AMOUNTS":        {NEW: HIDDEN, PRE_SHIPMENT: HIDDEN, SHIPPED: EDITABLE, ARRIVED: EDITABLE, COMPLETED: READ_ONLY},
    "ACTUAL_DEPARTURE":       {NEW: HIDDEN, PRE_SHIPMENT: EDITABLE, SHIPPED: EDITABLE, ARRIVED: READ_ONLY, COMPLETED: READ_ONLY},
    "ACTUAL_ARRIVAL":         {NEW: HIDDEN, PRE_SHIPMENT: HIDDEN, SHIPPED: EDITABLE, ARRIVED: EDITABLE, COMPLETED: READ_ONLY},
    "FINAL_SETTLEMENT":       {NEW: HIDDEN, PRE_SHIPMENT: HIDDEN, SHIPPED: EDITABLE, ARRIVED: EDITABLE, COMPLETED: READ_ONLY},
}
```

Route 根据 policy 生成 `FormModuleState`，Create/Edit/View/Commission/Dashboard presenter 共用；模板只渲染 module，不再自己比较中文状态。服务端仍必须验证字段是否在当前阶段可写，不能只靠隐藏控件。

### 模块可见性矩阵

| 模块 | NEW | PRE_SHIPMENT | SHIPPED | ARRIVED | COMPLETED |
|---|---|---|---|---|---|
|Commercial core / PIItem|显示|显示|显示|显示|显示|
|Payment plan|显示|显示|显示摘要|显示摘要|显示摘要|
|Payment receipts|显示（可为 0/未知）|显示|显示|显示|显示|
|Document requirements|显示|显示|显示摘要|显示摘要|显示摘要|
|Shipping preparation / booking|隐藏或仅计划日期|显示|显示历史|显示历史|显示历史|
|Driver info|隐藏|显示|显示历史|显示历史|显示历史|
|Freight requirements|隐藏|显示|显示|显示|显示摘要|
|Freight amounts/invoice/payment|隐藏|隐藏|显示|显示|显示摘要|
|Actual departure|隐藏|临近时显示|显示|显示|显示|
|Actual arrival|隐藏|隐藏|显示|显示|显示|
|Task history|按存在显示|显示|显示|显示|显示|

### 模块可编辑矩阵

| 模块 | NEW | PRE_SHIPMENT | SHIPPED | ARRIVED | COMPLETED |
|---|---|---|---|---|---|
|Commercial core / PIItem|EDITABLE|READ_ONLY（走更正）|READ_ONLY|READ_ONLY|READ_ONLY|
|Payment plan|EDITABLE|EDITABLE|READ_ONLY（受控更正）|READ_ONLY|READ_ONLY|
|Payment receipts|EDITABLE|EDITABLE|EDITABLE|EDITABLE|READ_ONLY|
|Document requirements|EDITABLE|EDITABLE|READ_ONLY（受控变更）|READ_ONLY|READ_ONLY|
|Shipping preparation / driver|HIDDEN|EDITABLE|READ_ONLY|READ_ONLY|READ_ONLY|
|Freight requirements|HIDDEN|EDITABLE|READ_ONLY（受控变更）|READ_ONLY|READ_ONLY|
|Freight amounts/invoice/payment|HIDDEN|HIDDEN|EDITABLE|EDITABLE|READ_ONLY|
|Actual departure / arrival|HIDDEN|按模块 EDITABLE|EDITABLE|到港后 departure R、arrival E|READ_ONLY|

COMPLETED 更正建议：使用“Reopen for correction”受权限保护，要求 reason，写 OrderActivity（未来表），临时解锁指定模块，而不是把订单整体永久可编辑；修正后重新 reconcile，再封存。

## 5. Create / Edit / View 信息架构

### Create PI

1. 商业主体：Order Type、PI No/Date、Customer、Exporter/Commission 结构、快照预览。
2. 产品与合同：PIItem、贸易方式、数量、价格、订单币种。
3. 付款计划：自由文本 payment terms + Advance %/expected + Balance expected；到账字段可用但弱化。
4. 初始计划：planned shipment、起运港/目的港；只有已知时填写。
5. Document Requirements：三态 Required，不含“已完成”。
6. Bank 与备注。

### Edit PI

- 顶部显示阶段和 Next Action。
- 只显示当前阶段可操作模块；历史模块显示折叠只读摘要。
- 字段更改后单 PI targeted reconcile 与 PI 同事务提交。
- 不把 Task Done 混入 PI 编辑表单。

### View PI

- 先显示商业摘要、当前执行阶段、关键日期、Next Action。
- “Not relevant yet” 的模块隐藏；“当前应有但缺失”由 Task/validation 显示异常；两者不能都渲染成 `--`。
- 当前阶段模块展开；早期已完成模块显示只读摘要；Task history 独立显示。

## 6. Freight 与 Payment 渐进披露

Freight：PRE_SHIPMENT 仅回答 USD/CNY Bill Required；SHIPPED 才开放两币种 Amount/Confirm，并继续 Invoice Issued、Payment Status/Paid At。两个币种绝不求和。金额确认通过 service 同时写确认事实和 Activity snapshot；确认后金额变化让同一 Task reactivated/exception。

Payment：NEW 即录入 currency、Advance %/Expected、Balance Expected；实收金额/时间按到账更新。Contract total 来自 PIItem，建议金额是派生建议，用户可覆盖。`payment_received` 在 V2 仅派生展示。当前轻量模型覆盖一次预付款 + 一次尾款的累计事实；频繁同阶段分批到账出现后再引入 PaymentRecord。

## 7. Legacy Adapter 分类

| Adapter / fallback | 分类 | V2 建议 |
|---|---|---|
|Document `需要/不需要/已完成`|V1_IMPORT_ONLY|导入时转 Boolean Required + 可选 Legacy Done Task；不进入日常 V2 reconcile|
|`coa_status`|V1_IMPORT_ONLY|安全映射到 COA requirement/history；V2 runtime 移除 fallback|
|`insurance_status`|V1_IMPORT_ONLY|因原件/电子版歧义只导入 note，不猜两条 Task|
|`document_shipping_status` / `tracking_number`|V1_IMPORT_ONLY|导入 Legacy Done；新 tracking 保存在 Activity payload|
|`payment_received` fallback|V1_IMPORT_ONLY|结构化付款为唯一 runtime 真相|
|Settlement legacy “已完成”|V1_IMPORT_ONLY|只导入一条 ambiguous legacy record，不伪造 advance/balance 两条|
|Legacy `telex_release`|V1_IMPORT_ONLY|只在 Required 明确时导入完成历史|
|`container_date` fallback|V1_IMPORT_ONLY|导入到 `container_loading_at`（时间未知需标注）|
|`freight_invoice_amount`|REMOVE_AFTER_CUTOVER（保留于 V1 archive）|币种不明，不导入权威金额|
|Legacy freight total confirmation|V1_IMPORT_ONLY|单条 ambiguous legacy history，不拆 USD/CNY|
|Legacy freight invoice/payment status|V1_IMPORT_ONLY|可靠完成值可导入，未知时间保持 NULL|

V2 runtime 只保留对 V2 schema 的直接 adapters/validators；V1 解释逻辑移动到显式、可预览、只运行一次的 import package。

## 8. V2 Clean Database 提案

### 初始 schema

- Master：User、Customer、Exporter、Factory、FreightForwarder、Product、BankAccount（BankAccount 可后置）。
- Orders：PI、PIItem，明确 `order_type`，使用本矩阵 KEEP 字段与 REPLACE 后的清晰类型。
- Work control：OrderTask、TaskActivity；不导入无价值的旧完成任务。
- 暂不建 Shipment、TradeGroup、PaymentRecord。

### Single source of truth

- Required：PI Boolean tri-state；Completed：OrderTask/Activity。
- Payment plan/receipt：结构化 Decimal facts；paid-in-full derived。
- Freight：USD 与 CNY 各自 Required/Amount/Confirmed；invoice/payment 为明确事实。
- 文档快照：PI/PIItem snapshots；主数据变更不回写历史单据。

### 初始化与重录

1. 新建 V2 空库及首个管理员（凭据不硬编码）；导入/重建必要 Master data。
2. 手工重录 active、unpaid、not-arrived、freight-unsettled、still-follow-up orders，保留原 PI No，内部 ID 可变。
3. 每张重录订单提供 `as_of` 初始化流程：录事实后 targeted reconcile；对已经完成但仍需保留的事项，用明确 “historical completion, time known/unknown” 导入操作，避免用户逐条误点 Done。
4. 先在 staging V2 演练与逐单核对，再切换写入口；V1 文件复制、hash、权限设 read-only，应用提供独立 Archive viewer 或只读旧版本。

### V1 archive

- 冻结前备份 + SHA-256 + integrity/FK 报告；保留原 SQLite、migration revision、应用 tag 和生成文件目录。
- V1 禁止写；V2 不共享 SQLite 文件。
- 建立 PI No 对照清单与未重录原因。Archive 不承担 V2 runtime fallback。

## 9. Migration baseline 建议

V2 应在 schema 决定后建立新的、可从空库一次建成的 initial baseline，而不是把 V1 的 drift、nullable 修补和 legacy additive chain 永久带进 V2。当前 migration 链暂时全部保留；建议先 tag `v1-archive`，另建 V2 migration lineage/独立 migrations directory，验证模型 metadata diff=0、fresh replay、downgrade 仅针对开发库。风险包括双 migration lineage 配错目标、旧应用误连 V2、V2 baseline 漏约束/索引，因此启动时应校验数据库 generation/version 标识。

## 10. Future TradeGroup compatibility

当前 clean PI 不把订单号前缀当统计逻辑。Phase 4 可增加 TradeGroup 与 PI 的 `trade_group_id`、`trade_role`、`include_in_business_stats`；普通单默认 group/role NULL、stats True。现阶段 OrderTask 继续 ORDER scope，未来 Shipment-level 去重不应阻塞两张关联普通 PI。核心商业字段和快照保持每张 PI 独立，因此不会阻塞三方贸易。

## 11. 仍需业务确认的字段

1. `ocean_freight` 是订舱报价、客户合同运费，还是旧货代账单？币种是什么？
2. `commission_exporter_id` 是佣金收取方、对外出口主体，还是内部结算公司？它与 `exporter_id`/snapshot 的准确关系是什么？
3. `commission_amount`、`factory_sale_amount` 的币种与含税口径，`commission_rate` 的计算基数。
4. `container_type_quantity` 与 `container_type`、`quantity_units` 的真实差异。
5. `batch_no` 是否可能每个 PIItem 不同；若是应下沉 PIItem。
6. `other_documents` 只是说明文字，还是每一项都应成为可完成 Task？
7. COMPLETED 订单更正权限：仅管理员，还是所有登录用户；是否需要二次确认。
