# -*- coding: utf-8 -*-

from odoo import api, fields, models, Command, _


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'
    
    def _get_computed_taxes(self):
        """Override to use journal tax mappings if configured, with fiscal position mapping.

        NEW BEHAVIOR:
        1. Get standard computed taxes (from product or account)  
        2. Apply journal tax mappings if configured
        3. Apply fiscal position mapping
        
        Applies to both sales and purchases on product lines.
        """
        move = self.move_id
        journal = move and move.journal_id or False
        
        # Get standard computed taxes first
        taxes = super(AccountMoveLine, self)._get_computed_taxes()
        
        # Apply journal tax mappings if configured
        is_frozen_ee_move = bool(getattr(move, 'deferred_original_move_ids', False) or getattr(move, 'asset_id', False))
        if move and journal and self.display_type == 'product' and taxes and not is_frozen_ee_move:
            # Determine tax type based on move type
            tax_type = 'sale' if move.move_type in ('out_invoice', 'out_refund') else 'purchase'
            
            # Apply mappings if configured
            if journal.has_tax_mappings(tax_type):
                taxes = journal.map_taxes(taxes, tax_type)
                
        return taxes
    
    @api.onchange('product_id')
    def _onchange_product_id_set_journal_account(self):
        """
        Sugerir la cuenta por defecto del diario cuando se selecciona un producto.
        
        Nota: Este método solo se ejecuta durante interacciones de UI (creación manual de facturas).
        Para facturas creadas desde órdenes de venta, la cuenta se establece automáticamente
        en sale.order.line._prepare_invoice_line().
        """
        if not self.product_id:
            return
        
        # Aplicar a ventas o compras según configuración del diario
        if not (self.display_type == 'product' and self.move_id and self.move_id.journal_id and self.move_id.journal_id.default_account_id):
            return

        is_sale = self.move_id.move_type in ['out_invoice', 'out_refund']
        is_purchase = self.move_id.move_type in ['in_invoice', 'in_refund']
        apply_sale = is_sale and self.move_id.journal_id.usar_cuenta_defecto_facturas
        apply_purchase = is_purchase and getattr(self.move_id.journal_id, 'usar_cuenta_defecto_compras', False)

        if apply_sale or apply_purchase:
            # Obtener la cuenta que habría usado el producto
            product_account = None
            if self.product_id:
                try:
                    fiscal_position = self.move_id.fiscal_position_id
                    accounts = self.with_company(self.move_id.company_id).product_id.product_tmpl_id.get_product_accounts(fiscal_pos=fiscal_position)
                    product_account = accounts.get('income') if is_sale else accounts.get('expense')
                except Exception:
                    # Si hay error obteniendo la cuenta del producto, continuamos
                    pass
            
            # Aplicar cuenta del diario
            journal_account = self.move_id.journal_id.default_account_id
            self.account_id = journal_account
            
            # Nota: Anteriormente se mostraba una notificación informando que la cuenta
            # del diario reemplazaba a la del producto. Se solicita ocultar/"comentar"
            # dicha notificación, por lo que no se devuelve el warning.
            #
            # if product_account and product_account != journal_account:
            #     return {
            #         'warning': {
            #             'title': _('Cuenta Modificada por Diario'),
            #             'message': _(
            #                 'Se aplicó la cuenta por defecto del diario (%s) '
            #                 'en lugar de la cuenta del producto (%s). '
            #                 'Puede cambiarla manualmente si lo desea.'
            #             ) % (journal_account.display_name, product_account.display_name),
            #             'type': 'notification',
            #         }
            #     }


