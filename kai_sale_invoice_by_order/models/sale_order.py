from odoo import models


class SaleOrder(models.Model):
    _inherit = "sale.order"

    def _get_invoice_grouping_keys(self):
        """Agrupa sin importar la dirección de entrega.

        El núcleo de Odoo 19 incluye ``partner_shipping_id`` en la clave, y
        eso parte en facturas distintas pedidos del mismo cliente de
        facturación con destinos distintos (p. ej. Ines y Laura en Bunge).
        El vendedor (``user_id``) no forma parte de la clave.
        """
        return [
            key
            for key in super()._get_invoice_grouping_keys()
            if key != "partner_shipping_id"
        ]

    def _create_invoices(self, grouped=False, final=False, date=None):
        moves = super()._create_invoices(grouped=grouped, final=final, date=date)
        if moves:
            moves._kai_align_header_from_grouped_orders()
            moves._kai_consolidate_invoice_lines_by_sale_order()
        return moves
