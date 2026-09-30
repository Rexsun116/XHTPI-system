"""Shared V2 visual-foundation and shell-navigation contracts."""
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from tests import test_v2_new_order_edit as fixture
from v2.fx_reference import FXReference
from v2.models import BankAccount, FreightForwarder, db

ROOT = Path(__file__).parents[1]


class SharedV2DesignSystemTest(TestCase):
    def setUp(self):
        self.css = (ROOT / "v2/static/v2_design_system.css").read_text()
        self.operations_css = (ROOT / "v2/static/v2_operations_dashboard.css").read_text()
        self.base = (ROOT / "v2/templates/v2/base.html").read_text()
        self.dashboard = (ROOT / "v2/templates/v2/dashboard.html").read_text()
        self.new_form = (ROOT / "v2/templates/v2/order_form.html").read_text()
        self.order_detail = (ROOT / "v2/templates/v2/order_view.html").read_text()
        self.order_detail_css = (ROOT / "v2/static/v2_order_detail.css").read_text()
        self.edit_form = (ROOT / "v2/templates/v2/order_edit.html").read_text()
        self.linked_export_form = (ROOT / "v2/templates/v2/create_linked_export_order.html").read_text()
        self.departure = (ROOT / "v2/templates/v2/enter_shipped.html").read_text()
        self.arrival = (ROOT / "v2/templates/v2/enter_arrived.html").read_text()
        self.completion = (ROOT / "v2/templates/v2/enter_completed.html").read_text()
        self.completion_check = (ROOT / "v2/templates/v2/_completion_check.html").read_text()
        self.secondary = (ROOT / "v2/templates/v2/_order_secondary.html").read_text()
        self.history = (ROOT / "v2/templates/v2/task_history.html").read_text()
        self.shipping = (ROOT / "v2/templates/v2/_order_shipping.html").read_text()
        self.finance = (ROOT / "v2/templates/v2/_order_finance.html").read_text()
        self.detail_js = (ROOT / "v2/static/v2_batch1.js").read_text()

    def test_business_first_foundation_tokens_and_shell_contract_are_present(self):
        for token in (
            "--v2-color-canvas:#f5f7fb", "--v2-color-surface:#fff",
            "--v2-color-text:#15253b", "--v2-color-muted:#62728a",
            "--v2-color-primary:#153b64", "--v2-color-primary-strong:#1d5c8e",
            "--v2-color-accent:#159a86", "--v2-color-success",
            "--v2-color-warning", "--v2-color-danger", "--v2-color-info",
            "--v2-color-analytics-purple", "--v2-radius-sm:9px",
            "--v2-radius-md:12px", "--v2-radius-lg:16px",
            "--v2-shadow-card", "--v2-control-height:40px",
            "--v2-focus-ring", "--v2-transition-fast:160ms ease",
            "--v2-shell-rail-width:74px", "--v2-shell-bg:#122c4d",
            "--v2-shell-bg-active:#1d5682", "--v2-shell-accent:#64c2c4",
            "width:var(--v2-shell-width)", "padding:28px 32px 46px",
            ".v2-shell-link.is-active", ".v2-shell-account", ".v2-card",
            ".v2-card--compact", ".v2-card--hero", ".v2-inset-panel",
            ".v2-card--danger", ".v2-btn", ".v2-btn--primary",
            ".v2-btn--secondary", ".v2-btn--ghost", ".v2-btn--subtle",
            ".v2-btn--success", ".v2-btn--danger", ".v2-btn--text",
            ".v2-btn--icon", ".v2-form-choice", ".v2-table-wrap",
            ".v2-table--compact", ".v2-table-number", ".v2-badge--neutral",
            ".v2-badge--success", ".v2-badge--warning", ".v2-badge--danger",
            ".v2-badge--info", ".v2-badge--analytics", ".v2-page-header__title",
        ):
            self.assertIn(token, self.css)

    def test_long_form_primitives_use_foundation_tokens_without_removing_v35_aliases(self):
        long_form_css = self.css.split("/* Shared long-form workspace primitives.", 1)[1]
        for token in (
            "var(--v2-color-border)", "var(--v2-color-surface-soft)",
            "var(--v2-color-text)", "var(--v2-color-muted)",
            "var(--v2-color-danger)", "var(--v2-color-accent)",
        ):
            self.assertIn(token, long_form_css)
        self.assertNotIn("var(--v35-", long_form_css)
        self.assertNotIn("--v35-", self.css)
        self.assertNotIn("var(--v35-", self.order_detail_css)

    def test_base_loads_foundation_but_defaults_to_legacy_scope(self):
        self.assertIn("v2_design_system.css", self.base)
        self.assertIn("{% block body_class %}v2-legacy{% endblock %}", self.base)
        self.assertIn("font-awesome/6.5.2/css/all.min.css", self.base)

    def test_modern_pages_have_a_semantic_grouped_shell_without_account_claims_yet(self):
        for token in (
            '<aside class="side v2-shell" aria-label="XHTPI navigation">',
            '<nav class="v2-shell-nav-group" aria-label="Workspace">',
            '<nav class="v2-shell-nav-group" aria-label="Orders">',
            '<nav class="v2-shell-nav-group" aria-label="Master Data">',
            'class="v2-shell-link-label"', 'class="v2-shell-account" aria-label="Signed in as',
            'id="v2-main-content"',
        ):
            self.assertIn(token, self.base)
        self.assertNotIn("V2 LOCAL TRIAL</span>", self.base.split("{% else %}", 1)[0])
        self.assertNotIn("Logout", self.base)

    def test_shell_navigation_uses_server_route_context_and_inline_svg_icons(self):
        for token in (
            "current_endpoint = request.endpoint or ''",
            "operations_active = current_endpoint == 'v2.dashboard'",
            "business_active = current_endpoint == 'v2.business_overview'",
            "new_order_active = current_endpoint == 'v2.order_new'",
            "current_endpoint in ('v2.master_list', 'v2.master_edit')",
            'aria-current="page"', "current_user.username", "account_username[:1]|upper",
        ):
            self.assertIn(token, self.base)
        modern_shell = self.base.split("{% if 'v2-modern' in page_body_class %}", 1)[1].split("{% else %}", 1)[0]
        self.assertIn('<svg viewBox="0 0 24 24"', self.base)
        self.assertNotIn('fa-', modern_shell)
        self.assertIn('.v2-shell-link.is-active,body.v2-modern .v2-shell-link.is-active:hover', self.css)
        self.assertIn('box-shadow:inset 0 -3px 0 var(--v2-shell-accent)', self.css)

    def test_sidebar_defers_freight_quotes_management_without_affecting_order_freight(self):
        self.assertNotIn("url_for('v2.quotes')", self.base)
        self.assertNotIn("Freight Quotes", self.base)
        self.assertIn('name="quote_id"', self.finance)
        self.assertIn('Accept Freight Quote', self.finance)

    def test_dashboard_and_order_detail_opt_into_shared_foundation(self):
        for page in (self.dashboard, self.order_detail):
            self.assertIn("{% block body_class %}v2-modern{% endblock %}", page)
            self.assertNotIn("body:has(", page)
            self.assertNotIn("@import url('https://cdnjs.cloudflare.com/ajax/libs/font-awesome", page)
        self.assertNotIn("--v35-primary:#14b8a6", self.dashboard)
        self.assertNotIn(":root{--v35-primary", self.order_detail)

    def test_operations_dashboard_uses_the_static_business_first_presentation_contract(self):
        for token in (
            "v2_operations_dashboard.css", "v2-page-header", "v2-page-header__title",
            "v2-card v2-card--compact", "v2-table-wrap", "v2-table--compact",
            "v2-badge v2-badge--neutral", "v2-btn", "Action Center",
            "v35-action-column {{ key|lower }}", "Active Orders Control Board",
            "applyCollapsedState", "limits={exception:3,action:5,waiting:4}",
        ):
            self.assertIn(token, self.dashboard)
        self.assertNotIn("{% block head_extra %}<style>", self.dashboard)
        for token in (
            "var(--v2-color-canvas)", "var(--v2-color-danger)",
            "var(--v2-color-warning)", "var(--v2-color-info)",
            "var(--v2-color-success)", "var(--v2-table-header-bg)",
            "var(--v2-table-row-hover)", ".v35-action-center",
            ".v35-action-column.exception", ".v35-action-column.action",
            ".v35-action-column.waiting", ".v35-group.done",
            ".v35-button.primary,.v35-small-button.primary",
            ".v35-small-button.warning", ".v35-small-button.danger",
            ".v35-summary-card.active", "var(--v2-color-primary-soft)",
            ".v35-board{box-sizing:border-box;width:100%;min-width:0;max-width:100%;overflow-x:auto",
        ):
            self.assertIn(token, self.operations_css)
        self.assertNotIn("var(--v35-", self.operations_css)
        self.assertNotIn("v2_business_dashboard.css", self.dashboard)

    def test_normal_edit_page_uses_the_shared_modern_form_shell(self):
        self.assertIn("{% block body_class %}v2-modern{% endblock %}", self.edit_form)
        self.assertIn('class="v2-new-order-form v2-order-workspace v2-order-workspace--edit"', self.edit_form)
        self.assertIn('class="v2-route-grid"', self.edit_form)
        self.assertIn('data-country-combobox', self.edit_form)
        self.assertIn('v2_country_combobox.js', self.edit_form)
        self.assertNotIn('class="card p-4 mb-3"', self.edit_form)

    def test_order_creation_workspaces_share_business_first_form_presentation_without_contract_loss(self):
        for page in (self.new_form, self.edit_form, self.linked_export_form):
            for token in ("v2-form-page-header v2-page-header", "v2-page-header__title",
                          "v2-page-header__description", "v2-new-order-form v2-order-workspace",
                          'name="csrf_token"'):
                self.assertIn(token, page)
        for token in (
            ".v2-order-workspace{display:grid;gap:18px;max-width:1200px}",
            ".v2-order-workspace>.v2-panel", ".v2-order-workspace .form-control",
            ".v2-order-workspace .v2-item-row", ".v2-order-workspace .v2-form-actions",
            ".v2-order-workspace .v2-form-actions>div{display:flex;align-items:center;justify-content:flex-end;gap:14px;flex-wrap:wrap}",
            ".v2-order-workspace .v2-readonly-facts", ".v2-order-workspace .v2-country-options",
        ):
            self.assertIn(token, self.css)
        for token in ("data-country-combobox", "data-country-code", "data-country-listbox",
                      "id=\"items\"", "id=\"add-item\""):
            self.assertIn(token, self.new_form)
        self.assertIn("id=\"linked-export-source-context\"", self.linked_export_form)
        for token in ("v2-order-edit-header", "v2-order-workspace--edit", "v2-order-save-action"):
            self.assertIn(token, self.edit_form)

    def test_linked_export_form_reuses_the_modern_shell_and_semantic_context_primitives(self):
        for token in ("{% block body_class %}v2-modern{% endblock %}", "v2-form-page-header",
                      "v2-form-error", "v2-panel", "v2-form-grid", "v2-item-row",
                      "v2-requirements-grid", "v2-form-actions", "v2-readonly-facts",
                      "linked-export-source-context"):
            self.assertIn(token, self.linked_export_form)
        for forbidden in ("card p-4", "loading_country_name", "add-item", "remove-item"):
            self.assertNotIn(forbidden, self.linked_export_form)
        self.assertIn(".v2-readonly-facts", self.css)

    def test_lifecycle_confirmation_pages_use_shared_modern_form_contracts(self):
        for page in (self.departure, self.arrival):
            self.assertIn("{% block body_class %}v2-modern v2-tail-workspace{% endblock %}", page)
            self.assertIn('class="v2-new-order-form"', page)
            self.assertIn('name="csrf_token"', page)
            self.assertIn("v2-lifecycle-transition", page)
            self.assertNotIn("max-width:560px", page)
        for token in ('name="actual_departure_date" required', 'name="eta"',
                      'name="shipping_company"', 'name="bill_of_lading_number"',
                      'Required export documents will be verified when departure is confirmed.'):
            self.assertIn(token, self.departure)
        self.assertIn('name="actual_arrival_date" required', self.arrival)
        for token in (".v2-lifecycle-transition", ".v2-linked-transition", "overflow-wrap:anywhere"):
            self.assertIn(token, self.css)

    def test_completion_and_task_history_use_shared_modern_semantic_primitives(self):
        for page in (self.completion, self.history):
            self.assertIn("{% block body_class %}v2-modern v2-tail-workspace{% endblock %}", page)
            self.assertIn("v2-form-page-header", page)
            self.assertIn("v2-panel", page)
        for token in ("v2-completion-readiness", "v2-readiness-group", "v2-lifecycle-transition",
                      "v2-completion-consequence", "v2-completion-actions"):
            self.assertIn(token, self.completion + self.completion_check)
        self.assertNotIn('id="completion-check"', self.completion_check)
        self.assertNotIn("<form", self.completion_check)
        for token in ("Review &amp; Complete", "v2-button v2-button--primary", "v2-status-badge",
                      "order-lifecycle-action"):
            self.assertIn(token, self.secondary)
        self.assertNotIn("确认订单完成", self.secondary)
        self.assertNotIn("btn btn-success mt-3", self.secondary)
        for token in ("v2-history-timeline", "v2-history-event", "v2-empty-state", "datetime="):
            self.assertIn(token, self.history)
        for token in (".v2-completion-readiness", ".v2-completion-consequence", ".v2-completion-actions",
                      ".v2-history-timeline", "overflow-wrap:anywhere"):
            self.assertIn(token, self.css)
        self.assertIn(".v2-new-order-form:not(.v2-completion-action-form) .v2-button--primary", self.css)
        self.assertNotIn(".v2-completion-actions .v2-button--primary{border-color:var(--v2-color-border-strong)", self.css)
        self.assertIn(".v2-completion-note-field", self.css)
        self.assertIn('name="completion_note"', self.completion)
        self.assertIn(".order-lifecycle-action .v2-button--primary,.order-lifecycle-action .v2-button--primary:hover{color:#fff}", self.order_detail_css)
        self.assertIn("body.v2-tail-workspace .v2-correction-actions .v2-button--primary{border-color:var(--v2-color-warning-border);background:var(--v2-color-warning-soft);color:var(--v2-color-warning)}", self.css)

    def test_tail_workspaces_use_scoped_business_first_presentation_without_contract_changes(self):
        tail_pages = (
            "enter_shipped.html", "enter_arrived.html", "enter_completed.html", "correction.html",
            "task_history.html", "document_requirements_editor.html", "booking_hs_codes.html",
            "order_delete.html", "linked_trade_delete.html",
        )
        for name in tail_pages:
            page = (ROOT / "v2/templates/v2" / name).read_text()
            self.assertIn("{% block body_class %}v2-modern v2-tail-workspace{% endblock %}", page)
        for token in (
            "body.v2-tail-workspace .v2-form-page-header", "body.v2-tail-workspace .v2-new-order-form:not(.v2-completion-action-form) .v2-button--primary",
            "body.v2-tail-workspace .v2-correction-context", "body.v2-tail-workspace .v2-delete-context",
            "body.v2-tail-workspace .v2-history-event::before",
        ):
            self.assertIn(token, self.css)

    def test_order_detail_scoped_saves_have_stable_return_targets_and_departure_entry(self):
        for token in (
            'name="return_to" value="shipping-plan"',
            'name="return_to" value="shipping-schedule"',
            'name="return_to" value="shipping-cargo"',
            'name="return_to" value="shipping-booking"',
            'name="return_to" value="shipping-loading"',
            'name="return_to" value="shipping-route"',
        ):
            self.assertIn(token, self.shipping)
        for token in ('name="return_to" value="customer-payment"',
                      'name="return_to" value="freight-agreement"',
                      'name="return_to" value="freight-settlement"'):
            self.assertIn(token, self.finance)
        schedule = self.shipping.split('id="shipping-plan"', 1)[1].split('</details>', 1)[0]
        self.assertIn('data-actual-departure-entry', schedule)
        self.assertIn("url_for('v2.enter_shipped'", schedule)
        self.assertIn('btn btn-sm btn-primary', schedule)
        self.assertNotIn('btn btn-sm btn-warning', schedule)
        self.assertIn('.order-lifecycle-action', self.order_detail_css)

    def test_order_detail_save_state_restores_prg_editors_and_limits_inplace_save_to_cargo(self):
        for token in (
            'id="shipment-preparation-editor"', 'id="shipping-schedule"',
            'id="booking-cargo-information"', 'id="shipping-booking-editor"',
            'id="shipping-loading-editor"', 'id="shipping-route-editor"',
        ):
            self.assertIn(token, self.shipping)
        for token in (
            'data-v2-prg-editor="shipment-preparation"',
            'data-v2-prg-editor="shipping-cargo"',
            'data-v2-prg-editor="shipping-booking"',
            'data-v2-prg-editor="shipping-loading"',
            'data-v2-prg-editor="shipping-route"',
            'data-v2-prg-editor="shipping-schedule"',
        ):
            self.assertIn(token, self.shipping)
        for token in (
            'data-v2-prg-editor="customer-payment"',
            'data-v2-prg-editor="freight-agreement"',
            'data-v2-prg-editor="freight-settlement"',
        ):
            self.assertIn(token, self.finance)
        for token in (
            'window.__v2OrderDetailEditors = editors',
            'xhtpi:v2:order-detail:restore:',
            'state.version === 1 && state.editor === editor',
            'window.sessionStorage.removeItem(storageKey)',
            'url.searchParams.delete("open_editor")',
            'window.history.scrollRestoration = "manual"',
            'const editorGroups = new Map(Object.entries(window.__v2OrderDetailEditors || {}));',
            'const capturePrgRestore = (editorId) => {',
            'viewportOffset: Math.round(target.getBoundingClientRect().top)',
            'createdAt: Date.now()',
            'document.getElementById("shipment-preparation")?.addEventListener("submit"',
            'document.querySelectorAll("form[data-v2-prg-editor]")',
            'closeOtherShippingGroups(group)',
            'window.scrollTo({',
            'behavior: "auto"',
            'window.history.scrollRestoration = "auto"',
        ):
            self.assertIn(token, self.order_detail + self.detail_js)
        self.assertNotIn('document.querySelector(openEditor)', self.detail_js)
        self.assertNotIn('requestAnimationFrame', self.detail_js)
        self.assertEqual(self.shipping.count('data-v2-inplace-save'), 1)
        self.assertIn('data-v2-inplace-scope="CARGO"', self.shipping)
        self.assertIn('data-v2-save-status', self.shipping)
        for page in (self.departure, self.arrival, self.finance):
            self.assertNotIn('data-v2-inplace-save', page)
        for page in (self.departure, self.arrival):
            self.assertNotIn('data-v2-prg-editor', page)
        for token in (
            'document.querySelectorAll("form[data-v2-inplace-save]")',
            'if (form.dataset.v2InplaceScope !== "CARGO") return;',
            'new FormData(form, event.submitter)',
            'window.fetch(form.action',
            'if (!response.ok || !payload.ok)',
            'form.dataset.v2Saving === "true"',
            '}, 1600);',
        ):
            self.assertIn(token, self.detail_js)
        self.assertNotIn('window.location.reload', self.detail_js)
        self.assertIn('.order-save-feedback{position:fixed', self.order_detail_css)
        self.assertIn('role="status" aria-live="polite"', self.base)
        self.assertIn('feedback.classList.add("is-dismissing")', self.detail_js)
        self.assertIn('.v2-save-status', self.css)
        restore_script = self.order_detail.split('<script>', 1)[1].split('</script>', 1)[0]
        self.assertNotIn('FormData', restore_script)
        self.assertNotIn('form.elements', restore_script)

    def test_order_detail_uses_its_dedicated_business_first_stylesheet_without_contract_changes(self):
        self.assertIn("filename='v2_order_detail.css'", self.order_detail)
        self.assertNotIn('{% block head_extra %}<style>', self.order_detail)
        for token in (
            '.order-hero,.order-stage-panel,.order-panel', '.order-nav',
            '.order-shell .btn-primary,.order-shell .btn-outline-primary',
            '.order-status-badge.is-pre_shipment,.order-status-badge.is-shipped',
            '.order-correction', '.order-file-download', '.order-shipping-group',
        ):
            self.assertIn(token, self.order_detail_css)
        for token in (
            'id="order-identity"', 'id="order-lifecycle"', 'class="order-nav"',
            'id="generated-files"', 'id="tasks"', 'id="activity"',
            "url_for('v2.document'", "{% include 'v2/_order_current_action.html' %}",
            "{% include 'v2/_order_shipping.html' %}",
        ):
            self.assertIn(token, self.order_detail)

    def test_shipping_accordion_compensates_only_for_a_closed_open_peer(self):
        accordion = self.detail_js.split('shippingGroups.forEach((group) => {', 1)[1].split(
            'cargoEdit?.addEventListener', 1)[0]
        for token in (
            'const closesOpenPeer = shippingGroups.some((peer) => peer !== group && peer.open);',
            'const beforeTop = closesOpenPeer ? group.getBoundingClientRect().top : null;',
            'closeOtherShippingGroups(group);',
            'const delta = group.getBoundingClientRect().top - beforeTop;',
            'if (delta) window.scrollBy({ top: delta, left: 0, behavior: "auto" });',
        ):
            self.assertIn(token, accordion)
        self.assertIn('if (beforeTop !== null)', accordion)
        self.assertNotIn('smooth', accordion)
        self.assertNotIn('.focus(', accordion)
        self.assertLess(self.detail_js.index('window.scrollBy({ top: delta'),
                        self.detail_js.index('const editorGroups ='))

    def test_modern_main_uses_shell_tokens_with_compact_and_mobile_transitions(self):
        self.assertIn('width:calc(100% - var(--v2-shell-width));min-width:0', self.css)
        self.assertIn('@media(max-width:1060px)', self.css)
        self.assertIn('width:calc(100% - var(--v2-shell-rail-width));margin-left:var(--v2-shell-rail-width)', self.css)
        self.assertIn('@media(max-width:700px)', self.css)
        self.assertIn('body.v2-modern .v2-shell-nav-area{display:flex;align-items:center;gap:2px;min-width:0;overflow-x:auto', self.css)
        self.assertIn('body.v2-modern .main{width:100%;max-width:100%;margin-left:0', self.css)
        self.assertNotIn('hamburger', self.base.lower() + self.css.lower())

    def test_dashboard_reflow_tracks_cannot_exceed_the_modern_main(self):
        for token in (
            'grid-template-columns:minmax(0,1.2fr) repeat(4,minmax(0,1fr))',
            'grid-template-columns:repeat(3,minmax(0,1fr))',
            'grid-template-columns:repeat(2,minmax(0,1fr))',
            '.v35-board{box-sizing:border-box;width:100%;min-width:0;max-width:100%;overflow-x:auto;overflow-y:hidden}',
        ):
            self.assertIn(token, self.operations_css)

    def test_dashboard_task_entries_shrink_and_wrap_within_their_column(self):
        for token in (
            '.v35-task-card{box-sizing:border-box;min-width:0;max-width:100%',
            '.v35-task-card>*{min-width:0;max-width:100%}',
            '.v35-task-title,.v35-meta,.v35-fact,.v35-waiting,.v35-warning{overflow-wrap:anywhere}',
            '.v35-task-actions details,.v35-task-actions form{min-width:0;max-width:100%',
            'width:min(230px,100%);min-width:0;max-width:100%',
        ):
            self.assertIn(token, self.operations_css)
        self.assertNotIn('WU1333', self.dashboard)

    def test_dashboard_keeps_wide_tables_local_and_main_content_out_of_sidebar(self):
        self.assertIn('z-index:10', self.css)
        for token in (
            '.v35-dashboard{min-width:0;max-width:100%',
            '.v35-top>*{min-width:0}',
            '.v35-toolbar{display:flex;align-items:center;min-width:0;gap:8px;flex-wrap:wrap;justify-content:flex-end}',
            '@media(max-width:900px){.v35-top{display:block}',
            '.v35-board{box-sizing:border-box;width:100%;min-width:0;max-width:100%;overflow-x:auto;overflow-y:hidden}',
        ):
            self.assertIn(token, self.operations_css)
        combined = self.css + self.dashboard + self.operations_css
        self.assertNotIn('html,body{overflow-x:hidden', combined)
        self.assertNotIn('body{overflow-x:hidden', combined)

    def test_country_combobox_shared_css_is_positioned_and_narrow_safe(self):
        self.assertIn('.v2-route-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}', self.css)
        self.assertIn('.v2-route-grid>.v2-form-field{grid-column:auto}', self.css)
        self.assertIn('.v2-country-combobox{position:relative;min-width:0}', self.css)
        self.assertIn('.v2-country-options{position:absolute;z-index:20', self.css)
        self.assertIn('max-height:220px', self.css)
        self.assertIn('overflow-y:auto', self.css)
        self.assertIn('.v2-route-grid{grid-template-columns:1fr}', self.css)
        self.assertNotIn('html,body{overflow-x:hidden', self.css)


