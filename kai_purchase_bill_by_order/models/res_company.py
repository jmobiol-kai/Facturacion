from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    bill_by_purchase_order = fields.Boolean(
        string="Agrupar la factura de proveedor por orden de compra",
        default=True,
        help="Al crear una factura de proveedor, cada orden queda como una sección "
             "con su referencia y debajo los productos reales (cantidad, unidad, "
             "precio y vínculo con la línea de compra). En el PDF y el portal la "
             "sección se imprime como un importe por orden, o uno por alícuota si "
             "la orden combina impuestos. Así la valuación y el costo usan el "
             "producto correcto al publicar.",
    )
