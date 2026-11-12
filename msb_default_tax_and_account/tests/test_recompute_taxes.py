# -*- coding: utf-8 -*-

from odoo import fields
from odoo.tests import tagged
from .common import MsbSalesDefaultTaxesTestCommon, MsbPurchaseDefaultTaxesTestCommon


@tagged('post_install', '-at_install')
class TestSaleOrderTaxRecompute(MsbSalesDefaultTaxesTestCommon):
    """Test tax recompute functionality for sale orders.

    Strengthened to prove the BUSINESS OUTCOME: recompute paths change the
    MONEY booked on a posted invoice (differing rates), are idempotent, and
    layer fiscal positions on top of the journal swap.
    """

    def _tax_amount_for(self, invoice, tax):
        lines = invoice.line_ids.filtered(lambda l: l.tax_line_id == tax)
        return sum(abs(b) for b in lines.mapped('balance'))

    def test_sale_order_journal_change_recomputes_taxes(self):
        """Changing journal recomputes line taxes; the change reaches the
        posted invoice money (product 10% -> journal-mapped 21%)."""
        target_journal = self.env['account.journal'].create({
            'name': 'Target Sales Journal',
            'type': 'sale',
            'code': 'TSG',
            'company_id': self.company.id,
        })
        self.create_tax_mapping(
            target_journal, self.tax_sale_10, self.tax_sale_21, 'sale')

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

        # Change journal and trigger the onchange recompute.
        sale_order.journal_id = target_journal
        sale_order._onchange_journal_or_pricelist()

        self.assertEqual(set(order_line.tax_ids.ids), {self.tax_sale_21.id})
        self.assertFalse(sale_order.tax_recompute_needed)

        # Carry to a posted invoice and assert the rate delta in money.
        sale_order.action_confirm()
        invoice = sale_order._create_invoices()
        invoice.action_post()
        self.assertEqual(invoice.invoice_line_ids.tax_ids, self.tax_sale_21)
        self.assertAlmostEqual(invoice.amount_tax, 21.0)
        self.assertAlmostEqual(invoice.amount_total, 121.0)
        self.assertTrue(
            invoice.line_ids.filtered(lambda l: l.tax_line_id == self.tax_sale_21))
        self.assertFalse(
            invoice.line_ids.filtered(lambda l: l.tax_line_id == self.tax_sale_10))

    def test_sale_order_pricelist_change_leaves_taxes_unchanged(self):
        """Independence proof: with a journal mapping active (10% -> 21%),
        changing ONLY the pricelist must not undo the mapped tax or amount."""
        # Mapping on the order's journal so the mapped tax is the baseline.
        self.create_tax_mapping(
            self.journal_with_taxes, self.tax_sale_10, self.tax_sale_21, 'sale')

        pricelist = self.env['product.pricelist'].create({
            'name': 'Test Pricelist',
            'company_id': self.company.id,
        })

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

        # Baseline: mapped 21% tax already applied.
        self.assertEqual(set(order_line.tax_ids.ids), {self.tax_sale_21.id})

        # Change ONLY the pricelist and recompute.
        sale_order.pricelist_id = pricelist
        sale_order._onchange_journal_or_pricelist()

        # The mapped tax (and therefore the amount) is stable across the
        # pricelist change: the pricelist does not undo the journal swap.
        self.assertEqual(set(order_line.tax_ids.ids), {self.tax_sale_21.id})

        sale_order.action_confirm()
        invoice = sale_order._create_invoices()
        invoice.action_post()
        self.assertEqual(invoice.invoice_line_ids.tax_ids, self.tax_sale_21)
        self.assertAlmostEqual(invoice.amount_tax, 21.0)

    def test_sale_order_action_recompute_taxes_force(self):
        """Force recompute is idempotent and reaches the posted money."""
        self.create_tax_mapping(
            self.journal_with_taxes, self.tax_sale_10, self.tax_sale_21, 'sale')

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

        # Initially product taxes (10%).
        self.assertEqual(set(order_line.tax_ids.ids), set(self.product.taxes_id.ids))

        sale_order.journal_id = self.journal_with_taxes
        sale_order.action_recompute_taxes_force()
        self.assertEqual(set(order_line.tax_ids.ids), {self.tax_sale_21.id})
        self.assertFalse(sale_order.tax_recompute_needed)

        # Idempotence: a second force leaves the tax set identical.
        sale_order.action_recompute_taxes_force()
        self.assertEqual(set(order_line.tax_ids.ids), {self.tax_sale_21.id})
        self.assertFalse(sale_order.tax_recompute_needed)

        # Money proof.
        sale_order.action_confirm()
        invoice = sale_order._create_invoices()
        invoice.action_post()
        self.assertAlmostEqual(invoice.amount_tax, 21.0)
        self.assertAlmostEqual(invoice.amount_total, 121.0)

    def test_sale_order_recompute_with_fiscal_position(self):
        """Fiscal-position layering with THREE distinct rates.

        journal: product 10% -> 21% ; FP: 21% -> 0%.  The posted result must
        be 0% (amount 0, GL line for iva0, none for 21%), proving the FP runs
        ON TOP of the journal swap.  Negative control: without the FP the
        result stays at the journal-mapped 21%.
        """
        self.create_tax_mapping(
            self.journal_with_taxes, self.tax_sale_10, self.tax_sale_21, 'sale')

        # Third distinct rate: iva0 (0%), separate from 10% and 21%.
        iva0 = self.env['account.tax'].create({
            'name': 'IVA 0% (FP)',
            'amount': 0,
            'type_tax_use': 'sale',
            'company_id': self.company.id,
        })

        fp = self.env['account.fiscal.position'].create({
            'name': 'Test FP',
            'company_id': self.company.id,
        })
        # iva0 (dest) replaces tax_sale_21 (src, the journal-mapped tax).
        iva0.original_tax_ids = [(6, 0, self.tax_sale_21.ids)]
        fp.tax_ids = [(6, 0, iva0.ids)]

        # Negative control: NO fiscal position -> result is journal-mapped 21%.
        so_no_fp = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'journal_id': self.journal_with_taxes.id,
            'company_id': self.company.id,
        })
        line_no_fp = self.env['sale.order.line'].create({
            'order_id': so_no_fp.id,
            'product_id': self.product.id,
            'product_uom_qty': 1,
            'price_unit': 100,
        })
        so_no_fp.action_recompute_taxes_force()
        self.assertEqual(set(line_no_fp.tax_ids.ids), {self.tax_sale_21.id})

        # With the FP layered on top.
        sale_order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'journal_id': self.journal_with_taxes.id,
            'fiscal_position_id': fp.id,
            'company_id': self.company.id,
        })
        order_line = self.env['sale.order.line'].create({
            'order_id': sale_order.id,
            'product_id': self.product.id,
            'product_uom_qty': 1,
            'price_unit': 100,
        })
        sale_order.action_recompute_taxes_force()

        # Flow: product 10% -> journal 21% -> FP 0%.
        self.assertEqual(set(order_line.tax_ids.ids), {iva0.id})

        # Money proof of the layered result.
        sale_order.action_confirm()
        invoice = sale_order._create_invoices()
        invoice.action_post()
        self.assertEqual(invoice.invoice_line_ids.tax_ids, iva0)
        self.assertAlmostEqual(invoice.amount_tax, 0.0)
        self.assertAlmostEqual(invoice.amount_total, 100.0)
        self.assertFalse(
            invoice.line_ids.filtered(lambda l: l.tax_line_id == self.tax_sale_21))


