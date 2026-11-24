from odoo import api, fields, models


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    def _kai_grouped_invoice_lines(self, invoice_lines):
        return invoice_lines.filtered(
            lambda line: line.kai_invoice_by_order
            or line.kai_invoice_by_product
            or len(line.sale_line_ids) > 1
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
                if (
                    invoice_line.kai_invoice_by_order
                    or invoice_line.kai_invoice_by_product
                    or len(invoice_line.sale_line_ids) > 1
                ):
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
                if (
                    invoice_line.kai_invoice_by_order
                    or invoice_line.kai_invoice_by_product
                    or len(invoice_line.sale_line_ids) > 1
                ):
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
        if len(invoice_line.sale_line_ids) > 1:
            return invoice_line._kai_untaxed_for_sale_line(sale_line)
        return invoice_line.price_subtotal or 0.0

    def _kai_allocated_total(self, invoice_line, sale_line):
        if invoice_line.kai_invoice_by_product:
            return invoice_line._kai_total_for_sale_line(sale_line)
        if not invoice_line.kai_invoice_by_order:
            if len(invoice_line.sale_line_ids) > 1:
                return invoice_line._kai_total_for_sale_line(sale_line)
            return invoice_line.price_total or 0.0
        amount_map = invoice_line.kai_invoice_by_order_amount_map or {}
        allocated = amount_map.get(str(sale_line.id), 0.0) or 0.0
        subtotal = invoice_line.price_subtotal or 0.0
        if not subtotal:
            return 0.0
        return (invoice_line.price_total or 0.0) * (allocated / subtotal)

    def _kai_recompute_shared_invoice_qty(self):
        """Recalcula qty_invoiced e invoice_status en líneas compartidas.

        Cubre facturas históricas agrupadas por producto sin flags del módulo.
        """
        self.env.cr.execute(
            """
            SELECT DISTINCT rel.order_line_id
              FROM sale_order_line_invoice_rel rel
              JOIN sale_order_line_invoice_rel other
                ON other.invoice_line_id = rel.invoice_line_id
              JOIN sale_order_line sol ON sol.id = rel.order_line_id
             WHERE sol.state IN ('sale', 'done')
             GROUP BY rel.order_line_id, rel.invoice_line_id
            HAVING count(other.order_line_id) > 1
            """
        )
        line_ids = [row[0] for row in self.env.cr.fetchall()]
        if not line_ids:
            return self.browse()
        lines = self.browse(line_ids)
        # Los stored computed no se recalcan solo con invalidate+read:
        # hay que ejecutar los computes.
        lines._compute_qty_invoiced()
        lines._compute_qty_to_invoice()
        if "qty_invoiced_posted" in lines._fields:
            lines._compute_qty_invoiced_posted()
        if "untaxed_amount_invoiced" in lines._fields:
            lines._compute_untaxed_amount_invoiced()
        if "amount_invoiced" in lines._fields:
            lines._compute_amount_invoiced()
        lines._compute_invoice_status()
        lines.order_id._compute_invoice_status()
        return lines
