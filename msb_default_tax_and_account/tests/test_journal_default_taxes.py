# -*- coding: utf-8 -*-

from odoo import fields
from odoo.tests import tagged
from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged('post_install', '-at_install')
class TestJournalDefaultTaxes(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        # Controlled tax environment: clear company default taxes so fixture products
        # carry exactly the taxes we assign (core _force_default_tax links defaults additively).
        cls.env['res.company'].sudo().search([]).write({
            'account_sale_tax_id': False, 'account_purchase_tax_id': False,
        })

        # Crear datos de prueba
        cls.company = cls.env.company

        # Crear diario de ventas
        cls.sales_journal = cls.env['account.journal'].create({
            'name': 'Test Sales Journal',
            'type': 'sale',
            'code': 'TSJ',
            'company_id': cls.company.id,
        })

        # Crear impuestos (distinct rates so any swap is financially observable).
        cls.tax_10 = cls.env['account.tax'].create({
            'name': 'Tax 10%',
            'amount': 10,
            'type_tax_use': 'sale',
            'company_id': cls.company.id,
        })

        cls.tax_21 = cls.env['account.tax'].create({
            'name': 'Tax 21%',
            'amount': 21,
            'type_tax_use': 'sale',
            'company_id': cls.company.id,
        })

        # A third distinct sale tax (5%) used to prove partial multi-tax passthrough.
        cls.tax_5 = cls.env['account.tax'].create({
            'name': 'Tax 5%',
            'amount': 5,
            'type_tax_use': 'sale',
            'company_id': cls.company.id,
        })

        # Create purchase tax for supplier tax testing
        cls.purchase_tax_21 = cls.env['account.tax'].create({
            'name': 'Purchase Tax 21%',
            'amount': 21,
            'type_tax_use': 'purchase',
            'company_id': cls.company.id,
        })

        # Crear producto con impuesto propio (both sales and supplier taxes).
        # B8: storable 'product' removed in 19 -> 'consu'.
        cls.product = cls.env['product.product'].create({
            'name': 'Test Product',
            'type': 'consu',
            'list_price': 100.0,
            'taxes_id': [(6, 0, [cls.tax_21.id])],  # Sales taxes
            'supplier_taxes_id': [(6, 0, [cls.purchase_tax_21.id])],  # Supplier taxes
        })
        # AccountTestInvoicingCommon's company ships default sale/purchase taxes
        # that leak into taxes_id/supplier_taxes_id on create. Pin both so the
        # company default doesn't pollute the expected tax recordsets.
        cls.product.taxes_id = cls.tax_21
        cls.product.supplier_taxes_id = cls.purchase_tax_21

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _gl_tax_line(self, move, tax):
        """Return the GL tax line on a posted move carrying ``tax`` as tax_line_id."""
        return move.line_ids.filtered(lambda l: l.tax_line_id == tax)

    # ------------------------------------------------------------------
    # Tests
    # ------------------------------------------------------------------
    def test_journal_without_default_taxes(self):
        """Sin mapeo en el diario, la factura POSTEADA cobra el impuesto del producto (21%)."""
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': self.partner.id,
            'journal_id': self.sales_journal.id,
            'invoice_date': fields.Date.context_today(self.env['account.move']),
            'invoice_line_ids': [(0, 0, {
                'product_id': self.product.id,
                'quantity': 1,
                'price_unit': 100.0,
            })],
        })

        line = invoice.invoice_line_ids.filtered('product_id')
        self.assertEqual(line.tax_ids, self.tax_21,
                         "No mapping: line keeps the product's own 21% tax")

        invoice.action_post()

        # Money proves the control: 21% of 100 -> tax 21, total 121.
        self.assertAlmostEqual(invoice.amount_tax, 21.0)
        self.assertAlmostEqual(invoice.amount_total, 121.0)
        # GL tax line for the product tax exists, and there is no 10% line.
        self.assertTrue(self._gl_tax_line(invoice, self.tax_21),
                        "Posted move must carry a GL tax line for the 21% product tax")
        self.assertFalse(self._gl_tax_line(invoice, self.tax_10))

    def test_journal_with_tax_mapping(self):
        """Con mapeo 21->10 en el diario, la factura POSTEADA cobra 10% (no 21%)."""
        # Mapea tax_21 del producto a tax_10.
        self.env['account.journal.tax.mapping'].create({
            'journal_id': self.sales_journal.id,
            'original_tax_id': self.tax_21.id,
            'replacement_tax_id': self.tax_10.id,
            'tax_type': 'sale',
        })

        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': self.partner.id,
            'journal_id': self.sales_journal.id,
            'invoice_date': fields.Date.context_today(self.env['account.move']),
            'invoice_line_ids': [(0, 0, {
                'product_id': self.product.id,
                'quantity': 1,
                'price_unit': 100.0,
            })],
        })

        line = invoice.invoice_line_ids.filtered('product_id')
        # The replacement (10%) drives the line, not the product's 21%.
        self.assertEqual(line.tax_ids, self.tax_10,
                         "Mapped line must carry the replacement 10% tax")

        invoice.action_post()

        # Money moved from 21 to 10 because of the journal mapping.
        self.assertAlmostEqual(invoice.amount_tax, 10.0)
        self.assertAlmostEqual(invoice.amount_total, 110.0)
        # GL tax line moved: 10% present, original 21% absent.
        self.assertTrue(self._gl_tax_line(invoice, self.tax_10),
                        "Posted move must carry a GL tax line for the mapped 10% tax")
        self.assertFalse(self._gl_tax_line(invoice, self.tax_21),
                         "No GL tax line should remain for the original 21% tax")

    def test_multiple_tax_mappings(self):
        """Producto con {21%, 5%}; mapeo solo 21->10: el 5% pasa intacto, el 21 cambia a 10."""
        # Producto con dos impuestos DISTINTOS para poder separar componentes.
        multi_tax_product = self.env['product.product'].create({
            'name': 'Multi Tax Product',
            'type': 'consu',
            'list_price': 100.0,
            'taxes_id': [(6, 0, [self.tax_21.id, self.tax_5.id])],
        })
        multi_tax_product.taxes_id = self.tax_21 + self.tax_5

        # Mapear SOLO tax_21 -> tax_10 (tax_5 sin mapeo).
        self.env['account.journal.tax.mapping'].create({
            'journal_id': self.sales_journal.id,
            'original_tax_id': self.tax_21.id,
            'replacement_tax_id': self.tax_10.id,
            'tax_type': 'sale',
        })

        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': self.partner.id,
            'journal_id': self.sales_journal.id,
            'invoice_date': fields.Date.context_today(self.env['account.move']),
            'invoice_line_ids': [(0, 0, {
                'product_id': multi_tax_product.id,
                'quantity': 1,
                'price_unit': 100.0,
            })],
        })

        line = invoice.invoice_line_ids.filtered('product_id')
        # Exactly the mapped component changed; the extra 5% is untouched.
        self.assertEqual(line.tax_ids, self.tax_10 + self.tax_5,
                         "21->10 swapped, 5% passes through unchanged")

        invoice.action_post()

        # Total tax = 10 (was 21) + 5 (unchanged) = 15; total = 115.
        self.assertAlmostEqual(invoice.amount_tax, 15.0)
        self.assertAlmostEqual(invoice.amount_total, 115.0)

        # Per-tax GL lines: mapped 10% present, untouched 5% present, original 21% gone.
        line_10 = self._gl_tax_line(invoice, self.tax_10)
        line_5 = self._gl_tax_line(invoice, self.tax_5)
        self.assertTrue(line_10, "Mapped 10% tax line must exist")
        self.assertTrue(line_5, "Unmapped 5% tax line must pass through")
        self.assertFalse(self._gl_tax_line(invoice, self.tax_21),
                         "Original 21% must be fully replaced")
        # Per-tax amounts: 10 on the swapped component, 5 unchanged on the extra.
        self.assertAlmostEqual(abs(line_10.balance), 10.0)
        self.assertAlmostEqual(abs(line_5.balance), 5.0)

    def test_sale_mappings_dont_apply_to_purchase_invoices(self):
        """Un mapeo de VENTA en el diario de ventas no debe tocar la factura de proveedor.

        Type-scoping probado por dinero: aunque exista un mapeo de venta 21->10,
        la factura de proveedor (in_invoice) cobra el impuesto de proveedor SIN mapear (21%).
        """
        # Mapeo de VENTA 21->10 en el diario de ventas (no debe disparar en compras).
        self.env['account.journal.tax.mapping'].create({
            'journal_id': self.sales_journal.id,
            'original_tax_id': self.tax_21.id,
            'replacement_tax_id': self.tax_10.id,
            'tax_type': 'sale',
        })

        purchase_journal = self.env['account.journal'].create({
            'name': 'Test Purchase Journal',
            'type': 'purchase',
            'code': 'TPJ',
            'company_id': self.company.id,
        })

        vendor_invoice = self.env['account.move'].create({
            'move_type': 'in_invoice',
            'partner_id': self.partner.id,
            'journal_id': purchase_journal.id,
            'invoice_date': fields.Date.context_today(self.env['account.move']),
            'invoice_line_ids': [(0, 0, {
                'product_id': self.product.id,
                'quantity': 1,
                'price_unit': 100.0,
            })],
        })

        line = vendor_invoice.invoice_line_ids.filtered('product_id')
        # Sale mapping must not fire: the supplier (purchase) tax is unmapped 21%.
        self.assertEqual(line.tax_ids, self.purchase_tax_21,
                         "Vendor bill must keep the unmapped supplier 21% tax")

        vendor_invoice.action_post()

        # Money proves scoping: if the sale 21->10 mapping had leaked, tax would be 10.
        self.assertAlmostEqual(vendor_invoice.amount_tax, 21.0)
        self.assertAlmostEqual(vendor_invoice.amount_total, 121.0)
        self.assertTrue(self._gl_tax_line(vendor_invoice, self.purchase_tax_21),
                        "Posted bill must carry the unmapped supplier 21% GL tax line")
        self.assertFalse(self._gl_tax_line(vendor_invoice, self.tax_10),
                         "Sale mapping (->10%) must not appear on a purchase document")
