from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    invoice_by_sale_order = fields.Boolean(
        string="Agrupar la factura por pedido de venta",
        default=True,
        help="Al crear una factura, cada pedido queda como una sección con su "
             "referencia y debajo los productos reales (cantidad, unidad, precio "
             "y vínculo con la línea de venta). En el PDF y el portal la sección "
             "se imprime como un importe por pedido, o uno por alícuota si el "
             "pedido combina impuestos. Así el costo de ventas usa el producto "
             "correcto al publicar.",
    )
