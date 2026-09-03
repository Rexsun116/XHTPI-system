# XHTPI-system V2 本地用户验收清单

本清单只用于本机 V2 试用数据库。不要使用 V1 地址或 V1 数据库。

## 准备

1. 按 `docs/v2_local_trial.md` 创建首个管理员并启动 V2。
   - 预期：浏览器打开 V2 登录页，使用新管理员登录。
2. 先备份 `instance_v2/database.db`。
   - 预期：获得一个带时间戳、可独立打开的副本。

## 主数据

3. 新增 Customer、Exporter、Factory、Product。
   - 预期：列表立即显示；代码重复时系统拒绝。
4. 新增 Freight Forwarder 和 Bank Account。
   - 预期：银行账号可在新订单选择；后来修改主数据不会改变已创建订单的银行快照。
5. 新增 Freight Quote（USD 100），再打开 Edit 修改备注。
   - 预期：金额保持 Decimal；报价可在 PRE_SHIPMENT 订单接受。

## 新订单

6. 创建 Sales PI，增加两个 PIItem，设置 20% 预付款。
   - 预期：合同总额为两行合计；预付款/尾款按结构化金额显示；Dashboard 出现等待预付款。
7. 在创建页设置 COO、COA、Original BL、Original Documents Mail 等文件需求。
   - 预期：Required 只有“需要/不需要/未确认”，没有“已完成”。
8. 查看 NEW 订单。
   - 预期：可维护付款计划、初始装运计划和文件需求；看不到司机、实际发运、货代实际账单。
9. 创建 Commission PI，输入 Rate。
   - 预期：Commission = Contract Total × Rate，按 ROUND_HALF_UP 取两位。
10. 测试 Explicit Override。
    - 预期：Amount 和 Reason 都必填；不填写原因时拒绝保存。

## 生命周期与 Reminder

11. 把 Sales PI 推进到 PRE_SHIPMENT。
    - 预期：出现装柜、司机、ETD/ETA、货代、USD/CNY Bill Required 和接受报价区域。
12. 接受 USD 100 Freight Quote，设置 USD/CNY 都需要，并设置 24 小时内装柜但司机资料不完整。
    - 预期：同一订单保存不可覆盖接受报价；Dashboard 出现 Driver Action/Exception（到装柜时间后为 Exception）。
13. 填齐司机姓名、电话、车牌。
    - 预期：Driver RULE_DATA Task 自动解决；清空任一项后同一 Task 重新激活。
14. 推进到 SHIPPED，填写 Actual Departure。
    - 预期：出现批号、付款收款、USD/CNY 实际账单和实际到港区域。
15. 给每个 PIItem 分别添加多个 Batch。
    - 预期：同一 PIItem 不允许重复批号；不同产品的批号不合并。
16. 完成 Payment Email；如需邮寄原件，填写 Tracking Number 和 Carrier 完成邮寄。
    - 预期：Tracking Number 必填并保存在 Activity；三天催款从两个前置完成时间的更早者起算。
17. 对催款 Task 执行 Follow-up，选择 CUSTOMER 和下一跟进时间。
    - 预期：Task 进入 Waiting；历史保留每次 note；到期投影为 Action/Overdue。
18. 录入 USD 120 与 CNY 本地费用。
    - 预期：USD 与接受报价 USD 100 比较并出现 +20 差异；CNY 不与 USD 比较，也不相加。
19. 分别确认 USD、CNY 金额。
    - 预期：Activity 保存币种和确认金额快照；确认后改金额会重新提醒并显示旧快照。
20. 确认货代发票、再把货代付款状态改为 Paid。
    - 预期：顺序任务正确；RULE_DATA 付款任务自动解决，不能手工 Done。
21. 填写 Actual Arrival，推进 ARRIVED。
    - 预期：到港异常自动解决；页面聚焦未收款、未结算和未完成任务。

## 单据

22. 下载 PI、Commercial Invoice、Packing List、Booking DOCX。
    - 预期：客户/出口商/银行使用订单快照；金额和币种正确；Packing List 批号按 PIItem 分组；Booking 箱型显示 `1 × 20GP`。
23. 特别人工核对 Booking 中固定 HS CODE `320611` 是否适用于本次产品。
    - 预期：如不适用，记录为验收问题，不要假定系统已结构化支持 HS Code。

## 完成与更正

24. 推进到 COMPLETED。
    - 预期：普通编辑全部只读。
25. 选择一个 Correction Module 并填写原因。
    - 预期：只有所选模块字段可改；其他字段的构造 POST 被拒绝。
26. 保存并关闭 Correction。
    - 预期：记录打开/关闭人员和时间；targeted reconcile 执行；原 Task 完成历史不删除。
27. 展开 Task Activity History。
    - 预期：可读地显示 Created、Waiting、Follow-up、Completed、Auto-resolved、Reactivated、Tracking 或 Freight Snapshot，不显示原始 JSON。

## 结束

28. 检查 Dashboard 的 Exception、Action、Waiting、Upcoming、Done History 和 Next Action。
    - 预期：刷新 Dashboard 不写数据库、不新增 Activity。
29. 停止服务并再次备份 V2 数据库。
    - 预期：备份可用于恢复本次试用数据。
