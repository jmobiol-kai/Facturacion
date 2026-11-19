from odoo import fields, models
from odoo.tools import float_is_zero


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

    def _kai_qty_for_sale_line(self, sale_line):
        """Cantidad de esta línea de factura imputable a una línea de venta.

        En el renglón por pedido el mapa es absoluto (la cantidad de la línea
        de factura es siempre 1). En la línea por producto el mapa está en la
        unidad de la venta y se escala si la cantidad de la factura cambió.
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
