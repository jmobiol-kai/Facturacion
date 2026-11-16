from odoo import _, Command, models
from odoo.tools.misc import format_date


class AccountMove(models.Model):
    _inherit = "account.move"

    def _kai_should_group_invoice_lines(self):
        """The wizard can override the company default for a single run."""
        self.ensure_one()
        mode = self.env.context.get("kai_invoice_line_mode")
        if mode == "products":
            return False
        if mode == "by_order":
            return True
        return bool(self.company_id.invoice_by_sale_order)

    def _kai_consolidate_invoice_lines_by_sale_order(self):
        for move in self:
            if (
                move.state != "draft"
                or move.move_type not in ("out_invoice", "out_refund")
                or not move._kai_should_group_invoice_lines()
            ):
                continue
            move._kai_do_consolidate_by_sale_order()


    def _kai_price_unit_for_group(self, group):
        """Respecta precios con IVA incluido (típico en l10n_ar / Factura B o C)."""
        included = any(group.tax_ids.mapped("price_include"))
        if not included and hasattr(self, "_l10n_ar_include_vat"):
            included = bool(self._l10n_ar_include_vat())
        field = "price_total" if included else "price_subtotal"
        return sum(group.mapped(field))

    def _kai_do_consolidate_by_sale_order(self):
        self.ensure_one()
        lines = self.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
            and line.sale_line_ids
            and not line.kai_invoice_by_order
        )
        if not lines:
            return

        groups = {}
        for line in lines:
            order = line.sale_line_ids.order_id[:1]
            if not order:
                continue
            key = (order.id, tuple(sorted(line.tax_ids.ids)), line.account_id.id)
            groups.setdefault(key, self.env["account.move.line"])
            groups[key] |= line

        if not groups:
            return

        def group_sort_key(item):
            _key, group_lines = item
            order = group_lines.sudo().sale_line_ids.order_id[:1]
            first = group_lines[:1]
            return (
                order.date_order,
                order.name or "",
                first.sequence,
                first.id,
            )

        create_vals = []
        to_remove = self.env["account.move.line"]
        sequence = min(lines.mapped("sequence") or [1])
        for (_order_id, tax_key, account_id), group in sorted(
            groups.items(), key=group_sort_key
        ):
            order = group.sudo().sale_line_ids.order_id[:1]
            qty_map = {}
            amount_map = {}
            for aml in group:
                sols = aml.sale_line_ids
                if not sols:
                    continue
                if len(sols) == 1:
                    sol = sols
                    qty = aml.quantity
                    if aml.product_uom_id and sol.product_uom_id:
                        qty = aml.product_uom_id._compute_quantity(
                            aml.quantity, sol.product_uom_id, round=False
                        )
                    qty_map[str(sol.id)] = qty_map.get(str(sol.id), 0.0) + qty
                    amount_map[str(sol.id)] = amount_map.get(str(sol.id), 0.0) + (
                        aml.price_subtotal or 0.0
                    )
                else:
                    subtotal = aml.price_subtotal or 0.0
                    weights = []
                    for sol in sols:
                        qty = aml.quantity
                        if aml.product_uom_id and sol.product_uom_id:
                            qty = aml.product_uom_id._compute_quantity(
                                aml.quantity, sol.product_uom_id, round=False
                            )
                        weights.append((sol, qty, abs(sol.price_subtotal or 0.0)))
                    weight_total = sum(weight for _sol, _qty, weight in weights) or len(weights)
                    for sol, qty, weight in weights:
                        share = (weight / weight_total) if weight_total else 0.0
                        qty_map[str(sol.id)] = qty_map.get(str(sol.id), 0.0) + qty
                        amount_map[str(sol.id)] = amount_map.get(str(sol.id), 0.0) + (
                            subtotal * share
                        )

            date_label = format_date(self.env, order.date_order)
            name = _("%s · %s · %s") % (
                order.name,
                date_label,
                order.partner_id.display_name or order.partner_id.name,
            )
            vals = {
                "move_id": self.id,
                "display_type": "product",
                "name": name,
                "product_id": False,
                "product_uom_id": group.product_uom_id[:1].id,
                "quantity": 1.0,
                "discount": 0.0,
                "price_unit": self._kai_price_unit_for_group(group),
                "tax_ids": [Command.set(list(tax_key))],
                "account_id": account_id,
                "sale_line_ids": [Command.set(group.sale_line_ids.ids)],
                "kai_invoice_by_order": True,
                "kai_invoice_by_order_qty_map": qty_map,
                "kai_invoice_by_order_amount_map": amount_map,
                "sequence": sequence,
                "currency_id": self.currency_id.id,
            }
            distributions = [dist for dist in group.mapped("analytic_distribution") if dist]
            unique = {str(dist) for dist in distributions}
            if len(unique) == 1:
                vals["analytic_distribution"] = distributions[0]
            create_vals.append(vals)
            to_remove |= group
            sequence += 1

        if not create_vals:
            return

        to_remove.with_context(
            check_move_validity=False, skip_invoice_sync=True
        ).unlink()
        self.env["account.move.line"].with_context(check_move_validity=False).create(
            create_vals
        )
