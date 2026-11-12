# -*- coding: utf-8 -*-

from odoo import api, fields, models, _


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

    def _recompute_line_taxes(self):
        """Core logic to recompute all invoice line taxes using journal mapping + fiscal position.
        Can be called from onchange (in-memory records) or from action methods (saved records).
        """
        for move in self:
            for line in move.invoice_line_ids.filtered(lambda l: l.display_type == 'product'):
                # Route through account.move.line._get_computed_taxes() instead of
                # rebuilding tax_ids by hand. That method is the single source of truth:
                #   - core account applies product/supplier taxes + fiscal_position_id.map_tax()
                #   - our AccountMoveLine._get_computed_taxes() override applies the journal
                #     tax mappings on top (journal.map_taxes)
                #   - on AR localization, l10n_ar_tax's override appends the fiscal-position
                #     perceptions for sale documents.
                # Rebuilding by hand here bypassed that last step and STRIPPED perceptions.
                # Using the computed-taxes pipeline preserves the journal VAT remap AND the
                # perceptions, and stays CE-safe (no l10n_ar-only API is called here; the
                # perception append happens only if that localization layer is installed).
                line.tax_ids = line._get_computed_taxes()

    @api.onchange('journal_id')
    def _onchange_journal_recompute_flag(self):
        """Automatically recompute taxes when journal changes."""
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
