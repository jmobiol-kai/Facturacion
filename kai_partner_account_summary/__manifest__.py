{
    'name': 'Resumen de cuenta en el contacto',
    'summary': 'Total de movimientos publicados del contacto y listado al pulsarlo',
    'description': """
Botón Movimientos en el formulario del contacto.

Muestra la suma de importes absolutos, en moneda de la compañía, de los
comprobantes publicados del contacto y de sus hijos: facturas y notas de
crédito de cliente y de proveedor, recibos de venta y de compra, cobros
y pagos a proveedor. No es el saldo. Al pulsarlo abre el resumen de cuenta.
""",
    'version': '19.0.1.0.0',
    'category': 'Accounting',
    'author': 'Kaizendo',
    'license': 'LGPL-3',
    'depends': [
        'account',
        'contacts',
    ],
    'data': [
        'security/ir.model.access.csv',
        'security/ir_rule.xml',
        'views/account_summary_views.xml',
        'views/res_partner_views.xml',
    ],
    'installable': True,
    'application': False,
}
