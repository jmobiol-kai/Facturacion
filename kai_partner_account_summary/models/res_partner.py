from odoo import api, fields, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    kai_total_movimientos = fields.Monetary(
        string='Movimientos',
        compute='_compute_kai_total_movimientos',
        currency_field='currency_id',
        groups='account.group_account_invoice,account.group_account_readonly',
        help=(
            'Total Debe − Haber, en moneda de la compañía, de los comprobantes '
            'publicados del contacto y de sus contactos hijos. El Debe suma '
            'las ventas y los pagos a proveedor: factura de cliente, recibo '
            'de venta, pago y nota de crédito de proveedor. El Haber suma las '
            'compras y los cobros: factura de proveedor, recibo de compra, '
            'cobro y nota de crédito de cliente. Es la suma del Saldo de cada '
            'línea, no el saldo del mayor.'
        ),
    )

    @api.depends_context('company', 'allowed_company_ids')
    def _compute_kai_total_movimientos(self):
        """Sum of line saldos of posted documents, including child contacts.

        Same partner scope as ``total_invoiced`` (``child_of``). Each line
        saldo is Debe − Haber in company currency. Customer invoices, sale
        receipts, vendor credit notes and supplier payments are Debe.
        Customer credit notes, vendor bills, purchase receipts and customer
        payments are Haber. A customer invoice and its cobro therefore
        offset. It is not the general-ledger partner balance: only these
        posted documents are included.
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
