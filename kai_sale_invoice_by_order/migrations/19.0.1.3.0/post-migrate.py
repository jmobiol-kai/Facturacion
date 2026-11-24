from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env["sale.order.line"]._kai_recompute_shared_invoice_qty()
