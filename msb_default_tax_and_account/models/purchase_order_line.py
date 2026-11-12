# -*- coding: utf-8 -*-

import logging
from odoo import api, fields, models
from odoo.tools import frozendict
from collections import defaultdict

_logger = logging.getLogger(__name__)


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    # UPDATED: Show tax mappings instead of default taxes
    impuestos_diario = fields.Many2many(
        'account.tax',
        compute='_compute_impuestos_diario',
        string='Impuestos del Mapeo del Diario',
        readonly=True,
        help='Muestra el resultado de aplicar los mapeos de impuestos del diario a los impuestos de proveedor de este producto'
    )
    
    usar_cuenta_diario = fields.Boolean(
        related='order_id.journal_id.usar_cuenta_defecto_compras',
        string='Usar Cuenta del Diario',
        readonly=True,
        help='Indica si este diario está configurado para usar cuenta por defecto'
    )

    @api.depends('product_id', 'order_id.journal_id')
    def _compute_impuestos_diario(self):
        """
        Compute the taxes that would result from journal mappings for this product.
        """
        for line in self:
            if (line.product_id and line.order_id.journal_id and
                line.order_id.journal_id.has_tax_mappings('purchase')):
                supplier_taxes = line.product_id.supplier_taxes_id._filter_taxes_by_company(line.company_id)
                line.impuestos_diario = line.order_id.journal_id.map_taxes(supplier_taxes, 'purchase')
            else:
                line.impuestos_diario = False

    def _compute_tax_id(self):
        """
        NEW BEHAVIOR: Apply journal tax mappings to supplier taxes when configured.
        
        1. Start with product supplier taxes (standard Odoo behavior)  
        2. Apply journal tax mappings if configured
        3. Apply fiscal position mappings

        Note: In purchase, `taxes_id` is not a computed field. This override is
        called by Odoo flows (e.g., `_product_id_change`, `_prepare_add_missing_fields`).
        """
        for line in self:
            line = line.with_company(line.company_id)
            fpos = line.order_id.fiscal_position_id or line.order_id.fiscal_position_id._get_fiscal_position(line.order_id.partner_id)

            # NEW TAX MAPPING LOGIC:
            # 1. Start with product taxes (standard Odoo behavior)
            if line.product_id and not line.display_type:
                taxes = line.product_id.supplier_taxes_id._filter_taxes_by_company(line.company_id)
                
                # 2. Apply journal tax mappings if configured
                journal = line.order_id.journal_id
                if journal and journal.has_tax_mappings('purchase'):
                    taxes = journal.map_taxes(taxes, 'purchase')
            else:
                taxes = False

            line.tax_ids = fpos.map_tax(taxes) if (fpos and taxes) else (taxes or False)


    def _prepare_account_move_line(self, move=False):
        """
        Override to ensure that when creating vendor bills from purchase orders,
        invoice line taxes come from the target journal's default purchase taxes
        and accounts (mapped through fiscal position) when configured.
        Fallback to standard behavior otherwise.
        """
        res = super()._prepare_account_move_line(move=move)

        # Only adjust for real product lines
        if self.display_type:
            return res

        # Get journal from multiple sources in order of preference
        journal = None
        if move and move.journal_id:
            journal = move.journal_id
        elif self.order_id.journal_id:
            # NEW: Get journal from PO directly
            journal = self.order_id.journal_id
        elif self.env.context.get('default_journal_id'):
            # Get journal from context when move is not available
            journal = self.env['account.journal'].browse(self.env.context['default_journal_id'])

        # FIXED: Handle move type check properly when move is False
        if move:
            if move.move_type not in ('in_invoice', 'in_refund'):
                return res
        # If no move but we have journal, assume we're creating vendor bill from PO

        if not journal:
            return res

        # Apply journal tax mappings (new behavior)
        if journal and journal.has_tax_mappings('purchase'):
            # Start from product supplier taxes filtered by company
            company = (move.company_id if move else self.order_id.company_id)
            taxes = self.product_id.supplier_taxes_id._filter_taxes_by_company(company) if self.product_id else self.tax_ids

            if taxes:
                # Apply journal mappings first
                mapped = journal.map_taxes(taxes, 'purchase')

                # Then map through fiscal position (prefer move FP; fallback to PO FP)
                fiscal_position = (move and move.fiscal_position_id) or self.order_id.fiscal_position_id
                mapped_taxes = fiscal_position.map_tax(mapped) if fiscal_position else mapped

                # Audit trail: log when journal taxes override PO line taxes
                try:
                    if set(mapped_taxes.ids) != set((self.tax_ids or self.env['account.tax']).ids):
                        _logger.info(
                            "PO line %s: Applying journal tax mappings %s instead of PO taxes %s",
                            self.id, mapped_taxes.mapped('name'), (self.tax_ids or self.env['account.tax']).mapped('name')
                        )
                except Exception:
                    # Be safe: never break invoice creation due to logging
                    pass

                res['tax_ids'] = [(6, 0, mapped_taxes.ids)]

        # Apply journal default account if configured
        if (journal.usar_cuenta_defecto_compras and 
            journal.default_account_id):
            
            res['account_id'] = journal.default_account_id.id
            
            # Log account override for audit trail
            try:
                _logger.info(
                    "PO line %s: Using journal default account %s",
                    self.id, journal.default_account_id.display_name
                )
            except Exception:
                pass

        return res
