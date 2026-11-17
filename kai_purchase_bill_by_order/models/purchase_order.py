from odoo import api, models


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    def action_create_invoice(self, attachment_ids=False):
        before = self.invoice_ids
        result = super().action_create_invoice(attachment_ids=attachment_ids)
        if not attachment_ids:
            # A bill built from an uploaded document keeps the lines of that document.
            self.invalidate_model(["invoice_ids"])
            (self.invoice_ids - before)._kai_consolidate_bill_lines_by_purchase_order()
        return result

    @api.depends("order_line.kai_bill_line_ids.move_id")
    def _compute_invoice(self):
        super()._compute_invoice()
        for order in self:
            invoices = order.order_line.kai_bill_line_ids.move_id
            if invoices - order.invoice_ids:
                order.invoice_ids |= invoices
                order.invoice_count = len(order.invoice_ids)
