# -*- coding: utf-8 -*-
{
    'name': 'Debitar cheque diferido',
    'version': '17.0.1.0.2',
    'category': 'Accounting/Localizations',
    'summary': 'Pasa un cheque propio diferido de Cheques diferidos a pagar a la cuenta del banco',
    'description': """
Debitar cheque diferido
=======================

En Odoo 17 Community, un pago a proveedor con cheque propio diferido
(método ``check_printing`` y diario con cheques electrónicos/diferidos de
``l10n_latam_check``) acredita la cuenta pendiente del método de pago
(``outstanding_account_id``, p. ej. Cheques diferidos a pagar) en lugar de
la cuenta del banco.

Este módulo agrega el botón **Debitar cheque** en ese pago para registrar,
en la fecha elegida, una línea de extracto en el diario bancario (importe
negativo, sin extracto cerrado). El banco se acredita una sola vez en esa
transacción, así que el Balance del tablero baja, y la contrapartida se
concilia con el crédito diferido. No queda en Operaciones varias.

El asiento de la transacción queda vinculado al pago para auditoría y no se
puede debitar dos veces. Para anularlo: restablecer el asiento a borrador y
cancelarlo, o eliminar la línea de extracto.
    """,
    'author': 'jmobiol-kai',
    'license': 'LGPL-3',
    'depends': [
        'l10n_latam_check',
    ],
    'data': [
        'security/ir.model.access.csv',
        'wizard/kai_check_debit_wizard_views.xml',
        'views/account_payment_views.xml',
        'views/account_move_views.xml',
    ],
    'installable': True,
    'application': False,
}
