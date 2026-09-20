"""P2.1 shared V2 visual-foundation source contracts."""
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).parents[1]


class SharedV2DesignSystemTest(TestCase):
    def setUp(self):
        self.css = (ROOT / "v2/static/v2_design_system.css").read_text()
        self.base = (ROOT / "v2/templates/v2/base.html").read_text()
        self.dashboard = (ROOT / "v2/templates/v2/dashboard.html").read_text()
        self.order_detail = (ROOT / "v2/templates/v2/order_view.html").read_text()
        self.edit_form = (ROOT / "v2/templates/v2/order_edit.html").read_text()
        self.linked_export_form = (ROOT / "v2/templates/v2/create_linked_export_order.html").read_text()

    def test_authoritative_tokens_and_approved_shell_are_shared(self):
        for token in (
            "--v35-primary:#14b8a6", "--v35-border:#e2e9ed",
            "--v35-soft:#f8fbfc", "--v35-ink:#172033",
            "--v35-muted:#667085", "width:245px",
            "background:#fbfdfd", "padding:28px 32px 46px",
            "font-size:16px", "font-weight:400", "line-height:normal",
            ".v2-summary-card", ".v2-form-control", ".v2-table",
            ".v2-status--exception", ".v2-status--done",
        ):
            self.assertIn(token, self.css)

    def test_base_loads_foundation_but_defaults_to_legacy_scope(self):
        self.assertIn("v2_design_system.css", self.base)
        self.assertIn("{% block body_class %}v2-legacy{% endblock %}", self.base)
        self.assertIn("font-awesome/6.5.2/css/all.min.css", self.base)

    def test_dashboard_and_order_detail_opt_into_shared_foundation(self):
        for page in (self.dashboard, self.order_detail):
            self.assertIn("{% block body_class %}v2-modern{% endblock %}", page)
            self.assertNotIn("body:has(", page)
            self.assertNotIn("@import url('https://cdnjs.cloudflare.com/ajax/libs/font-awesome", page)
        self.assertNotIn("--v35-primary:#14b8a6", self.dashboard)
        self.assertNotIn(":root{--v35-primary", self.order_detail)

    def test_normal_edit_page_uses_the_shared_modern_form_shell(self):
        self.assertIn("{% block body_class %}v2-modern{% endblock %}", self.edit_form)
        self.assertIn('class="v2-new-order-form"', self.edit_form)
        self.assertIn('class="v2-route-grid"', self.edit_form)
        self.assertIn('data-country-combobox', self.edit_form)
        self.assertIn('v2_country_combobox.js', self.edit_form)
        self.assertNotIn('class="card p-4 mb-3"', self.edit_form)

    def test_linked_export_form_reuses_the_modern_shell_and_semantic_context_primitives(self):
        for token in ("{% block body_class %}v2-modern{% endblock %}", "v2-form-page-header",
                      "v2-form-error", "v2-panel", "v2-form-grid", "v2-item-row",
                      "v2-document-grid", "v2-form-actions", "v2-readonly-facts",
                      "linked-export-source-context"):
            self.assertIn(token, self.linked_export_form)
        for forbidden in ("card p-4", "loading_country_name", "add-item", "remove-item"):
            self.assertNotIn(forbidden, self.linked_export_form)
        self.assertIn(".v2-readonly-facts", self.css)

    def test_modern_main_uses_the_remaining_width_and_narrow_shell_stacks(self):
        self.assertIn('box-sizing:border-box; width:calc(100% - 245px); min-width:0', self.css)
        self.assertIn('@media(max-width:575.98px)', self.css)
        self.assertIn('body.v2-modern .main { width:100%; margin-left:0', self.css)

    def test_dashboard_reflow_tracks_cannot_exceed_the_modern_main(self):
        for token in (
            'grid-template-columns:minmax(0,1.2fr) repeat(4,minmax(0,1fr))',
            'grid-template-columns:repeat(3,minmax(0,1fr))',
            'grid-template-columns:repeat(2,minmax(0,1fr))',
            '.v35-board{box-sizing:border-box;width:100%;min-width:0;max-width:100%;overflow-x:auto;overflow-y:hidden}',
        ):
            self.assertIn(token, self.dashboard)

    def test_dashboard_task_entries_shrink_and_wrap_within_their_column(self):
        for token in (
            '.v35-task-card{box-sizing:border-box;min-width:0;max-width:100%',
            '.v35-task-card>*{min-width:0;max-width:100%}',
            '.v35-task-title,.v35-meta,.v35-fact,.v35-waiting,.v35-warning{overflow-wrap:anywhere}',
            '.v35-task-actions details,.v35-task-actions form{min-width:0;max-width:100%',
            'width:min(230px,100%);min-width:0;max-width:100%',
        ):
            self.assertIn(token, self.dashboard)
        self.assertNotIn('WU1333', self.dashboard)

    def test_dashboard_keeps_wide_tables_local_and_main_content_out_of_sidebar(self):
        self.assertIn('z-index:1', self.css)
        for token in (
            '.v35-dashboard,.v35-panel{min-width:0;max-width:100%',
            '.v35-top>*{min-width:0}',
            '.v35-toolbar{min-width:0;gap:8px;flex-wrap:wrap;justify-content:flex-end}',
            '@media(max-width:900px){.v35-top{display:block}',
            '.v35-board{box-sizing:border-box;width:100%;min-width:0;max-width:100%;overflow-x:auto;overflow-y:hidden}',
        ):
            self.assertIn(token, self.dashboard)
        combined = self.css + self.dashboard
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
