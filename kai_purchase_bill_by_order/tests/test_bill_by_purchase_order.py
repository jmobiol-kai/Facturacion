from odoo import Command
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.tests import tagged
from odoo.tools.misc import format_date


@tagged("post_install", "-at_install")
class TestBillByPurchaseOrder(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # El alcance por usuario se prueba aparte, con un usuario real de Compras.
        cls.env = cls.env(su=True)
        cls.env.company.bill_by_purchase_order = True
        cls.tax_a = cls.company_data["default_tax_purchase"]
        cls.tax_b = cls.tax_a.copy({"name": "Otro IVA compras", "amount": 10.0})
        cls.product = cls.env["product.product"].create({
            "name": "Producto de compra",
            "type": "consu",
            "purchase_method": "purchase",
        })
        cls.partner_b = cls.partner_b or cls.env["res.partner"].create({"name": "Proveedor B"})

    def _make_order(self, partner, lines):
        order = self.env["purchase.order"].create({
            "partner_id": partner.id,
            "order_line": [Command.create({
                "product_id": self.product.id,
                "name": "Linea",
                "product_qty": qty,
                "price_unit": price,
                "tax_ids": [Command.set(taxes.ids)],
            }) for qty, price, taxes in lines],
        })
        order.button_confirm()
        return order

    def _wizard(self, orders, **values):
        values.setdefault("invoice_line_mode", "by_order")
        return self.env["kai.purchase.bill.wizard"].with_context(
            active_model="purchase.order", active_ids=orders.ids
        ).create(values)

    def _bill_lines(self, bill):
        return bill.invoice_line_ids.filtered(lambda l: l.display_type == "product")

    def test_one_line_per_order_with_consistent_billed_qty(self):
        first = self._make_order(self.partner_a, [(2, 100.0, self.tax_a), (1, 50.0, self.tax_a)])
        second = self._make_order(self.partner_a, [(3, 10.0, self.tax_a)])
        self._wizard(first | second).action_create_bills()
        bill = (first | second).invoice_ids
        self.assertEqual(len(bill), 1)
        lines = self._bill_lines(bill)
        self.assertEqual(len(lines), 2)
        self.assertEqual(set(lines.mapped("quantity")), {1.0})
        by_name = {l.name: l for l in lines}
        label = "%s · %s · %s" % (
            first.name, format_date(self.env, first.date_order), self.partner_a.display_name)
        self.assertAlmostEqual(by_name[label].price_subtotal, 250.0, places=2)
        self.assertEqual(by_name[label].purchase_line_id.order_id, first)
        self.assertAlmostEqual(sum(lines.mapped("price_total")), first.amount_total + second.amount_total, places=2)
        for order in (first, second):
            for line in order.order_line:
                self.assertAlmostEqual(line.qty_invoiced, line.product_qty)
                self.assertAlmostEqual(line.qty_to_invoice, 0.0)
            self.assertEqual(order.invoice_status, "invoiced")
            self.assertEqual(order.invoice_ids, bill)

    def test_products_mode_keeps_native_lines(self):
        order = self._make_order(self.partner_a, [(2, 100.0, self.tax_a), (1, 50.0, self.tax_a)])
        self._wizard(order, invoice_line_mode="products").action_create_bills()
        lines = self._bill_lines(order.invoice_ids)
        self.assertEqual(len(lines), 2)
        self.assertFalse(any(lines.mapped("kai_bill_by_order")))
        self.assertEqual(order.invoice_status, "invoiced")

    def test_company_setting_is_the_default_for_native_action(self):
        order = self._make_order(self.partner_a, [(2, 100.0, self.tax_a), (1, 50.0, self.tax_a)])
        order.action_create_invoice()
        self.assertEqual(len(self._bill_lines(order.invoice_ids)), 1)
        self.env.company.bill_by_purchase_order = False
        other = self._make_order(self.partner_a, [(2, 100.0, self.tax_a), (1, 50.0, self.tax_a)])
        other.action_create_invoice()
        self.assertEqual(len(self._bill_lines(other.invoice_ids)), 2)

    def test_groups_by_tax_inside_the_same_order(self):
        order = self._make_order(self.partner_a, [(1, 100.0, self.tax_a), (2, 40.0, self.tax_b)])
        self._wizard(order).action_create_bills()
        lines = self._bill_lines(order.invoice_ids)
        self.assertEqual(len(lines), 2)
        by_tax = {l.tax_ids: l for l in lines}
        self.assertAlmostEqual(by_tax[self.tax_a].price_subtotal, 100.0, places=2)
        self.assertAlmostEqual(by_tax[self.tax_b].price_subtotal, 80.0, places=2)
        self.assertAlmostEqual(sum(lines.mapped("price_total")), order.amount_total, places=2)
        for line in order.order_line:
            self.assertAlmostEqual(line.qty_invoiced, line.product_qty)
        self.assertEqual(order.invoice_status, "invoiced")

    def test_one_bill_per_order_when_not_consolidating(self):
        first = self._make_order(self.partner_a, [(1, 100.0, self.tax_a)])
        second = self._make_order(self.partner_a, [(1, 70.0, self.tax_a)])
        self._wizard(first | second, consolidate_by_vendor=False).action_create_bills()
        self.assertEqual(len(first.invoice_ids), 1)
        self.assertEqual(len(second.invoice_ids), 1)
        self.assertNotEqual(first.invoice_ids, second.invoice_ids)

    def test_different_vendors_get_different_bills(self):
        first = self._make_order(self.partner_a, [(1, 100.0, self.tax_a)])
        second = self._make_order(self.partner_b, [(1, 70.0, self.tax_a)])
        self._wizard(first | second).action_create_bills()
        self.assertNotEqual(first.invoice_ids, second.invoice_ids)

    def test_cancelled_or_deleted_bill_frees_the_order(self):
        order = self._make_order(self.partner_a, [(2, 100.0, self.tax_a), (1, 50.0, self.tax_a)])
        self._wizard(order).action_create_bills()
        bill = order.invoice_ids
        bill.button_cancel()
        for line in order.order_line:
            self.assertAlmostEqual(line.qty_invoiced, 0.0)
        self.assertEqual(order.invoice_status, "to invoice")

    def test_posted_bill_and_partial_receipt(self):
        order = self._make_order(self.partner_a, [(4, 25.0, self.tax_a)])
        order.order_line.product_id.purchase_method = "receive"
        order.order_line.qty_received_manual = 1.0
        order.order_line.invalidate_recordset(["qty_to_invoice"])
        self.assertAlmostEqual(order.order_line.qty_to_invoice, 1.0)
        self._wizard(order).action_create_bills()
        self.assertAlmostEqual(order.order_line.qty_invoiced, 1.0)
        bill = order.invoice_ids
        self.assertAlmostEqual(self._bill_lines(bill).price_subtotal, 25.0, places=2)
        bill.invoice_date = bill.date
        bill.action_post()
        self.assertAlmostEqual(order.order_line.qty_invoiced, 1.0)
        order.order_line.qty_received_manual = 4.0
        self.assertAlmostEqual(order.order_line.qty_to_invoice, 3.0)
        self.assertEqual(order.invoice_status, "to invoice")

    def test_not_billable_orders_are_rejected(self):
        order = self._make_order(self.partner_a, [(1, 100.0, self.tax_a)])
        self._wizard(order).action_create_bills()
        with self.assertRaises(Exception):
            self._wizard(order).action_create_bills()
