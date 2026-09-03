"""Central V2 lifecycle policy for order modules and server-side mutations."""

from dataclasses import dataclass


class OrderStage:
    NEW = "NEW"
    PRE_SHIPMENT = "PRE_SHIPMENT"
    SHIPPED = "SHIPPED"
    ARRIVED = "ARRIVED"
    COMPLETED = "COMPLETED"
    VALUES = (NEW, PRE_SHIPMENT, SHIPPED, ARRIVED, COMPLETED)


class ModuleState:
    HIDDEN = "HIDDEN"
    EDITABLE = "EDITABLE"
    READ_ONLY = "READ_ONLY"


class OrderModule:
    COMMERCIAL_CORE = "COMMERCIAL_CORE"
    PI_ITEMS = "PI_ITEMS"
    PAYMENT_PLAN = "PAYMENT_PLAN"
    PAYMENT_RECEIPTS = "PAYMENT_RECEIPTS"
    INITIAL_SHIPMENT_PLAN = "INITIAL_SHIPMENT_PLAN"
    DOCUMENT_REQUIREMENTS = "DOCUMENT_REQUIREMENTS"
    SHIPPING_PREPARATION = "SHIPPING_PREPARATION"
    DRIVER_INFO = "DRIVER_INFO"
    FREIGHT_REQUIREMENTS = "FREIGHT_REQUIREMENTS"
    POST_SHIPMENT = "POST_SHIPMENT"
    FREIGHT_SETTLEMENT = "FREIGHT_SETTLEMENT"
    # Compatibility alias while current V1 templates are retired.
    FREIGHT_AMOUNTS = FREIGHT_SETTLEMENT
    ACTUAL_DEPARTURE = "ACTUAL_DEPARTURE"
    ACTUAL_ARRIVAL = "ACTUAL_ARRIVAL"
    FINAL_SETTLEMENT = "FINAL_SETTLEMENT"
    COMMISSION_SETTLEMENT = "COMMISSION_SETTLEMENT"


STATUS_TO_STAGE = {
    "新建": OrderStage.NEW,
    "待发运": OrderStage.PRE_SHIPMENT,
    "已发运": OrderStage.SHIPPED,
    "已到港": OrderStage.ARRIVED,
    "已完成": OrderStage.COMPLETED,
    OrderStage.NEW: OrderStage.NEW,
    OrderStage.PRE_SHIPMENT: OrderStage.PRE_SHIPMENT,
    OrderStage.SHIPPED: OrderStage.SHIPPED,
    OrderStage.ARRIVED: OrderStage.ARRIVED,
    OrderStage.COMPLETED: OrderStage.COMPLETED,
}


def _states(hidden_until, editable_through):
    stages = list(OrderStage.VALUES)
    visible_at = stages.index(hidden_until)
    editable_to = stages.index(editable_through)
    return {
        stage: (
            ModuleState.HIDDEN
            if index < visible_at
            else ModuleState.EDITABLE
            if index <= editable_to
            else ModuleState.READ_ONLY
        )
        for index, stage in enumerate(stages)
    }


