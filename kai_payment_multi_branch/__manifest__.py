{
    "name": "Pagos multi-sucursal",
    "summary": "Un pago de la casa central cancela facturas de otras sucursales. El excedente queda como anticipo en la sucursal.",
    "description": """
Pago o recibo desde una compañía de la entidad legal que cancela facturas,
notas de crédito y notas de débito de otras sucursales (mismo CUIT, mismo
plan de cuentas).

El asiento del pago deja el banco en la compañía que paga y, por la parte de
cada otra sucursal (aunque sea una sola), una cuenta corriente con sucursales.
En cada sucursal se publica un asiento puente que cancela la deuda del
comprobante contra una cuenta corriente con la casa central. Las cuentas
corrientes se netean en el consolidado.

Si el pago supera la deuda seleccionada, el excedente queda como anticipo
abierto, en la cuenta a pagar o a cobrar del contacto, sin conciliar. Puede
quedar en una sucursal, en la casa central o en la misma compañía del pago.
Si queda en quien paga, no hay puente: la línea está en el asiento del pago.
La casa central es la compañía raíz. Las cuentas corrientes se eligen según
ese rol, no según quién publica el pago: la sucursal usa su cuenta con la
casa central y la casa central usa su cuenta con sucursales. Entre dos
sucursales no cambia. La intercompañía se asienta por el bruto, que incluye
las retenciones: esas retenciones permanecen en quien paga. Los pagos
parciales, por un importe menor que la deuda, siguen sin estar soportados.

Los pagos ya publicados sin ese puente se corrigen con la acción
«Regularizar pago existente». Esa regularización sigue exigiendo el importe
exacto.

Depende de account_payment_pro, l10n_ar_tax y l10n_latam_check_ux para que
el reparto entre sucursales corra después del ajuste de retenciones y de
las líneas de cheques. No envía correos ni trámites de AFIP.
""",
    "version": "19.0.1.4.1",
    "category": "Accounting/Accounting",
    "author": "Kaizendo",
    "license": "LGPL-3",
    "depends": ["account_payment_pro", "l10n_ar_tax", "l10n_latam_check_ux"],
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
