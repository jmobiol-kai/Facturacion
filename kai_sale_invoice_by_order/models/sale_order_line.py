from odoo import api, fields, models


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    def _kai_grouped_invoice_lines(self, invoice_lines):
        return invoice_lines.filtered(
            lambda line: line.kai_invoice_by_order or line.kai_invoice_by_product
        )

    def _prepare_qty_invoiced(self):
        invoiced_qties = super()._prepare_qty_invoiced()
        for line in self:
            invoice_lines = line._get_invoice_lines()
            if not self._kai_grouped_invoice_lines(invoice_lines):
                continue
            qty = 0.0
            for invoice_line in invoice_lines:
                move = invoice_line.move_id
                if move.state == "cancel" and move.payment_state != "invoicing_legacy":
                    continue
                if move.move_type == "out_invoice":
                    sign = 1
                elif move.move_type == "out_refund":
                    sign = -1
                else:
                    continue
                if invoice_line.kai_invoice_by_order or invoice_line.kai_invoice_by_product:
                    qty += sign * invoice_line._kai_qty_for_sale_line(line)
                elif invoice_line.product_uom_id and line.product_uom_id:
                    qty += sign * invoice_line.product_uom_id._compute_quantity(
                        invoice_line.quantity, line.product_uom_id, round=False
                    )
                else:
                    qty += sign * invoice_line.quantity
            invoiced_qties[line] = qty
        return invoiced_qties

    @api.depends(
        "invoice_lines.move_id.state",
        "invoice_lines.quantity",
        "invoice_lines.kai_invoice_by_order",
        "invoice_lines.kai_invoice_by_product",
        "invoice_lines.kai_invoice_by_order_qty_map",
    )
    def _compute_qty_invoiced_posted(self):
        super()._compute_qty_invoiced_posted()
        for line in self:
            invoice_lines = line._get_invoice_lines()
            if not self._kai_grouped_invoice_lines(invoice_lines):
                continue
            qty_invoiced_posted = 0.0
            for invoice_line in invoice_lines:
                move = invoice_line.move_id
                if move.state != "posted" and move.payment_state != "invoicing_legacy":
                    continue
                sign = -move.direction_sign
                if invoice_line.kai_invoice_by_order or invoice_line.kai_invoice_by_product:
                    qty_invoiced_posted += sign * invoice_line._kai_qty_for_sale_line(line)
                else:
                    qty_unsigned = invoice_line.product_uom_id._compute_quantity(
                        invoice_line.quantity, line.product_uom_id
                    )
                    qty_invoiced_posted += sign * qty_unsigned
            line.qty_invoiced_posted = qty_invoiced_posted

    @api.depends(
        "invoice_lines",
        "invoice_lines.price_total",
        "invoice_lines.price_subtotal",
        "invoice_lines.move_id.state",
        "invoice_lines.move_id.move_type",
        "invoice_lines.kai_invoice_by_order",
        "invoice_lines.kai_invoice_by_product",
        "invoice_lines.kai_invoice_by_order_amount_map",
    )
    def _compute_untaxed_amount_invoiced(self):
        super()._compute_untaxed_amount_invoiced()
        for line in self:
            invoice_lines = line._get_invoice_lines()
            if not self._kai_grouped_invoice_lines(invoice_lines):
                continue
            amount_invoiced = 0.0
            for invoice_line in invoice_lines:
                move = invoice_line.move_id
                if move.state != "posted" and move.payment_state != "invoicing_legacy":
                    continue
                invoice_date = move.invoice_date or fields.Date.today()
                if move.move_type == "out_invoice":
                    sign = 1
                elif move.move_type == "out_refund":
                    sign = -1
                else:
                    continue
                raw = self._kai_allocated_untaxed(invoice_line, line)
                amount_invoiced += sign * invoice_line.currency_id._convert(
                    raw, line.currency_id, line.company_id, invoice_date
                )
            line.untaxed_amount_invoiced = amount_invoiced

    @api.depends(
        "invoice_lines",
        "invoice_lines.price_total",
        "invoice_lines.move_id.state",
        "invoice_lines.kai_invoice_by_order",
        "invoice_lines.kai_invoice_by_product",
        "invoice_lines.kai_invoice_by_order_amount_map",
    )
    def _compute_amount_invoiced(self):
        super()._compute_amount_invoiced()
        for line in self:
            invoice_lines = line._get_invoice_lines()
            if not self._kai_grouped_invoice_lines(invoice_lines):
                continue
            amount_invoiced = 0.0
            for invoice_line in invoice_lines:
                move = invoice_line.move_id
                if move.state != "posted" and move.payment_state != "invoicing_legacy":
                    continue
                invoice_date = move.invoice_date or fields.Date.context_today(self)
                raw = self._kai_allocated_total(invoice_line, line)
                converted = invoice_line.currency_id._convert(
                    raw, line.currency_id, line.company_id, invoice_date
                )
                amount_invoiced += converted * -move.direction_sign
            line.amount_invoiced = amount_invoiced

    def _kai_allocated_untaxed(self, invoice_line, sale_line):
        if invoice_line.kai_invoice_by_product:
            return invoice_line._kai_untaxed_for_sale_line(sale_line)
        if invoice_line.kai_invoice_by_order:
            amount_map = invoice_line.kai_invoice_by_order_amount_map or {}
            return amount_map.get(str(sale_line.id), 0.0) or 0.0
        return invoice_line.price_subtotal or 0.0

    def _kai_allocated_total(self, invoice_line, sale_line):
        if invoice_line.kai_invoice_by_product:
            return invoice_line._kai_total_for_sale_line(sale_line)
        if not invoice_line.kai_invoice_by_order:
            return invoice_line.price_total or 0.0
        amount_map = invoice_line.kai_invoice_by_order_amount_map or {}
        allocated = amount_map.get(str(sale_line.id), 0.0) or 0.0
        subtotal = invoice_line.price_subtotal or 0.0
        if not subtotal:
            return 0.0
        return (invoice_line.price_total or 0.0) * (allocated / subtotal)
