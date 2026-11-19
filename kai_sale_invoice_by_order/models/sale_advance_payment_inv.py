from odoo import fields, models


class SaleAdvancePaymentInv(models.TransientModel):
    _inherit = "sale.advance.payment.inv"

    invoice_line_mode = fields.Selection(
        # Las claves `products` y `by_order` no cambian: el asistente es
        # transitorio y el default de la compañía sigue apuntando a las mismas.
        # `native` es el detalle anterior de `products` (una línea por venta).
        selection=[
            ("products", "Una línea por producto"),
            ("by_order", "Un renglón por orden de venta"),
            ("native", "Sin consolidar"),
        ],
        string="Renglones de factura",
        required=True,
        default=lambda self: "by_order" if self.env.company.invoice_by_sale_order else "products",
        help="Una línea por producto suma las cantidades del mismo producto cuando "
             "coinciden el precio unitario, el descuento, los impuestos, la unidad "
             "de medida, la cuenta contable y la distribución analítica, y conserva "
             "el vínculo con todas las líneas de venta. "
             "Un renglón por orden de venta antepone una sección con la referencia "
             "del pedido y mantiene los productos: el PDF muestra un importe por "
             "pedido (o por alícuota) y el asiento conserva el producto para el "
             "costo de ventas. "
             "Sin consolidar deja una línea por cada línea de venta.",
    )

    def _create_invoices(self, sale_orders):
        self.ensure_one()
        # The mode must travel on the sale orders: super() calls
        # sale_orders._create_invoices(), which would otherwise keep
        # the caller context and ignore the wizard choice.
        return super()._create_invoices(
            sale_orders.with_context(kai_invoice_line_mode=self.invoice_line_mode)
        )
