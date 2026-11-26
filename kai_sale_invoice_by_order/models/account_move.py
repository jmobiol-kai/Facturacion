from odoo import _, Command, models
from odoo.tools import float_round
from odoo.tools.misc import format_date


class AccountMove(models.Model):
    _inherit = "account.move"

    def _kai_invoice_line_mode(self):
        """Modo de renglones para esta factura.

        El asistente manda en el contexto. Sin asistente se respeta el ajuste
        de la compañía: activo agrupa por pedido, inactivo deja el detalle nativo.
        """
        self.ensure_one()
        mode = self.env.context.get("kai_invoice_line_mode")
        if mode in ("products", "by_order", "native"):
            return mode
        if self.company_id.invoice_by_sale_order:
            return "by_order"
        return "native"

    def _kai_consolidate_invoice_lines_by_sale_order(self):
        for move in self:
            if move.state != "draft" or move.move_type not in ("out_invoice", "out_refund"):
                continue
            mode = move._kai_invoice_line_mode()
            if mode == "by_order":
                move._kai_do_consolidate_by_sale_order()
            elif mode == "products":
                move._kai_do_consolidate_by_product()


    def _kai_sale_order_section_date(self, order):
        """Fecha de la sección: última entrega validada, compromiso o pedido.

        La entrega validada es ``effective_date`` cuando el campo existe y
        tiene valor. Si no, es el ``date_done`` más reciente de los remitos
        en estado hecho. Sin entrega, se usa ``commitment_date`` y, si
        tampoco, ``date_order``. Los campos de stock se miran en ``_fields``
        porque este módulo no depende de sale_stock.
        """
        self.ensure_one()
        if "effective_date" in order._fields and order.effective_date:
            return order.effective_date
        if "picking_ids" in order._fields:
            done_dates = order.picking_ids.filtered(
                lambda picking: picking.state == "done" and picking.date_done
            ).mapped("date_done")
            if done_dates:
                return max(done_dates)
        if "commitment_date" in order._fields and order.commitment_date:
            return order.commitment_date
        return order.date_order

    def _kai_sale_order_section_name(self, order):
        self.ensure_one()
        date = self._kai_sale_order_section_date(order)
        return _("%s · %s · %s") % (
            order.name,
            format_date(self.env, date),
            order.partner_id.display_name or order.partner_id.name or "",
        )

    def _kai_do_consolidate_by_sale_order(self):
        """Una sección por pedido, con los renglones de producto intactos.

        El PDF y el portal usan collapse_composition y muestran el subtotal de
        la sección. Cada línea conserva product_id, cantidad, unidad, precio y
        sale_line_ids para que el costo de ventas (CMV) se genere al publicar.
        Consolidar el mismo producto queda solo en el modo «una línea por
        producto». No se reescriben facturas ya publicadas.
        """
        self.ensure_one()
        candidates = self.invoice_line_ids.filtered(
            lambda line: line.display_type in (
                "product", "line_section", "line_subsection", "line_note",
            )
            and not line.kai_invoice_by_order
            and not line.kai_invoice_by_product
            and not line.is_downpayment
            and len(line.sale_line_ids.order_id) == 1
        )
        done_orders = self.invoice_line_ids.filtered(
            lambda line: line.display_type == "line_section" and line.kai_sale_order_id
        ).kai_sale_order_id
        candidates = candidates.filtered(
            lambda line: line.sale_line_ids.order_id not in done_orders
        )
        if not candidates:
            return

        grouped = {}
        for line in candidates:
            order = line.sale_line_ids.order_id
            grouped.setdefault(order, self.env["account.move.line"])
            grouped[order] |= line
        orders = self.env["sale.order"].browse([order.id for order in grouped]).sorted(
            lambda order: (order.date_order, order.name or "", order.id)
        )
        grouped_lines = self.env["account.move.line"].concat(*grouped.values())
        # Anticipos y renglones sueltos van antes de la primera sección para no
        # quedar como hijos de la última (parent_id sigue la secuencia).
        others = self.invoice_line_ids.filtered(
            lambda line: line not in grouped_lines
            and not line.kai_sale_order_id
            and line.display_type in (
                "product", "line_section", "line_subsection", "line_note",
            )
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
                "name": self._kai_sale_order_section_name(order),
                "sequence": sequence,
                "collapse_composition": True,
                "kai_invoice_by_order": True,
                "kai_sale_order_id": order.id,
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

    def _kai_product_group_key(self, line):
        """Clave que no debe mezclar importes distintos del mismo producto."""
        price_rounding = self.currency_id.rounding or 0.01
        discount_digits = self.env["decimal.precision"].precision_get("Discount")
        return (
            line.product_id.id,
            float_round(line.price_unit, precision_rounding=price_rounding),
            float_round(line.discount or 0.0, precision_digits=discount_digits),
            tuple(sorted(line.tax_ids.ids)),
            line.product_uom_id.id,
            line.account_id.id,
            self._kai_analytic_key(line.analytic_distribution),
        )

    def _kai_analytic_key(self, distribution):
        if not distribution:
            return ()
        return tuple(sorted(
            (str(key), float_round(percentage, precision_digits=2))
            for key, percentage in distribution.items()
        ))

    def _kai_qty_in_sale_uom(self, invoice_line, sale_line):
        qty = invoice_line.quantity
        if (
            invoice_line.product_uom_id
            and sale_line.product_uom_id
            and invoice_line.product_uom_id != sale_line.product_uom_id
        ):
            qty = invoice_line.product_uom_id._compute_quantity(
                invoice_line.quantity, sale_line.product_uom_id, round=False
            )
        return qty

    def _kai_accumulate_product_maps(self, invoice_line, qty_map, amount_map):
        """Suma cantidad e importe sin impuestos de una línea nativa a los mapas.

        Una línea de factura nativa apunta a una sola línea de venta. Si alguna
        vez apunta a varias, la cantidad y el subtotal se prorratean por el
        importe de cada línea de venta para que el mapa sume la línea de factura.
        """
        sale_lines = invoice_line.sale_line_ids
        if not sale_lines:
            return
        subtotal = invoice_line.price_subtotal or 0.0
        if len(sale_lines) == 1:
            sale_line = sale_lines
            key = str(sale_line.id)
            qty_map[key] = qty_map.get(key, 0.0) + self._kai_qty_in_sale_uom(
                invoice_line, sale_line
            )
            amount_map[key] = amount_map.get(key, 0.0) + subtotal
            return
        weights = [(sale_line, abs(sale_line.price_subtotal or 0.0)) for sale_line in sale_lines]
        weight_total = sum(weight for _sale_line, weight in weights) or len(weights)
        for sale_line, weight in weights:
            share = weight / weight_total if weight_total else 0.0
            key = str(sale_line.id)
            qty_map[key] = qty_map.get(key, 0.0) + (
                self._kai_qty_in_sale_uom(invoice_line, sale_line) * share
            )
            amount_map[key] = amount_map.get(key, 0.0) + subtotal * share

    def _kai_product_invoice_line_name(self, product):
        """Nombre del producto, sin referencia interna ni texto de la venta."""
        self.ensure_one()
        lang = self.partner_id.lang or self.env.lang
        named = product.with_context(
            lang=lang,
            display_default_code=False,
            partner_id=False,
        )
        return named.display_name or named.name

    def _kai_do_consolidate_by_product(self):
        """Una línea por producto dentro de esta factura.

        Odoo ya separó las facturas por cliente de facturación, compañía,
        dirección de entrega, moneda y posición fiscal. Acá solo se agrupan
        las líneas de producto de esta factura. Secciones, notas y anticipos
        se dejan como están.
        """
        self.ensure_one()
        lines = self.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
            and line.product_id
            and line.sale_line_ids
            and not line.is_downpayment
            and not line.kai_invoice_by_order
            and not line.kai_invoice_by_product
        )
        if not lines:
            return

        groups = {}
        for line in lines:
            key = self._kai_product_group_key(line)
            groups.setdefault(key, self.env["account.move.line"])
            groups[key] |= line

        mergeable = {
            key: group for key, group in groups.items() if len(group) > 1
        }
        if not mergeable:
            return

        def sort_key(item):
            _key, group_lines = item
            first = group_lines.sorted(lambda line: (line.sequence, line.id))[:1]
            return (first.sequence, first.id)

        create_vals = []
        to_remove = self.env["account.move.line"]
        for key, group in sorted(mergeable.items(), key=sort_key):
            group = group.sorted(lambda line: (line.sequence, line.id))
            sample = group[:1]
            qty_map = {}
            amount_map = {}
            for invoice_line in group:
                self._kai_accumulate_product_maps(invoice_line, qty_map, amount_map)
            _product_id, _price, _discount, tax_key, _uom, account_id, _analytic = key
            vals = {
                "move_id": self.id,
                "display_type": "product",
                "name": self._kai_product_invoice_line_name(sample.product_id),
                "product_id": sample.product_id.id,
                "product_uom_id": sample.product_uom_id.id,
                "quantity": sum(group.mapped("quantity")),
                "discount": sample.discount,
                "price_unit": sample.price_unit,
                "tax_ids": [Command.set(list(tax_key))],
                "account_id": account_id,
                "sale_line_ids": [Command.set(group.sale_line_ids.ids)],
                "kai_invoice_by_product": True,
                "kai_invoice_by_order_qty_map": qty_map,
                "kai_invoice_by_order_amount_map": amount_map,
                "sequence": min(group.mapped("sequence") or [1]),
                "currency_id": self.currency_id.id,
            }
            if sample.analytic_distribution:
                vals["analytic_distribution"] = sample.analytic_distribution
            create_vals.append(vals)
            to_remove |= group

        if not create_vals:
            return
        to_remove.with_context(
            check_move_validity=False, skip_invoice_sync=True
        ).unlink()
        self.env["account.move.line"].with_context(check_move_validity=False).create(
            create_vals
        )
