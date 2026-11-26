from odoo import Command, fields
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

    def _make_order(self, partner, lines, shipping=None, user=None, invoice_partner=None, commitment_date=None):
        vals = {"partner_id": partner.id}
        if invoice_partner is not None:
            vals["partner_invoice_id"] = invoice_partner.id
        if shipping is not None:
            vals["partner_shipping_id"] = shipping.id
        if user is not None:
            vals["user_id"] = user.id
        if commitment_date is not None:
            vals["commitment_date"] = commitment_date
        order = self.env["sale.order"].create(vals)
        for spec in lines:
            product, qty, price, taxes = spec[:4]
            extra = spec[4] if len(spec) > 4 else {}
            self.env["sale.order.line"].create({
                "order_id": order.id,
                "product_id": product.id,
                "product_uom_qty": qty,
                "price_unit": price,
                "tax_ids": [(6, 0, taxes.ids)],
                **extra,
            })
        order.action_confirm()
        return order

    def _product_lines(self, moves):
        return moves.invoice_line_ids.filtered(lambda line: line.display_type == "product")

    def _sections(self, moves):
        return moves.invoice_line_ids.filtered(
            lambda line: line.display_type == "line_section" and line.kai_invoice_by_order
        )

    def _assert_section_date(self, order, expected_date):
        invoice = self._wizard(order, "by_order")._create_invoices(order)
        self.assertEqual(len(invoice), 1)
        self.assertEqual(invoice._kai_sale_order_section_date(order), expected_date)
        section = self._sections(invoice)
        self.assertEqual(len(section), 1)
        self.assertEqual(
            section.name,
            f"{order.name} · {format_date(self.env, expected_date)} · {order.partner_id.display_name}",
        )
        return invoice

    def _post(self, moves):
        """Publica sin documentos locales ni web service de AFIP."""
        journals = moves.journal_id
        if "l10n_latam_use_documents" in journals._fields:
            journals.write({"l10n_latam_use_documents": False})
        if "l10n_ar_afip_ws" in journals._fields:
            journals.write({"l10n_ar_afip_ws": False})
        for move in moves:
            if move.is_invoice(include_receipts=True) and not move.invoice_date:
                move.invoice_date = fields.Date.context_today(move)
        return moves.action_post()

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
        product_lines = self._product_lines(invoice)
        self.assertEqual(len(product_lines), 3)
        self.assertEqual(set(product_lines.mapped("product_id")), {product, product2})
        self.assertTrue(all(product_lines.mapped("product_uom_id")))
        self.assertFalse(any(product_lines.mapped("kai_invoice_by_order")))
        self.assertFalse(any(product_lines.mapped("kai_invoice_by_product")))
        sections = self._sections(invoice)
        self.assertEqual(len(sections), 2)
        self.assertTrue(all(sections.mapped("collapse_composition")))
        by_order = {section.kai_sale_order_id: section for section in sections}
        self.assertEqual(set(by_order), {first, second})
        for order, section in by_order.items():
            # Sin entrega validada ni fecha de compromiso, la sección usa el pedido.
            self.assertFalse(order.commitment_date)
            self.assertEqual(invoice._kai_sale_order_section_date(order), order.date_order)
            label_date = format_date(self.env, order.date_order)
            self.assertEqual(
                section.name,
                f"{order.name} · {label_date} · {order.partner_id.display_name}",
            )
            order_lines = product_lines.filtered(
                lambda line: line.sale_line_ids.order_id == order
            )
            self.assertTrue(order_lines)
            self.assertLess(section.sequence, min(order_lines.mapped("sequence")))
            self.assertEqual(
                set(order_lines.sale_line_ids),
                set(order.order_line.filtered(lambda line: not line.display_type)),
            )
            for line in order_lines:
                self.assertEqual(len(line.sale_line_ids), 1)
                self.assertEqual(line.product_id, line.sale_line_ids.product_id)
                self.assertEqual(line.product_uom_id, line.sale_line_ids.product_uom_id)
                self.assertAlmostEqual(line.quantity, line.sale_line_ids.product_uom_qty)
                self.assertAlmostEqual(line.price_unit, line.sale_line_ids.price_unit)
                self.assertEqual(line.tax_ids, tax)
            collapsed = section._get_child_lines()
            self.assertAlmostEqual(
                sum(item["price_subtotal"] for item in collapsed), order.amount_untaxed, places=2,
            )
        self.assertAlmostEqual(invoice.amount_untaxed, first.amount_untaxed + second.amount_untaxed, places=2)
        self.assertAlmostEqual(invoice.amount_tax, first.amount_tax + second.amount_tax, places=2)
        self.assertAlmostEqual(invoice.amount_total, first.amount_total + second.amount_total, places=2)

        for line in first.order_line.filtered(lambda l: not l.display_type):
            self.assertAlmostEqual(line.qty_invoiced, line.product_uom_qty)
            self.assertEqual(line.invoice_status, "invoiced")
        for line in second.order_line.filtered(lambda l: not l.display_type):
            self.assertAlmostEqual(line.qty_invoiced, line.product_uom_qty)
            self.assertEqual(line.invoice_status, "invoiced")
        self.assertEqual(first.invoice_status, "invoiced")
        self.assertEqual(second.invoice_status, "invoiced")

    def test_section_date_uses_commitment_without_validated_delivery(self):
        """Sin entrega validada, la fecha de compromiso le gana a la del pedido."""
        product = self._medialuna(type="service", invoice_policy="order")
        order = self._make_order(self.partner_a, [(product, 1, 100.0, self.tax_sale_a)])
        order_date = fields.Datetime.to_datetime("2026-01-10 12:00:00")
        commitment = fields.Datetime.to_datetime("2026-02-10 12:00:00")
        order.write({"date_order": order_date, "commitment_date": commitment})
        if "picking_ids" in order._fields:
            self.assertFalse(order.picking_ids.filtered(lambda picking: picking.state == "done"))
        if "effective_date" in order._fields:
            self.assertFalse(order.effective_date)
        self.assertNotEqual(
            format_date(self.env, commitment), format_date(self.env, order.date_order),
        )
        self._assert_section_date(order, commitment)

    def test_section_date_uses_validated_delivery(self):
        """Un remito validado en otra fecha le gana al compromiso y al pedido."""
        if "picking_ids" not in self.env["sale.order"]._fields or "stock.picking" not in self.env:
            self.skipTest("Hace falta sale_stock para una entrega validada.")
        product = self._medialuna(invoice_policy="order")
        order = self._make_order(self.partner_a, [(product, 1, 100.0, self.tax_sale_a)])
        order_date = fields.Datetime.to_datetime("2026-01-10 12:00:00")
        commitment = fields.Datetime.to_datetime("2026-02-10 12:00:00")
        delivery_date = fields.Datetime.to_datetime("2026-03-10 12:00:00")
        order.write({"date_order": order_date, "commitment_date": commitment})
        pickings = order.picking_ids.filtered(lambda picking: picking.state not in ("done", "cancel"))
        self.assertTrue(pickings)
        for picking in pickings:
            picking.action_assign()
            for move in picking.move_ids:
                move.quantity = move.product_uom_qty
                move.picked = True
            result = picking.button_validate()
            if isinstance(result, dict):
                wizard = self.env[result["res_model"]].with_context(result.get("context") or {}).create({})
                wizard.process()
        done = order.picking_ids.filtered(lambda picking: picking.state == "done")
        self.assertTrue(done)
        done.write({"date_done": delivery_date})
        order.invalidate_recordset(["effective_date"])
        if "effective_date" in order._fields:
            self.assertEqual(order.effective_date, delivery_date)
        self.assertNotEqual(format_date(self.env, delivery_date), format_date(self.env, order.date_order))
        self.assertNotEqual(format_date(self.env, delivery_date), format_date(self.env, order.commitment_date))
        self._assert_section_date(order, delivery_date)

    def test_section_date_falls_back_to_order_date(self):
        """Sin entrega validada ni compromiso, la sección usa la fecha del pedido."""
        product = self._medialuna(type="service", invoice_policy="order")
        order = self._make_order(self.partner_a, [(product, 1, 100.0, self.tax_sale_a)])
        order_date = fields.Datetime.to_datetime("2026-01-10 12:00:00")
        order.write({"date_order": order_date, "commitment_date": False})
        self.assertEqual(order.date_order, order_date)
        self.assertFalse(order.commitment_date)
        if "picking_ids" in order._fields:
            self.assertFalse(order.picking_ids.filtered(lambda picking: picking.state == "done"))
        if "effective_date" in order._fields:
            self.assertFalse(order.effective_date)
        self._assert_section_date(order, order_date)

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
        self.assertTrue(all(product_lines.mapped("product_id")))
        sections = self._sections(invoice)
        self.assertEqual(len(sections), 1)
        collapsed = sections._get_child_lines()
        self.assertEqual(sorted(round(item["price_subtotal"], 2) for item in collapsed), [80.0, 100.0])
        self.assertAlmostEqual(sum(product_lines.mapped('price_total')), order.amount_total, places=1)
        self.assertAlmostEqual(invoice.amount_tax, order.amount_tax, places=2)
        self.assertEqual(order.invoice_status, "invoiced")
        for line in order.order_line.filtered(lambda l: not l.display_type):
            self.assertAlmostEqual(line.qty_invoiced, line.product_uom_qty)

    def test_grouped_section_amount_is_the_exact_subtotal_sum(self):
        """La sección usa el neto del asiento, no la suma de price_subtotal.

        Con IVA incluido Odoo deja un centavo en amount_currency. La suma de
        las secciones tiene que ser amount_untaxed, y el total con IVA amount_total.
        """
        tax = self.env["account.tax"].create({
            "name": "IVA 21% incluido ventas kai",
            "amount": 21.0,
            "amount_type": "percent",
            "type_tax_use": "sale",
            "price_include_override": "tax_included",
        })
        product = self.company_data["product_order_no"]
        first = self._make_order(self.partner_a, [
            (product, 1, price, tax)
            for price in (10.10, 20.20, 30.05, 40.07, 50.09, 33.33)
        ])
        second = self._make_order(self.partner_a, [
            (product, 1, 21.53, tax),
            (product, 1, 21.53, tax),
        ])
        orders = first | second
        invoice = orders._create_invoices()
        self.assertEqual(len(invoice), 1)
        sections = self._sections(invoice)
        self.assertEqual(len(sections), 2)
        shown_net = 0.0
        shown_gross = 0.0
        for section in sections:
            collapsed = section._get_child_lines()
            shown_net += sum(item["price_subtotal"] for item in collapsed)
            shown_gross += sum(item["price_total"] for item in collapsed)
        self.assertAlmostEqual(shown_net, invoice.amount_untaxed, places=2)
        self.assertAlmostEqual(shown_gross, invoice.amount_total, places=2)
        raw_net = sections[:1]._kai_exact_monetary_sum(
            self._product_lines(invoice).mapped("price_subtotal")
        )
        if not invoice.currency_id.is_zero(raw_net - invoice.amount_untaxed):
            self.assertFalse(invoice.currency_id.is_zero(shown_net - raw_net))

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
        self.assertTrue(all(product_lines.mapped("product_id")))
        self.assertFalse(any(product_lines.mapped("kai_invoice_by_order")))
        self.assertFalse(self._sections(invoice))
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
        labels = dict(self.env["sale.advance.payment.inv"]._fields["invoice_line_mode"].selection)
        self.assertEqual(labels["products"], "Una línea por producto")
        self.assertEqual(labels["by_order"], "Un renglón por orden de venta")
        self.assertEqual(labels["native"], "Sin consolidar")

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
        self.assertEqual(len(lines), 3)
        self.assertTrue(all(lines.mapped("product_id")))
        self.assertFalse(any(lines.mapped("kai_invoice_by_order")))
        self.assertFalse(any(lines.mapped("kai_invoice_by_product")))
        sections = self._sections(invoices)
        self.assertEqual(len(sections), 2)
        self.assertTrue(all(sections.mapped("collapse_composition")))
        self.assertAlmostEqual(invoices.amount_untaxed, first.amount_untaxed + second.amount_untaxed, places=1)
        self.assertAlmostEqual(invoices.amount_tax, first.amount_tax + second.amount_tax, places=1)
        self.assertAlmostEqual(invoices.amount_total, first.amount_total + second.amount_total, places=1)
        self.assertEqual(first.invoice_status, "invoiced")
        self.assertEqual(second.invoice_status, "invoiced")
        invoices.unlink()

        invoices = self._wizard(orders, "products", consolidated=True)._create_invoices(orders)
        self.assertEqual(len(invoices), 1)
        lines = invoices.invoice_line_ids.filtered(lambda line: line.display_type == "product")
        self.assertEqual(len(lines), 3)
        self.assertFalse(any(lines.mapped("kai_invoice_by_order")))
        self.assertFalse(any(lines.mapped("kai_invoice_by_product")))
        self.assertFalse(self._sections(invoices))
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
            self.assertEqual(lines.product_id, product)
            self.assertFalse(lines.kai_invoice_by_order)
            self.assertEqual(lines.sale_line_ids, order.order_line)
            section = self._sections(invoice)
            self.assertEqual(len(section), 1)
            self.assertTrue(section.collapse_composition)
            self.assertEqual(section.kai_sale_order_id, order)
            self.assertAlmostEqual(lines.price_total, order.amount_total, places=1)
            self.assertEqual(order.invoice_status, "invoiced")

    def _medialuna(self, **extra):
        vals = {
            "name": "Medialuna dulce",
            "type": "consu",
            "invoice_policy": "order",
            "list_price": 100.0,
            "sale_ok": True,
            "default_code": "MED-01",
        }
        vals.update(extra)
        return self.env["product.product"].create(vals)

    def _sale_product_lines(self, orders):
        return orders.order_line.filtered(lambda line: not line.display_type)

    def test_products_mode_sums_same_product_across_orders(self):
        """Varias OV del mismo cliente: una sola línea de medialunas con la cantidad sumada."""
        tax = self.tax_sale_a
        product = self._medialuna()
        first = self._make_order(self.partner_a, [
            (product, 2, 100.0, tax),
            (product, 3, 100.0, tax),
        ])
        second = self._make_order(self.partner_a, [
            (product, 4, 100.0, tax),
        ])
        for line in self._sale_product_lines(first | second):
            line.name = "Medialuna dulce\nEntregar en el local"
        orders = first | second
        invoice = self._wizard(orders, "products")._create_invoices(orders)
        self.assertEqual(len(invoice), 1)
        lines = self._product_lines(invoice)
        self.assertEqual(len(lines), 1)
        line = lines
        self.assertEqual(line.product_id, product)
        self.assertFalse(line.kai_invoice_by_order)
        self.assertTrue(line.kai_invoice_by_product)
        self.assertEqual(line.name, "Medialuna dulce")
        self.assertNotIn("MED-01", line.name)
        self.assertNotIn("Entregar", line.name)
        self.assertAlmostEqual(line.quantity, 9.0)
        self.assertAlmostEqual(line.price_unit, 100.0)
        self.assertEqual(line.discount, 0.0)
        self.assertEqual(set(line.sale_line_ids), set(self._sale_product_lines(orders)))
        qty_map = line.kai_invoice_by_order_qty_map
        amount_map = line.kai_invoice_by_order_amount_map
        for sale_line in self._sale_product_lines(orders):
            self.assertAlmostEqual(qty_map[str(sale_line.id)], sale_line.product_uom_qty)
            self.assertAlmostEqual(sale_line.qty_invoiced, sale_line.product_uom_qty)
            self.assertEqual(sale_line.invoice_status, "invoiced")
        self.assertAlmostEqual(sum(amount_map.values()), line.price_subtotal, places=2)
        self.assertAlmostEqual(invoice.amount_total, first.amount_total + second.amount_total, places=2)
        self.assertEqual(first.invoice_status, "invoiced")
        self.assertEqual(second.invoice_status, "invoiced")

    def test_products_mode_keeps_separate_lines_when_price_discount_or_tax_differs(self):
        tax_a = self.tax_sale_a
        tax_b = self.tax_sale_b
        product = self._medialuna()
        order = self._make_order(self.partner_a, [
            (product, 2, 100.0, tax_a, {"discount": 10.0}),
            (product, 3, 100.0, tax_a, {"discount": 10.0}),
            (product, 1, 100.0, tax_a),
            (product, 1, 80.0, tax_a),
            (product, 1, 100.0, tax_b),
        ])
        invoice = self._wizard(order, "products")._create_invoices(order)
        lines = self._product_lines(invoice)
        # 10% se suma; precio, descuento 0 e impuesto distinto quedan aparte.
        self.assertEqual(len(lines), 4)
        discounted = lines.filtered(lambda line: line.discount == 10.0)
        self.assertEqual(len(discounted), 1)
        self.assertAlmostEqual(discounted.quantity, 5.0)
        self.assertEqual(discounted.product_id, product)
        self.assertTrue(discounted.kai_invoice_by_product)
        self.assertAlmostEqual(invoice.amount_total, order.amount_total, places=2)
        for sale_line in self._sale_product_lines(order):
            self.assertAlmostEqual(sale_line.qty_invoiced, sale_line.product_uom_qty)

    def test_products_mode_splits_uom_analytic_and_account(self):
        tax = self.tax_sale_a
        product = self._medialuna()
        unit = product.uom_id
        dozen = self.env["uom.uom"].create({
            "name": "Docena kai",
            "relative_uom_id": unit.id,
            "relative_factor": 12.0,
        })
        plan = self.env["account.analytic.plan"].create({"name": "Sucursales kai"})
        north = self.env["account.analytic.account"].create({
            "name": "Sucursal Norte",
            "plan_id": plan.id,
            "company_id": self.env.company.id,
        })
        south = self.env["account.analytic.account"].create({
            "name": "Sucursal Sur",
            "plan_id": plan.id,
            "company_id": self.env.company.id,
        })
        north_dist = {str(north.id): 100.0}
        south_dist = {str(south.id): 100.0}
        order = self._make_order(self.partner_a, [
            (product, 2, 100.0, tax, {"analytic_distribution": north_dist}),
            (product, 3, 100.0, tax, {"analytic_distribution": north_dist}),
            (product, 1, 100.0, tax, {"analytic_distribution": south_dist}),
            (product, 1, 100.0, tax, {"product_uom_id": dozen.id}),
        ])
        invoice = self._wizard(order, "products")._create_invoices(order)
        lines = self._product_lines(invoice)
        self.assertEqual(len(lines), 3)
        merged = lines.filtered(
            lambda line: line.kai_invoice_by_product and line.product_uom_id == unit
        )
        self.assertEqual(len(merged), 1)
        self.assertAlmostEqual(merged.quantity, 5.0)
        self.assertEqual(merged.product_id, product)
        self.assertAlmostEqual(merged.analytic_distribution[str(north.id)], 100.0, places=2)
        self.assertTrue(lines.filtered(lambda line: line.product_uom_id == dozen))
        south_lines = lines.filtered(
            lambda line: line.analytic_distribution
            and str(south.id) in line.analytic_distribution
            and not line.kai_invoice_by_product
        )
        self.assertEqual(len(south_lines), 1)

        other_income = self.env["account.account"].create({
            "name": "Ingresos alternativos kai",
            "code": "KAIALT01",
            "account_type": "income",
            "company_ids": [Command.set([self.env.company.id])],
        })
        # Misma clave salvo la cuenta: no se pueden sumar.
        same_price = self._make_order(self.partner_a, [
            (product, 2, 100.0, tax),
            (product, 4, 100.0, tax),
        ])
        draft = self._wizard(same_price, "native")._create_invoices(same_price)
        native_lines = self._product_lines(draft)
        self.assertEqual(len(native_lines), 2)
        native_lines[:1].account_id = other_income
        draft.with_context(kai_invoice_line_mode="products")._kai_consolidate_invoice_lines_by_sale_order()
        self.assertEqual(len(self._product_lines(draft)), 2)
        native_lines = self._product_lines(draft)
        other = native_lines.filtered(lambda line: line.account_id == other_income)
        other.account_id = (native_lines - other).account_id
        draft.with_context(kai_invoice_line_mode="products")._kai_consolidate_invoice_lines_by_sale_order()
        merged_account = self._product_lines(draft)
        self.assertEqual(len(merged_account), 1)
        self.assertAlmostEqual(merged_account.quantity, 6.0)
        self.assertEqual(merged_account.product_id, product)

    def test_products_mode_with_included_tax_keeps_the_total(self):
        tax = self.env["account.tax"].create({
            "name": "IVA 21% incluido kai",
            "amount": 21.0,
            "amount_type": "percent",
            "type_tax_use": "sale",
            "price_include_override": "tax_included",
        })
        product = self._medialuna()
        first = self._make_order(self.partner_a, [(product, 2, 121.0, tax)])
        second = self._make_order(self.partner_a, [(product, 3, 121.0, tax)])
        invoice = self._wizard(first | second, "products")._create_invoices(first | second)
        line = self._product_lines(invoice)
        self.assertEqual(len(line), 1)
        self.assertAlmostEqual(line.quantity, 5.0)
        self.assertAlmostEqual(line.price_unit, 121.0)
        self.assertEqual(line.product_id, product)
        self.assertAlmostEqual(invoice.amount_total, first.amount_total + second.amount_total, places=2)
        self.assertAlmostEqual(invoice.amount_untaxed, first.amount_untaxed + second.amount_untaxed, places=2)

    def test_sections_and_notes_survive_product_consolidation(self):
        tax = self.tax_sale_a
        product = self._medialuna()
        other = self.company_data["product_order_sales_price"]
        order = self.env["sale.order"].create({"partner_id": self.partner_a.id})
        self.env["sale.order.line"].create({
            "order_id": order.id,
            "display_type": "line_section",
            "name": "Desayuno",
        })
        self.env["sale.order.line"].create({
            "order_id": order.id,
            "display_type": "line_note",
            "name": "Entregar temprano",
        })
        self.env["sale.order.line"].create({
            "order_id": order.id,
            "product_id": product.id,
            "product_uom_qty": 2,
            "price_unit": 100.0,
            "tax_ids": [(6, 0, tax.ids)],
        })
        self.env["sale.order.line"].create({
            "order_id": order.id,
            "product_id": product.id,
            "product_uom_qty": 3,
            "price_unit": 100.0,
            "tax_ids": [(6, 0, tax.ids)],
        })
        self.env["sale.order.line"].create({
            "order_id": order.id,
            "product_id": other.id,
            "product_uom_qty": 1,
            "price_unit": 50.0,
            "tax_ids": [(6, 0, tax.ids)],
        })
        order.action_confirm()
        invoice = self._wizard(order, "products")._create_invoices(order)
        self.assertTrue(invoice.invoice_line_ids.filtered(lambda line: line.display_type == "line_section"))
        self.assertTrue(invoice.invoice_line_ids.filtered(lambda line: line.display_type == "line_note"))
        products = self._product_lines(invoice)
        self.assertEqual(len(products), 2)
        merged = products.filtered(lambda line: line.product_id == product)
        self.assertEqual(len(merged), 1)
        self.assertAlmostEqual(merged.quantity, 5.0)
        self.assertAlmostEqual(invoice.amount_total, order.amount_total, places=2)
        for sale_line in self._sale_product_lines(order):
            self.assertAlmostEqual(sale_line.qty_invoiced, sale_line.product_uom_qty)

    def test_each_invoice_consolidates_on_its_own(self):
        """Odoo arma una factura por cliente. Cada una suma sus propios productos."""
        tax = self.tax_sale_a
        product = self._medialuna()
        partner_a_orders = self._make_order(self.partner_a, [
            (product, 2, 100.0, tax),
            (product, 1, 100.0, tax),
        ]) | self._make_order(self.partner_a, [(product, 4, 100.0, tax)])
        partner_b_order = self._make_order(self.partner_b, [
            (product, 5, 100.0, tax),
            (product, 1, 100.0, tax),
        ])
        invoices = self._wizard(
            partner_a_orders | partner_b_order, "products",
        )._create_invoices(partner_a_orders | partner_b_order)
        self.assertEqual(len(invoices), 2)
        by_partner = {invoice.partner_id: invoice for invoice in invoices}
        line_a = self._product_lines(by_partner[self.partner_a])
        line_b = self._product_lines(by_partner[self.partner_b])
        self.assertEqual(len(line_a), 1)
        self.assertEqual(len(line_b), 1)
        self.assertAlmostEqual(line_a.quantity, 7.0)
        self.assertAlmostEqual(line_b.quantity, 6.0)
        self.assertEqual(line_a.product_id, product)
        self.assertEqual(line_b.product_id, product)

    def test_native_mode_and_company_default_do_not_follow_product_consolidation(self):
        tax = self.tax_sale_a
        product = self._medialuna()
        first = self._make_order(self.partner_a, [
            (product, 2, 100.0, tax),
            (product, 1, 100.0, tax),
        ])
        second = self._make_order(self.partner_a, [(product, 4, 100.0, tax)])
        orders = first | second
        self.env.company.invoice_by_sale_order = True

        by_order = orders._create_invoices()
        by_order_lines = self._product_lines(by_order)
        self.assertEqual(len(by_order_lines), 3)
        self.assertFalse(any(by_order_lines.mapped("kai_invoice_by_order")))
        self.assertFalse(any(by_order_lines.mapped("kai_invoice_by_product")))
        self.assertEqual(set(by_order_lines.mapped("product_id")), {product})
        self.assertEqual(len(self._sections(by_order)), 2)
        self.assertAlmostEqual(by_order.amount_total, first.amount_total + second.amount_total, places=2)
        by_order.unlink()

        native = self._wizard(orders, "native")._create_invoices(orders)
        native_lines = self._product_lines(native)
        self.assertEqual(len(native_lines), 3)
        self.assertFalse(any(native_lines.mapped("kai_invoice_by_order")))
        self.assertFalse(any(native_lines.mapped("kai_invoice_by_product")))
        self.assertEqual(set(native_lines.mapped("product_id")), {product})
        native.unlink()

        merged = self._wizard(orders, "products")._create_invoices(orders)
        merged_lines = self._product_lines(merged)
        self.assertEqual(len(merged_lines), 1)
        self.assertTrue(merged_lines.kai_invoice_by_product)
        self.assertEqual(merged_lines.product_id, product)

    def test_partial_deliveries_invoice_twice_and_keep_qty_invoiced(self):
        tax = self.tax_sale_a
        product = self._medialuna(type="service", invoice_policy="delivery")
        order = self._make_order(self.partner_a, [
            (product, 10, 100.0, tax),
            (product, 10, 100.0, tax),
        ])
        first_line, second_line = self._sale_product_lines(order).sorted("id")
        first_line.qty_delivered = 4
        second_line.qty_delivered = 0

        first_invoice = self._wizard(order, "products")._create_invoices(order)
        first_products = self._product_lines(first_invoice)
        self.assertEqual(len(first_products), 1)
        self.assertAlmostEqual(first_products.quantity, 4.0)
        self.assertFalse(first_products.kai_invoice_by_product)
        self.assertEqual(first_products.product_id, product)
        self._post(first_invoice)
        self.assertAlmostEqual(first_line.qty_invoiced, 4.0)
        self.assertAlmostEqual(first_line.qty_invoiced_posted, 4.0)
        self.assertAlmostEqual(second_line.qty_invoiced, 0.0)

        first_line.qty_delivered = 10
        second_line.qty_delivered = 10
        second_invoice = self._wizard(order, "products")._create_invoices(order)
        second_products = self._product_lines(second_invoice)
        self.assertEqual(len(second_products), 1)
        self.assertTrue(second_products.kai_invoice_by_product)
        self.assertEqual(second_products.product_id, product)
        self.assertAlmostEqual(second_products.quantity, 16.0)
        self.assertEqual(second_products.name, "Medialuna dulce")
        qty_map = second_products.kai_invoice_by_order_qty_map
        self.assertAlmostEqual(qty_map[str(first_line.id)], 6.0)
        self.assertAlmostEqual(qty_map[str(second_line.id)], 10.0)
        self._post(second_invoice)

        self.assertAlmostEqual(first_line.qty_invoiced, 10.0)
        self.assertAlmostEqual(second_line.qty_invoiced, 10.0)
        self.assertAlmostEqual(first_line.qty_invoiced_posted, 10.0)
        self.assertAlmostEqual(second_line.qty_invoiced_posted, 10.0)
        self.assertAlmostEqual(first_line.untaxed_amount_invoiced, first_line.price_subtotal, places=2)
        self.assertAlmostEqual(second_line.untaxed_amount_invoiced, second_line.price_subtotal, places=2)
        self.assertAlmostEqual(first_line.amount_invoiced, first_line.price_total, places=2)
        self.assertAlmostEqual(second_line.amount_invoiced, second_line.price_total, places=2)
        self.assertEqual(order.invoice_status, "invoiced")

    def test_credit_note_and_partial_refund_update_sale_quantities(self):
        tax = self.tax_sale_a
        product = self._medialuna()
        orders = self._make_order(self.partner_a, [(product, 2, 100.0, tax)]) | self._make_order(
            self.partner_a, [(product, 3, 100.0, tax)]
        )
        invoice = self._wizard(orders, "products")._create_invoices(orders)
        product_line = self._product_lines(invoice)
        self.assertEqual(len(product_line), 1)
        self.assertAlmostEqual(product_line.quantity, 5.0)
        self._post(invoice)

        refund = invoice._reverse_moves()
        refund_line = self._product_lines(refund)
        self.assertEqual(refund.move_type, "out_refund")
        self.assertEqual(len(refund_line), 1)
        self.assertTrue(refund_line.kai_invoice_by_product)
        self.assertEqual(refund_line.product_id, product)
        self.assertEqual(set(refund_line.sale_line_ids), set(product_line.sale_line_ids))
        refund_line.quantity = 2
        self._post(refund)

        for sale_line in self._sale_product_lines(orders):
            expected_qty = sale_line.product_uom_qty * 3.0 / 5.0
            self.assertAlmostEqual(sale_line.qty_invoiced, expected_qty, places=4)
            self.assertAlmostEqual(sale_line.qty_invoiced_posted, expected_qty, places=4)
            self.assertAlmostEqual(
                sale_line.untaxed_amount_invoiced,
                sale_line.price_subtotal * expected_qty / sale_line.product_uom_qty,
                places=2,
            )
            self.assertAlmostEqual(
                sale_line.amount_invoiced,
                sale_line.price_total * expected_qty / sale_line.product_uom_qty,
                places=2,
            )

    def test_full_credit_note_clears_invoiced_quantities(self):
        tax = self.tax_sale_a
        product = self._medialuna()
        orders = self._make_order(self.partner_a, [(product, 2, 100.0, tax)]) | self._make_order(
            self.partner_a, [(product, 3, 100.0, tax)]
        )
        invoice = self._wizard(orders, "products")._create_invoices(orders)
        self._post(invoice)
        refund = invoice._reverse_moves()
        self.assertTrue(self._product_lines(refund).kai_invoice_by_product)
        self._post(refund)
        for sale_line in self._sale_product_lines(orders):
            self.assertAlmostEqual(sale_line.qty_invoiced, 0.0)
            self.assertAlmostEqual(sale_line.qty_invoiced_posted, 0.0)
            self.assertAlmostEqual(sale_line.untaxed_amount_invoiced, 0.0, places=2)
            self.assertAlmostEqual(sale_line.amount_invoiced, 0.0, places=2)
        self.assertEqual(set(orders.mapped("invoice_status")), {"to invoice"})

    def test_partial_return_from_the_sale_order_consolidates_the_credit_note(self):
        tax = self.tax_sale_a
        product = self._medialuna(type="service", invoice_policy="delivery")
        order = self._make_order(self.partner_a, [
            (product, 10, 100.0, tax),
            (product, 10, 100.0, tax),
        ])
        first_line, second_line = self._sale_product_lines(order).sorted("id")
        first_line.qty_delivered = 10
        second_line.qty_delivered = 10
        invoice = self._wizard(order, "products")._create_invoices(order)
        self.assertAlmostEqual(self._product_lines(invoice).quantity, 20.0)
        self._post(invoice)

        first_line.qty_delivered = 7
        second_line.qty_delivered = 8
        refund = self._wizard(order, "products")._create_invoices(order)
        self.assertEqual(refund.move_type, "out_refund")
        refund_line = self._product_lines(refund)
        self.assertEqual(len(refund_line), 1)
        self.assertTrue(refund_line.kai_invoice_by_product)
        self.assertEqual(refund_line.product_id, product)
        self.assertAlmostEqual(refund_line.quantity, 5.0)
        self.assertEqual(set(refund_line.sale_line_ids), {first_line, second_line})
        self._post(refund)
        self.assertAlmostEqual(first_line.qty_invoiced, 7.0)
        self.assertAlmostEqual(second_line.qty_invoiced, 8.0)
        self.assertAlmostEqual(first_line.qty_invoiced_posted, 7.0)
        self.assertAlmostEqual(second_line.qty_invoiced_posted, 8.0)
        self.assertAlmostEqual(
            first_line.untaxed_amount_invoiced,
            first_line.price_subtotal * 0.7,
            places=2,
        )
        self.assertAlmostEqual(
            second_line.untaxed_amount_invoiced,
            second_line.price_subtotal * 0.8,
            places=2,
        )

    def test_consolidated_product_line_posts_fifo_cogs(self):
        """El product_id se conserva y el COGS FIFO de tiempo real suma el costo de los remitos."""
        installed = self.env["ir.module.module"].sudo().search_count([
            ("name", "in", ("stock_account", "sale_stock")),
            ("state", "=", "installed"),
        ])
        if installed < 2:
            self.skipTest("Hace falta stock_account y sale_stock para el costo de ventas.")
        company = self.env.company
        warehouse = self.env["stock.warehouse"].search([("company_id", "=", company.id)], limit=1)
        if not warehouse:
            self.skipTest("La compañía de prueba no tiene almacén.")
        expense = self.company_data["default_account_expense"]
        income = self.company_data["default_account_revenue"]
        stock_account = self.env["account.account"].create({
            "name": "Valuación stock kai",
            "code": "KAISTKVAL",
            "account_type": "asset_current",
            "reconcile": True,
            "company_ids": [Command.set([company.id])],
        })
        category = self.env["product.category"].with_company(company).create({
            "name": "FIFO tiempo real kai",
            "property_valuation": "real_time",
            "property_cost_method": "fifo",
            "property_stock_valuation_account_id": stock_account.id,
            "property_account_income_categ_id": income.id,
            "property_account_expense_categ_id": expense.id,
        })
        product = self._medialuna(
            name="Medialuna dulce",
            is_storable=True,
            invoice_policy="delivery",
            categ_id=category.id,
            standard_price=8.0,
            taxes_id=[Command.clear()],
        )
        supplier = self.env.ref("stock.stock_location_suppliers")

        def receive(qty, cost):
            move = self.env["stock.move"].create({
                "product_id": product.id,
                "location_id": supplier.id,
                "location_dest_id": warehouse.lot_stock_id.id,
                "product_uom": product.uom_id.id,
                "product_uom_qty": qty,
                "picking_type_id": warehouse.in_type_id.id,
                "value_manual": cost * qty,
                "price_unit": cost,
            })
            move._action_confirm()
            move._action_assign()
            move.picked = True
            move._action_done()
            return move

        receive(2, 8.0)
        receive(3, 14.0)
        no_tax = self.env["account.tax"]
        first = self._make_order(self.partner_a, [(product, 2, 100.0, no_tax)])
        second = self._make_order(self.partner_a, [(product, 3, 100.0, no_tax)])
        orders = first | second
        pickings = orders.picking_ids.filtered(lambda picking: picking.state not in ("done", "cancel"))
        for picking in pickings:
            picking.action_assign()
            for move in picking.move_ids:
                move.quantity = move.product_uom_qty
                move.picked = True
            result = picking.button_validate()
            if isinstance(result, dict):
                wizard = self.env[result["res_model"]].with_context(result.get("context") or {}).create({})
                wizard.process()
        delivered = orders.picking_ids.move_ids.filtered(lambda move: move.state == "done")
        self.assertEqual(sum(delivered.mapped("product_uom_qty")), 5)
        self.assertAlmostEqual(sum(delivered.mapped("value")), 58.0, places=2)

        invoice = self._wizard(orders, "products")._create_invoices(orders)
        line = self._product_lines(invoice)
        self.assertEqual(len(line), 1)
        self.assertEqual(line.product_id, product)
        self.assertAlmostEqual(line.quantity, 5.0)
        self.assertEqual(set(line.sale_line_ids), set(self._sale_product_lines(orders)))
        self._post(invoice)
        cogs = invoice.line_ids.filtered(lambda move_line: move_line.display_type == "cogs")
        self.assertTrue(cogs)
        self.assertEqual(set(cogs.product_id), {product})
        expense_debit = sum(cogs.filtered(lambda move_line: move_line.account_id == expense).mapped("debit"))
        stock_credit = sum(cogs.filtered(lambda move_line: move_line.account_id == stock_account).mapped("credit"))
        self.assertAlmostEqual(expense_debit, 58.0, places=2)
        self.assertAlmostEqual(stock_credit, 58.0, places=2)

    def test_grouped_by_order_posts_fifo_cogs(self):
        """Dos pedidos agrupados conservan el producto y, al publicar, generan el CMV.

        En Odoo 19 el asiento de costo debita el gasto y acredita la cuenta de
        valuación (el stock output del esquema anterior). El importe es el de
        las entregas FIFO, y los totales de la factura siguen siendo los de los
        pedidos.
        """
        installed = self.env["ir.module.module"].sudo().search_count([
            ("name", "in", ("stock_account", "sale_stock")),
            ("state", "=", "installed"),
        ])
        if installed < 2:
            self.skipTest("Hace falta stock_account y sale_stock para el costo de ventas.")
        company = self.env.company
        warehouse = self.env["stock.warehouse"].search([("company_id", "=", company.id)], limit=1)
        if not warehouse:
            self.skipTest("La compañía de prueba no tiene almacén.")
        expense = self.company_data["default_account_expense"]
        income = self.company_data["default_account_revenue"]
        stock_account = self.env["account.account"].create({
            "name": "Valuación stock por pedido",
            "code": "KAISTKORD",
            "account_type": "asset_current",
            "reconcile": True,
            "company_ids": [Command.set([company.id])],
        })
        category = self.env["product.category"].with_company(company).create({
            "name": "FIFO por pedido kai",
            "property_valuation": "real_time",
            "property_cost_method": "fifo",
            "property_stock_valuation_account_id": stock_account.id,
            "property_account_income_categ_id": income.id,
            "property_account_expense_categ_id": expense.id,
        })
        product = self._medialuna(
            name="Medialuna por pedido",
            is_storable=True,
            invoice_policy="delivery",
            categ_id=category.id,
            standard_price=8.0,
            taxes_id=[Command.clear()],
        )
        supplier = self.env.ref("stock.stock_location_suppliers")

        def receive(qty, cost):
            move = self.env["stock.move"].create({
                "product_id": product.id,
                "location_id": supplier.id,
                "location_dest_id": warehouse.lot_stock_id.id,
                "product_uom": product.uom_id.id,
                "product_uom_qty": qty,
                "picking_type_id": warehouse.in_type_id.id,
                "value_manual": cost * qty,
                "price_unit": cost,
            })
            move._action_confirm()
            move._action_assign()
            move.picked = True
            move._action_done()

        receive(2, 8.0)
        receive(3, 14.0)
        tax = self.tax_sale_a
        first = self._make_order(self.partner_a, [(product, 2, 100.0, tax)])
        second = self._make_order(self.partner_a, [(product, 3, 100.0, tax)])
        orders = first | second
        pickings = orders.picking_ids.filtered(lambda picking: picking.state not in ("done", "cancel"))
        for picking in pickings:
            picking.action_assign()
            for move in picking.move_ids:
                move.quantity = move.product_uom_qty
                move.picked = True
            result = picking.button_validate()
            if isinstance(result, dict):
                wizard = self.env[result["res_model"]].with_context(result.get("context") or {}).create({})
                wizard.process()
        delivered = orders.picking_ids.move_ids.filtered(lambda move: move.state == "done")
        self.assertAlmostEqual(sum(delivered.mapped("value")), 58.0, places=2)

        invoice = self._wizard(orders, "by_order")._create_invoices(orders)
        self.assertEqual(len(invoice), 1)
        lines = self._product_lines(invoice)
        self.assertEqual(len(lines), 2)
        self.assertEqual(set(lines.mapped("product_id")), {product})
        self.assertFalse(any(lines.mapped("kai_invoice_by_order")))
        self.assertEqual(set(lines.sale_line_ids), set(self._sale_product_lines(orders)))
        for line in lines:
            self.assertEqual(line.product_uom_id, product.uom_id)
            self.assertAlmostEqual(line.quantity, line.sale_line_ids.product_uom_qty)
            self.assertAlmostEqual(line.price_unit, line.sale_line_ids.price_unit)
        sections = self._sections(invoice)
        self.assertEqual(set(sections.mapped("kai_sale_order_id")), set(orders))
        for section in sections:
            collapsed = section._get_child_lines()
            self.assertAlmostEqual(
                sum(item["price_subtotal"] for item in collapsed),
                section.kai_sale_order_id.amount_untaxed,
                places=2,
            )
        self.assertAlmostEqual(invoice.amount_untaxed, first.amount_untaxed + second.amount_untaxed, places=2)
        self.assertAlmostEqual(invoice.amount_tax, first.amount_tax + second.amount_tax, places=2)
        self.assertAlmostEqual(invoice.amount_total, first.amount_total + second.amount_total, places=2)

        self._post(invoice)
        cogs = invoice.line_ids.filtered(lambda move_line: move_line.display_type == "cogs")
        self.assertTrue(cogs)
        self.assertEqual(set(cogs.mapped("product_id")), {product})
        expense_debit = sum(cogs.filtered(lambda move_line: move_line.account_id == expense).mapped("debit"))
        stock_credit = sum(cogs.filtered(lambda move_line: move_line.account_id == stock_account).mapped("credit"))
        self.assertAlmostEqual(expense_debit, 58.0, places=2)
        self.assertAlmostEqual(stock_credit, 58.0, places=2)

    def _merge_invoice_lines_like_history(self, invoice_lines):
        """Imita una factura histórica: una línea, varios pedidos, sin flags."""
        invoice_lines = invoice_lines.sorted("id")
        keeper = invoice_lines[:1]
        others = invoice_lines[1:]
        sale_lines = invoice_lines.sale_line_ids
        quantity = sum(invoice_lines.mapped("quantity"))
        keeper.with_context(check_move_validity=False, skip_invoice_sync=True).write({
            "quantity": quantity,
            "sale_line_ids": [Command.set(sale_lines.ids)],
            "kai_invoice_by_product": False,
            "kai_invoice_by_order": False,
            "kai_invoice_by_order_qty_map": False,
            "kai_invoice_by_order_amount_map": False,
        })
        if others:
            others.with_context(check_move_validity=False, skip_invoice_sync=True).unlink()
        return keeper

    def test_historical_shared_invoice_line_without_module_flags(self):
        """Facturas ya emitidas que agrupan por producto, sin flags ni mapa.

        Es el caso de La Fija: varios pedidos facturados juntos, mismas
        guarniciones en $0 y un plato pago, una sola línea de factura
        vinculada a todas las líneas de venta. Odoo estándar deja las
        órdenes «por facturar» porque cuenta la cantidad completa en cada
        pedido. El recálculo tiene que usar la cantidad pedida, no el precio.
        """
        self.env.company.invoice_by_sale_order = False
        tax = self.tax_sale_a
        dish = self.company_data["product_order_no"]
        side = self.company_data["product_order_sales_price"]
        partner = self.partner_a
        first = self._make_order(partner, [
            (dish, 2, 100.0, tax),
            (side, 3, 0.0, tax),
        ])
        second = self._make_order(partner, [
            (dish, 1, 100.0, tax),
            (side, 5, 0.0, tax),
        ])
        orders = first | second
        invoice = self._wizard(orders, "native", consolidated=True)._create_invoices(orders)
        self.assertEqual(len(invoice), 1)
        products = self._product_lines(invoice)
        self.assertEqual(len(products), 4)
        self.assertFalse(any(products.mapped("kai_invoice_by_product")))

        dish_line = self._merge_invoice_lines_like_history(
            products.filtered(lambda line: line.product_id == dish)
        )
        side_line = self._merge_invoice_lines_like_history(
            products.filtered(lambda line: line.product_id == side)
        )
        self.assertAlmostEqual(dish_line.quantity, 3.0)
        self.assertAlmostEqual(side_line.quantity, 8.0)
        self.assertFalse(dish_line.kai_invoice_by_product)
        self.assertFalse(bool(dish_line.kai_invoice_by_order_qty_map))

        for sale_line in self._sale_product_lines(orders):
            sale_line.invalidate_recordset([
                "qty_invoiced", "qty_to_invoice", "invoice_status",
            ])
            self.assertAlmostEqual(sale_line.qty_invoiced, sale_line.product_uom_qty)
            self.assertAlmostEqual(sale_line.qty_to_invoice, 0.0)
            self.assertEqual(sale_line.invoice_status, "invoiced")
        orders.invalidate_recordset(["invoice_status"])
        self.assertEqual(first.invoice_status, "invoiced")
        self.assertEqual(second.invoice_status, "invoiced")

        self.env["sale.order.line"]._kai_recompute_shared_invoice_qty()
        self.assertEqual(first.invoice_status, "invoiced")
        self.assertEqual(second.invoice_status, "invoiced")


    def _sales_user(self, login, name):
        group = self.env.ref("sales_team.group_sale_salesman")
        user = self.env["res.users"].search([("login", "=", login)], limit=1)
        if user:
            return user
        vals = {
            "name": name,
            "login": login,
            "email": f"{login}@example.com",
        }
        # Odoo 19 usa group_ids; versiones anteriores, groups_id.
        if "group_ids" in self.env["res.users"]._fields:
            vals["group_ids"] = [Command.set(group.ids)]
        else:
            vals["groups_id"] = [Command.set(group.ids)]
        return self.env["res.users"].create(vals)

    def _delivery_child(self, parent, name, street):
        return self.env["res.partner"].create({
            "name": name,
            "type": "delivery",
            "parent_id": parent.id,
            "street": street,
        })

    def test_grouping_keys_omit_shipping_address(self):
        keys = self.env["sale.order"]._get_invoice_grouping_keys()
        self.assertNotIn("partner_shipping_id", keys)
        self.assertIn("company_id", keys)
        self.assertIn("partner_id", keys)
        self.assertIn("currency_id", keys)
        self.assertNotIn("user_id", keys)
        self.assertNotIn("invoice_user_id", keys)

    def test_different_shipping_addresses_create_one_invoice(self):
        tax = self.tax_sale_a
        product = self.company_data["product_order_no"]
        partner = self.partner_a
        partner.user_id = False
        ines = self._delivery_child(partner, "Ines - Ing. white", "Av. Maria Guido s/n")
        laura = self._delivery_child(partner, "Laura Picchi - Paunero", "Paunero 280")
        alem = self._sales_user("kai_alem_group", "Alem mañana")
        pamela = self._sales_user("kai_pamela_group", "Pamela Piñero")
        commitment = fields.Datetime.to_datetime("2026-11-19 15:00:00")
        first = self._make_order(
            partner, [(product, 2, 100.0, tax)],
            shipping=ines, user=alem, invoice_partner=partner,
            commitment_date=commitment,
        )
        second = self._make_order(
            laura, [(product, 1, 80.0, tax)],
            shipping=laura, user=pamela, invoice_partner=partner,
            commitment_date=commitment,
        )

        invoices = (first | second)._create_invoices()
        self.assertEqual(len(invoices), 1)
        invoice = invoices
        self.assertEqual(invoice.partner_id, partner)
        self.assertEqual(invoice.partner_shipping_id, partner)
        # La ficha del cliente no tiene vendedor: queda el de la primera orden.
        self.assertEqual(invoice.invoice_user_id, alem)
        sections = self._sections(invoice)
        self.assertEqual(len(sections), 2)
        self.assertEqual(set(sections.mapped("kai_sale_order_id")), {first, second})
        self.assertEqual(invoice._kai_sale_order_section_date(first), commitment)
        self.assertEqual(invoice._kai_sale_order_section_date(second), commitment)
        self.assertIn(
            f"{first.name} · {format_date(self.env, commitment)} · {first.partner_id.display_name}",
            sections.mapped("name"),
        )
        self.assertIn(
            f"{second.name} · {format_date(self.env, commitment)} · {second.partner_id.display_name}",
            sections.mapped("name"),
        )

    def test_same_shipping_and_salesperson_keep_invoice_header(self):
        tax = self.tax_sale_a
        product = self.company_data["product_order_no"]
        partner = self.partner_a
        partner.user_id = False
        ines = self._delivery_child(partner, "Ines shared", "Av. Maria Guido s/n")
        alem = self._sales_user("kai_alem_keep", "Alem keep")
        first = self._make_order(
            partner, [(product, 2, 100.0, tax)],
            shipping=ines, user=alem, invoice_partner=partner,
        )
        second = self._make_order(
            partner, [(product, 1, 80.0, tax)],
            shipping=ines, user=alem, invoice_partner=partner,
        )

        invoice = (first | second)._create_invoices()
        self.assertEqual(len(invoice), 1)
        self.assertEqual(invoice.partner_id, partner)
        self.assertEqual(invoice.partner_shipping_id, ines)
        self.assertEqual(invoice.invoice_user_id, alem)
        self.assertEqual(len(self._sections(invoice)), 2)

    def test_different_salesperson_uses_partner_user_and_main_address(self):
        tax = self.tax_sale_a
        product = self.company_data["product_order_no"]
        partner = self.partner_a
        ines = self._delivery_child(partner, "Ines only", "Av. Maria Guido s/n")
        alem = self._sales_user("kai_alem_partner", "Alem partner")
        pamela = self._sales_user("kai_pamela_partner", "Pamela partner")
        owner = self._sales_user("kai_bunge_owner", "Vendedor ficha")
        partner.user_id = owner
        first = self._make_order(
            partner, [(product, 2, 100.0, tax)],
            shipping=ines, user=alem, invoice_partner=partner,
        )
        second = self._make_order(
            partner, [(product, 1, 80.0, tax)],
            shipping=ines, user=pamela, invoice_partner=partner,
        )

        invoice = (first | second)._create_invoices()
        self.assertEqual(len(invoice), 1)
        # Aunque la entrega coincida, al diferir el vendedor se usa la
        # dirección del cliente principal y el vendedor de su ficha.
        self.assertEqual(invoice.partner_shipping_id, partner)
        self.assertEqual(invoice.invoice_user_id, owner)
        self.assertEqual(invoice.user_id, owner)
        self.assertEqual(len(self._sections(invoice)), 2)
        partner.user_id = False

    def test_different_shipping_with_partner_salesperson(self):
        tax = self.tax_sale_a
        product = self.company_data["product_order_no"]
        partner = self.partner_a
        ines = self._delivery_child(partner, "Ines dest", "Av. Maria Guido s/n")
        laura = self._delivery_child(partner, "Laura dest", "Paunero 280")
        alem = self._sales_user("kai_alem_dest", "Alem dest")
        pamela = self._sales_user("kai_pamela_dest", "Pamela dest")
        owner = self._sales_user("kai_owner_dest", "Vendedor destino")
        partner.user_id = owner
        first = self._make_order(
            partner, [(product, 2, 100.0, tax)],
            shipping=ines, user=alem, invoice_partner=partner,
        )
        second = self._make_order(
            partner, [(product, 1, 80.0, tax)],
            shipping=laura, user=pamela, invoice_partner=partner,
        )

        invoice = (first | second)._create_invoices()
        self.assertEqual(len(invoice), 1)
        self.assertEqual(invoice.partner_id, partner)
        self.assertEqual(invoice.partner_shipping_id, partner)
        self.assertEqual(invoice.invoice_user_id, owner)
        self.assertEqual(invoice.user_id, owner)
        partner.user_id = False
