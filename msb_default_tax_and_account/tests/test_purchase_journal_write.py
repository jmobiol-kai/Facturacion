# -*- coding: utf-8 -*-

from odoo import fields
from odoo.tests import tagged

from .common import MsbPurchaseDefaultTaxesTestCommon


@tagged('post_install', '-at_install')
class TestPurchaseJournalWrite(MsbPurchaseDefaultTaxesTestCommon):
    """journal_id changes made through write(), not only onchange."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tax_x = cls.env['account.tax'].create({
            'name': 'IVA 10,5% X',
            'amount': 27.0,
            'type_tax_use': 'purchase',
            'company_id': cls.company.id,
        })
        cls.tax_105 = cls.env['account.tax'].create({
            'name': 'IVA 10,5%',
            'amount': 10.5,
            'type_tax_use': 'purchase',
            'company_id': cls.company.id,
        })
        cls.tax_other = cls.env['account.tax'].create({
            'name': 'Other purchase 5%',
            'amount': 5.0,
            'type_tax_use': 'purchase',
            'company_id': cls.company.id,
        })
        cls.tax_other_repl = cls.env['account.tax'].create({
            'name': 'Other purchase 8%',
            'amount': 8.0,
            'type_tax_use': 'purchase',
            'company_id': cls.company.id,
        })
        cls.journal_x = cls.env['account.journal'].create({
            'name': 'Diario Compras X',
            'type': 'purchase',
            'code': 'POX',
            'company_id': cls.company.id,
            'default_account_id': cls.purchase_journal_account_1.id,
        })
        cls.journal_fe = cls.env['account.journal'].create({
            'name': 'Diario Compras FE',
            'type': 'purchase',
            'code': 'POFE',
            'company_id': cls.company.id,
            'default_account_id': cls.purchase_journal_account_2.id,
        })
        # X journal: product 10% → IVA 10,5% X (booked at 27% so a missed remap is visible).
        cls.env['account.journal.tax.mapping'].create({
            'journal_id': cls.journal_x.id,
            'original_tax_id': cls.tax_purchase_10.id,
            'replacement_tax_id': cls.tax_x.id,
            'tax_type': 'purchase',
        })

    def _line(self, order):
        return order.order_line.filtered(lambda line: not line.display_type and not line.is_downpayment)

    def _bill_product_lines(self, bill):
        return bill.invoice_line_ids.filtered(
            lambda line: line.display_type == 'product' and not line.is_downpayment
        )

    def _confirmed_po_and_draft_bill(self, journal):
        order = self.env['purchase.order'].create({
            'partner_id': self.vendor.id,
            'journal_id': journal.id,
            'company_id': self.company.id,
        })
        self.env['purchase.order.line'].create({
            'order_id': order.id,
            'product_id': self.purchase_product.id,
            'product_qty': 1,
            'product_uom_id': self.purchase_product.uom_id.id,
            'price_unit': 100.0,
        })
        order.button_confirm()
        order.action_create_invoice()
        bill = order.invoice_ids
        bill.invoice_date = fields.Date.context_today(bill)
        return order, bill

    def test_write_remaps_po_lines_and_draft_bill_without_maps(self):
        """X → FE with no maps: PO lines and the draft bill fall back to the product tax."""
        order, bill = self._confirmed_po_and_draft_bill(self.journal_x)
        line = self._line(order)
        bill_line = self._bill_product_lines(bill)
        self.assertEqual(line.tax_ids, self.tax_x)
        self.assertEqual(bill.journal_id, self.journal_x)
        self.assertEqual(bill_line.tax_ids, self.tax_x)

        order.write({'journal_id': self.journal_fe.id})

        self.assertEqual(line.tax_ids, self.tax_purchase_10)
        self.assertEqual(bill.journal_id, self.journal_fe)
        self.assertEqual(bill_line.tax_ids, self.tax_purchase_10)
        self.assertEqual(bill.state, 'draft')
        self.assertAlmostEqual(bill.amount_tax, 10.0)
        self.assertAlmostEqual(bill.amount_total, 110.0)

    def test_reverse_map_wins_over_product_tax(self):
        """A reverse map on the tax currently on the line beats the product tax.

        Product supplier tax is 21%. The line carries IVA 10,5% X. The FE
        journal maps that X tax to IVA 10,5% and does not map 21%. Resetting
        from the product alone would leave 21%.
        """
        product = self.env['product.product'].create({
            'name': 'Product taxed at 21',
            'type': 'consu',
            'purchase_method': 'purchase',
            'supplier_taxes_id': [(6, 0, [self.tax_purchase_21.id])],
        })
        product.supplier_taxes_id = self.tax_purchase_21
        journal_x = self.env['account.journal'].create({
            'name': 'X journal 21 to X',
            'type': 'purchase',
            'code': 'PX21',
            'company_id': self.company.id,
            'default_account_id': self.purchase_journal_account_1.id,
        })
        self.create_tax_mapping(journal_x, self.tax_purchase_21, self.tax_x, 'purchase')
        self.create_tax_mapping(self.journal_fe, self.tax_x, self.tax_105, 'purchase')

        order = self.env['purchase.order'].create({
            'partner_id': self.vendor.id,
            'journal_id': journal_x.id,
            'company_id': self.company.id,
        })
        line = self.env['purchase.order.line'].create({
            'order_id': order.id,
            'product_id': product.id,
            'product_qty': 1,
            'product_uom_id': product.uom_id.id,
            'price_unit': 100.0,
        })
        self.assertEqual(line.tax_ids, self.tax_x)
        order.button_confirm()
        order.action_create_invoice()
        bill = order.invoice_ids
        bill.invoice_date = fields.Date.context_today(bill)
        bill_line = self._bill_product_lines(bill)
        self.assertEqual(bill_line.tax_ids, self.tax_x)

        order.write({'journal_id': self.journal_fe.id})

        self.assertEqual(line.tax_ids, self.tax_105)
        self.assertNotEqual(line.tax_ids, self.tax_purchase_21)
        self.assertEqual(bill.journal_id, self.journal_fe)
        self.assertEqual(bill_line.tax_ids, self.tax_105)
        self.assertAlmostEqual(bill.amount_tax, 10.5)
        self.assertAlmostEqual(bill.amount_total, 110.5)

    def test_unrelated_maps_do_not_keep_the_x_tax(self):
        """An FE journal that maps other taxes, but not this X tax, drops the X tax."""
        self.create_tax_mapping(self.journal_fe, self.tax_other, self.tax_other_repl, 'purchase')
        order, bill = self._confirmed_po_and_draft_bill(self.journal_x)

        order.write({'journal_id': self.journal_fe.id})

        line = self._line(order)
        bill_line = self._bill_product_lines(bill)
        self.assertEqual(line.tax_ids, self.tax_purchase_10)
        self.assertEqual(bill_line.tax_ids, self.tax_purchase_10)
        self.assertFalse(line.tax_ids & self.tax_x)
        self.assertFalse(bill_line.tax_ids & self.tax_x)
        self.assertAlmostEqual(bill.amount_tax, 10.0)

    def test_posted_bill_is_not_modified(self):
        order, bill = self._confirmed_po_and_draft_bill(self.journal_x)
        bill.action_post()
        self.assertEqual(bill.state, 'posted')
        posted_tax = bill.invoice_line_ids.filtered(lambda line: line.display_type == 'product').tax_ids
        posted_amount = bill.amount_tax

        order.write({'journal_id': self.journal_fe.id})

        self.assertEqual(self._line(order).tax_ids, self.tax_purchase_10)
        self.assertEqual(bill.state, 'posted')
        self.assertEqual(bill.journal_id, self.journal_x)
        self.assertEqual(
            bill.invoice_line_ids.filtered(lambda line: line.display_type == 'product').tax_ids,
            posted_tax,
        )
        self.assertAlmostEqual(bill.amount_tax, posted_amount)

    def test_cancelled_bill_is_not_modified(self):
        order, bill = self._confirmed_po_and_draft_bill(self.journal_x)
        bill.button_cancel()
        self.assertEqual(bill.state, 'cancel')

        order.write({'journal_id': self.journal_fe.id})

        self.assertEqual(self._line(order).tax_ids, self.tax_purchase_10)
        self.assertEqual(bill.state, 'cancel')
        self.assertEqual(bill.journal_id, self.journal_x)
        self.assertEqual(self._bill_product_lines(bill).tax_ids, self.tax_x)

    def test_section_note_and_downpayment_lines_are_skipped(self):
        order = self.env['purchase.order'].create({
            'partner_id': self.vendor.id,
            'journal_id': self.journal_x.id,
            'company_id': self.company.id,
        })
        product_line = self.env['purchase.order.line'].create({
            'order_id': order.id,
            'product_id': self.purchase_product.id,
            'product_qty': 1,
            'product_uom_id': self.purchase_product.uom_id.id,
            'price_unit': 100.0,
        })
        section = self.env['purchase.order.line'].create({
            'order_id': order.id,
            'name': 'Seccion',
            'display_type': 'line_section',
            'product_qty': 0,
        })
        note = self.env['purchase.order.line'].create({
            'order_id': order.id,
            'name': 'Nota',
            'display_type': 'line_note',
            'product_qty': 0,
        })
        downpayment = self.env['purchase.order.line'].create({
            'order_id': order.id,
            'name': 'Anticipo',
            'is_downpayment': True,
            'product_qty': 1,
            'price_unit': 10.0,
            'tax_ids': [(6, 0, [self.tax_x.id])],
        })
        self.assertEqual(product_line.tax_ids, self.tax_x)

        order.write({'journal_id': self.journal_fe.id})

        self.assertEqual(product_line.tax_ids, self.tax_purchase_10)
        self.assertFalse(section.tax_ids)
        self.assertFalse(note.tax_ids)
        self.assertEqual(downpayment.tax_ids, self.tax_x)
        self.assertTrue(downpayment.is_downpayment)

    def test_vendor_bill_journal_write_resets_to_product_tax(self):
        """Changing the journal on a draft vendor bill with no PO uses the same fallback."""
        bill = self.env['account.move'].create({
            'move_type': 'in_invoice',
            'partner_id': self.vendor.id,
            'journal_id': self.journal_x.id,
            'invoice_date': fields.Date.context_today(self.env.user),
        })
        line = self.env['account.move.line'].create({
            'move_id': bill.id,
            'product_id': self.purchase_product.id,
            'quantity': 1,
            'price_unit': 100.0,
            'display_type': 'product',
        })
        self.assertEqual(line.tax_ids, self.tax_x)

        bill.write({'journal_id': self.journal_fe.id})

        self.assertEqual(bill.journal_id, self.journal_fe)
        self.assertEqual(line.tax_ids, self.tax_purchase_10)
        self.assertAlmostEqual(bill.amount_tax, 10.0)
        self.assertAlmostEqual(bill.amount_total, 110.0)

    def test_sale_journal_does_not_replace_the_bill_journal(self):
        order, bill = self._confirmed_po_and_draft_bill(self.journal_x)
        sale_journal = self.env['account.journal'].create({
            'name': 'Not a purchase journal',
            'type': 'sale',
            'code': 'NPSJ',
            'company_id': self.company.id,
        })

        order.write({'journal_id': sale_journal.id})

        self.assertEqual(order.journal_id, sale_journal)
        self.assertEqual(self._line(order).tax_ids, self.tax_purchase_10)
        self.assertEqual(bill.journal_id, self.journal_x)
        self.assertEqual(self._bill_product_lines(bill).tax_ids, self.tax_x)
        self.assertEqual(bill.state, 'draft')

    def test_customer_invoice_journal_write_does_not_remap(self):
        """Manual customer invoices still remap only through onchange, not write()."""
        tax_sale = self.env['account.tax'].create({
            'name': 'Sale 10',
            'amount': 10.0,
            'type_tax_use': 'sale',
            'company_id': self.company.id,
        })
        tax_sale_mapped = self.env['account.tax'].create({
            'name': 'Sale 21 mapped',
            'amount': 21.0,
            'type_tax_use': 'sale',
            'company_id': self.company.id,
        })
        plain = self.env['account.journal'].create({
            'name': 'Plain sale',
            'type': 'sale',
            'code': 'PLNS',
            'company_id': self.company.id,
        })
        mapped = self.env['account.journal'].create({
            'name': 'Mapped sale',
            'type': 'sale',
            'code': 'MPDS',
            'company_id': self.company.id,
        })
        self.create_tax_mapping(mapped, tax_sale, tax_sale_mapped, 'sale')
        product = self.env['product.product'].create({
            'name': 'Sale product',
            'type': 'consu',
            'taxes_id': [(6, 0, [tax_sale.id])],
        })
        product.taxes_id = tax_sale
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': self.vendor.id,
            'journal_id': plain.id,
            'invoice_date': fields.Date.context_today(self.env.user),
        })
        line = self.env['account.move.line'].create({
            'move_id': invoice.id,
            'product_id': product.id,
            'quantity': 1,
            'price_unit': 100.0,
            'display_type': 'product',
        })
        self.assertEqual(line.tax_ids, tax_sale)

        invoice.write({'journal_id': mapped.id})

        self.assertEqual(invoice.journal_id, mapped)
        self.assertEqual(line.tax_ids, tax_sale)
