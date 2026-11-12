# -*- coding: utf-8 -*-

from odoo import models, fields, api
from odoo.tools import frozendict
from collections import defaultdict


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    # UPDATED: Show tax mappings instead of default taxes
    impuestos_diario = fields.Many2many(
        'account.tax',
        compute='_compute_impuestos_diario',
        string='Impuestos del Mapeo del Diario',
        readonly=True,
        help='Muestra el resultado de aplicar los mapeos de impuestos del diario a los impuestos de este producto'
    )
    
    usar_cuenta_diario = fields.Boolean(
        related='order_id.journal_id.usar_cuenta_defecto_facturas',
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
                line.order_id.journal_id.has_tax_mappings('sale')):
                product_taxes = line.product_id.taxes_id._filter_taxes_by_company(line.company_id)
                line.impuestos_diario = line.order_id.journal_id.map_taxes(product_taxes, 'sale')
            else:
                line.impuestos_diario = False

    @api.depends(
        'product_id',
        'company_id',
        'order_id.fiscal_position_id',
        'order_id.journal_id',
        'order_id.journal_id.tax_mapping_ids',
    )
    def _compute_tax_ids(self):
        """
        Override tax computation to use journal tax mappings when configured.
        
        NEW BEHAVIOR:
        1. Start with product taxes (standard Odoo behavior)
        2. Apply journal tax mappings if configured
        3. Apply fiscal position mappings
        
        Maintains the same performance optimizations as the original method.
        """
        # Group lines by company for efficiency
        lines_by_company = defaultdict(lambda: self.env['sale.order.line'])
        cached_taxes = {}
        
        for line in self:
            lines_by_company[line.company_id] += line
        
        for company, lines in lines_by_company.items():
            for line in lines.with_company(company):
                taxes = None
                
                # NEW TAX MAPPING LOGIC:
                # 1. Start with product taxes (standard Odoo behavior)
                if line.product_id and not line.display_type:
                    taxes = line.product_id.taxes_id._filter_taxes_by_company(company)
                    
                    # 2. Apply journal tax mappings if configured
                    if line.order_id.journal_id and line.order_id.journal_id.has_tax_mappings('sale'):
                        taxes = line.order_id.journal_id.map_taxes(taxes, 'sale')
                else:
                    taxes = None
                
                if not taxes:
                    line.tax_ids = self.env['account.tax']
                    continue
                
                # Apply fiscal position mapping if exists
                fiscal_position = line.order_id.fiscal_position_id
                
                # Use caching for performance (include journal in cache key)
                cache_key = (
                    fiscal_position.id, 
                    company.id, 
                    tuple(taxes.ids),
                    line.order_id.journal_id.id if line.order_id.journal_id else False
                )
                cache_key += line._get_custom_compute_tax_cache_key()
                
                if cache_key in cached_taxes:
                    result = cached_taxes[cache_key]
                else:
                    result = fiscal_position.map_tax(taxes) if fiscal_position else taxes
                    cached_taxes[cache_key] = result

                line.tax_ids = result

    def _get_custom_compute_tax_cache_key(self):
        """
        Extend the cache key to include journal tax mapping information for tax computation.
        This ensures proper cache invalidation when journal settings change.
        """
        cache_key = super()._get_custom_compute_tax_cache_key()
        
        # Add journal tax mapping configuration to cache key
        if self.order_id.journal_id:
            # Include tax mapping information in cache key
            mapping_data = tuple(
                (m.original_tax_id.id, m.replacement_tax_id.id, m.active) 
                for m in self.order_id.journal_id.sale_tax_mapping_ids
            )
            use_journal_account = self.order_id.journal_id.usar_cuenta_defecto_facturas
            cache_key += (mapping_data, use_journal_account)
        
        return cache_key

    def _prepare_invoice_line(self, **optional_values):
        """
        Override to ensure journal-specific settings are properly transferred
        to the invoice line during invoice creation.
        """
        values = super()._prepare_invoice_line(**optional_values)
        
        # The tax_ids are already set correctly by _compute_tax_ids
        # Just ensure they are properly transferred
        if self.tax_ids:
            values['tax_ids'] = [(6, 0, self.tax_ids.ids)]
        
        # Handle journal default account if configured
        if (not self.display_type and 
            self.order_id.journal_id and 
            self.order_id.journal_id.usar_cuenta_defecto_facturas and
            self.order_id.journal_id.default_account_id):
            
            values['account_id'] = self.order_id.journal_id.default_account_id.id
        
        return values

    @api.onchange('product_id')
    def _onchange_product_id_taxes(self):
        """No UI warnings required; behavior handled in _compute_tax_ids."""
        return None
