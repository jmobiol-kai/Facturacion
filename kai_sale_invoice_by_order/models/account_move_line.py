from itertools import groupby

from odoo import fields, models
from odoo.tools import float_is_zero
from odoo.tools.float_utils import float_round


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    kai_invoice_by_order = fields.Boolean(
        string="Línea agrupada por pedido de venta",
        copy=True,
    )
    kai_invoice_by_order_qty_map = fields.Json(copy=True)
    kai_invoice_by_order_amount_map = fields.Json(copy=True)
    kai_invoice_by_product = fields.Boolean(
        string="Línea agrupada por producto",
        copy=True,
        help="La cantidad y el importe de esta línea se reparten entre las líneas "
             "de venta según los mapas. Si se modifica la cantidad (por ejemplo en "
             "una nota de crédito parcial), el reparto se escala en proporción.",
    )
    kai_sale_order_id = fields.Many2one(
        "sale.order",
        string="Pedido de venta de la sección",
        copy=True,
        help="En la sección que agrupa un pedido. Cada producto conserva sus "
             "líneas de venta en sale_line_ids.",
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
        if not (self.display_type == "line_section" and self.kai_invoice_by_order):
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

    def _kai_sale_line_qty_weights(self):
        """Pesos (cantidad pedida en la UdM de la factura) de cada línea de venta."""
        self.ensure_one()
        weights = []
        sale_lines = self.sale_line_ids.filtered(lambda line: not line.display_type).sorted("id")
        for sale_line in sale_lines:
            qty = sale_line.product_uom_qty or 0.0
            if (
                sale_line.product_uom_id
                and self.product_uom_id
                and sale_line.product_uom_id != self.product_uom_id
            ):
                qty = sale_line.product_uom_id._compute_quantity(
                    qty, self.product_uom_id, round=False
                )
            weights.append((sale_line, qty))
        return weights

    def _kai_proportional_share(self, sale_line, total):
        """Reparte un total según la cantidad pedida. El último se queda el resto.

        Se usa la cantidad pedida, no el importe: los acompañamientos en $0
        (flan, pan, fruta) no podrían prorratearse por precio.
        """
        self.ensure_one()
        weights = self._kai_sale_line_qty_weights()
        if not weights:
            return 0.0
        if len(weights) == 1:
            return total if weights[0][0] == sale_line else 0.0
        total_weight = sum(weight for _line, weight in weights)
        if not total_weight:
            total_weight = float(len(weights))
            weights = [(line, 1.0) for line, _weight in weights]
        remaining = total
        last_id = weights[-1][0].id
        for line, weight in weights:
            share = remaining if line.id == last_id else total * (weight / total_weight)
            if line.id != last_id:
                remaining -= share
            if line == sale_line:
                return share
        return 0.0

    def _kai_proportional_qty_for_sale_line(self, sale_line):
        """Cantidad imputable cuando no hay mapa (facturas históricas agrupadas)."""
        self.ensure_one()
        share = self._kai_proportional_share(sale_line, self.quantity or 0.0)
        if (
            self.product_uom_id
            and sale_line.product_uom_id
            and self.product_uom_id != sale_line.product_uom_id
        ):
            share = self.product_uom_id._compute_quantity(
                share, sale_line.product_uom_id, round=False
            )
        return share

    def _kai_qty_for_sale_line(self, sale_line):
        """Cantidad de esta línea de factura imputable a una línea de venta.

        Las facturas nuevas agrupadas por pedido no usan este mapa: cada
        producto conserva su cantidad y su sale_line_ids. El mapa absoluto
        (cantidad de factura 1) queda para comprobantes ya emitidos con el
        renglón único. En la línea por producto el mapa está en la unidad de
        la venta y se escala si la cantidad de la factura cambió.

        Si la línea está vinculada a varias ventas y no hay mapa (facturas
        hechas antes de instalar el módulo), se prorratea por cantidad pedida.
        """
        self.ensure_one()
        qty_map = self.kai_invoice_by_order_qty_map or {}
        if qty_map or self.kai_invoice_by_product or self.kai_invoice_by_order:
            mapped = qty_map.get(str(sale_line.id), 0.0) or 0.0
            if not self.kai_invoice_by_product:
                return mapped
            return self._kai_scale_mapped_value(mapped, qty_map, self.quantity)
        return self._kai_proportional_qty_for_sale_line(sale_line)

    def _kai_untaxed_for_sale_line(self, sale_line):
        """Importe sin impuestos de esta línea imputable a una línea de venta."""
        self.ensure_one()
        amount_map = self.kai_invoice_by_order_amount_map or {}
        if amount_map or self.kai_invoice_by_product or self.kai_invoice_by_order:
            allocated = amount_map.get(str(sale_line.id), 0.0) or 0.0
            if not self.kai_invoice_by_product:
                return allocated
            return self._kai_scale_mapped_value(
                allocated, amount_map, self.price_subtotal or 0.0, monetary=True
            )
        return self._kai_proportional_share(sale_line, self.price_subtotal or 0.0)

    def _kai_total_for_sale_line(self, sale_line):
        """Importe con impuestos, prorrateado con el peso del mapa sin impuestos."""
        self.ensure_one()
        amount_map = self.kai_invoice_by_order_amount_map or {}
        if amount_map:
            allocated = amount_map.get(str(sale_line.id), 0.0) or 0.0
            total = sum((value or 0.0) for value in amount_map.values())
            if not total:
                return 0.0
            return (self.price_total or 0.0) * (allocated / total)
        return self._kai_proportional_share(sale_line, self.price_total or 0.0)

    def _kai_scale_mapped_value(self, value, mapping, current_total, monetary=False):
        """Escala un valor del mapa cuando el total de la línea ya no coincide.

        Si la cantidad (o el subtotal) no cambió, dentro de la precisión de la
        unidad o de la moneda, se devuelve el valor original.
        """
        self.ensure_one()
        total = sum((item or 0.0) for item in mapping.values())
        if not total:
            return 0.0
        current_total = current_total or 0.0
        if monetary and self.currency_id:
            unchanged = self.currency_id.is_zero(total - current_total)
        else:
            rounding = self.product_uom_id.rounding if self.product_uom_id else 0.01
            unchanged = float_is_zero(current_total - total, precision_rounding=rounding)
        if unchanged:
            return value
        return value * current_total / total
