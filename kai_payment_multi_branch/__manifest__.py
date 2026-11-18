{
    "name": "Pagos multi-sucursal",
    "summary": "Un pago de la casa central cancela facturas de varias sucursales de la misma entidad legal.",
    "description": """
Pago o recibo desde una compañía de la entidad legal que cancela facturas,
notas de crédito y notas de débito de otras sucursales (mismo CUIT, mismo
plan de cuentas).

El asiento del pago deja el banco en la compañía que paga y, por la parte de
cada otra sucursal, una cuenta corriente con sucursales. En cada sucursal se
publica un asiento puente que cancela la deuda del comprobante contra una
cuenta corriente con la casa central. Los saldos de proveedores y clientes
quedan en cero en cada compañía y las cuentas corrientes se netean en el
consolidado.

Depende de account_payment_pro. No envía correos ni trámites de AFIP.
""",
    "version": "19.0.1.0.0",
    "category": "Accounting/Accounting",
    "author": "Kaizendo",
    "license": "LGPL-3",
    "depends": ["account_payment_pro"],
    "data": [
        "views/res_company_views.xml",
        "views/res_config_settings_views.xml",
        "views/account_payment_views.xml",
    ],
    "installable": True,
    "application": False,
}
