# -*- coding: utf-8 -*-

from odoo import fields
from odoo.tests import tagged
from .common import MsbSalesDefaultTaxesTestCommon


@tagged('post_install', '-at_install')
class TestSaleOrderDefaultTaxes(MsbSalesDefaultTaxesTestCommon):
    """Test default taxes and accounts functionality in sales orders"""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _gl_tax_line(self, move, tax):
        """GL tax line on a posted move carrying ``tax`` as tax_line_id."""
        return move.line_ids.filtered(lambda l: l.tax_line_id == tax)

    def _today(self):
        return fields.Date.context_today(self.env['account.move'])

    # ------------------------------------------------------------------
    # Tests
    # ------------------------------------------------------------------
    def test_sale_order_journal_computation(self):
        """End-to-end: journal mapping 10->21 makes the POSTED invoice charge 21% (not 10%)."""
        # Set up tax mapping in the journal first (product 10% -> 21%).
        self.create_tax_mapping(
            self.journal_with_taxes,
            self.tax_sale_10,  # Product tax
            self.tax_sale_21,  # Mapped to
            'sale'
        )

        sale_order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'journal_id': self.journal_with_taxes.id,
            'company_id': self.company.id,
            'order_line': [(0, 0, {
                'product_id': self.product.id,
                'product_uom_qty': 1,
                'price_unit': 100,
            })],
        })

        # The journal is wired and the swap reaches the order line.
        self.assertEqual(sale_order.journal_id, self.journal_with_taxes)
        order_line = sale_order.order_line
        self.assertEqual(order_line.tax_ids, self.tax_sale_21,
                         "Order line must carry the mapped 21% tax, not the product 10%")

        sale_order.action_confirm()
        invoice = sale_order._create_invoices()
        invoice.action_post()

        # Money proves the swap: 21% of 100 -> tax 21, total 121.
        self.assertAlmostEqual(invoice.amount_tax, 21.0)
        self.assertAlmostEqual(invoice.amount_total, 121.0)
        self.assertTrue(self._gl_tax_line(invoice, self.tax_sale_21),
                        "Posted invoice must carry a GL tax line for the mapped 21% tax")
        self.assertFalse(self._gl_tax_line(invoice, self.tax_sale_10),
                         "No GL tax line should remain for the original 10% tax")

    def test_sale_order_line_tax_computation_with_journal_tax_mappings(self):
        """Sale order lines use journal tax mappings, and the rate drives real money."""
        # Create tax mapping: product tax (10%) -> mapped tax (21%)
        self.create_tax_mapping(
            self.journal_with_taxes,
            self.tax_sale_10,  # Original product tax
            self.tax_sale_21,  # Mapped to
            'sale'
        )

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

        # Should use mapped taxes instead of product taxes
        self.assertEqual(order_line.tax_ids, self.tax_sale_21,
                         "Sale order line should use journal mapped taxes")

        # Financial proof on the same line: 21% must drive the tax amount, not the product 10%.
        self.assertAlmostEqual(order_line.price_tax, 21.0,
                               msg='21% must drive the tax amount, not the product 10%')
        self.assertAlmostEqual(order_line.price_total, 121.0)

        # Helper field should show the mapped (replacement) tax.
        self.assertEqual(order_line.impuestos_diario, self.tax_sale_21,
                         "Helper field should show mapped taxes")

    def test_sale_order_line_tax_computation_without_journal_tax_mappings(self):
        """Without a journal mapping the line keeps the product 10% and charges exactly 10."""
        sale_order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'journal_id': self.journal_without_taxes.id,
            'company_id': self.company.id,
        })

        order_line = self.env['sale.order.line'].create({
            'order_id': sale_order.id,
            'product_id': self.product.id,
            'product_uom_qty': 1,
            'price_unit': 100,
        })

        # Anchor the control to a concrete tax and concrete money so it has teeth.
        self.assertEqual(order_line.tax_ids, self.tax_sale_10,
                         "Line should keep the product's own 10% tax")
        self.assertAlmostEqual(order_line.price_tax, 10.0,
                               msg="Exactly the unmapped 10% must be charged")
        self.assertAlmostEqual(order_line.price_total, 110.0)

        # Helper field should be empty when journal has no tax mappings.
        self.assertFalse(order_line.impuestos_diario,
                         "Helper field should be empty when journal has no tax mappings")

    def test_sale_order_tax_cache_key_includes_journal(self):
        """Cache invalidation is observable: deactivating the mapping flips the resolved tax.

        Instead of introspecting the cache-key tuple shape (tautological), prove the
        outcome the cache key exists to guarantee: when the active mapping is removed,
        recomputation yields the original product tax again.
        """
        mapping = self.create_tax_mapping(
            self.journal_with_taxes,
            self.tax_sale_10,
            self.tax_sale_21,
            'sale'
        )

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

        # With the active mapping, the line resolves to the replacement 21%.
        self.assertEqual(order_line.tax_ids, self.tax_sale_21)
        self.assertAlmostEqual(order_line.price_tax, 21.0)

        # Deactivate the mapping; the cache key must reflect the new mapping state
        # so recomputation falls back to the product's own 10%.
        mapping.active = False
        order_line.invalidate_recordset(['tax_ids'])
        order_line._compute_tax_ids()
        self.assertEqual(order_line.tax_ids, self.tax_sale_10,
                         "Deactivating the mapping must flip the line back to the product 10%")
        self.assertAlmostEqual(order_line.price_tax, 10.0)

    def test_invoice_creation_from_sale_order_with_journal_tax_mappings(self):
        """Invoices from SOs keep the mapping and POST the mapped money (21%, not 10%)."""
        self.create_tax_mapping(
            self.journal_with_taxes,
            self.tax_sale_10,
            self.tax_sale_21,
            'sale'
        )

        sale_order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'journal_id': self.journal_with_taxes.id,
            'company_id': self.company.id,
            'order_line': [(0, 0, {
                'product_id': self.product.id,
                'product_uom_qty': 1,
                'price_unit': 100,
            })]
        })

        sale_order.action_confirm()
        invoice = sale_order._create_invoices()

        self.assertEqual(invoice.journal_id, self.journal_with_taxes,
                         "Invoice should use the journal from sale order")

        invoice_line = invoice.invoice_line_ids.filtered('product_id')
        self.assertEqual(invoice_line.tax_ids, self.tax_sale_21,
                         "Invoice line should carry the mapped 21% tax")

        invoice.action_post()

        # 21% of 100, NOT 10.
        self.assertAlmostEqual(invoice.amount_tax, 21.0)
        self.assertAlmostEqual(invoice.amount_total, 121.0)
        self.assertTrue(self._gl_tax_line(invoice, self.tax_sale_21),
                        "Posted invoice must carry the GL tax line for the mapped 21% tax")
        self.assertFalse(self._gl_tax_line(invoice, self.tax_sale_10),
                         "No GL tax line should remain for the original 10% tax")

    def test_invoice_creation_with_journal_account_setting(self):
        """With usar_cuenta_defecto_facturas=True, the POSTED line books the journal account."""
        self.create_tax_mapping(
            self.journal_with_taxes,
            self.tax_sale_10,
            self.tax_sale_21,
            'sale'
        )

        sale_order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'journal_id': self.journal_with_taxes.id,
            'company_id': self.company.id,
            'order_line': [(0, 0, {
                'product_id': self.product.id,
                'product_uom_qty': 1,
                'price_unit': 100,
            })]
        })

        sale_order.action_confirm()
        invoice = sale_order._create_invoices()
        invoice.action_post()

        invoice_line = invoice.invoice_line_ids.filtered('product_id')
        self.assertTrue(invoice_line, "Invoice line should be created")

        # Mapped taxes drive the money.
        self.assertEqual(invoice_line.tax_ids, self.tax_sale_21)
        self.assertAlmostEqual(invoice.amount_tax, 21.0)

        # Account override ON: the posted product line books the journal default account,
        # and explicitly NOT the product/category income account.
        self.assertEqual(invoice_line.account_id, self.journal_with_taxes.default_account_id,
                         "Posted invoice line should use the journal default account")
        prod_income = self.product.product_tmpl_id.with_company(self.company).get_product_accounts()['income']
        self.assertNotEqual(invoice_line.account_id, prod_income,
                            "Account override ON must NOT use the product income account")

    def test_invoice_creation_without_journal_account_setting(self):
        """With usar_cuenta_defecto_facturas=False, the POSTED line books the product account."""
        sale_order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'journal_id': self.journal_without_taxes.id,
            'company_id': self.company.id,
            'order_line': [(0, 0, {
                'product_id': self.product.id,
                'product_uom_qty': 1,
                'price_unit': 100,
            })]
        })

        sale_order.action_confirm()
        invoice = sale_order._create_invoices()
        invoice.action_post()

        invoice_line = invoice.invoice_line_ids.filtered('product_id')

        # No journal mapping -> product's own 10% and exactly 10 of tax.
        self.assertEqual(invoice_line.tax_ids, self.tax_sale_10)
        self.assertAlmostEqual(invoice.amount_tax, 10.0)
        self.assertAlmostEqual(invoice.amount_total, 110.0)

        # Account override OFF: the posted line books the product/category income account,
        # positively pinned (and NOT the journal default account).
        prod_income = self.product.product_tmpl_id.with_company(self.company).get_product_accounts()['income']
        self.assertEqual(invoice_line.account_id, prod_income,
                         "Account override OFF must use the product income account")
        self.assertNotEqual(invoice_line.account_id, self.journal_without_taxes.default_account_id,
                            "Account override OFF must NOT use the journal default account")

    def test_sale_order_line_compute_applies_mapping_silently(self):
        """The UI warning was removed; _compute_tax_ids now applies the mapping silently.

        Repurposed from the old warning test: prove the behavior that replaced the
        warning — when product taxes differ from the journal mapping, the line is
        recomputed to the mapped tax with no warning dict returned.
        """
        self.create_tax_mapping(
            self.journal_with_taxes,
            self.tax_sale_10,  # Product tax
            self.tax_sale_21,  # Mapped to a different tax
            'sale'
        )

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

        # The (now silent) onchange returns nothing — no warning surfaces to the UI.
        self.assertFalse(order_line._onchange_product_id_taxes(),
                         "The product onchange must no longer return a warning dict")

        # And the real behavior that replaced it: the mapping is applied.
        self.assertEqual(order_line.tax_ids, self.tax_sale_21,
                         "_compute_tax_ids must silently apply the journal mapping")
        self.assertAlmostEqual(order_line.price_tax, 21.0)

    def test_sale_order_prepare_invoice_maintains_journal(self):
        """_prepare_invoice keeps the journal AND the mapped taxes reach the posted invoice."""
        self.create_tax_mapping(
            self.journal_with_taxes,
            self.tax_sale_10,
            self.tax_sale_21,
            'sale'
        )

        sale_order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'journal_id': self.journal_with_taxes.id,
            'company_id': self.company.id,
            'order_line': [(0, 0, {
                'product_id': self.product.id,
                'product_uom_qty': 1,
                'price_unit': 100,
            })],
        })

        # The prepared values must carry the order's journal.
        invoice_values = sale_order._prepare_invoice()
        self.assertEqual(invoice_values['journal_id'], self.journal_with_taxes.id,
                         "_prepare_invoice should maintain journal from sale order")

        # Promote to an outcome: the journal carries through to a posted invoice
        # and drives the mapped money.
        sale_order.action_confirm()
        invoice = sale_order._create_invoices()
        invoice.action_post()
        self.assertEqual(invoice.journal_id, self.journal_with_taxes)
        self.assertAlmostEqual(invoice.amount_tax, 21.0)
        self.assertTrue(self._gl_tax_line(invoice, self.tax_sale_21))

    def test_fiscal_position_mapping_with_journal_tax_mappings(self):
        """FP layers ON TOP of the journal swap: 10 -> (journal) 21 -> (FP) 27.

        Three DISTINCT rates make each layer observable. The posted result must be 27%
        (amount + GL tax line), proving the fiscal position runs after the journal swap.
        """
        # Journal mapping: product 10% -> 21%.
        self.create_tax_mapping(
            self.journal_with_taxes,
            self.tax_sale_10,
            self.tax_sale_21,
            'sale'
        )

        # Fiscal position: 21% -> 27% (B11: account.tax.original_tax_ids "dest replaces src"
        # + dest added to fp.tax_ids; _compute_tax_map builds src->dest from these).
        fp = self.env['account.fiscal.position'].create({
            'name': 'Test Fiscal Position',
            'company_id': self.company.id,
        })
        self.tax_sale_27.original_tax_ids = [(6, 0, self.tax_sale_21.ids)]
        fp.tax_ids = [(6, 0, self.tax_sale_27.ids)]

        sale_order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'journal_id': self.journal_with_taxes.id,
            'fiscal_position_id': fp.id,
            'company_id': self.company.id,
            'order_line': [(0, 0, {
                'product_id': self.product.id,
                'product_uom_qty': 1,
                'price_unit': 100,
            })],
        })

        order_line = sale_order.order_line
        # Equality (not membership): the final tax is exactly 27%, proving the layering
        # 10 -> 21 -> 27 (neither 10 nor 21 survives).
        self.assertEqual(order_line.tax_ids, self.tax_sale_27,
                         "FP must run on top of the journal swap, yielding exactly 27%")

        sale_order.action_confirm()
        invoice = sale_order._create_invoices()
        invoice.action_post()

        # 27% of 100 proves the layered result financially.
        self.assertAlmostEqual(invoice.amount_tax, 27.0)
        self.assertAlmostEqual(invoice.amount_total, 127.0)
        self.assertTrue(self._gl_tax_line(invoice, self.tax_sale_27),
                        "Posted invoice must carry the 27% GL tax line (FP on top of journal)")
        self.assertFalse(self._gl_tax_line(invoice, self.tax_sale_21),
                         "The intermediate journal 21% must not survive the FP layer")
        self.assertFalse(self._gl_tax_line(invoice, self.tax_sale_10),
                         "The original product 10% must not survive either layer")
