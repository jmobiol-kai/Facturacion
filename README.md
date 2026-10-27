# Facturacion

Módulos Odoo de facturación. Una rama por versión (`17.0` para Odoo 17 Community).

## Módulos

| Módulo | Rama | Odoo | Descripción |
| --- | --- | --- | --- |
| `kai_check_debit` | `17.0` | 17.0 CE | Debitar cheque diferido: mueve el saldo de Cheques diferidos a pagar a la cuenta del banco, sin extracto. |

## kai_check_debit

Dependencia verificada: `l10n_latam_check` (Odoo 17.0 Community), que ya depende de `account_check_printing` y `account`. No depende de `account_payment`: ese módulo es el puente de pagos en portal, y los pagos contables viven en `account`.

En 17.0 el cheque propio no es un modelo aparte. Es un `account.payment` con método `check_printing` y el diario marcado como cheques electrónicos/diferidos (`l10n_latam_manual_checks`). Al publicarlo, la línea de liquidez acredita `outstanding_account_id` (cuenta pendiente del método de pago, o la cuenta outstanding de pagos de la compañía).

**Debitar cheque** (visible en el pago publicado, mientras el saldo diferido siga abierto y no haya otro débito vigente) abre un asistente de fecha y diario bancario y publica:

- Débito: cuenta de liquidez del pago (Cheques diferidos a pagar)
- Crédito: `default_account_id` del diario bancario
- Fecha: la elegida en el asistente (por defecto, hoy; no anterior al pago)
- Vínculo: `account.move.kai_check_debit_payment_id`

Si la cuenta diferida es conciliable, el débito se concilia con el crédito original. Cancelar ese asiento libera el cheque para debitarlo de nuevo. Con el débito vigente, el pago no vuelve a borrador ni se cancela.
