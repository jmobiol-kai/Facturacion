{
    'name': 'Resumen de cuenta en el contacto',
    'summary': 'Debe, Haber y Saldo de los movimientos publicados del contacto',
    'description': """
Botón Movimientos en el formulario del contacto.

Muestra el total Debe − Haber, en moneda de la compañía, de los comprobantes
publicados del contacto y de sus hijos. El Debe suma las ventas y los pagos
a proveedor: facturas de cliente, recibos de venta, pagos y notas de crédito
de proveedor. El Haber suma las compras y los cobros: facturas de proveedor,
recibos de compra, cobros y notas de crédito de cliente. No es el saldo del
mayor. Al pulsarlo abre el resumen con las columnas Debe, Haber y Saldo
(Saldo de cada línea = Debe − Haber).
""",
    'version': '19.0.1.2.0',
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
