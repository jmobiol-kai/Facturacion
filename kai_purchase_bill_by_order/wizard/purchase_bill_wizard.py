from odoo import _, api, fields, models
from odoo.exceptions import UserError


class PurchaseBillWizard(models.TransientModel):
    _name = "kai.purchase.bill.wizard"
    _description = "Crear facturas de proveedor desde órdenes de compra"

    purchase_order_ids = fields.Many2many(
        "purchase.order",
        string="Órdenes de compra",
        default=lambda self: self._default_purchase_orders(),
    )
    invoice_line_mode = fields.Selection(
        selection=[
            ("products", "Detalle de productos"),
            ("by_order", "Un renglón por orden de compra"),
        ],
        string="Renglones de factura",
        required=True,
        default=lambda self: "by_order" if self.env.company.bill_by_purchase_order else "products",
        help="Detalle de productos deja una línea por producto, como en la orden, "
             "sin sección. Un renglón por orden de compra antepone una sección con "
             "la referencia de la orden y mantiene los productos: el PDF muestra "
             "un importe por orden (o por alícuota) y el asiento conserva el "
             "producto para la valuación y el costo.",
    )
    consolidate_by_vendor = fields.Boolean(
        string="Una factura por proveedor",
        default=True,
        help="Si está tildado, las órdenes del mismo proveedor, compañía y moneda van "
             "en una sola factura. Si no, se crea una factura por cada orden.",
    )

    @api.model
    def _default_purchase_orders(self):
        if self.env.context.get("active_model") != "purchase.order":
            return self.env["purchase.order"]
        return self.env["purchase.order"].browse(self.env.context.get("active_ids") or []).exists()

    def action_create_bills(self):
        self.ensure_one()
        orders = self.purchase_order_ids
        if not orders:
            raise UserError(_("Elegí al menos una orden de compra."))
        not_billable = orders.filtered(lambda order: order.invoice_status != "to invoice")
        if not_billable:
            raise UserError(_(
                "Estas órdenes no tienen nada para facturar: %s.",
                ", ".join(not_billable.mapped("name")),
            ))
        before = orders.invoice_ids
        orders = orders.with_context(kai_bill_line_mode=self.invoice_line_mode)
        if self.consolidate_by_vendor:
            orders.action_create_invoice()
        else:
            for order in orders:
                order.action_create_invoice()
        orders.invalidate_model(["invoice_ids"])
        return self.purchase_order_ids.action_view_invoice(orders.invoice_ids - before)
