from itertools import groupby

from odoo import Command, fields, models
from odoo.tools.float_utils import float_round


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

    def _kai_exact_monetary_sum(self, amounts):
        """Suma importes en la unidad mínima de la moneda, sin deriva binaria."""
        self.ensure_one()
        currency = self.currency_id or self.company_currency_id
        rounding = currency.rounding or 0.01
        units = 0.0
        for amount in amounts:
            units += float_round((amount or 0.0) / rounding, precision_digits=0)
        return currency.round(units * rounding)

    def _get_collapsed_lines(self, children_lines):
        """El importe impreso de la sección es la suma exacta de los netos.

        Con precio e IVA incluido, sumar los subtotales en flotante (o mostrar
        la suma de los totales) puede dejar un centavo de más respecto del neto
        contable. El PDF usa price_subtotal o price_total según la compañía:
        en esta sección los dos valen la suma de price_subtotal.
        """
        if not (self.display_type == "line_section" and self.kai_bill_by_order):
            return super()._get_collapsed_lines(children_lines)
        product_lines = children_lines.filtered(
            lambda line: line.display_type == "product"
        ).sorted(lambda line: (tuple(sorted(line.tax_ids.ids)), line.sequence, line.id))
        result = []
        for _taxes, lines_iter in groupby(product_lines, key=lambda line: line.tax_ids):
            lines = sum(lines_iter, start=self.env["account.move.line"])
            subtotal = self._kai_exact_monetary_sum(lines.mapped("price_subtotal"))
            tax_labels = [tax.tax_label for tax in lines.tax_ids if tax.tax_label]
            if self.currency_id.is_zero(subtotal) and not tax_labels:
                continue
            result.append({
                "name": self.name,
                "taxes": tax_labels,
                "price_subtotal": subtotal,
                "price_total": subtotal,
                "display_type": "product",
                "quantity": 1,
                "line_uom": False,
                "product_uom": False,
                "discount": 0.0,
            })
        return result or [{
            "name": self.name,
            "taxes": [],
            "price_subtotal": 0.0,
            "price_total": 0.0,
            "display_type": "product",
            "quantity": 0,
            "line_uom": False,
            "product_uom": False,
            "discount": 0.0,
        }]

    def _copy_data_extend_business_fields(self, values):
        super()._copy_data_extend_business_fields(values)
        if self.kai_purchase_line_ids:
            values["kai_purchase_line_ids"] = [Command.set(self.kai_purchase_line_ids.ids)]
