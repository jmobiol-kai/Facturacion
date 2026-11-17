from odoo import _, Command, models
from odoo.tools.misc import format_date


class AccountMove(models.Model):
    _inherit = "account.move"

    def _kai_should_group_bill_lines(self):
        """El asistente puede reemplazar el valor de la compañía para una sola corrida."""
        self.ensure_one()
        mode = self.env.context.get("kai_bill_line_mode")
        if mode == "products":
            return False
        if mode == "by_order":
            return True
        return bool(self.company_id.bill_by_purchase_order)

    def _kai_consolidate_bill_lines_by_purchase_order(self):
        for move in self:
            if (
                move.state != "draft"
                or move.move_type != "in_invoice"
                or not move._kai_should_group_bill_lines()
            ):
                continue
            move._kai_do_consolidate_by_purchase_order()

    def _kai_bill_price_unit_for_group(self, group):
        """Respeta precios con IVA incluido."""
        included = any(group.tax_ids.mapped("price_include"))
        field = "price_total" if included else "price_subtotal"
        return sum(group.mapped(field))

    def _kai_do_consolidate_by_purchase_order(self):
        self.ensure_one()
        lines = self.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
            and line.purchase_line_id
            and not line.purchase_line_id.is_downpayment
            and not line.kai_bill_by_order
        )
        if not lines:
            return

        groups = {}
        for line in lines:
            order = line.purchase_line_id.order_id
            key = (order.id, tuple(sorted(line.tax_ids.ids)), line.account_id.id)
            groups.setdefault(key, self.env["account.move.line"])
            groups[key] |= line

        def group_sort_key(item):
            _key, group_lines = item
            order = group_lines[:1].purchase_line_id.order_id
            first = group_lines[:1]
            return (order.date_order, order.name or "", first.sequence, first.id)

        orders = lines.purchase_line_id.order_id
        # Los títulos y notas de una orden agrupada quedarían sueltos sobre un renglón único.
        stray = self.invoice_line_ids.filtered(
            lambda line: line.display_type in ("line_section", "line_subsection", "line_note")
            and line.purchase_line_id.order_id in orders
        )

        create_vals = []
        to_remove = self.env["account.move.line"]
        sequence = min(lines.mapped("sequence") or [1])
        for (_order_id, tax_key, account_id), group in sorted(groups.items(), key=group_sort_key):
            order = group[:1].purchase_line_id.order_id
            qty_map = {}
            for aml in group:
                pol = aml.purchase_line_id
                qty = aml.quantity
                if aml.product_uom_id and pol.product_uom_id:
                    qty = aml.product_uom_id._compute_quantity(
                        aml.quantity, pol.product_uom_id, round=False
                    )
                qty_map[str(pol.id)] = qty_map.get(str(pol.id), 0.0) + qty
            name = _("%s · %s · %s") % (
                order.name,
                format_date(self.env, order.date_order),
                order.partner_id.display_name or order.partner_id.name,
            )
            purchase_lines = group.purchase_line_id
            vals = {
                "move_id": self.id,
                "display_type": "product",
                "name": name,
                "product_id": False,
                "product_uom_id": group.product_uom_id[:1].id,
                "quantity": 1.0,
                "discount": 0.0,
                "price_unit": self._kai_bill_price_unit_for_group(group),
                "tax_ids": [Command.set(list(tax_key))],
                "account_id": account_id,
                "purchase_line_id": purchase_lines[:1].id,
                "kai_purchase_line_ids": [Command.set(purchase_lines.ids)],
                "kai_bill_by_order": True,
                "kai_bill_by_order_qty_map": qty_map,
                "sequence": sequence,
                "currency_id": self.currency_id.id,
            }
            distributions = [dist for dist in group.mapped("analytic_distribution") if dist]
            if len({str(dist) for dist in distributions}) == 1:
                vals["analytic_distribution"] = distributions[0]
            create_vals.append(vals)
            to_remove |= group
            sequence += 1

        if not create_vals:
            return
        (to_remove | stray).with_context(
            check_move_validity=False, skip_invoice_sync=True
        ).unlink()
        self.env["account.move.line"].with_context(check_move_validity=False).create(create_vals)