class AccountMove(models.Model):
    _inherit = 'account.move'

    tax_recompute_needed = fields.Boolean(
        string='Recálculo de Impuestos Necesario',
        default=False,
        help='Se activa cuando cambia el diario; muestra solicitud para recalcular impuestos (sobrescribir todo).'
    )

    def write(self, vals):
        # A draft vendor bill whose journal changes (from the bill itself or
        # from the linked PO) must remap line taxes. Customer invoices keep
        # the onchange / _get_computed_taxes path.
        if self.env.context.get('msb_skip_journal_tax_sync') or 'journal_id' not in vals:
            return super().write(vals)
        previous = {move.id: move.journal_id.id for move in self}
        res = super().write(vals)
        changed = self.filtered(
            lambda move: move.state == 'draft'
            and move.move_type in ('in_invoice', 'in_refund')
            and previous.get(move.id) != move.journal_id.id
        )
        if changed:
            changed._msb_remap_draft_vendor_bill_taxes()
        return res

    def _msb_remap_draft_vendor_bill_taxes(self, purchase_lines_only=False, purchase_order=None):
        """Remap draft vendor-bill product lines and rebuild tax totals.

        Posted and cancelled moves are ignored. Down payment lines are left
        as the user entered them. ``purchase_lines_only`` limits the update to
        lines linked to ``purchase_order`` (or to any PO line).
        """
        drafts = self.filtered(
            lambda move: move.state == 'draft' and move.move_type in ('in_invoice', 'in_refund')
        )
        resolver = self.env['account.journal']._msb_resolve_line_taxes
        for move in drafts:
            lines = move.invoice_line_ids.filtered(
                lambda line: line.display_type == 'product' and not line.is_downpayment
            )
            if purchase_lines_only:
                lines = lines.filtered('purchase_line_id')
                if purchase_order:
                    lines = lines.filtered(lambda line: line.purchase_line_id.order_id == purchase_order)
            commands = []
            for line in lines:
                taxes = resolver(
                    move.journal_id, line.product_id, line.tax_ids,
                    move.fiscal_position_id, move.company_id, 'purchase',
                )
                if set(taxes.ids) != set(line.tax_ids.ids):
                    commands.append(Command.update(line.id, {
                        'tax_ids': [Command.set(taxes.ids)],
                    }))
            if commands:
                # A second write (no journal_id) runs the dynamic tax-line sync
                # so amount_tax follows the new taxes. The context stops that
                # write from remapping again.
                move.with_context(msb_skip_journal_tax_sync=True).write({
                    'invoice_line_ids': commands,
                })

    def _msb_assign_vendor_bill_taxes(self):
        """In-memory remap used by onchange and the manual recompute action."""
        resolver = self.env['account.journal']._msb_resolve_line_taxes
        for move in self:
            for line in move.invoice_line_ids.filtered(
                lambda line: line.display_type == 'product' and not line.is_downpayment
            ):
                taxes = resolver(
                    move.journal_id, line.product_id, line.tax_ids,
                    move.fiscal_position_id, move.company_id, 'purchase',
                )
                if set(line.tax_ids.ids) != set(taxes.ids):
                    line.tax_ids = taxes

    def _recompute_line_taxes(self):
        """Recompute invoice line taxes from the journal.

        Vendor bills use the purchase resolver (product supplier taxes, journal
        mappings including reverse maps, fiscal position). With no mappings the
        line falls back to the product taxes, so a leftover X tax is removed.

        Customer invoices stay on ``_get_computed_taxes``. That pipeline applies
        the product tax, the fiscal position and the journal map, and keeps
        localization extras (AR perceptions) that a hand-built tax set would drop.
        """
        purchase_moves = self.filtered(lambda move: move.move_type in ('in_invoice', 'in_refund'))
        if purchase_moves:
            purchase_moves._msb_assign_vendor_bill_taxes()
        for move in self - purchase_moves:
            for line in move.invoice_line_ids.filtered(lambda line: line.display_type == 'product'):
                line.tax_ids = line._get_computed_taxes()

    @api.onchange('journal_id')
    def _onchange_journal_recompute_flag(self):
        """Recompute taxes in the form. A saved journal change also runs in ``write``."""
        for move in self:
            if move.invoice_line_ids:
                move._recompute_line_taxes()
            move.tax_recompute_needed = False

    def action_recompute_taxes_force(self):
        """Overwrite all invoice line taxes using journal mapping + fiscal position.
        Applies to product lines only. Clears the recompute flag afterwards.
        Kept for backward compatibility.
        """
        self._recompute_line_taxes()
        self.write({'tax_recompute_needed': False})
        return True
