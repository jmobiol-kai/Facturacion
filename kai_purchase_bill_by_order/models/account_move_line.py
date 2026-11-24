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

    def _kai_is_grouped_order_section(self):
        self.ensure_one()
        if self.display_type != "line_section":
            return False
        if "kai_bill_by_order" in self._fields and self.kai_bill_by_order:
            return True
        if "kai_invoice_by_order" in self._fields and self.kai_invoice_by_order:
            return True
        return False

    def _kai_signed_journal_net(self, line):
        """Neto contable de una línea de producto, con el signo de amount_untaxed.

        Con IVA incluido y redondeo global, price_subtotal no trae el centavo
        que Odoo reparte en amount_currency (el balance del asiento).
        """
        move = line.move_id
        currency = move.currency_id or line.currency_id
        sign = move.direction_sign or 1
        if currency and not currency.is_zero(line.amount_currency):
            return sign * line.amount_currency
        return line.price_subtotal or 0.0

    def _kai_section_amount_gaps(self):
        """Centavos de redondeo que no quedaron en ninguna línea de producto.

        Solo la última sección los absorbe, para que la suma de las secciones
        sea amount_untaxed y amount_total.
        """
        self.ensure_one()
        move = self.move_id
        currency = move.currency_id or self.currency_id or self.company_currency_id
        sections = move.invoice_line_ids.filtered(
            lambda line: line._kai_is_grouped_order_section()
        ).sorted(lambda line: (line.sequence, line.id))
        if not sections or sections[-1] != self:
            return 0.0, 0.0
        products = move.invoice_line_ids.filtered(lambda line: line.display_type == "product")
        net = self._kai_exact_monetary_sum(
            self._kai_signed_journal_net(line) for line in products
        )
        gross = self._kai_exact_monetary_sum(products.mapped("price_total"))
        return (
            currency.round((move.amount_untaxed or 0.0) - net),
            currency.round((move.amount_total or 0.0) - gross),
        )

    def _get_collapsed_lines(self, children_lines):
        """Neto de la sección = amount_currency del asiento; total con IVA = price_total.

        Sumar price_subtotal deja afuera el centavo del redondeo global
        (27.768,60 + 15.867,77 = 43.636,37 contra un neto contable de 43.636,36).
        El PDF imprime price_subtotal o price_total según la compañía.
        """
        if not (self.display_type == "line_section" and self.kai_bill_by_order):
            return super()._get_collapsed_lines(children_lines)
        product_lines = children_lines.filtered(
            lambda line: line.display_type == "product"
        ).sorted(lambda line: (tuple(sorted(line.tax_ids.ids)), line.sequence, line.id))
        result = []
        for _taxes, lines_iter in groupby(product_lines, key=lambda line: line.tax_ids):
            lines = sum(lines_iter, start=self.env["account.move.line"])
            subtotal = self._kai_exact_monetary_sum(
                self._kai_signed_journal_net(line) for line in lines
            )
            total = self._kai_exact_monetary_sum(lines.mapped("price_total"))
            tax_labels = [tax.tax_label for tax in lines.tax_ids if tax.tax_label]
            if self.currency_id.is_zero(subtotal) and self.currency_id.is_zero(total) and not tax_labels:
                continue
            result.append({
                "name": self.name,
                "taxes": tax_labels,
                "price_subtotal": subtotal,
                "price_total": total,
                "display_type": "product",
                "quantity": 1,
                "line_uom": False,
                "product_uom": False,
                "discount": 0.0,
            })
        if not result:
            return [{
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
        net_gap, gross_gap = self._kai_section_amount_gaps()
        currency = self.currency_id or self.company_currency_id
        if not currency.is_zero(net_gap) or not currency.is_zero(gross_gap):
            result[-1]["price_subtotal"] = currency.round(result[-1]["price_subtotal"] + net_gap)
            result[-1]["price_total"] = currency.round(result[-1]["price_total"] + gross_gap)
        return result

    def _copy_data_extend_business_fields(self, values):
        super()._copy_data_extend_business_fields(values)
        if self.kai_purchase_line_ids:
            values["kai_purchase_line_ids"] = [Command.set(self.kai_purchase_line_ids.ids)]
