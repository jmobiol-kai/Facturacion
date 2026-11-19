from odoo import Command, fields
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

    def _sections(self, bill):
        return bill.invoice_line_ids.filtered(
            lambda line: line.display_type == "line_section" and line.kai_bill_by_order
        )

    def test_one_line_per_order_with_consistent_billed_qty(self):
        first = self._make_order(self.partner_a, [(2, 100.0, self.tax_a), (1, 50.0, self.tax_a)])
        second = self._make_order(self.partner_a, [(3, 10.0, self.tax_a)])
        self._wizard(first | second).action_create_bills()
        bill = (first | second).invoice_ids
        self.assertEqual(len(bill), 1)
        lines = self._bill_lines(bill)
        self.assertEqual(len(lines), 3)
        self.assertEqual(set(lines.mapped("product_id")), {self.product})
        self.assertTrue(all(lines.mapped("product_uom_id")))
        self.assertFalse(any(lines.mapped("kai_bill_by_order")))
        self.assertEqual(set(lines.mapped("purchase_line_id")), set((first | second).order_line))
        for line in lines:
            po_line = line.purchase_line_id
            self.assertEqual(line.product_uom_id, po_line.product_uom_id)
            self.assertAlmostEqual(line.quantity, po_line.product_qty)
            self.assertAlmostEqual(line.price_unit, po_line.price_unit)
        sections = self._sections(bill)
        self.assertEqual(len(sections), 2)
        self.assertTrue(all(sections.mapped("collapse_composition")))
        by_order = {section.kai_purchase_order_id: section for section in sections}
        self.assertEqual(set(by_order), {first, second})
        for order, section in by_order.items():
            self.assertEqual(section.name, "%s · %s · %s" % (
                order.name, format_date(self.env, order.date_order), self.partner_a.display_name,
            ))
            self.assertLess(section.sequence, min(lines.filtered(
                lambda line: line.purchase_line_id.order_id == order
            ).mapped("sequence")))
            collapsed = section._get_child_lines()
            self.assertAlmostEqual(
                sum(item["price_subtotal"] for item in collapsed), order.amount_untaxed, places=2,
            )
        self.assertAlmostEqual(bill.amount_untaxed, first.amount_untaxed + second.amount_untaxed, places=2)
        self.assertAlmostEqual(bill.amount_tax, first.amount_tax + second.amount_tax, places=2)
        self.assertAlmostEqual(bill.amount_total, first.amount_total + second.amount_total, places=2)
        bill._kai_consolidate_bill_lines_by_purchase_order()
        self.assertEqual(len(self._sections(bill)), 2)
        self.assertEqual(len(self._bill_lines(bill)), 3)
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
        self.assertTrue(all(lines.mapped("product_id")))
        self.assertFalse(any(lines.mapped("kai_bill_by_order")))
        self.assertFalse(self._sections(order.invoice_ids))
        self.assertEqual(order.invoice_status, "invoiced")

    def test_company_setting_is_the_default_for_native_action(self):
        order = self._make_order(self.partner_a, [(2, 100.0, self.tax_a), (1, 50.0, self.tax_a)])
        order.action_create_invoice()
        self.assertEqual(len(self._bill_lines(order.invoice_ids)), 2)
        self.assertEqual(len(self._sections(order.invoice_ids)), 1)
        self.env.company.bill_by_purchase_order = False
        other = self._make_order(self.partner_a, [(2, 100.0, self.tax_a), (1, 50.0, self.tax_a)])
        other.action_create_invoice()
        self.assertEqual(len(self._bill_lines(other.invoice_ids)), 2)
        self.assertFalse(self._sections(other.invoice_ids))

    def test_groups_by_tax_inside_the_same_order(self):
        order = self._make_order(self.partner_a, [(1, 100.0, self.tax_a), (2, 40.0, self.tax_b)])
        self._wizard(order).action_create_bills()
        lines = self._bill_lines(order.invoice_ids)
        self.assertEqual(len(lines), 2)
        by_tax = {l.tax_ids: l for l in lines}
        self.assertAlmostEqual(by_tax[self.tax_a].price_subtotal, 100.0, places=2)
        self.assertAlmostEqual(by_tax[self.tax_b].price_subtotal, 80.0, places=2)
        self.assertTrue(all(lines.mapped("product_id")))
        self.assertEqual({line.purchase_line_id for line in lines}, set(order.order_line))
        sections = self._sections(order.invoice_ids)
        self.assertEqual(len(sections), 1)
        collapsed = sections._get_child_lines()
        self.assertEqual(sorted(round(item["price_subtotal"], 2) for item in collapsed), [80.0, 100.0])
        self.assertAlmostEqual(sum(lines.mapped("price_total")), order.amount_total, places=2)
        self.assertAlmostEqual(order.invoice_ids.amount_tax, order.amount_tax, places=2)
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

    def _set_received_qty(self, order, quantity):
        """Cantidad recibida manual, o una recepción real si purchase_stock está instalado.

        Con purchase_stock, todo consumible usa qty_received_method = stock_moves
        y qty_received_manual se ignora.
        """
        line = order.order_line
        if line.qty_received_method == "stock_moves":
            pickings = order.picking_ids.filtered(
                lambda picking: picking.state not in ("done", "cancel")
            )
            self.assertTrue(pickings, "La orden no tiene una recepción abierta.")
            for picking in pickings:
                if "create_backorder" in picking.picking_type_id._fields:
                    picking.picking_type_id.create_backorder = "always"
                picking.action_assign()
                pending = picking.move_ids.filtered(
                    lambda move: move.state not in ("done", "cancel")
                )
                self.assertTrue(pending)
                for move in pending:
                    move.quantity = quantity
                    move.picked = True
                result = picking.button_validate()
                if isinstance(result, dict) and result.get("res_model"):
                    wizard = self.env[result["res_model"]].with_context(
                        **(result.get("context") or {})
                    ).create({})
                    wizard.process()
        else:
            line.qty_received_manual = quantity
        line.invalidate_recordset(["qty_received", "qty_to_invoice"])

    def test_posted_bill_and_partial_receipt(self):
        order = self._make_order(self.partner_a, [(4, 25.0, self.tax_a)])
        order.order_line.product_id.purchase_method = "receive"
        line = order.order_line
        self._set_received_qty(order, 1.0)
        self.assertAlmostEqual(line.qty_received, 1.0)
        self.assertAlmostEqual(line.qty_to_invoice, 1.0)
        self._wizard(order).action_create_bills()
        self.assertAlmostEqual(line.qty_invoiced, 1.0)
        bill = order.invoice_ids
        self.assertAlmostEqual(self._bill_lines(bill).price_subtotal, 25.0, places=2)
        bill.invoice_date = bill.date
        bill.action_post()
        self.assertAlmostEqual(line.qty_invoiced, 1.0)
        if line.qty_received_method == "stock_moves":
            self._set_received_qty(order, 3.0)
        else:
            self._set_received_qty(order, 4.0)
        self.assertAlmostEqual(line.qty_received, 4.0)
        self.assertAlmostEqual(line.qty_to_invoice, 3.0)
        self.assertEqual(order.invoice_status, "to invoice")

    def test_grouped_section_amount_is_the_exact_subtotal_sum(self):
        """La sección usa el neto del asiento, no la suma de price_subtotal.

        Con IVA incluido Odoo deja un centavo en amount_currency. La suma de
        las secciones tiene que ser amount_untaxed, y el total con IVA amount_total.
        """
        tax = self.env["account.tax"].create({
            "name": "IVA 21% incluido compras kai",
            "amount": 21.0,
            "amount_type": "percent",
            "type_tax_use": "purchase",
            "price_include_override": "tax_included",
        })
        first = self._make_order(self.partner_a, [
            (1, price, tax) for price in (10.10, 20.20, 30.05, 40.07, 50.09, 33.33)
        ])
        second = self._make_order(self.partner_a, [(1, 21.53, tax), (1, 21.53, tax)])
        orders = first | second
        self._wizard(orders).action_create_bills()
        bill = orders.invoice_ids
        self.assertEqual(len(bill), 1)
        sections = self._sections(bill)
        self.assertEqual(len(sections), 2)
        shown_net = 0.0
        shown_gross = 0.0
        for section in sections:
            collapsed = section._get_child_lines()
            shown_net += sum(item["price_subtotal"] for item in collapsed)
            shown_gross += sum(item["price_total"] for item in collapsed)
        self.assertAlmostEqual(shown_net, bill.amount_untaxed, places=2)
        self.assertAlmostEqual(shown_gross, bill.amount_total, places=2)
        raw_net = sections[:1]._kai_exact_monetary_sum(
            self._bill_lines(bill).mapped("price_subtotal")
        )
        if not bill.currency_id.is_zero(raw_net - bill.amount_untaxed):
            self.assertFalse(bill.currency_id.is_zero(shown_net - raw_net))

    def test_not_billable_orders_are_rejected(self):
        order = self._make_order(self.partner_a, [(1, 100.0, self.tax_a)])
        self._wizard(order).action_create_bills()
        with self.assertRaises(Exception):
            self._wizard(order).action_create_bills()

    def _require_fifo_purchase(self):
        installed = self.env["ir.module.module"].sudo().search_count([
            ("name", "in", ("stock_account", "purchase_stock")),
            ("state", "=", "installed"),
        ])
        if installed < 2:
            self.skipTest("Hace falta stock_account y purchase_stock para la valuación FIFO.")
        company = self.env.company
        warehouse = self.env["stock.warehouse"].search([("company_id", "=", company.id)], limit=1)
        if not warehouse:
            self.skipTest("La compañía de prueba no tiene almacén.")
        return company

    def _fifo_category(self, company, code):
        stock_account = self.env["account.account"].create({
            "name": "Valuación FIFO %s" % code,
            "code": code,
            "account_type": "asset_current",
            "reconcile": True,
            "company_ids": [Command.set([company.id])],
        })
        return self.env["product.category"].with_company(company).create({
            "name": "FIFO tiempo real %s" % code,
            "property_valuation": "real_time",
            "property_cost_method": "fifo",
            "property_stock_valuation_account_id": stock_account.id,
            "property_account_expense_categ_id": self.company_data["default_account_expense"].id,
        })

    def _fifo_product(self, category, name, cost):
        product = self.env["product.product"].with_company(self.env.company).create({
            "name": name,
            "is_storable": True,
            "purchase_method": "receive",
            "purchase_ok": True,
            "categ_id": category.id,
            "standard_price": cost,
            "supplier_taxes_id": [Command.clear()],
            "taxes_id": [Command.clear()],
        })
        product.with_company(self.env.company).standard_price = cost
        return product

    def _order_lines(self, partner, specs):
        order = self.env["purchase.order"].create({
            "partner_id": partner.id,
            "order_line": [Command.create({
                "product_id": product.id,
                "name": product.name,
                "product_qty": qty,
                "price_unit": price,
                "tax_ids": [Command.clear()],
            }) for product, qty, price in specs],
        })
        order.button_confirm()
        return order

    def _receive(self, orders):
        pickings = orders.picking_ids.filtered(lambda picking: picking.state not in ("done", "cancel"))
        self.assertTrue(pickings)
        for picking in pickings:
            picking.action_assign()
            for move in picking.move_ids.filtered(lambda move: move.state not in ("done", "cancel")):
                move.quantity = move.product_uom_qty
                move.picked = True
            result = picking.button_validate()
            if isinstance(result, dict):
                wizard = self.env[result["res_model"]].with_context(**(result.get("context") or {})).create({})
                wizard.process()

    def _incoming_value(self, po_line):
        moves = po_line.move_ids.filtered(
            lambda move: move.state == "done" and move.product_id == po_line.product_id
        )
        self.assertTrue(moves)
        return sum(moves.mapped("value"))

    def _post_bill(self, bills):
        journals = bills.journal_id
        if "l10n_latam_use_documents" in journals._fields:
            journals.write({"l10n_latam_use_documents": False})
        if "l10n_ar_afip_ws" in journals._fields:
            journals.write({"l10n_ar_afip_ws": False})
        for bill in bills:
            if not bill.invoice_date:
                bill.invoice_date = bill.date or fields.Date.context_today(bill)
        return bills.action_post()

    def test_grouped_bill_two_pos_fifo_value_equals_bill_price(self):
        """Dos OC del mismo producto FIFO: al publicar, la capa vale el precio de la factura.

        Con el renglón único (cantidad 1 y sin producto) la recepción de 2 unidades
        a 10 quedaba mezclada con el costo anterior y la capa salía 15 en lugar de 10.
        """
        company = self._require_fifo_purchase()
        category = self._fifo_category(company, "KAIFIFO01")
        product = self._fifo_product(category, "Insumo FIFO", 15.0)
        first = self._order_lines(self.partner_a, [(product, 2, 15.0)])
        second = self._order_lines(self.partner_a, [(product, 1, 15.0)])
        orders = first | second
        self._receive(orders)
        self.assertAlmostEqual(self._incoming_value(first.order_line), 30.0, places=2)
        self.assertAlmostEqual(self._incoming_value(second.order_line), 15.0, places=2)
        self.assertAlmostEqual(product.with_company(company).standard_price, 15.0, places=2)

        self._wizard(orders).action_create_bills()
        bill = orders.invoice_ids
        self.assertEqual(len(bill), 1)
        lines = self._bill_lines(bill)
        self.assertEqual(len(lines), 2)
        self.assertEqual(len(self._sections(bill)), 2)
        self.assertEqual(set(lines.mapped("product_id")), {product})
        self.assertEqual(set(lines.mapped("purchase_line_id")), set(orders.order_line))
        for line in lines:
            self.assertEqual(line.product_uom_id, line.purchase_line_id.product_uom_id)
            self.assertAlmostEqual(line.quantity, line.purchase_line_id.product_qty)
            line.price_unit = 10.0
        self._post_bill(bill)

        for line in lines:
            self.assertAlmostEqual(self._incoming_value(line.purchase_line_id), line.price_subtotal, places=2)
            self.assertAlmostEqual(line.price_subtotal, line.quantity * 10.0, places=2)
        product.invalidate_recordset(["standard_price"])
        self.assertAlmostEqual(product.with_company(company).standard_price, 10.0, places=2)

    def test_grouped_bill_does_not_dump_the_order_total_on_one_layer(self):
        """Una OC con dos productos no puede cargar la suma (15) en la capa del primero (10)."""
        company = self._require_fifo_purchase()
        category = self._fifo_category(company, "KAIFIFO02")
        first_product = self._fifo_product(category, "Insumo A", 15.0)
        second_product = self._fifo_product(category, "Insumo B", 15.0)
        order = self._order_lines(self.partner_a, [
            (first_product, 1, 10.0),
            (second_product, 1, 5.0),
        ])
        self._receive(order)
        self._wizard(order).action_create_bills()
        bill = order.invoice_ids
        lines = self._bill_lines(bill)
        self.assertEqual(len(lines), 2)
        self.assertEqual(len(self._sections(bill)), 1)
        self.assertTrue(all(lines.mapped("product_id")))
        self.assertEqual(set(lines.mapped("purchase_line_id")), set(order.order_line))
        self.assertAlmostEqual(bill.amount_untaxed, order.amount_untaxed, places=2)
        self.assertAlmostEqual(bill.amount_tax, order.amount_tax, places=2)
        self.assertAlmostEqual(bill.amount_total, order.amount_total, places=2)
        self._post_bill(bill)
        by_product = {line.product_id: line for line in lines}
        self.assertAlmostEqual(self._incoming_value(by_product[first_product].purchase_line_id), 10.0, places=2)
        self.assertAlmostEqual(self._incoming_value(by_product[second_product].purchase_line_id), 5.0, places=2)
        self.assertAlmostEqual(first_product.with_company(company).standard_price, 10.0, places=2)
        self.assertAlmostEqual(second_product.with_company(company).standard_price, 5.0, places=2)