class SharedShellNavigationRenderTest(TestCase):
    setUp = fixture.NewOrderEditTest.setUp
    tearDown = fixture.NewOrderEditTest.tearDown
    client = fixture.NewOrderEditTest.client
    form = fixture.NewOrderEditTest.form
    order = fixture.NewOrderEditTest.order

    @staticmethod
    def _nav_link(html, label):
        label_at = html.index(f'aria-label="{label}"', html.index('<aside class="side v2-shell"'))
        return html[html.rfind('<a ', 0, label_at):html.index('>', label_at) + 1]

    def _assert_active(self, html, label, expected):
        link = self._nav_link(html, label)
        self.assertEqual('is-active' in link, expected, link)
        self.assertEqual('aria-current="page"' in link, expected, link)

    def _business_page(self):
        reference = FXReference(
            base='USD', quote='CNY', rate=Decimal('7.1'), as_of=date(2026, 9, 20),
            fetched_at=datetime(2026, 9, 20, tzinfo=timezone.utc), source='test',
            stale=False, available=True,
        )
        with patch('v2.management_dashboard.fx_reference_service.current_usd_cny', return_value=reference):
            response = self.client().get('/v2/business')
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True)

    def test_workspace_new_order_and_order_context_active_states_are_exact(self):
        dashboard = self.client().get('/v2/').get_data(as_text=True)
        self._assert_active(dashboard, 'Operations Dashboard', True)
        self._assert_active(dashboard, 'Business Overview', False)
        self._assert_active(dashboard, 'New Order', False)

        business = self._business_page()
        self._assert_active(business, 'Operations Dashboard', False)
        self._assert_active(business, 'Business Overview', True)
        self._assert_active(business, 'New Order', False)

        new_order = self.client().get('/v2/orders/new').get_data(as_text=True)
        self._assert_active(new_order, 'New Order', True)
        pi = self.order('SHELL-NAV')
        for page in (
            self.client().get(f'/v2/orders/{pi.id}').get_data(as_text=True),
            self.client().get(f'/v2/orders/{pi.id}/edit').get_data(as_text=True),
        ):
            self._assert_active(page, 'New Order', False)

    def test_every_master_list_and_edit_marks_only_its_own_navigation_entry_current(self):
        forwarder = FreightForwarder(code='FF-SHELL', name='Shell Forwarder')
        bank = BankAccount(code='BNK-SHELL', name='Shell Bank', beneficiary_name='Shell Beneficiary',
                           bank_name='Shell Bank', account_number='123', currency='USD')
        db.session.add_all((forwarder, bank)); db.session.commit()
        rows = {
            'customers': self.customer,
            'exporters': self.exporter,
            'factories': self.factory,
            'forwarders': forwarder,
            'products': self.product,
            'banks': bank,
        }
        labels = {
            'customers': 'Customers', 'exporters': 'Exporters', 'factories': 'Factories',
            'forwarders': 'Freight Forwarders', 'products': 'Products', 'banks': 'Bank Accounts',
        }
        for kind, row in rows.items():
            for url in (f'/v2/master/{kind}', f'/v2/master/{kind}/{row.id}/edit'):
                with self.subTest(kind=kind, url=url):
                    response = self.client().get(url)
                    self.assertEqual(response.status_code, 200)
                    page = response.get_data(as_text=True)
                    for sibling_kind, label in labels.items():
                        self._assert_active(page, label, sibling_kind == kind)

    def test_account_uses_authenticated_username_and_has_no_role_or_logout_claim(self):
        page = self.client().get('/v2/').get_data(as_text=True)
        self.assertIn('aria-label="Signed in as create"', page)
        self.assertIn('<span class="v2-shell-avatar" aria-hidden="true">C</span>', page)
        self.assertIn('<strong>create</strong><span>Signed in</span>', page)
        self.assertNotIn('Logout', page)
        self.assertNotIn('Role', page)
        self.assertNotIn('Freight Quotes', page)
