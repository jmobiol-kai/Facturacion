# -*- coding: utf-8 -*-
{
    'name': 'Debitar cheque diferido',
    'version': '17.0.1.0.1',
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

Este módulo agrega el botón **Debitar cheque** en ese pago para generar,
en la fecha elegida y sin extracto bancario, el asiento:

* Débito: cuenta pendiente usada al emitir el cheque
* Crédito: cuenta por defecto del diario bancario (``default_account_id``)

El asiento queda vinculado al pago para auditoría y no se puede debitar dos veces.
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
