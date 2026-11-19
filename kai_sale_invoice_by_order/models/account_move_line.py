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

    def _get_collapsed_lines(self, children_lines):
        """El importe impreso de la sección es la suma exacta de los netos.

        Con precio e IVA incluido, sumar los subtotales en flotante (o mostrar
        la suma de los totales) puede dejar un centavo de más respecto del neto
        contable. El PDF usa price_subtotal o price_total según la compañía:
        en esta sección los dos valen la suma de price_subtotal.
        """
        if not (self.display_type == "line_section" and self.kai_invoice_by_order):
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

    def _kai_qty_for_sale_line(self, sale_line):
        """Cantidad de esta línea de factura imputable a una línea de venta.

        Las facturas nuevas agrupadas por pedido no usan este mapa: cada
        producto conserva su cantidad y su sale_line_ids. El mapa absoluto
        (cantidad de factura 1) queda para comprobantes ya emitidos con el
        renglón único. En la línea por producto el mapa está en la unidad de
        la venta y se escala si la cantidad de la factura cambió.
        """
        self.ensure_one()
        qty_map = self.kai_invoice_by_order_qty_map or {}
        mapped = qty_map.get(str(sale_line.id), 0.0) or 0.0
        if not self.kai_invoice_by_product:
            return mapped
        return self._kai_scale_mapped_value(mapped, qty_map, self.quantity)

    def _kai_untaxed_for_sale_line(self, sale_line):
        """Importe sin impuestos de esta línea imputable a una línea de venta."""
        self.ensure_one()
        amount_map = self.kai_invoice_by_order_amount_map or {}
        allocated = amount_map.get(str(sale_line.id), 0.0) or 0.0
        if not self.kai_invoice_by_product:
            return allocated
        return self._kai_scale_mapped_value(
            allocated, amount_map, self.price_subtotal or 0.0, monetary=True
        )

    def _kai_total_for_sale_line(self, sale_line):
        """Importe con impuestos, prorrateado con el peso del mapa sin impuestos."""
        self.ensure_one()
        amount_map = self.kai_invoice_by_order_amount_map or {}
        allocated = amount_map.get(str(sale_line.id), 0.0) or 0.0
        total = sum((value or 0.0) for value in amount_map.values())
        if not total:
            return 0.0
        return (self.price_total or 0.0) * (allocated / total)

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
