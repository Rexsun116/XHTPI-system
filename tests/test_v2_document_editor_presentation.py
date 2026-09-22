"""Presentation contracts for authoritative PRG document editors."""
from pathlib import Path
from unittest import TestCase

ROOT = Path(__file__).resolve().parents[1]

class DocumentEditorPresentationTest(TestCase):
    def setUp(self):
        self.requirements = (ROOT / 'v2/templates/v2/document_requirements_editor.html').read_text()
        self.booking = (ROOT / 'v2/templates/v2/booking_hs_codes.html').read_text()
        self.new_order = (ROOT / 'v2/templates/v2/order_form.html').read_text()
        self.normal_edit = (ROOT / 'v2/templates/v2/order_edit.html').read_text()
        self.linked_export = (ROOT / 'v2/templates/v2/create_linked_export_order.html').read_text()
        self.css = (ROOT / 'v2/static/v2_design_system.css').read_text()

    def test_document_requirements_use_human_labels_and_existing_tristate_contract(self):
        for token in ('v2-modern', 'Document workflow', 'Document Requirements', 'Certificate of Origin',
                      'Original Documents Mail', 'Settlement Documents', 'Not Confirmed', 'Required',
                      'Not Required', 'document-requirements-form', 'name="csrf_token"', 'Save Requirements'):
            self.assertIn(token, self.requirements)
        self.assertNotIn('{{ fact }}</label>', self.requirements)
        self.assertNotIn('<script', self.requirements)

    def test_booking_editor_preserves_item_snapshot_input_contract(self):
        for token in ('v2-modern', 'Booking HS Codes', 'Booking Items', 'Item {{ loop.index }}',
                      'product_hs_code_snapshot_{{ item.id }}', 'maxlength="32"', 'booking-hs-codes-form',
                      'name="csrf_token"', 'Save HS Codes', 'does not update the Product master record'):
            self.assertIn(token, self.booking)
        self.assertNotIn('<script', self.booking)

    def test_shared_editor_primitives_are_responsive(self):
        for token in ('.v2-requirements-grid', '.v2-booking-item', '.v2-editor-note',
                      '.v2-setup-requirements-grid', '.v2-document-notes',
                      'overflow-wrap:anywhere', '@media(max-width:700px)'):
            self.assertIn(token, self.css)

    def test_setup_forms_share_presentation_without_changing_their_form_contracts(self):
        for template in (self.new_order, self.normal_edit, self.linked_export):
            for token in ('v2-requirements-grid v2-setup-requirements-grid',
                          'COO / Certificate of Origin', 'Original B/L',
                          'Original Insurance Policy', 'Not Confirmed',
                          'value="true"', 'value="false"'):
                self.assertIn(token, template)
            self.assertEqual(template.count('<form'), 1)

        self.assertIn('name="other_document_notes"', self.new_order)
        self.assertIn("'other_document_notes'", self.normal_edit)
        self.assertNotIn('name="other_document_notes"', self.linked_export)
        self.assertIn('name="edit_version"', self.normal_edit)
        self.assertIn('name="edit_version"', self.linked_export)

    def test_operational_editor_remains_requirements_only(self):
        self.assertIn('document-requirements-form', self.requirements)
        self.assertEqual(self.requirements.count('<form'), 1)
        self.assertNotIn('other_document_notes', self.requirements)
