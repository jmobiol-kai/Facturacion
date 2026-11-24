from odoo import _, models
from odoo.tools.misc import format_date

_INVOICE_DISPLAY_TYPES = ("product", "line_section", "line_subsection", "line_note")


class AccountMove(models.Model):
    _inherit = "account.move"

    def _kai_should_group_bill_lines(self):
        """El asistente puede reemplazar el valor de la compañía para una sola corrida."""
        self.ensure_one()
        mode = self.env.context.get("kai_bill_line_mode")
        if mode == "products":
            return False
        if mode == "by_order":
            return True
        return bool(self.company_id.bill_by_purchase_order)

    def _kai_consolidate_bill_lines_by_purchase_order(self):
        for move in self:
            if (
                move.state != "draft"
                or move.move_type != "in_invoice"
                or not move._kai_should_group_bill_lines()
            ):
                continue
            move._kai_do_consolidate_by_purchase_order()

    def _kai_purchase_order_section_name(self, order):
        self.ensure_one()
        return _("%s · %s · %s") % (
            order.name,
            format_date(self.env, order.date_order),
            order.partner_id.display_name or order.partner_id.name or "",
        )

    def _kai_do_consolidate_by_purchase_order(self):
        """Una sección por orden, con los renglones de producto intactos.

        El PDF y el portal usan collapse_composition: la sección se imprime
        como un importe (o uno por alícuota). Producto, cantidad, unidad,
        precio y purchase_line_id quedan en el asiento para la valuación FIFO,
        la diferencia de precio y el costo. No se reescriben facturas ya
        publicadas: las que se armaron con un renglón sin producto siguen
        usando su mapa de cantidades.
        """
        self.ensure_one()
        candidates = self.invoice_line_ids.filtered(
            lambda line: line.purchase_line_id
            and not line.purchase_line_id.is_downpayment
            and not line.kai_bill_by_order
            and line.display_type in _INVOICE_DISPLAY_TYPES
        )
        done_orders = self.invoice_line_ids.filtered(
            lambda line: line.display_type == "line_section" and line.kai_purchase_order_id
        ).kai_purchase_order_id
        candidates = candidates.filtered(
            lambda line: line.purchase_line_id.order_id not in done_orders
        )
        if not candidates:
            return

        grouped = {}
        for line in candidates:
            order = line.purchase_line_id.order_id
            grouped.setdefault(order, self.env["account.move.line"])
            grouped[order] |= line
        orders = self.env["purchase.order"].browse([order.id for order in grouped]).sorted(
            lambda order: (order.date_order, order.name or "", order.id)
        )
        grouped_lines = self.env["account.move.line"].concat(*grouped.values())
        # Anticipos y renglones sueltos van antes de la primera sección: si
        # quedaran después, el cómputo de parent_id los metería en la última orden.
        others = self.invoice_line_ids.filtered(
            lambda line: line not in grouped_lines
            and not line.kai_purchase_order_id
            and line.display_type in _INVOICE_DISPLAY_TYPES
        )

        sequence = 10
        rewrites = []
        section_vals = []
        for line in others.sorted(lambda item: (item.sequence, item.id)):
            rewrites.append((line, sequence, False))
            sequence += 10
        for order in orders:
            section_vals.append({
                "move_id": self.id,
                "display_type": "line_section",
                "name": self._kai_purchase_order_section_name(order),
                "sequence": sequence,
                "collapse_composition": True,
                "kai_bill_by_order": True,
                "kai_purchase_order_id": order.id,
            })
            sequence += 10
            for line in grouped[order].sorted(lambda item: (item.sequence, item.id)):
                rewrites.append((line, sequence, line.display_type == "line_section"))
                sequence += 10

        context = {"check_move_validity": False, "skip_invoice_sync": True}
        sections = self.env["account.move.line"]
        if section_vals:
            sections = self.env["account.move.line"].with_context(**context).create(section_vals)
        # _compute_sequence depende de display_type y, al crear la sección o al
        # volverla subsección, la deja en 100. La secuencia definitiva se escribe
        # después, cuando ese cómputo ya no corre.
        for line, _sequence, becomes_subsection in rewrites:
            if becomes_subsection:
                line.with_context(**context).write({"display_type": "line_subsection"})
        for section, vals in zip(sections, section_vals):
            if section.sequence != vals["sequence"]:
                section.with_context(**context).write({"sequence": vals["sequence"]})
        for line, line_sequence, _becomes_subsection in rewrites:
            if line.sequence != line_sequence:
                line.with_context(**context).write({"sequence": line_sequence})
