{
    'name': 'Resumen de cuenta en el contacto',
    'summary': 'Suma con signo de los movimientos publicados del contacto',
    'description': """
Botón Movimientos en el formulario del contacto.

Muestra la suma con signo, en moneda de la compañía, de los comprobantes
publicados del contacto y de sus hijos. Las ventas suman igual que los
cobros: facturas de cliente, recibos de venta y cobros. La nota de crédito
de cliente resta. Las compras y los pagos restan: facturas de proveedor,
recibos de compra y pagos a proveedor. La nota de crédito de proveedor
suma. No es el saldo. Al pulsarlo abre el resumen de cuenta.
""",
    'version': '19.0.1.0.1',
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
