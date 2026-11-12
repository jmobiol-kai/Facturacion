# -*- coding: utf-8 -*-

from odoo import api, fields, models, _


class RecomputeTaxesSimpleWizard(models.TransientModel):
    _name = 'msb.recompute.taxes.simple.wizard'
    _description = 'Recalcular Impuestos — Confirmación'

    message = fields.Html(
        default=lambda self: _(
            '<div class="d-flex align-items-start mb-3">'
            '<i class="fa fa-info-circle text-info me-3" style="font-size: 24px; margin-top: 4px;"></i>'
            '<div>'
            '<p class="mb-2"><strong>Esta acción recalculará los impuestos de todas las líneas</strong></p>'
            '<p class="mb-2">Se aplicará el siguiente proceso:</p>'
            '<ul class="mb-2">'
            '<li>Impuestos del producto → Mapeo del diario → Posición fiscal</li>'
            '<li>Se sobrescribirán <strong>todos</strong> los impuestos existentes</li>'
            '<li>No se podrá deshacer esta operación</li>'
            '</ul>'
            '<p class="text-muted mb-0">¿Desea continuar con el recálculo?</p>'
            '</div>'
            '</div>'
        ),
        readonly=True,
    )

    def action_confirm(self):
        active_model = self.env.context.get('active_model')
        active_ids = self.env.context.get('active_ids', [])
        if not active_model or not active_ids:
            return {'type': 'ir.actions.act_window_close'}

        records = self.env[active_model].browse(active_ids)

        if active_model in ('sale.order', 'purchase.order', 'account.move'):
            records.action_recompute_taxes_force()

        return {'type': 'ir.actions.act_window_close'}
