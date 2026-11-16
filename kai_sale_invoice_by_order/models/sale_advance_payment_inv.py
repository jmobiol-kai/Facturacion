from odoo import fields, models


class SaleAdvancePaymentInv(models.TransientModel):
    _inherit = "sale.advance.payment.inv"

    invoice_line_mode = fields.Selection(
        selection=[
            ("products", "Detalle de productos"),
            ("by_order", "Un renglón por orden de venta"),
        ],
        string="Renglones de factura",
        required=True,
        default=lambda self: "by_order" if self.env.company.invoice_by_sale_order else "products",
        help="Detalle de productos deja una línea por producto, como en el pedido. "
             "Un renglón por orden de venta agrupa cada pedido en una sola línea "
             "(o una por alícuota, si un pedido combina impuestos distintos).",
    )

    def _create_invoices(self, sale_orders):
        self.ensure_one()
        # The mode must travel on the sale orders: super() calls
        # sale_orders._create_invoices(), which would otherwise keep
        # the caller context and ignore the wizard choice.
        return super()._create_invoices(
            sale_orders.with_context(kai_invoice_line_mode=self.invoice_line_mode)
        )
