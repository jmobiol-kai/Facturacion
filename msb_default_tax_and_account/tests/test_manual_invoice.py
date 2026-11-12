# -*- coding: utf-8 -*-

from odoo import fields
from odoo.tests import tagged
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
import logging

_logger = logging.getLogger(__name__)


@tagged('post_install', '-at_install')
class TestManualInvoiceTaxes(AccountTestInvoicingCommon):
    """Test that simulates manual invoice creation in UI"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        # Crear datos de prueba
        cls.company = cls.env.company

        # Controlled tax environment: clear company default taxes so fixture
        # products carry EXACTLY the taxes we assign (core _force_default_tax
        # links the company default additively, which would pollute amounts).
        cls.env['res.company'].sudo().search([]).write({
            'account_sale_tax_id': False,
            'account_purchase_tax_id': False,
        })

        # Crear diario de ventas
        cls.sales_journal = cls.env['account.journal'].create({
            'name': 'Test Sales Journal',
            'type': 'sale',
            'code': 'TSJ',
            'company_id': cls.company.id,
        })

        # Crear diario sin impuestos configurados
        cls.sales_journal_no_taxes = cls.env['account.journal'].create({
            'name': 'Test Sales Journal No Taxes',
            'type': 'sale',
            'code': 'TSNT',
            'company_id': cls.company.id,
        })

        # Crear impuestos. Replacement rate (15%) deliberately DIFFERS from the
        # product rate (21%) so the journal swap is financially observable.
        cls.tax_journal = cls.env['account.tax'].create({
            'name': 'Journal Tax 15%',
            'amount': 15,
            'type_tax_use': 'sale',
            'company_id': cls.company.id,
        })

        cls.tax_product = cls.env['account.tax'].create({
            'name': 'Product Tax 21%',
            'amount': 21,
            'type_tax_use': 'sale',
            'company_id': cls.company.id,
        })

        # Configurar mapeo de impuestos en el diario (mapea product tax a journal tax)
        cls.env['account.journal.tax.mapping'].create({
            'journal_id': cls.sales_journal.id,
            'original_tax_id': cls.tax_product.id,
            'replacement_tax_id': cls.tax_journal.id,
            'tax_type': 'sale',
        })

        # Crear producto con impuesto propio. B8: 'product' -> 'consu'.
        cls.product = cls.env['product.product'].create({
            'name': 'Test Product',
            'type': 'consu',
            'list_price': 100.0,
            'taxes_id': [(6, 0, [cls.tax_product.id])],
        })
        # Pin to exactly tax_product (company default leaks in on create).
        cls.product.taxes_id = cls.tax_product

        # Crear cliente
        cls.partner = cls.env['res.partner'].create({
            'name': 'Test Customer',
            'is_company': True,
        })

    def test_manual_invoice_with_tax_mapping_debug(self):
        """Mapped journal tax (15%) replaces the product tax (21%) and drives the posted money."""
        # Crear factura manualmente (como lo haría el usuario)
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': self.partner.id,
            'journal_id': self.sales_journal.id,
        })

        line = self.env['account.move.line'].create({
            'move_id': invoice.id,
            'name': 'Test Line',
            'quantity': 1,
            'price_unit': 100.0,
            'display_type': 'product',  # Importante!
            'product_id': self.product.id,
        })

        # The mapping must replace product 21% with journal 15% on the line.
        self.assertEqual(line.tax_ids, self.tax_journal,
                         "Line must carry the mapped journal tax (15%), not product tax (21%)")

        # FINANCIAL EFFECT: post and assert the money reflects the 15% replacement,
        # NOT the original 21%. With price_unit=100, qty=1: amount_tax == 15.0.
        invoice.invoice_date = fields.Date.context_today(invoice)
        invoice.action_post()

        self.assertAlmostEqual(invoice.amount_tax, 15.0,
                               msg="Mapped 15% must drive the booked tax, not product 21%")
        self.assertNotAlmostEqual(invoice.amount_tax, 21.0)
        self.assertAlmostEqual(invoice.amount_total, 115.0)

        # GL tax line must be for the replacement tax; none for the original.
        self.assertTrue(
            invoice.line_ids.filtered(lambda l: l.tax_line_id == self.tax_journal),
            "A GL tax line for the mapped 15% tax must exist",
        )
        self.assertFalse(
            invoice.line_ids.filtered(lambda l: l.tax_line_id == self.tax_product),
            "No GL tax line for the original 21% tax may exist",
        )

    def test_manual_invoice_without_tax_mapping(self):
        """Without journal mappings the product tax (21%) survives to the posted GL (control)."""
        # Crear factura con diario sin impuestos / sin mapeos
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': self.partner.id,
            'journal_id': self.sales_journal_no_taxes.id,
        })

        line = self.env['account.move.line'].create({
            'move_id': invoice.id,
            'name': 'Test Line',
            'quantity': 1,
            'price_unit': 100.0,
            'product_id': self.product.id,
            'display_type': 'product',
        })

        # Sin mapeos en diario, debería usar los del producto
        self.assertEqual(line.tax_ids, self.tax_product, "Should keep the product tax")

        # FINANCIAL EFFECT control: the product's own 21% must drive the posted money.
        invoice.invoice_date = fields.Date.context_today(invoice)
        invoice.action_post()

        self.assertAlmostEqual(invoice.amount_tax, 21.0,
                               msg="Product rate (21%) must be untouched on a journal without mappings")
        self.assertAlmostEqual(invoice.amount_total, 121.0)
        self.assertTrue(
            invoice.line_ids.filtered(lambda l: l.tax_line_id == self.tax_product),
            "A GL tax line for the product 21% tax must exist",
        )
        self.assertFalse(
            invoice.line_ids.filtered(lambda l: l.tax_line_id == self.tax_journal),
            "No GL tax line for the journal 15% tax may exist (no mapping on this journal)",
        )

    def test_get_computed_taxes_directly(self):
        """The helper result (mapped 15%) must reach the line AND the posted document."""
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': self.partner.id,
            'journal_id': self.sales_journal.id,
        })

        line = self.env['account.move.line'].create({
            'move_id': invoice.id,
            'name': 'Test Line',
            'quantity': 1,
            'price_unit': 100.0,
            'product_id': self.product.id,
            'display_type': 'product',
        })

        # Helper returns the mapped tax...
        computed_taxes = line._get_computed_taxes()
        self.assertEqual(computed_taxes, self.tax_journal,
                         "_get_computed_taxes should return the mapped tax (15%)")
        # ...and that result is what actually sits on the line.
        self.assertEqual(line.tax_ids, computed_taxes,
                         "The line must carry exactly the computed (mapped) tax")

        # End-to-end: the helper result must survive to the posted GL.
        invoice.invoice_date = fields.Date.context_today(invoice)
        invoice.action_post()

        self.assertAlmostEqual(invoice.amount_tax, 15.0,
                               msg="Posted tax must equal the mapped 15%, proving the helper reaches the GL")
        self.assertTrue(
            invoice.line_ids.filtered(lambda l: l.tax_line_id == self.tax_journal),
            "Posted GL tax line must be for the mapped 15% tax",
        )
