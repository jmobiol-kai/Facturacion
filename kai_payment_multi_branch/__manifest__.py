{
    "name": "Pagos multi-sucursal",
    "summary": "Un pago de la casa central cancela facturas de varias sucursales de la misma entidad legal.",
    "description": """
Pago o recibo desde una compañía de la entidad legal que cancela facturas,
notas de crédito y notas de débito de otras sucursales (mismo CUIT, mismo
plan de cuentas).

El asiento del pago deja el banco en la compañía que paga y, por la parte de
cada otra sucursal (aunque sea una sola), una cuenta corriente con sucursales.
En cada sucursal se publica un asiento puente que cancela la deuda del
comprobante contra una cuenta corriente con la casa central. Los saldos de
proveedores y clientes quedan en cero en cada compañía y las cuentas
corrientes se netean en el consolidado.

Los pagos ya publicados sin ese puente se corrigen con la acción
«Regularizar pago existente».

Depende de account_payment_pro. No envía correos ni trámites de AFIP.
""",
    "version": "19.0.1.1.3",
    "category": "Accounting/Accounting",
    "author": "Kaizendo",
    "license": "LGPL-3",
    "depends": ["account_payment_pro"],
    "data": [
        "security/ir.model.access.csv",
        "security/account_move_line_security.xml",
        "views/res_company_views.xml",
        "views/res_config_settings_views.xml",
        "views/account_payment_views.xml",
        "views/report_payment_receipt_templates.xml",
        "wizard/kai_payment_regularize_wizard_views.xml",
    ],
    "installable": True,
    "application": False,
    "uninstall_hook": "uninstall_hook",
}