MODULE_POLICIES = {
    OrderModule.COMMERCIAL_CORE: _states(OrderStage.NEW, OrderStage.NEW),
    OrderModule.PI_ITEMS: _states(OrderStage.NEW, OrderStage.NEW),
    OrderModule.PAYMENT_PLAN: _states(OrderStage.NEW, OrderStage.PRE_SHIPMENT),
    OrderModule.PAYMENT_RECEIPTS: _states(OrderStage.SHIPPED, OrderStage.ARRIVED),
    OrderModule.INITIAL_SHIPMENT_PLAN: _states(OrderStage.NEW, OrderStage.PRE_SHIPMENT),
    OrderModule.DOCUMENT_REQUIREMENTS: _states(OrderStage.NEW, OrderStage.PRE_SHIPMENT),
    OrderModule.SHIPPING_PREPARATION: _states(OrderStage.PRE_SHIPMENT, OrderStage.PRE_SHIPMENT),
    OrderModule.DRIVER_INFO: _states(OrderStage.PRE_SHIPMENT, OrderStage.PRE_SHIPMENT),
    OrderModule.FREIGHT_REQUIREMENTS: _states(OrderStage.PRE_SHIPMENT, OrderStage.PRE_SHIPMENT),
    OrderModule.POST_SHIPMENT: _states(OrderStage.SHIPPED, OrderStage.ARRIVED),
    OrderModule.FREIGHT_SETTLEMENT: _states(OrderStage.SHIPPED, OrderStage.ARRIVED),
    OrderModule.ACTUAL_DEPARTURE: _states(OrderStage.SHIPPED, OrderStage.SHIPPED),
    OrderModule.ACTUAL_ARRIVAL: _states(OrderStage.SHIPPED, OrderStage.ARRIVED),
    OrderModule.FINAL_SETTLEMENT: _states(OrderStage.ARRIVED, OrderStage.ARRIVED),
    OrderModule.COMMISSION_SETTLEMENT: _states(OrderStage.NEW, OrderStage.ARRIVED),
}


MODULE_FIELDS = {
    OrderModule.COMMERCIAL_CORE: {
        "pi_no", "pi_date", "customer", "customer_id", "exporter", "exporter_id",
        "payment_terms", "loading_port", "destination_port", "bank", "note",
        "commission_factory_id", "commission_amount", "commission_rate",
        "commission_currency", "commission_amount_mode", "commission_override_reason",
    },
    OrderModule.PI_ITEMS: {"product_", "factory_", "trade_term_", "unit_price_", "quantity_"},
    OrderModule.PAYMENT_PLAN: {
        "currency", "advance_payment_percent", "advance_payment_amount", "balance_payment_amount",
    },
    OrderModule.PAYMENT_RECEIPTS: {
        "advance_received_amount", "advance_received_at", "balance_received_amount",
        "balance_received_at", "payment_received",
    },
    OrderModule.INITIAL_SHIPMENT_PLAN: {"planned_shipment_date", "loading_port", "destination_port"},
    OrderModule.DOCUMENT_REQUIREMENTS: {
        "coo_required", "apta_required", "export_license_required", "customs_docs_required",
        "coc_required", "coa_required", "original_bl_required", "obd_electronic_required",
        "insurance_original_required", "insurance_electronic_required",
        "original_documents_mail_required", "telex_release_required", "other_document_notes",
        "settlement_documents_required",
    },
    OrderModule.SHIPPING_PREPARATION: {
        "freight_forwarder_id", "container_loading_at", "container_loading_date", "container_loading_period",
        "container_location", "etd", "eta", "vessel_info", "booking_number",
        "shipping_mark", "freight_term", "contract_number",
        "freight_clause", "waybill_option", "container_type", "container_count",
        "package_count", "package_unit", "gross_weight", "gross_weight_display_unit", "volume",
        "product_hs_code_snapshot_",
        "notify_party_same_as_consignee", "notify_party_name_snapshot", "notify_party_address_snapshot",
        "notify_party_tax_code_snapshot",
    },
    OrderModule.DRIVER_INFO: {"driver_name", "driver_phone", "vehicle_number"},
    OrderModule.FREIGHT_REQUIREMENTS: {"usd_bill_required", "cny_bill_required", "quote_id",
                                       "agreement_amount", "agreement_currency", "agreement_note"},
    OrderModule.POST_SHIPMENT: {
        "bill_of_lading_number", "shipping_company", "booking_number", "shipping_mark",
        "container_number", "seal_number", "vgm", "vgm_display_unit",
    },
    OrderModule.FREIGHT_SETTLEMENT: {
        "usd_bill_amount", "usd_bill_confirmed", "cny_bill_amount",
        "cny_bill_confirmed", "invoice_issued", "payment_status", "paid_at",
        # V1 form aliases remain guarded while V1 pages are still importable.
        "freight_usd_amount", "freight_usd_confirmed", "freight_cny_amount",
        "freight_cny_confirmed", "freight_invoice_issued", "freight_payment_status", "freight_paid_at",
    },
    OrderModule.ACTUAL_DEPARTURE: {"actual_departure_date"},
    OrderModule.ACTUAL_ARRIVAL: {"actual_arrival_date"},
    OrderModule.FINAL_SETTLEMENT: set(),
    OrderModule.COMMISSION_SETTLEMENT: {"commission_status"},
}


