# -*- coding: utf-8 -*-

from odoo import fields
from odoo.tests import tagged
from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged('post_install', '-at_install')
class TestPurchaseJournalDefaults(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        # Controlled tax environment: clear company default taxes so fixture products
        # carry exactly the taxes we assign (core _force_default_tax links defaults additively).
        cls.env['res.company'].sudo().search([]).write({
            'account_sale_tax_id': False, 'account_purchase_tax_id': False,
        })

        cls.company = cls.env.company

        # Vendor
        cls.vendor = cls.env['res.partner'].create({
            'name': 'Test Vendor',
            'is_company': True,
            'supplier_rank': 1,
        })

        # Purchase taxes. Replacement rate (21%) deliberately DIFFERS from the
        # product rate (10%) so the journal swap is financially observable.
        cls.tax_purch_10 = cls.env['account.tax'].create({
            'name': 'P-Tax 10%',
            'amount': 10,
            'type_tax_use': 'purchase',
            'company_id': cls.company.id,
        })
        cls.tax_purch_21 = cls.env['account.tax'].create({
            'name': 'P-Tax 21%',
            'amount': 21,
            'type_tax_use': 'purchase',
            'company_id': cls.company.id,
        })

        # Expense accounts (B9: company_ids M2m, not company_id).
        cls.expense_account_1 = cls.env['account.account'].create({
            'name': 'Default Expense 1',
            'code': 'DEXP1',
            'account_type': 'expense',
            'company_ids': [(6, 0, cls.company.ids)],
        })
        cls.expense_account_2 = cls.env['account.account'].create({
            'name': 'Default Expense 2',
            'code': 'DEXP2',
            'account_type': 'expense',
            'company_ids': [(6, 0, cls.company.ids)],
        })

        # Purchase journal (with tax mapping)
        cls.purchase_journal_with_taxes = cls.env['account.journal'].create({
            'name': 'Test Purchase Journal With Taxes',
            'type': 'purchase',
            'code': 'TPJW',
            'company_id': cls.company.id,
            'default_account_id': cls.expense_account_1.id,
            'usar_cuenta_defecto_compras': True,
        })

        # Create tax mapping for purchase journal (map product tax 10% to journal tax 21%)
        cls.env['account.journal.tax.mapping'].create({
            'journal_id': cls.purchase_journal_with_taxes.id,
            'original_tax_id': cls.tax_purch_10.id,  # Product supplier tax
            'replacement_tax_id': cls.tax_purch_21.id,  # Journal mapped tax
            'tax_type': 'purchase',
        })

        # Purchase journal (without defaults)
        cls.purchase_journal_no_taxes = cls.env['account.journal'].create({
            'name': 'Test Purchase Journal No Taxes',
            'type': 'purchase',
            'code': 'TPJN',
            'company_id': cls.company.id,
            'default_account_id': cls.expense_account_2.id,
            'usar_cuenta_defecto_compras': False,
        })

        # Product's own expense account, so the "override OFF" path has a
        # deterministic, non-empty product account to fall back to (independent
        # of whatever category default the chart ships).
        cls.product_expense_account = cls.env['account.account'].create({
            'name': 'Product Expense Account',
            'code': 'PRODEXP',
            'account_type': 'expense',
            'company_ids': [(6, 0, cls.company.ids)],
        })

        # Product with supplier taxes. B8: 'product' -> 'consu'.
        # price_unit on the lines is 100.0, so rate% == amount_tax directly.
        cls.product = cls.env['product.product'].create({
            'name': 'Test Purch Product',
            'type': 'consu',
            'standard_price': 50.0,
            'supplier_taxes_id': [(6, 0, [cls.tax_purch_10.id])],
        })
        cls.product.product_tmpl_id.property_account_expense_id = cls.product_expense_account
        # AccountTestInvoicingCommon's company ships a default purchase tax that
        # leaks into supplier_taxes_id on create. Pin it to exactly tax_purch_10
        # so the company default doesn't pollute the expected supplier tax set.
        cls.product.supplier_taxes_id = cls.tax_purch_10

    def test_vendor_bill_without_tax_mapping(self):
        """Control: a journal without mappings keeps the product 10% through to the posted GL."""
        invoice = self.env['account.move'].create({
            'move_type': 'in_invoice',
            'partner_id': self.vendor.id,
            'journal_id': self.purchase_journal_no_taxes.id,
        })

        line = self.env['account.move.line'].create({
            'move_id': invoice.id,
            'name': 'Line',
            'quantity': 1,
            'price_unit': 100.0,
            'display_type': 'product',
            'product_id': self.product.id,
        })
        # Simulate UI onchange for account override
        line._onchange_product_id_set_journal_account()

        self.assertEqual(line.tax_ids, self.tax_purch_10,
                         "Line must keep the product supplier tax (10%)")

        # FINANCIAL EFFECT control: product's own 10% drives the posted money.
        invoice.invoice_date = fields.Date.context_today(invoice)
        invoice.action_post()

        self.assertAlmostEqual(invoice.amount_tax, 10.0,
                               msg="Product rate (10%) must be untouched on a journal without mappings")
        self.assertAlmostEqual(invoice.amount_total, 110.0)
        self.assertTrue(
            invoice.line_ids.filtered(lambda l: l.tax_line_id == self.tax_purch_10),
            "A GL tax line for the product 10% tax must exist",
        )
        self.assertFalse(
            invoice.line_ids.filtered(lambda l: l.tax_line_id == self.tax_purch_21),
            "No GL tax line for the 21% replacement may exist (no mapping on this journal)",
        )

    def test_vendor_bill_with_tax_mapping(self):
        """Mapped journal tax (21%) replaces supplier 10% and drives the posted GL."""
        invoice = self.env['account.move'].create({
            'move_type': 'in_invoice',
            'partner_id': self.vendor.id,
            'journal_id': self.purchase_journal_with_taxes.id,
        })

        line = self.env['account.move.line'].create({
            'move_id': invoice.id,
            'name': 'Line',
            'quantity': 1,
            'price_unit': 100.0,
            'display_type': 'product',
            'product_id': self.product.id,
        })
        # Simulate UI onchange for account override
        line._onchange_product_id_set_journal_account()

        # Should use mapped tax (21%) instead of product supplier tax (10%)
        self.assertEqual(line.tax_ids, self.tax_purch_21,
                         "Line must carry the mapped journal tax (21%), not supplier 10%")

        # FINANCIAL EFFECT: the higher mapped 21% drives the booked money, NOT 10%.
        invoice.invoice_date = fields.Date.context_today(invoice)
        invoice.action_post()

        self.assertAlmostEqual(invoice.amount_tax, 21.0,
                               msg="Mapped 21% must drive the booked tax, not supplier 10%")
        self.assertNotAlmostEqual(invoice.amount_tax, 10.0)
        self.assertAlmostEqual(invoice.amount_total, 121.0)
        self.assertTrue(
            invoice.line_ids.filtered(lambda l: l.tax_line_id == self.tax_purch_21),
            "A GL tax line for the mapped 21% tax must exist",
        )
        self.assertFalse(
            invoice.line_ids.filtered(lambda l: l.tax_line_id == self.tax_purch_10),
            "No GL tax line for the original 10% tax may exist",
        )

    def test_multiple_purchase_tax_mappings(self):
        """Partial multi-tax: only the MAPPED component (10->21) changes; the unmapped 7% is untouched."""
        # Unmapped extra tax: never referenced by any mapping.
        tax_unmapped_7 = self.env['account.tax'].create({
            'name': 'P-Tax 7%',
            'amount': 7,
            'type_tax_use': 'purchase',
            'company_id': self.company.id,
        })

        # Product carries {mapped 10%, UNMAPPED 7%}.
        multi_tax_product = self.env['product.product'].create({
            'name': 'Multi Tax Product',
            'type': 'consu',
            'standard_price': 50.0,
            'supplier_taxes_id': [(6, 0, [self.tax_purch_10.id, tax_unmapped_7.id])],
        })
        multi_tax_product.supplier_taxes_id = self.tax_purch_10 + tax_unmapped_7

        # Only the 10% mapping exists (from setUp). 7% is deliberately unmapped.
        invoice = self.env['account.move'].create({
            'move_type': 'in_invoice',
            'partner_id': self.vendor.id,
            'journal_id': self.purchase_journal_with_taxes.id,
        })

        line = self.env['account.move.line'].create({
            'move_id': invoice.id,
            'name': 'Line',
            'quantity': 1,
            'price_unit': 100.0,
            'display_type': 'product',
            'product_id': multi_tax_product.id,
        })

        # 10% maps to 21%; 7% passes through unchanged.
        self.assertEqual(line.tax_ids, self.tax_purch_21 + tax_unmapped_7,
                         "Mapped tax becomes 21%; the unmapped 7% must remain")

        # FINANCIAL EFFECT: per-tax amounts must show exactly the mapped component changed.
        invoice.invoice_date = fields.Date.context_today(invoice)
        invoice.action_post()

        # The two taxes are not in groups by default; assert per-tax GL lines.
        mapped_tax_line = invoice.line_ids.filtered(lambda l: l.tax_line_id == self.tax_purch_21)
        extra_tax_line = invoice.line_ids.filtered(lambda l: l.tax_line_id == tax_unmapped_7)
        self.assertTrue(mapped_tax_line, "GL tax line for mapped 21% must exist")
        self.assertTrue(extra_tax_line, "GL tax line for unmapped 7% must exist")
        # Vendor bill: tax lines are debits. Compare magnitudes.
        self.assertAlmostEqual(abs(mapped_tax_line.balance), 21.0,
                               msg="Mapped component must book 21 (10 -> 21)")
        self.assertAlmostEqual(abs(extra_tax_line.balance), 7.0,
                               msg="Unmapped 7% must book exactly 7, unchanged")
        self.assertAlmostEqual(invoice.amount_tax, 28.0,
                               msg="Total tax = mapped 21 + unmapped 7")
        # The original 10% must NOT appear anywhere in the GL.
        self.assertFalse(
            invoice.line_ids.filtered(lambda l: l.tax_line_id == self.tax_purch_10),
            "No GL tax line for the original 10% may exist after mapping",
        )

    def test_account_override_on_purchases_enabled(self):
        """PO -> bill: the journal default expense account overrides on the POSTED line."""
        po = self.env['purchase.order'].create({
            'partner_id': self.vendor.id,
            'journal_id': self.purchase_journal_with_taxes.id,
        })
        self.env['purchase.order.line'].create({
            'order_id': po.id,
            'product_id': self.product.id,
            'name': 'POLine',
            'product_qty': 1,
            'product_uom_id': self.product.uom_id.id,
            'price_unit': 100.0,
            'tax_ids': [(6, 0, self.product.supplier_taxes_id.ids)],
        })

        po.button_confirm()
        for pol in po.order_line:
            pol.qty_received = 1

        po.action_create_invoice()
        bill = po.invoice_ids[:1]
        bill.invoice_date = fields.Date.context_today(bill)
        bill.action_post()

        inv_line = bill.invoice_line_ids.filtered('product_id')
        self.assertTrue(inv_line, "Bill product line should exist")
        # Toggle ON -> posted product line account is the journal default account.
        self.assertEqual(inv_line.account_id, self.expense_account_1,
                         "Posted product line must use the journal default expense account when enabled")

    def test_account_override_on_purchases_disabled(self):
        """Toggle OFF: the posted line account derives from the product/category, not the journal."""
        po = self.env['purchase.order'].create({
            'partner_id': self.vendor.id,
            'journal_id': self.purchase_journal_no_taxes.id,
        })
        self.env['purchase.order.line'].create({
            'order_id': po.id,
            'product_id': self.product.id,
            'name': 'POLine',
            'product_qty': 1,
            'product_uom_id': self.product.uom_id.id,
            'price_unit': 100.0,
            'tax_ids': [(6, 0, self.product.supplier_taxes_id.ids)],
        })

        po.button_confirm()
        for pol in po.order_line:
            pol.qty_received = 1

        po.action_create_invoice()
        bill = po.invoice_ids[:1]
        bill.invoice_date = fields.Date.context_today(bill)
        bill.action_post()

        inv_line = bill.invoice_line_ids.filtered('product_id')
        self.assertTrue(inv_line, "Bill product line should exist")

        # The expected account is the product expense account that core resolves
        # (NOT the journal default account).
        expected_account = self.product.product_tmpl_id.with_company(
            self.company).get_product_accounts()['expense']
        self.assertEqual(expected_account, self.product_expense_account,
                         "Sanity: product resolves to its configured expense account")
        self.assertEqual(inv_line.account_id, expected_account,
                         "Posted line must derive its account from the product when override is OFF")
        self.assertNotEqual(inv_line.account_id, self.expense_account_2,
                            "Posted line must NOT use the journal default account when override is OFF")

    def test_po_to_bill_uses_journal_purchase_tax_mapping(self):
        """PO -> bill continuity: posted bill books the mapped 21% (not supplier 10%)."""
        po = self.env['purchase.order'].create({
            'partner_id': self.vendor.id,
            'journal_id': self.purchase_journal_with_taxes.id,
        })
        self.env['purchase.order.line'].create({
            'order_id': po.id,
            'product_id': self.product.id,
            'name': 'POLine',
            'product_qty': 1,
            # B10: purchase line uom field renamed product_uom -> product_uom_id;
            # product.uom_po_id removed in 19 -> uom_id.
            'product_uom_id': self.product.uom_id.id,
            'price_unit': 100.0,
            # B2: purchase line taxes_id -> tax_ids.
            'tax_ids': [(6, 0, self.product.supplier_taxes_id.ids)],
        })

        # Put PO in to invoice state by confirming and marking as received
        po.button_confirm()
        for line in po.order_line:
            line.qty_received = 1

        po.action_create_invoice()
        invoice = po.invoice_ids[:1]

        inv_line = invoice.invoice_line_ids.filtered('product_id')
        self.assertTrue(inv_line, "Invoice line should exist")
        # Should use mapped tax (21%) with fiscal position applied if exists
        expected_tax = self.tax_purch_21  # The mapped tax from our setup
        expected_taxes = (invoice.fiscal_position_id.map_tax(expected_tax)
                          if invoice.fiscal_position_id else expected_tax)
        self.assertEqual(inv_line.tax_ids, expected_taxes,
                         "Bill line must carry the mapped purchase tax")

        # FINANCIAL EFFECT: post and assert the mapped 21% reaches the GL.
        invoice.invoice_date = fields.Date.context_today(invoice)
        invoice.action_post()

        self.assertAlmostEqual(invoice.amount_tax, 21.0,
                               msg="Posted bill must book the mapped 21%, not supplier 10%")
        self.assertNotAlmostEqual(invoice.amount_tax, 10.0)
        self.assertTrue(
            invoice.line_ids.filtered(lambda l: l.tax_line_id == self.tax_purch_21),
            "Posted GL tax line must be for the mapped 21% tax",
        )
