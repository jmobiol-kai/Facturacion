from odoo import models


class SaleOrder(models.Model):
    _inherit = "sale.order"

    def _create_invoices(self, grouped=False, final=False, date=None):
        moves = super()._create_invoices(grouped=grouped, final=final, date=date)
        if moves:
            moves._kai_consolidate_invoice_lines_by_sale_order()
        return moves
