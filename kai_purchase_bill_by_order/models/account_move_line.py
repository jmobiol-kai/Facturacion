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
    kai_purchase_order_id = fields.Many2one(
        "purchase.order",
        string="Orden de compra de la sección",
        copy=True,
        help="En la sección que agrupa una orden. Cada producto conserva su propia "
             "línea de compra en purchase_line_id.",
    )

    def _get_collapsed_lines(self, children_lines):
        """Agrupa por alícuota aunque los productos no vengan ordenados por impuesto."""
        if self.display_type == "line_section" and self.kai_bill_by_order:
            children_lines = children_lines.sorted(
                lambda line: (
                    0 if line.display_type == "product" else 1,
                    tuple(sorted(line.tax_ids.ids)),
                    line.sequence,
                    line.id,
                )
            )
        return super()._get_collapsed_lines(children_lines)

    def _copy_data_extend_business_fields(self, values):
        super()._copy_data_extend_business_fields(values)
        if self.kai_purchase_line_ids:
            values["kai_purchase_line_ids"] = [Command.set(self.kai_purchase_line_ids.ids)]
