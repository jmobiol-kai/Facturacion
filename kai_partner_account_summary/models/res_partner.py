from odoo import api, fields, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    kai_total_movimientos = fields.Monetary(
        string='Movimientos',
        compute='_compute_kai_total_movimientos',
        currency_field='currency_id',
        groups='account.group_account_invoice,account.group_account_readonly',
        help=(
            'Suma de los importes absolutos, en moneda de la compañía, de los '
            'comprobantes publicados del contacto y de sus contactos hijos: '
            'facturas y notas de crédito de cliente y de proveedor, recibos de '
            'venta y de compra, cobros de cliente y pagos a proveedor. '
            'Es el total de movimientos, no el saldo.'
        ),
    )

    @api.depends_context('company', 'allowed_company_ids')
    def _compute_kai_total_movimientos(self):
        """Sum absolute posted document totals, including child contacts.

        Same partner scope as ``total_invoiced`` (``child_of``), but the
        amount is the document total in company currency (``amount_total_signed``
        and the payment's company amount), not the residual and not the net
        of invoices minus refunds.
        """
        self.kai_total_movimientos = 0.0
        partners = self.filtered('id')
        if not partners:
            return

        all_partners = self.with_context(active_test=False).search_fetch(
            [('id', 'child_of', partners.ids)],
            ['parent_id'],
        )
        company_ids = self.env.companies.ids
        if not all_partners or not company_ids:
            return

        summary = self.env['kai.partner.account.summary.line']
        grouped = summary._read_group(
            domain=[
                ('partner_id', 'in', all_partners.ids),
                ('company_id', 'in', company_ids),
            ],
            groupby=['partner_id'],
            aggregates=['amount:sum'],
        )
        totals = {partner.id: 0.0 for partner in partners}
        parent_by_id = {partner.id: partner.parent_id.id for partner in all_partners}
        for partner, amount in grouped:
            if not partner:
                continue
            current_id = partner.id
            seen = set()
            while current_id and current_id not in seen:
                seen.add(current_id)
                if current_id in totals:
                    totals[current_id] += amount or 0.0
                current_id = parent_by_id.get(current_id)

        for partner in partners:
            partner.kai_total_movimientos = totals[partner.id]

    def action_kai_view_account_summary(self):
        self.ensure_one()
        partners = self.with_context(active_test=False).search([
            ('id', 'child_of', self.id),
        ])
        action = self.env['ir.actions.act_window']._for_xml_id(
            'kai_partner_account_summary.action_kai_partner_account_summary',
        )
        action['domain'] = [
            ('partner_id', 'in', partners.ids),
            ('company_id', 'in', self.env.companies.ids),
        ]
        action['name'] = 'Resumen de cuenta'
        action['context'] = {'create': False, 'edit': False, 'delete': False}
        return action
