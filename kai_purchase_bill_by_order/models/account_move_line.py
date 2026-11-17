from odoo import Command, fields, models


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    kai_bill_by_order = fields.Boolean(
        string="Línea agrupada por orden de compra",
        copy=True,
    )
    # {purchase_line_id: cantidad facturada, en la unidad de la línea de la orden}
    kai_bill_by_order_qty_map = fields.Json(copy=True)
    # Un renglón agrupado representa varias líneas de la orden, pero
    # purchase_line_id solo guarda una: acá quedan todas.
    kai_purchase_line_ids = fields.Many2many(
        "purchase.order.line",
        "kai_bill_line_purchase_line_rel",
        "move_line_id",
        "purchase_line_id",
        string="Líneas de orden de compra agrupadas",
        copy=False,
    )

    def _copy_data_extend_business_fields(self, values):
        super()._copy_data_extend_business_fields(values)
        if self.kai_purchase_line_ids:
            values["kai_purchase_line_ids"] = [Command.set(self.kai_purchase_line_ids.ids)]
