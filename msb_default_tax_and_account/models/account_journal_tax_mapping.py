# -*- coding: utf-8 -*-

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class AccountJournalTaxMapping(models.Model):
    _name = 'account.journal.tax.mapping'
    _description = 'Mapeo de Impuestos para Diario'
    _order = 'journal_id, tax_type, original_tax_id'

    # Core fields
    journal_id = fields.Many2one(
        'account.journal',
        string='Diario',
        required=True,
        ondelete='cascade',
        help='Diario al cual se aplica este mapeo de impuestos'
    )
    
    original_tax_id = fields.Many2one(
        'account.tax',
        string='Impuesto Original',
        required=True,
        domain="[('type_tax_use', '=', tax_type), '|', ('company_id', '=', company_id), ('company_id', 'parent_of', company_id)]",
        help='Impuesto que será reemplazado'
    )
    
    replacement_tax_id = fields.Many2one(
        'account.tax',
        string='Impuesto de Reemplazo',
        required=True,
        domain="[('type_tax_use', '=', tax_type), '|', ('company_id', '=', company_id), ('company_id', 'parent_of', company_id)]",
        help='Impuesto que se usará en lugar del impuesto original'
    )
    
    tax_type = fields.Selection([
        ('sale', 'Ventas'),
        ('purchase', 'Compras'),
    ], string='Tipo de Impuesto', required=True, help='Tipo de operación al cual se aplica este mapeo')
    
    active = fields.Boolean(
        default=True,
        help='Desmarcar para desactivar este mapeo sin eliminarlo'
    )
    
    # Related fields for better UX
    company_id = fields.Many2one(
        related='journal_id.company_id',
        string='Compañía',
        store=True,
        readonly=True
    )
    
    original_tax_name = fields.Char(
        related='original_tax_id.name',
        string='Nombre del Impuesto Original',
        readonly=True
    )
    
    replacement_tax_name = fields.Char(
        related='replacement_tax_id.name',
        string='Nombre del Impuesto de Reemplazo',
        readonly=True
    )

    # Constraints
    _unique_mapping = models.Constraint(
        'UNIQUE(journal_id, original_tax_id, tax_type)',
        "\u00a1Ya existe un mapeo para este impuesto en este diario!",
    )

    @api.constrains('original_tax_id', 'replacement_tax_id', 'tax_type')
    def _check_tax_types_compatibility(self):
        """Ensure original and replacement taxes are compatible"""
        for mapping in self:
            if mapping.original_tax_id.type_tax_use != mapping.replacement_tax_id.type_tax_use:
                raise ValidationError(_(
                    'El impuesto original "%(original)s" (%(original_type)s) y el impuesto de reemplazo "%(replacement)s" '
                    '(%(replacement_type)s) deben tener el mismo tipo de uso de impuesto.',
                    original=mapping.original_tax_id.name,
                    original_type=mapping.original_tax_id.type_tax_use,
                    replacement=mapping.replacement_tax_id.name,
                    replacement_type=mapping.replacement_tax_id.type_tax_use,
                ))
                
            # Verify tax_type matches the taxes' type_tax_use
            expected_tax_use = mapping.tax_type
            if mapping.replacement_tax_id.type_tax_use != expected_tax_use:
                raise ValidationError(_(
                    'El tipo de impuesto "%(tax_type)s" no coincide con el uso del impuesto de reemplazo "%(tax_use)s"',
                    tax_type=mapping.tax_type,
                    tax_use=mapping.replacement_tax_id.type_tax_use,
                ))

    @api.constrains('original_tax_id', 'replacement_tax_id')
    def _check_no_circular_mapping(self):
        """Prevent circular mappings"""
        for mapping in self:
            if mapping.original_tax_id == mapping.replacement_tax_id:
                raise ValidationError(_(
                    'No se puede mapear el impuesto "%(tax)s" a sí mismo',
                    tax=mapping.original_tax_id.name
                ))

    @api.depends('journal_id', 'original_tax_id', 'replacement_tax_id', 'active')
    def _compute_display_name(self):
        """Custom name for better identification"""
        for mapping in self:
            name = f"{mapping.journal_id.name}: {mapping.original_tax_id.name} → {mapping.replacement_tax_id.name}"
            if not mapping.active:
                name = f"[INACTIVE] {name}"
            mapping.display_name = name

    @api.model_create_multi
    def create(self, vals_list):
        """Enhanced create with validation and default tax_type from context"""
        # Set tax_type from context if not provided (for inline creation in journal form)
        for vals in vals_list:
            if 'tax_type' not in vals and self.env.context.get('default_tax_type'):
                vals['tax_type'] = self.env.context.get('default_tax_type')

        return super().create(vals_list)
