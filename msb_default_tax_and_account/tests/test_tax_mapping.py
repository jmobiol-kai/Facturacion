# -*- coding: utf-8 -*-

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import tagged
from .common import MsbSalesDefaultTaxesTestCommon


@tagged('post_install', '-at_install')
class TestTaxMapping(MsbSalesDefaultTaxesTestCommon):
    """Test the journal tax mapping functionality (Version 2.0).

    These tests prove the BUSINESS OUTCOME: a per-journal mapping
    original_T -> replacement_R changes the MONEY booked on a posted
    invoice (amount_tax / amount_total / GL tax lines), not merely an
    in-memory ``tax_ids`` field.  Replacements use a DIFFERING rate so
    the swap is observable in the totals.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        # Replacement tax at a DIFFERING rate (21%) so a swap from the
        # product's own 10% tax is observable in amount_tax/amount_total.
        cls.iva_21_export = cls.env['account.tax'].create({
            'name': 'IVA 21% Export',
            'amount': 21,
            'type_tax_use': 'sale',
            'company_id': cls.company.id,
        })

        # A genuinely different rate (10.5%) for the "differing rate" proofs.
        cls.iva_10_5 = cls.env['account.tax'].create({
            'name': 'IVA 10.5%',
            'amount': 10.5,
            'type_tax_use': 'sale',
            'company_id': cls.company.id,
        })

        # A 0% tax: mapping 21% -> 0% makes the swap unmistakable in money.
        cls.iva0 = cls.env['account.tax'].create({
            'name': 'IVA 0%',
            'amount': 0,
            'type_tax_use': 'sale',
            'company_id': cls.company.id,
        })

        # A small extra rate (5%) that we never map, to prove granular
        # passthrough in the partial multi-tax scenario.
        cls.tax_sale_5 = cls.env['account.tax'].create({
            'name': 'IVA 5%',
            'amount': 5,
            'type_tax_use': 'sale',
            'company_id': cls.company.id,
        })

        # Purchase-side tax so we can prove sale mappings do NOT fire on a
        # purchase document booked on the same journal-style tax setup.
        cls.tax_purchase_21 = cls.env['account.tax'].create({
            'name': 'Purchase VAT 21%',
            'amount': 21,
            'type_tax_use': 'purchase',
            'company_id': cls.company.id,
        })

        # Product carrying {21%, 5%}: one tax we map, one we leave alone.
        cls.product_multi_tax = cls.env['product.product'].create({
            'name': 'Product with Multiple Taxes',
            'type': 'consu',
            'list_price': 100.0,
            'standard_price': 50.0,
            'taxes_id': [(6, 0, [cls.tax_sale_21.id, cls.tax_sale_5.id])],
            'company_id': cls.company.id,
        })
        # Pin to exactly {21%, 5%} (company default leaks in on create).
        cls.product_multi_tax.taxes_id = cls.tax_sale_21 | cls.tax_sale_5

        # A product whose own sale tax is 21% so a journal mapping 21%->R
        # applies to it directly.
        cls.product_21 = cls.env['product.product'].create({
            'name': 'Product 21%',
            'type': 'consu',
            'list_price': 100.0,
            'taxes_id': [(6, 0, [cls.tax_sale_21.id])],
            'company_id': cls.company.id,
        })
        cls.product_21.taxes_id = cls.tax_sale_21

        # A product with both a sale tax (21%) and a supplier tax (21%) so we
        # can book a posted purchase document and prove the unmapped supplier
        # rate survives a sale-only journal mapping.
        cls.product_sale_and_purchase = cls.env['product.product'].create({
            'name': 'Product Sale+Purchase',
            'type': 'consu',
            'list_price': 100.0,
            'taxes_id': [(6, 0, [cls.tax_sale_21.id])],
            'supplier_taxes_id': [(6, 0, [cls.tax_purchase_21.id])],
            'company_id': cls.company.id,
        })
        cls.product_sale_and_purchase.taxes_id = cls.tax_sale_21
        cls.product_sale_and_purchase.supplier_taxes_id = cls.tax_purchase_21

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _post_invoice(self, journal, product, move_type='out_invoice', price_unit=100.0):
        """Create + post an invoice/bill with a single product line and return it.

        The journal's tax mapping is exercised through the standard line tax
        computation (_get_computed_taxes) so the result is the booked money.
        """
        invoice = self.env['account.move'].create({
            'move_type': move_type,
            'partner_id': self.partner.id,
            'journal_id': journal.id,
            'invoice_date': fields.Date.context_today(self.env.user),
            'invoice_line_ids': [(0, 0, {
                'product_id': product.id,
                'quantity': 1,
                'price_unit': price_unit,
            })],
        })
        # Apply the journal mapping (+ FP) deterministically through the
        # module's own recompute path, then post so we assert booked money.
        invoice.action_recompute_taxes_force()
        invoice.action_post()
        return invoice

    def _tax_amount_for(self, invoice, tax):
        """Sum of GL tax-line amounts attributed to ``tax`` on a posted move."""
        lines = invoice.line_ids.filtered(lambda l: l.tax_line_id == tax)
        # balance is signed; magnitude is the tax amount for a 1-line invoice.
        return sum(abs(b) for b in lines.mapped('balance'))

    # ------------------------------------------------------------------
    # Tests
    # ------------------------------------------------------------------
    def test_tax_mapping_creation(self):
        """A created mapping resolves the original tax to the replacement AND
        changes the booked money on a posted invoice (differing rate)."""
        mapping = self.create_tax_mapping(
            self.journal_with_taxes, self.tax_sale_21, self.iva0, 'sale')

        # Resolution: the journal now maps 21% -> 0%.
        self.assertTrue(mapping.active)
        self.assertEqual(
            self.journal_with_taxes.map_taxes(self.tax_sale_21, 'sale'),
            self.iva0,
        )

        # Financial effect: product_21 (own tax 21%) booked on this journal
        # must pay 0 tax because the mapping swaps 21% -> 0%.
        invoice = self._post_invoice(self.journal_with_taxes, self.product_21)
        self.assertEqual(invoice.invoice_line_ids.tax_ids, self.iva0)
        self.assertAlmostEqual(invoice.amount_tax, 0.0)
        self.assertAlmostEqual(invoice.amount_total, 100.0)
        # The original 21% tax produced NO GL tax line.
        self.assertFalse(
            invoice.line_ids.filtered(lambda l: l.tax_line_id == self.tax_sale_21))

    def test_journal_has_tax_mappings(self):
        """has_tax_mappings is scoped by tax_type, and the scoping holds in
        money: a SALE mapping must not change a PURCHASE document's tax."""
        # Initially no mappings.
        self.assertFalse(self.journal_with_taxes.has_tax_mappings('sale'))

        # Create a SALE mapping 21% -> 0%.
        self.create_tax_mapping(
            self.journal_with_taxes, self.tax_sale_21, self.iva0, 'sale')

        self.assertTrue(self.journal_with_taxes.has_tax_mappings('sale'))
        self.assertFalse(self.journal_with_taxes.has_tax_mappings('purchase'))

        # Book a PURCHASE document (vendor bill) on a purchase journal: the
        # sale mapping must not fire, so the supplier 21% tax stays at 21.0.
        purchase_journal = self.env['account.journal'].create({
            'name': 'Purchase Journal (scope test)',
            'type': 'purchase',
            'code': 'PJSC',
            'company_id': self.company.id,
        })
        # Mirror the sale mapping onto the purchase journal but only for SALE
        # type: it must be inert for purchase lines.
        self.create_tax_mapping(
            purchase_journal, self.tax_sale_21, self.iva0, 'sale')

        bill = self._post_invoice(
            purchase_journal, self.product_sale_and_purchase, move_type='in_invoice')
        self.assertEqual(bill.invoice_line_ids.tax_ids, self.tax_purchase_21)
        self.assertAlmostEqual(bill.amount_tax, 21.0)
        self.assertAlmostEqual(bill.amount_total, 121.0)

    def test_single_tax_mapping(self):
        """End-to-end single-tax swap through a posted SALE invoice with a
        DIFFERING rate (21% product tax -> 0%)."""
        self.create_tax_mapping(
            self.journal_with_taxes, self.tax_sale_21, self.iva0, 'sale')

        # map_taxes resolution.
        mapped = self.journal_with_taxes.map_taxes(self.tax_sale_21, 'sale')
        self.assertEqual(mapped, self.iva0)

        # Financial effect on a posted invoice: the 21% product tax mapped to
        # 0% means zero tax booked and no GL line for the original 21% tax.
        invoice = self._post_invoice(self.journal_with_taxes, self.product_21)
        self.assertEqual(invoice.invoice_line_ids.tax_ids, self.iva0)
        self.assertAlmostEqual(invoice.amount_tax, 0.0)
        self.assertAlmostEqual(invoice.amount_total, 100.0)
        self.assertFalse(
            invoice.line_ids.filtered(lambda l: l.tax_line_id == self.tax_sale_21))

    def test_multiple_tax_mapping(self):
        """Partial multi-tax: product carries {21%, 5%}; map only 21%->0%.
        After posting, the mapped component changes (21 -> 0) while the
        unmapped 5% component is untouched."""
        self.create_tax_mapping(
            self.journal_with_taxes, self.tax_sale_21, self.iva0, 'sale')

        # Resolution: 21% swapped to 0%, 5% kept.
        mapped = self.journal_with_taxes.map_taxes(
            self.tax_sale_21 | self.tax_sale_5, 'sale')
        self.assertEqual(mapped, self.iva0 | self.tax_sale_5)

        invoice = self._post_invoice(self.journal_with_taxes, self.product_multi_tax)

        # Per-tax money: only the 5% line carries tax; the 21% mapped to 0
        # carries none; the original 21% has no GL line.
        self.assertAlmostEqual(self._tax_amount_for(invoice, self.tax_sale_5), 5.0)
        self.assertAlmostEqual(self._tax_amount_for(invoice, self.iva0), 0.0)
        self.assertAlmostEqual(self._tax_amount_for(invoice, self.tax_sale_21), 0.0)
        self.assertFalse(
            invoice.line_ids.filtered(lambda l: l.tax_line_id == self.tax_sale_21))

        # Totals: base 100 + 5 tax (the 21% component became 0).
        self.assertAlmostEqual(invoice.amount_tax, 5.0)
        self.assertAlmostEqual(invoice.amount_total, 105.0)

    def test_sale_order_line_with_mapping(self):
        """SO -> invoice continuity: a journal mapping (product 10% -> 21%)
        drives the posted invoice money, not the product's own 10%."""
        self.create_tax_mapping(
            self.journal_with_taxes, self.tax_sale_10, self.iva_21_export, 'sale')

        sale_order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'journal_id': self.journal_with_taxes.id,
            'company_id': self.company.id,
        })
        order_line = self.env['sale.order.line'].create({
            'order_id': sale_order.id,
            'product_id': self.product.id,
            'product_uom_qty': 1,
            'price_unit': 100,
        })

        # The order line resolves to the mapped 21% tax.
        self.assertEqual(order_line.tax_ids, self.iva_21_export)

        # Confirm -> invoice -> post and assert the booked money + GL line.
        sale_order.action_confirm()
        invoice = sale_order._create_invoices()
        invoice.action_post()

        self.assertEqual(invoice.invoice_line_ids.tax_ids, self.iva_21_export)
        self.assertAlmostEqual(invoice.amount_tax, 21.0)
        self.assertAlmostEqual(invoice.amount_total, 121.0)
        self.assertTrue(
            invoice.line_ids.filtered(lambda l: l.tax_line_id == self.iva_21_export))
        # The product's own 10% tax produced no GL line.
        self.assertFalse(
            invoice.line_ids.filtered(lambda l: l.tax_line_id == self.tax_sale_10))

    def test_no_mapping_uses_original_taxes(self):
        """No-mapping control with teeth: the product's own 10% rate drives
        the posted money on a journal that has no mappings."""
        invoice = self._post_invoice(self.journal_without_taxes, self.product)

        self.assertEqual(invoice.invoice_line_ids.tax_ids, self.tax_sale_10)
        # The control asserts the actual stock amount (10% of 100), not just
        # equality of recordsets.
        self.assertAlmostEqual(invoice.amount_tax, 10.0)
        self.assertAlmostEqual(invoice.amount_total, 110.0)
        self.assertTrue(
            invoice.line_ids.filtered(lambda l: l.tax_line_id == self.tax_sale_10))

        # Delta proof: the SAME product on the mapped journal (10% -> 21%)
        # books a different amount, confirming the control isolates "no map".
        self.create_tax_mapping(
            self.journal_with_taxes, self.tax_sale_10, self.iva_21_export, 'sale')
        mapped_invoice = self._post_invoice(self.journal_with_taxes, self.product)
        self.assertAlmostEqual(mapped_invoice.amount_tax, 21.0)

    def test_inactive_mapping_ignored(self):
        """An inactive mapping falls back to the original rate; activating it
        flips the booked money (differing rate 10% -> 10.5%)."""
        mapping = self.create_tax_mapping(
            self.journal_with_taxes, self.tax_sale_10, self.iva_10_5, 'sale')
        mapping.active = False

        # Inactive: the original 10% rate drives the money.
        inv_inactive = self._post_invoice(self.journal_with_taxes, self.product)
        self.assertEqual(inv_inactive.invoice_line_ids.tax_ids, self.tax_sale_10)
        self.assertAlmostEqual(inv_inactive.amount_tax, 10.0)
        self.assertAlmostEqual(inv_inactive.amount_total, 110.0)

        # Activate: the mapped 10.5% rate now drives the money.
        mapping.active = True
        inv_active = self._post_invoice(self.journal_with_taxes, self.product)
        self.assertEqual(inv_active.invoice_line_ids.tax_ids, self.iva_10_5)
        self.assertAlmostEqual(inv_active.amount_tax, 10.5)
        self.assertAlmostEqual(inv_active.amount_total, 110.5)
        self.assertTrue(
            inv_active.line_ids.filtered(lambda l: l.tax_line_id == self.iva_10_5))

    def test_mapping_validation(self):
        """Validation: narrow to ValidationError and cover the config rules.

        Rule 1: original/replacement must share type_tax_use (cross-type).
        Rule 2: replacement type must match the declared tax_type.
        Rule 3: no self-mapping (circular).
        Rule 4: the (journal, original, tax_type) uniqueness constraint.
        """
        purchase_tax = self.env['account.tax'].create({
            'name': 'Purchase Tax (validation)',
            'amount': 21,
            'type_tax_use': 'purchase',
            'company_id': self.company.id,
        })

        # Rule 1: cross-type original(sale) -> replacement(purchase).
        with self.assertRaises(ValidationError):
            self.env['account.journal.tax.mapping'].create({
                'journal_id': self.journal_with_taxes.id,
                'original_tax_id': self.tax_sale_21.id,
                'replacement_tax_id': purchase_tax.id,
                'tax_type': 'sale',
            })

        # Rule 2: declared tax_type=purchase but both taxes are sale taxes ->
        # replacement type_tax_use ('sale') mismatches declared 'purchase'.
        with self.assertRaises(ValidationError):
            self.env['account.journal.tax.mapping'].create({
                'journal_id': self.journal_with_taxes.id,
                'original_tax_id': self.tax_sale_21.id,
                'replacement_tax_id': self.iva_21_export.id,
                'tax_type': 'purchase',
            })

        # Rule 3: self-mapping (original == replacement).
        with self.assertRaises(ValidationError):
            self.env['account.journal.tax.mapping'].create({
                'journal_id': self.journal_with_taxes.id,
                'original_tax_id': self.tax_sale_21.id,
                'replacement_tax_id': self.tax_sale_21.id,
                'tax_type': 'sale',
            })

        # Rule 4: duplicate (journal, original, tax_type) -> SQL UNIQUE.
        self.create_tax_mapping(
            self.journal_with_taxes, self.tax_sale_21, self.iva_21_export, 'sale')
        from psycopg2 import IntegrityError
        from odoo.tools import mute_logger
        with self.assertRaises(IntegrityError), mute_logger('odoo.sql_db'):
            with self.env.cr.savepoint():
                self.env['account.journal.tax.mapping'].create({
                    'journal_id': self.journal_with_taxes.id,
                    'original_tax_id': self.tax_sale_21.id,
                    'replacement_tax_id': self.iva_10_5.id,
                    'tax_type': 'sale',
                })

    def test_compute_impuestos_diario_field(self):
        """The real outcome is the line tax + booked money; impuestos_diario
        is only the secondary preview field."""
        self.create_tax_mapping(
            self.journal_with_taxes, self.tax_sale_10, self.iva_21_export, 'sale')

        sale_order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'journal_id': self.journal_with_taxes.id,
            'company_id': self.company.id,
        })
        order_line = self.env['sale.order.line'].create({
            'order_id': sale_order.id,
            'product_id': self.product.id,
            'product_uom_qty': 1,
            'price_unit': 100,
        })

        # Primary outcome: the actual line tax is the mapped 21% tax.
        self.assertEqual(order_line.tax_ids, self.iva_21_export)

        # Carry to a posted invoice and assert the money.
        sale_order.action_confirm()
        invoice = sale_order._create_invoices()
        invoice.action_post()
        self.assertAlmostEqual(invoice.amount_tax, 21.0)
        self.assertAlmostEqual(invoice.amount_total, 121.0)

        # Secondary: the preview field mirrors the mapped tax.
        self.assertEqual(order_line.impuestos_diario, self.iva_21_export)
