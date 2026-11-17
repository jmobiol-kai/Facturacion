from odoo import api, fields, models


class PurchaseOrderLine(models.Model):
    _inherit = "purchase.order.line"

    kai_bill_line_ids = fields.Many2many(
        "account.move.line",
        "kai_bill_line_purchase_line_rel",
        "purchase_line_id",
        "move_line_id",
        string="Renglones de factura agrupados",
        copy=False,
    )

    def _get_invoice_lines(self):
        self.ensure_one()
        lines = super()._get_invoice_lines()
        grouped = self.kai_bill_line_ids
        accrual = self.env.context.get("accrual_entry_date")
        if accrual:
            accrual = fields.Date.from_string(accrual)
            grouped = grouped.filtered(
                lambda l: l.move_id.invoice_date and l.move_id.invoice_date <= accrual
            )
        return lines | grouped

    @api.depends(
        "kai_bill_line_ids.move_id.state",
        "kai_bill_line_ids.kai_bill_by_order_qty_map",
    )
    def _compute_qty_invoiced(self):
        super()._compute_qty_invoiced()

    def _prepare_qty_invoiced(self):
        invoiced_qties = super()._prepare_qty_invoiced()
        for line in self:
            invoice_lines = line._get_invoice_lines()
            if not any(invoice_line.kai_bill_by_order for invoice_line in invoice_lines):
                continue
            qty = 0.0
            for invoice_line in invoice_lines:
                move = invoice_line.move_id
                if move.state == "cancel" and move.payment_state != "invoicing_legacy":
                    continue
                if move.move_type == "in_invoice":
                    sign = 1
                elif move.move_type == "in_refund":
                    sign = -1
                else:
                    continue
                if invoice_line.kai_bill_by_order:
                    qty_map = invoice_line.kai_bill_by_order_qty_map or {}
                    qty += sign * (qty_map.get(str(line.id), 0.0) or 0.0)
                elif invoice_line.product_uom_id and line.product_uom_id:
                    qty += sign * invoice_line.product_uom_id._compute_quantity(
                        invoice_line.quantity, line.product_uom_id
                    )
                else:
                    qty += sign * invoice_line.quantity
            invoiced_qties[line] = qty
        return invoiced_qties