class LifecyclePolicyError(ValueError):
    pass


def get_order_lifecycle(order_or_status):
    status = getattr(order_or_status, "status", order_or_status)
    try:
        return STATUS_TO_STAGE[status]
    except KeyError as exc:
        raise LifecyclePolicyError(f"Unsupported order status: {status!r}") from exc


@dataclass(frozen=True)
class LifecycleContext:
    stage: str
    correction_modules: frozenset = frozenset()

    @property
    def is_completed(self):
        return self.stage == OrderStage.COMPLETED

    def state(self, module):
        if module == "FREIGHT_AMOUNTS":
            module = OrderModule.FREIGHT_SETTLEMENT
        return MODULE_POLICIES[module][self.stage]

    def can_view(self, module):
        return self.state(module) != ModuleState.HIDDEN

    def can_edit(self, module):
        return self.state(module) == ModuleState.EDITABLE or (
            self.is_completed and module in self.correction_modules
        )


def lifecycle_context(order_or_status, *, correction_modules=()):
    return LifecycleContext(
        get_order_lifecycle(order_or_status), frozenset(correction_modules)
    )


def _field_is_present(form, token):
    if token.endswith("_"):
        return any(key.startswith(token) for key in form.keys())
    return token in form


def validate_lifecycle_submission(order_or_status, form, *, operation="edit"):
    """Reject non-empty fields submitted outside the lifecycle write policy.

    Status-transition forms are allowed to populate operational modules up to
    the destination stage. Completed orders remain immutable through ordinary
    create/edit endpoints.
    """
    context = lifecycle_context(order_or_status)
    if context.is_completed and operation != "status_transition":
        raise LifecyclePolicyError(
            "Completed orders are read-only. Use Reopen for Correction when available."
        )

    transition_allowed = set()
    if operation == "status_transition":
        stage_index = OrderStage.VALUES.index(context.stage)
        for module, policy in MODULE_POLICIES.items():
            if policy[context.stage] != ModuleState.HIDDEN and module != OrderModule.COMMERCIAL_CORE:
                transition_allowed.add(module)
        if stage_index == OrderStage.VALUES.index(OrderStage.COMPLETED):
            transition_allowed.add(OrderModule.FINAL_SETTLEMENT)

    allowed_tokens = set()
    for module, fields in MODULE_FIELDS.items():
        if context.can_edit(module) or module in transition_allowed:
            allowed_tokens.update(fields)

    violations = []
    for module, fields in MODULE_FIELDS.items():
        allowed = context.can_edit(module) or module in transition_allowed
        if allowed:
            continue
        for field in fields:
            if not _field_is_present(form, field):
                continue
            # Some facts legitimately participate in more than one module
            # across the lifecycle (e.g. shipping_mark and booking_number).
            # A current-stage editable module authorizes the field; a later
            # module must not veto that valid submission.
            if any(
                _field_is_present(form, token)
                and (token == field or token.endswith("_") and field.startswith(token))
                for token in allowed_tokens
            ):
                continue
            if field.endswith("_") or str(form.get(field, "")).strip():
                violations.append(field)
    if violations:
        names = ", ".join(sorted(violations)[:8])
        raise LifecyclePolicyError(
            f"Fields are not editable in lifecycle {context.stage}: {names}"
        )
    return context