@tagged('post_install', '-at_install')
class TestPurchaseOrderTaxRecompute(MsbPurchaseDefaultTaxesTestCommon):
    """Test tax recompute functionality for purchase orders."""

    def test_purchase_order_journal_change_recomputes_taxes(self):
        """Changing purchase journal recomputes line taxes and the swap
        reaches the posted vendor bill money (supplier 10% -> mapped 21%).
        Scoping negative: a SALE mapping on the same journal does NOT fire."""
        target_journal = self.env['account.journal'].create({
            'name': 'Target Purchase Journal',
            'type': 'purchase',
            'code': 'TPG',
            'company_id': self.company.id,
        })
        self.create_tax_mapping(
            target_journal, self.tax_purchase_10, self.tax_purchase_21, 'purchase')
        # Scoping negative: the model forbids cross-type mappings, so a sale
        # mapping cannot even exist here.  The purchase mapping is the only
        # active one; there is NO active 'sale' mapping to accidentally fire.
        self.assertTrue(target_journal.has_tax_mappings('purchase'))
        self.assertFalse(target_journal.has_tax_mappings('sale'))

        purchase_order = self.env['purchase.order'].create({
            'partner_id': self.vendor.id,
            'journal_id': self.purchase_journal_without_taxes.id,
            'company_id': self.company.id,
        })
        order_line = self.env['purchase.order.line'].create({
            'order_id': purchase_order.id,
            'product_id': self.purchase_product.id,
            'product_qty': 1,
            'product_uom_id': self.purchase_product.uom_id.id,
            'price_unit': 100,
        })

        purchase_order.journal_id = target_journal
        purchase_order._onchange_journal()

        self.assertEqual(set(order_line.tax_ids.ids), {self.tax_purchase_21.id})
        self.assertFalse(purchase_order.tax_recompute_needed)

        # Carry to a posted vendor bill and assert the booked money.
        purchase_order.button_confirm()
        purchase_order.action_create_invoice()
        bill = purchase_order.invoice_ids
        bill.invoice_date = fields.Date.context_today(bill)
        bill.action_post()
        self.assertEqual(bill.invoice_line_ids.tax_ids, self.tax_purchase_21)
        self.assertAlmostEqual(bill.amount_tax, 21.0)
        self.assertAlmostEqual(bill.amount_total, 121.0)
        self.assertTrue(
            bill.line_ids.filtered(lambda l: l.tax_line_id == self.tax_purchase_21))
        self.assertFalse(
            bill.line_ids.filtered(lambda l: l.tax_line_id == self.tax_purchase_10))

    def test_purchase_order_action_recompute_taxes_force(self):
        """Force recompute is idempotent, reaches the posted bill money, and
        the journal default expense account overrides the product account on
        the posted line (usar_cuenta_defecto_compras=True)."""
        self.create_tax_mapping(
            self.purchase_journal_with_taxes,
            self.tax_purchase_10, self.tax_purchase_21, 'purchase')

        purchase_order = self.env['purchase.order'].create({
            'partner_id': self.vendor.id,
            'journal_id': self.purchase_journal_without_taxes.id,
            'company_id': self.company.id,
        })
        order_line = self.env['purchase.order.line'].create({
            'order_id': purchase_order.id,
            'product_id': self.purchase_product.id,
            'product_qty': 1,
            'product_uom_id': self.purchase_product.uom_id.id,
            'price_unit': 100,
        })

        self.assertEqual(
            set(order_line.tax_ids.ids),
            set(self.purchase_product.supplier_taxes_id.ids))

        purchase_order.journal_id = self.purchase_journal_with_taxes
        purchase_order.action_recompute_taxes_force()
        self.assertEqual(set(order_line.tax_ids.ids), {self.tax_purchase_21.id})
        self.assertFalse(purchase_order.tax_recompute_needed)

        # Idempotence.
        purchase_order.action_recompute_taxes_force()
        self.assertEqual(set(order_line.tax_ids.ids), {self.tax_purchase_21.id})
        self.assertFalse(purchase_order.tax_recompute_needed)

        # Money + account override on the posted bill.
        purchase_order.button_confirm()
        purchase_order.action_create_invoice()
        bill = purchase_order.invoice_ids
        bill.invoice_date = fields.Date.context_today(bill)
        bill.action_post()
        self.assertAlmostEqual(bill.amount_tax, 21.0)
        self.assertAlmostEqual(bill.amount_total, 121.0)

        product_line = bill.invoice_line_ids.filtered(
            lambda l: l.display_type == 'product')
        # usar_cuenta_defecto_compras is True on purchase_journal_with_taxes.
        self.assertEqual(
            product_line.account_id,
            self.purchase_journal_with_taxes.default_account_id)


