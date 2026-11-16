from odoo.addons.sale.tests.common import TestSaleCommon
from odoo.tests import tagged
from odoo.tools.misc import format_date


@tagged("post_install", "-at_install")
class TestInvoiceBySaleOrder(TestSaleCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # On databases that also have modules with group-restricted
        # sale.order fields (e.g. msb_lafija.lf_preparation_date),
        # a regular test user can fail when invoicing. These tests
        # cover the generic invoice grouping, so they run as superuser.
        cls.env = cls.env(su=True)
        cls.env.company.invoice_by_sale_order = True

    def _make_order(self, partner, lines):
        order = self.env["sale.order"].create({"partner_id": partner.id})
        for product, qty, price, taxes in lines:
            self.env["sale.order.line"].create({
                "order_id": order.id,
                "product_id": product.id,
                "product_uom_qty": qty,
                "price_unit": price,
                "tax_ids": [(6, 0, taxes.ids)],
            })
        order.action_confirm()
        return order

    def test_consolidated_invoice_one_line_per_order(self):
        tax = self.tax_sale_a
        product = self.company_data["product_order_no"]
        product2 = self.company_data["product_order_sales_price"]
        partner = self.partner_a
        first = self._make_order(partner, [
            (product, 2, 100.0, tax),
            (product2, 3, 50.0, tax),
        ])
        second = self._make_order(partner, [
            (product, 1, 80.0, tax),
        ])
        moves = (first | second)._create_invoices()
        self.assertEqual(len(moves), 1)
        invoice = moves
        product_lines = invoice.invoice_line_ids.filtered(lambda line: line.display_type == "product")
        self.assertEqual(len(product_lines), 2)
        by_order = {line.sale_line_ids.order_id: line for line in product_lines}
        self.assertEqual(set(by_order), {first, second})

        first_line = by_order[first]
        second_line = by_order[second]
        first_date = format_date(self.env, first.date_order)
        second_date = format_date(self.env, second.date_order)
        self.assertEqual(
            first_line.name,
            f"{first.name} · {first_date} · {first.partner_id.display_name}",
        )
        self.assertEqual(
            second_line.name,
            f"{second.name} · {second_date} · {second.partner_id.display_name}",
        )
        self.assertEqual(first_line.quantity, 1.0)
        self.assertEqual(second_line.quantity, 1.0)
        self.assertAlmostEqual(first_line.price_subtotal, first.amount_untaxed, places=1)
        self.assertAlmostEqual(second_line.price_subtotal, second.amount_untaxed, places=1)
        self.assertAlmostEqual(first_line.price_total, first.amount_total, places=1)
        self.assertAlmostEqual(second_line.price_total, second.amount_total, places=1)
        self.assertAlmostEqual(invoice.amount_total, first.amount_total + second.amount_total, places=1)
        self.assertEqual(first_line.tax_ids, tax)
        self.assertEqual(second_line.tax_ids, tax)
        self.assertTrue(first_line.kai_invoice_by_order)
        self.assertEqual(set(first_line.sale_line_ids), set(first.order_line.filtered(lambda l: not l.display_type)))

        for line in first.order_line.filtered(lambda l: not l.display_type):
            self.assertAlmostEqual(line.qty_invoiced, line.product_uom_qty)
            self.assertEqual(line.invoice_status, "invoiced")
        for line in second.order_line.filtered(lambda l: not l.display_type):
            self.assertAlmostEqual(line.qty_invoiced, line.product_uom_qty)
            self.assertEqual(line.invoice_status, "invoiced")
        self.assertEqual(first.invoice_status, "invoiced")
        self.assertEqual(second.invoice_status, "invoiced")

    def test_groups_by_tax_inside_the_same_order(self):
        tax_a = self.tax_sale_a
        tax_b = self.tax_sale_b
        product = self.company_data["product_order_no"]
        order = self._make_order(self.partner_a, [
            (product, 1, 100.0, tax_a),
            (product, 2, 40.0, tax_b),
        ])
        invoice = order._create_invoices()
        product_lines = invoice.invoice_line_ids.filtered(lambda line: line.display_type == "product")
        self.assertEqual(len(product_lines), 2)
        by_tax = {line.tax_ids: line for line in product_lines}
        self.assertAlmostEqual(by_tax[tax_a].price_subtotal, 100.0, places=1)
        self.assertAlmostEqual(by_tax[tax_b].price_subtotal, 80.0, places=1)
        self.assertAlmostEqual(sum(product_lines.mapped('price_total')), order.amount_total, places=1)
        self.assertEqual(order.invoice_status, "invoiced")
        for line in order.order_line.filtered(lambda l: not l.display_type):
            self.assertAlmostEqual(line.qty_invoiced, line.product_uom_qty)

    def test_setting_off_keeps_native_lines(self):
        self.env.company.invoice_by_sale_order = False
        product = self.company_data["product_order_no"]
        order = self._make_order(self.partner_a, [
            (product, 2, 100.0, self.tax_sale_a),
            (product, 1, 50.0, self.tax_sale_a),
        ])
        invoice = order._create_invoices()
        product_lines = invoice.invoice_line_ids.filtered(lambda line: line.display_type == "product")
        self.assertEqual(len(product_lines), 2)
        self.assertFalse(any(product_lines.mapped("kai_invoice_by_order")))
        self.assertEqual(order.invoice_status, "invoiced")

    def _wizard(self, orders, line_mode, consolidated=True):
        return self.env["sale.advance.payment.inv"].with_context(
            active_ids=orders.ids, active_model="sale.order",
        ).create({
            "advance_payment_method": "delivered",
            "consolidated_billing": consolidated,
            "invoice_line_mode": line_mode,
            "sale_order_ids": [(6, 0, orders.ids)],
        })

    def test_wizard_default_follows_company_setting(self):
        self.env.company.invoice_by_sale_order = True
        wizard = self.env["sale.advance.payment.inv"].new({})
        self.assertEqual(wizard.invoice_line_mode, "by_order")
        self.env.company.invoice_by_sale_order = False
        wizard = self.env["sale.advance.payment.inv"].new({})
        self.assertEqual(wizard.invoice_line_mode, "products")

    def test_wizard_by_order_and_products_with_consolidated_billing(self):
        tax = self.tax_sale_a
        product = self.company_data["product_order_no"]
        partner = self.partner_a
        first = self._make_order(partner, [
            (product, 2, 100.0, tax),
            (product, 1, 50.0, tax),
        ])
        second = self._make_order(partner, [
            (product, 1, 80.0, tax),
        ])
        orders = first | second

        invoices = self._wizard(orders, "by_order", consolidated=True)._create_invoices(orders)
        self.assertEqual(len(invoices), 1)
        lines = invoices.invoice_line_ids.filtered(lambda line: line.display_type == "product")
        self.assertEqual(len(lines), 2)
        self.assertTrue(all(lines.mapped("kai_invoice_by_order")))
        self.assertAlmostEqual(invoices.amount_total, first.amount_total + second.amount_total, places=1)
        self.assertEqual(first.invoice_status, "invoiced")
        self.assertEqual(second.invoice_status, "invoiced")
        invoices.unlink()

        invoices = self._wizard(orders, "products", consolidated=True)._create_invoices(orders)
        self.assertEqual(len(invoices), 1)
        lines = invoices.invoice_line_ids.filtered(lambda line: line.display_type == "product")
        self.assertEqual(len(lines), 3)
        self.assertFalse(any(lines.mapped("kai_invoice_by_order")))
        self.assertAlmostEqual(invoices.amount_total, first.amount_total + second.amount_total, places=1)
        self.assertEqual(first.invoice_status, "invoiced")
        invoices.unlink()

        # Setting on should not override an explicit "products" choice
        self.env.company.invoice_by_sale_order = True
        invoices = self._wizard(orders, "products", consolidated=False)._create_invoices(orders)
        self.assertEqual(len(invoices), 2)
        lines = invoices.invoice_line_ids.filtered(lambda line: line.display_type == "product")
        self.assertEqual(len(lines), 3)
        self.assertFalse(any(lines.mapped("kai_invoice_by_order")))

    def test_wizard_by_order_without_consolidated_billing(self):
        tax = self.tax_sale_a
        product = self.company_data["product_order_no"]
        first = self._make_order(self.partner_a, [(product, 2, 100.0, tax)])
        second = self._make_order(self.partner_a, [(product, 1, 80.0, tax)])
        orders = first | second
        self.env.company.invoice_by_sale_order = False
        invoices = self._wizard(orders, "by_order", consolidated=False)._create_invoices(orders)
        self.assertEqual(len(invoices), 2)
        for invoice, order in zip(invoices.sorted("id"), (first | second).sorted("id")):
            lines = invoice.invoice_line_ids.filtered(lambda line: line.display_type == "product")
            self.assertEqual(len(lines), 1)
            self.assertTrue(lines.kai_invoice_by_order)
            self.assertAlmostEqual(lines.price_total, order.amount_total, places=1)
            self.assertEqual(order.invoice_status, "invoiced")