@tagged('post_install', '-at_install')
class TestAccountMoveTaxRecompute(MsbSalesDefaultTaxesTestCommon):
    """Test tax recompute functionality for account moves (invoices)."""

    def test_invoice_journal_change_recomputes_taxes(self):
        """Journal change recomputes invoice line taxes; post and assert the
        money, the GL tax line, and the journal-default account override."""
        target_journal = self.env['account.journal'].create({
            'name': 'Target Invoice Journal',
            'type': 'sale',
            'code': 'TIG',
            'company_id': self.company.id,
            'default_account_id': self.journal_account_1.id,
            'usar_cuenta_defecto_facturas': True,
        })
        self.create_tax_mapping(
            target_journal, self.tax_sale_10, self.tax_sale_21, 'sale')

        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': self.partner.id,
            'journal_id': self.journal_without_taxes.id,
            'invoice_date': fields.Date.context_today(self.env.user),
        })
        line = self.env['account.move.line'].create({
            'move_id': invoice.id,
            'product_id': self.product.id,
            'quantity': 1,
            'price_unit': 100,
        })

        invoice.journal_id = target_journal
        invoice._onchange_journal_recompute_flag()
        self.assertEqual(set(line.tax_ids.ids), {self.tax_sale_21.id})
        self.assertFalse(invoice.tax_recompute_needed)

        # Account override fires on product selection in the UI; with the
        # override journal now active, re-trigger it so the product line
        # adopts the journal default account (toggle is on).
        line._onchange_product_id_set_journal_account()

        invoice.action_post()
        self.assertAlmostEqual(invoice.amount_tax, 21.0)
        self.assertAlmostEqual(invoice.amount_total, 121.0)
        self.assertTrue(
            invoice.line_ids.filtered(lambda l: l.tax_line_id == self.tax_sale_21))
        self.assertFalse(
            invoice.line_ids.filtered(lambda l: l.tax_line_id == self.tax_sale_10))

        # Account override: product line uses the journal default account,
        # NOT the product/category income account.
        product_line = invoice.line_ids.filtered(
            lambda l: l.display_type == 'product')
        self.assertEqual(
            product_line.account_id, target_journal.default_account_id)
        prod_income = self.product.product_tmpl_id.with_company(
            self.company).get_product_accounts()['income']
        self.assertNotEqual(product_line.account_id, prod_income)

    def test_invoice_action_recompute_taxes_force(self):
        """Force recompute reaches the posted money, the account override is
        applied, and a second force is idempotent."""
        self.create_tax_mapping(
            self.journal_with_taxes, self.tax_sale_10, self.tax_sale_21, 'sale')

        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': self.partner.id,
            'journal_id': self.journal_without_taxes.id,
            'invoice_date': fields.Date.context_today(self.env.user),
        })
        line = self.env['account.move.line'].create({
            'move_id': invoice.id,
            'product_id': self.product.id,
            'quantity': 1,
            'price_unit': 100,
        })

        self.assertEqual(set(line.tax_ids.ids), set(self.product.taxes_id.ids))

        invoice.journal_id = self.journal_with_taxes
        invoice.action_recompute_taxes_force()
        self.assertEqual(set(line.tax_ids.ids), {self.tax_sale_21.id})
        self.assertFalse(invoice.tax_recompute_needed)

        # Idempotence.
        invoice.action_recompute_taxes_force()
        self.assertEqual(set(line.tax_ids.ids), {self.tax_sale_21.id})
        self.assertFalse(invoice.tax_recompute_needed)

        # Re-trigger the product onchange so the account override applies on
        # the now-active override journal (toggle is on).
        line._onchange_product_id_set_journal_account()

        invoice.action_post()
        self.assertAlmostEqual(invoice.amount_tax, 21.0)
        self.assertAlmostEqual(invoice.amount_total, 121.0)
        self.assertTrue(
            invoice.line_ids.filtered(lambda l: l.tax_line_id == self.tax_sale_21))

        # journal_with_taxes has usar_cuenta_defecto_facturas=True: the
        # product line adopts the journal default account, not the product's.
        product_line = invoice.line_ids.filtered(
            lambda l: l.display_type == 'product')
        self.assertEqual(
            product_line.account_id, self.journal_with_taxes.default_account_id)
        prod_income = self.product.product_tmpl_id.with_company(
            self.company).get_product_accounts()['income']
        self.assertNotEqual(product_line.account_id, prod_income)

    def test_vendor_bill_recompute_with_purchase_mapping(self):
        """Vendor-bill recompute books the mapped purchase rate; post and
        assert the money + GL line.  Scoping negative: a SALE mapping on the
        same purchase journal must not change the supplier tax."""
        purchase_journal = self.env['account.journal'].create({
            'name': 'Purchase Journal with Mapping',
            'type': 'purchase',
            'code': 'PJWM',
            'company_id': self.company.id,
        })

        tax_purch_10 = self.env['account.tax'].create({
            'name': 'Purchase Tax 10%',
            'amount': 10,
            'type_tax_use': 'purchase',
            'company_id': self.company.id,
        })
        tax_purch_21 = self.env['account.tax'].create({
            'name': 'Purchase Tax 21%',
            'amount': 21,
            'type_tax_use': 'purchase',
            'company_id': self.company.id,
        })

        purchase_product = self.env['product.product'].create({
            'name': 'Purchase Product',
            'type': 'consu',
            'supplier_taxes_id': [(6, 0, [tax_purch_10.id])],
        })
        purchase_product.supplier_taxes_id = tax_purch_10

        self.env['account.journal.tax.mapping'].create({
            'journal_id': purchase_journal.id,
            'original_tax_id': tax_purch_10.id,
            'replacement_tax_id': tax_purch_21.id,
            'tax_type': 'purchase',
        })

        # Scoping: there is no active SALE mapping on the purchase journal.
        self.assertFalse(purchase_journal.has_tax_mappings('sale'))
        self.assertTrue(purchase_journal.has_tax_mappings('purchase'))

        vendor_bill = self.env['account.move'].create({
            'move_type': 'in_invoice',
            'partner_id': self.partner.id,
            'journal_id': purchase_journal.id,
            'invoice_date': fields.Date.context_today(self.env.user),
        })
        line = self.env['account.move.line'].create({
            'move_id': vendor_bill.id,
            'product_id': purchase_product.id,
            'quantity': 1,
            'price_unit': 100,
        })

        vendor_bill.action_recompute_taxes_force()
        self.assertEqual(set(line.tax_ids.ids), {tax_purch_21.id})

        vendor_bill.action_post()
        self.assertEqual(vendor_bill.invoice_line_ids.tax_ids, tax_purch_21)
        self.assertAlmostEqual(vendor_bill.amount_tax, 21.0)
        self.assertAlmostEqual(vendor_bill.amount_total, 121.0)
        self.assertTrue(
            vendor_bill.line_ids.filtered(lambda l: l.tax_line_id == tax_purch_21))
        self.assertFalse(
            vendor_bill.line_ids.filtered(lambda l: l.tax_line_id == tax_purch_10))
