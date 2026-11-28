from itertools import zip_longest

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools.misc import formatLang

CHECK_PAYMENT_CODES = {
    "own_checks",
    "new_third_party_checks",
    "in_third_party_checks",
    "out_third_party_checks",
    "return_third_party_checks",
    "check_printing",
}
WITHHOLDING_LINE_FIELDS = (
    "l10n_ar_withholding_line_ids",
    "withholding_line_ids",
)
CURRENT_ACCOUNT_TYPES = {
    "asset_receivable",
    "liability_payable",
    "asset_cash",
    "liability_credit_card",
    "off_balance",
}


class AccountPayment(models.Model):
    _inherit = "account.payment"

    kai_bridge_move_ids = fields.One2many(
        "account.move",
        "kai_origin_payment_id",
        string="Asientos puente",
        copy=False,
    )
    kai_bridge_move_count = fields.Integer(compute="_compute_kai_bridge_move_count")
    kai_advance_company_id = fields.Many2one(
        "res.company",
        string="Compañía del anticipo",
        compute="_compute_kai_advance_company_id",
        store=True,
        readonly=False,
        precompute=True,
        copy=False,
        check_company=False,
        help="Compañía en la que queda el excedente: una sucursal, la casa central "
        "o la misma compañía del pago. Si es la compañía del pago, el anticipo "
        "queda en la cuenta a pagar o a cobrar del contacto, sin asiento puente. "
        "Si la deuda es de una sola sucursal, se completa sola.",
    )
    # La elección del usuario, separada del cómputo. Si no, al recomputar (por
    # ejemplo al confirmar, que invalida las líneas a pagar) se pierde la sucursal
    # elegida cuando hay más de una.
    kai_advance_company_manual_id = fields.Many2one(
        "res.company",
        string="Sucursal del anticipo elegida",
        copy=False,
        check_company=False,
    )
    kai_foreign_company_count = fields.Integer(
        string="Sucursales de la deuda",
        compute="_compute_kai_foreign_company_count",
    )
    kai_advance_company_candidate_ids = fields.Many2many(
        "res.company",
        string="Sucursales para el anticipo",
        compute="_compute_kai_advance_company_candidate_ids",
    )

    @api.depends("kai_bridge_move_ids")
    def _compute_kai_bridge_move_count(self):
        for payment in self:
            payment.kai_bridge_move_count = len(payment.kai_bridge_move_ids)

    @api.depends("company_id", "to_pay_move_line_ids.company_id")
    def _compute_kai_foreign_company_count(self):
        for payment in self:
            payment.kai_foreign_company_count = len(payment._kai_foreign_companies())

    @api.depends("company_id")
    def _compute_kai_advance_company_candidate_ids(self):
        for payment in self:
            company = payment.company_id
            if not company:
                payment.kai_advance_company_candidate_ids = False
                continue
            # Incluye a quien paga y a la casa central: el anticipo puede quedar ahí.
            payment.kai_advance_company_candidate_ids = company._get_legal_entity_companies()

    @api.depends("company_id", "to_pay_move_line_ids.company_id", "kai_advance_company_manual_id")
    def _compute_kai_advance_company_id(self):
        for payment in self:
            foreign = payment._kai_foreign_companies()
            manual = payment.kai_advance_company_manual_id
            if manual and payment._kai_advance_company_allowed(manual):
                payment.kai_advance_company_id = manual
            elif len(foreign) == 1:
                payment.kai_advance_company_id = foreign
            else:
                payment.kai_advance_company_id = False

    def _kai_vals_with_advance_choice(self, vals):
        """Guarda la sucursal que eligió el usuario, no la que se autocompleta."""
        if "kai_advance_company_id" not in vals or "kai_advance_company_manual_id" in vals:
            return vals
        if self.env.context.get("kai_advance_company_autofill"):
            return vals
        chosen = vals["kai_advance_company_id"]
        # La elección entra primero para que el cómputo, si se dispara en este
        # write, lea la sucursal que acaba de indicar el usuario.
        ordered = {"kai_advance_company_manual_id": chosen}
        ordered.update(vals)
        return ordered

    @api.model_create_multi
    def create(self, vals_list):
        vals_list = [self._kai_vals_with_advance_choice(vals) for vals in vals_list]
        return super().create(vals_list)

    def write(self, vals):
        return super().write(self._kai_vals_with_advance_choice(vals))

    def _kai_foreign_companies(self):
        self.ensure_one()
        return self.to_pay_move_line_ids.company_id - self.company_id

    def _kai_advance_company_allowed(self, company):
        """El anticipo puede quedar en quien paga, en la casa central o en otra sucursal."""
        self.ensure_one()
        if not company or not self.company_id:
            return False
        if company == self.company_id:
            return True
        return self._kai_shares_legal_entity(company | self.company_id)

    def _kai_is_head_office(self, company):
        """La casa central es la compañía raíz, la que no tiene parent_id."""
        return bool(company) and not company.parent_id

    def _kai_intercompany_sides(self, counterparty):
        """Cuentas según el rol, no según quién publica el pago.

        Devuelve la cuenta y el partner del asiento del pago, y la cuenta y
        el partner del puente. Si quien paga es la casa central, es el caso
        de siempre: cuenta corriente con sucursales en el pago y cuenta
        corriente con casa central en el puente. Si la contraparte es la
        casa central, se invierte. Entre dos sucursales no cambia.
        """
        self.ensure_one()
        payer = self.company_id
        payer_is_central = self._kai_is_head_office(payer)
        other_is_central = self._kai_is_head_office(counterparty)
        if other_is_central and not payer_is_central:
            return (
                payer.kai_central_current_account_id,
                counterparty.partner_id,
                counterparty.kai_branch_current_account_id,
                payer.partner_id,
            )
        return (
            payer.kai_branch_current_account_id,
            counterparty.partner_id,
            counterparty.kai_central_current_account_id,
            payer.partner_id,
        )

    def _kai_check_intercompany_pair(self, counterparty):
        """Exige las cuentas de esta pareja y el diario puente de la contraparte."""
        self.ensure_one()
        pay_account, _pay_partner, bridge_account, _bridge_partner = self._kai_intercompany_sides(counterparty)
        payer_is_central = self._kai_is_head_office(self.company_id)
        other_is_central = self._kai_is_head_office(counterparty)
        if other_is_central and not payer_is_central:
            self._kai_check_current_account(pay_account, self.company_id, "central")
            self._kai_check_current_account(bridge_account, counterparty, "branches")
        else:
            self._kai_check_current_account(pay_account, self.company_id, "branches")
            self._kai_check_current_account(bridge_account, counterparty, "central")
        self._kai_bridge_journal(counterparty)

    def _kai_is_multi_branch_payment(self):
        """Deuda de otra compañía de la entidad, aunque sea una sola sucursal.

        No alcanza con mirar si hay más de una compañía entre las líneas: un
        pago de Blinis SA contra facturas solo de Yesterday también tiene que
        usar la cuenta corriente y el asiento puente.
        """
        self.ensure_one()
        if self.env.context.get("kai_skip_branch_bridge"):
            return False
        return bool(self.to_pay_move_line_ids.company_id - self.company_id)

    def _kai_shares_legal_entity(self, companies):
        """True when every company is the same root and the same legal entity as the payment."""
        self.ensure_one()
        payment_company = self.company_id
        if not payment_company or not companies:
            return False
        if any(company.root_id != payment_company.root_id for company in companies):
            return False
        legal_entity = payment_company._get_legal_entity_companies()
        return not (companies - legal_entity)

    def _kai_format_amount(self, amount):
        self.ensure_one()
        return formatLang(self.env, amount, currency_obj=self.company_currency_id)

    def _kai_has_withholdings(self):
        self.ensure_one()
        for fname in WITHHOLDING_LINE_FIELDS:
            if fname in self._fields and self[fname]:
                return True
        if "withholdings_amount" in self._fields and not self.currency_id.is_zero(self.withholdings_amount or 0.0):
            return True
        return False

    def _kai_misc_journal_codes(self):
        codes = {"MISC", "MISCE", "VARIOS"}
        translated = (self.env._("MISC") or "").strip().upper()
        if translated:
            codes.add(translated)
        return codes

    def _kai_misc_journal_names(self):
        names = {"Miscellaneous Operations", "Operaciones misceláneas", "Operaciones varias"}
        translated = (self.env._("Miscellaneous Operations") or "").strip()
        if translated:
            names.add(translated)
        return names

    def _kai_journal_is_reserved(self, journal):
        """Punto de venta, diferencia de cambio y otros usos que no son el diario misceláneo."""
        code = (journal.code or "").upper()
        misc_codes = self._kai_misc_journal_codes()
        if code in {"POSS", "POS"} or (journal.name or "").strip().lower() in {"point of sale", "punto de venta"}:
            return True
        if code in {"EXCH", "CABA"} and code not in misc_codes:
            return True
        company = journal.company_id
        reserved = company.currency_exchange_journal_id | company.tax_cash_basis_journal_id
        if journal in reserved and code not in misc_codes:
            return True
        if "pos.config" in self.env and self.env["pos.config"].sudo().search_count(
            [("journal_id", "=", journal.id)], limit=1
        ):
            return True
        if "pos.payment.method" in self.env and self.env["pos.payment.method"].sudo().search_count(
            [("journal_id", "=", journal.id)], limit=1
        ):
            return True
        return False

    def _kai_default_misc_journal(self, company):
        """El diario de operaciones misceláneas del plan, no el primer diario general."""
        journal_model = self.env["account.journal"]
        xml_journals = journal_model.browse()
        for xmlid in (f"account.{company.id}_general", f"account.{company.root_id.id}_general"):
            found = self.env.ref(xmlid, raise_if_not_found=False)
            if (
                found
                and found._name == "account.journal"
                and found.company_id == company
                and found.type == "general"
                and found.active
            ):
                xml_journals |= found
        codes = self._kai_misc_journal_codes()
        by_code = journal_model.search(
            [
                ("company_id", "=", company.id),
                ("type", "=", "general"),
                ("code", "in", list(codes)),
            ]
        )
        by_name = journal_model.search(
            [
                ("company_id", "=", company.id),
                ("type", "=", "general"),
                ("name", "in", list(self._kai_misc_journal_names())),
            ]
        )
        candidates = (xml_journals | by_code | by_name).filtered(lambda journal: not self._kai_journal_is_reserved(journal))
        if not candidates:
            return journal_model

        def rank(journal):
            code = (journal.code or "").upper()
            return (
                0 if journal in xml_journals else 1,
                0 if code in codes else 1,
                journal.sequence,
                journal.id,
            )

        return candidates.sorted(key=rank)[:1]

    def _kai_bridge_journal(self, company):
        configured = company.kai_bridge_journal_id
        if configured:
            if configured.company_id != company or configured.type != "general" or not configured.active:
                raise UserError(
                    _(
                        "El diario de asientos puente de %(company)s tiene que ser un diario "
                        "general activo de esa compañía.",
                        company=company.display_name,
                    )
                )
            return configured
        journal = self._kai_default_misc_journal(company)
        if not journal:
            raise UserError(
                _(
                    "%(company)s no tiene el diario de operaciones misceláneas para el asiento "
                    "puente. Configuralo en Ajustes de contabilidad o en la ficha de la compañía. "
                    "No se usa el diario de punto de venta ni otro diario general.",
                    company=company.display_name,
                )
            )
        return journal

    def _kai_check_current_account(self, account, company, role):
        if not account:
            if role == "branches":
                raise UserError(
                    _(
                        "Falta la cuenta corriente con sucursales en %(company)s. "
                        "Configurala en Ajustes de contabilidad o en la ficha de la compañía "
                        "antes de confirmar el pago.",
                        company=company.display_name,
                    )
                )
            raise UserError(
                _(
                    "Falta la cuenta corriente con casa central en %(company)s. "
                    "Esa cuenta se usa en el asiento puente de la sucursal.",
                    company=company.display_name,
                )
            )
        if account.account_type in CURRENT_ACCOUNT_TYPES:
            raise UserError(
                _(
                    "La cuenta %(account)s de %(company)s no puede ser a cobrar, a pagar, "
                    "de banco ni de tarjeta. Elegí una cuenta corriente de activo o de pasivo.",
                    account=account.display_name,
                    company=company.display_name,
                )
            )

    def _kai_check_multi_branch_ready(self):
        """Configuration and scope checks that do not need the counterpart amount yet."""
        for payment in self.filtered(lambda item: item._kai_is_multi_branch_payment()):
            companies = payment.to_pay_move_line_ids.company_id
            if not payment._kai_shares_legal_entity(companies):
                raise UserError(_("You can't create payments for entries belonging to different companies."))
            if payment.is_internal_transfer:
                raise UserError(_("Una transferencia interna no puede cancelar deuda de otra sucursal."))
            # Cheques propios y retenciones se admiten en el pago nuevo: la
            # retención queda en la compañía que paga. Cada sucursal recibe el
            # bruto de su deuda y, si el pago supera esa deuda, también el
            # anticipo. La regularización de pagos viejos sigue rechazando
            # cheques y retenciones en _kai_regularize_block_reason.
            if payment.currency_id != payment.company_currency_id:
                raise UserError(
                    _(
                        "El pago de varias sucursales solo está soportado en la moneda de la "
                        "compañía (%(currency)s). La diferencia de cambio no se reparte entre sucursales.",
                        currency=payment.company_currency_id.name,
                    )
                )
            foreign = payment.to_pay_move_line_ids.filtered(
                lambda line: line.currency_id and line.currency_id != line.company_currency_id
            )
            if foreign:
                names = ", ".join(sorted(set(foreign.currency_id.mapped("name"))))
                raise UserError(
                    _(
                        "Hay líneas a pagar en moneda extranjera (%(currencies)s). "
                        "El pago multi-sucursal no genera la diferencia de cambio en cada sucursal.",
                        currencies=names,
                    )
                )
            unposted = payment.to_pay_move_line_ids.filtered(lambda line: line.move_id.state != "posted")
            if unposted:
                raise UserError(_("Las líneas a pagar tienen que estar publicadas."))
            for company in companies - payment.company_id:
                payment._kai_check_intercompany_pair(company)

    def _kai_debt_allocations(self):
        """Amounts of the payment counterpart that fully clear each selected line.

        The balance is expressed in company currency with the sign of the payment
        counterpart: a supplier invoice (credit balance) takes a positive debit.
        """
        self.ensure_one()
        allocations = []
        company_currency = self.company_currency_id
        for line in self.to_pay_move_line_ids.sorted(key=lambda item: (item.date_maturity or item.date, item.id)):
            balance = company_currency.round(-line.amount_residual)
            if company_currency.is_zero(balance):
                continue
            allocations.append((line, balance))
        return allocations

    def _kai_classify_coverage(self, total_balance, debt_balance):
        """Bruto menos deuda, con el signo de la contrapartida.

        ``exact`` si coinciden. ``advance`` si el pago supera la deuda: el gap
        se suma a la cuenta corriente de la sucursal. ``partial`` si no la cubre.
        En un pago a proveedor la contrapartida es al debe (positiva); en un
        cobro es al haber (negativa).
        """
        self.ensure_one()
        currency = self.company_currency_id
        gap = currency.round(total_balance - debt_balance)
        if currency.is_zero(gap):
            return "exact", 0.0
        covers_more = gap < 0 if self.payment_type == "inbound" else gap > 0
        return ("advance" if covers_more else "partial"), gap

    def _kai_partial_payment_message(self, total_balance, debt_balance):
        self.ensure_one()
        return _(
            "El pago multi-sucursal no puede ser menor que la deuda seleccionada "
            "(facturas, notas de crédito y notas de débito). El asiento del pago cancela "
            "%(payment)s y las líneas seleccionadas suman %(debt)s. Los pagos parciales "
            "no están soportados entre sucursales.",
            payment=self._kai_format_amount(abs(total_balance)),
            debt=self._kai_format_amount(abs(debt_balance)),
        )

    def _kai_require_advance_company(self, total_balance, debt_balance):
        """Sucursal donde queda el excedente, o error si no se puede saber."""
        self.ensure_one()
        company = self.kai_advance_company_id
        foreign = self._kai_foreign_companies()
        if not company and len(foreign) == 1:
            company = foreign
        if not company:
            advance = self.company_currency_id.round(abs(total_balance) - abs(debt_balance))
            raise UserError(
                _(
                    "Indicá la sucursal del anticipo. El pago cancela %(payment)s y la deuda "
                    "seleccionada suma %(debt)s, así que quedan %(advance)s a cuenta. Hay deuda "
                    "de más de una sucursal y el excedente no se puede asignar solo.",
                    payment=self._kai_format_amount(abs(total_balance)),
                    debt=self._kai_format_amount(abs(debt_balance)),
                    advance=self._kai_format_amount(advance),
                )
            )
        if not self._kai_advance_company_allowed(company):
            raise UserError(
                _(
                    "La compañía del anticipo %(company)s tiene que ser de la misma entidad legal que %(payer)s.",
                    company=company.display_name,
                    payer=self.company_id.display_name,
                )
            )
        # En la compañía del pago el anticipo va a la cuenta a pagar o cobrar, sin puente.
        if company != self.company_id:
            self._kai_check_intercompany_pair(company)
        return company

    def _kai_branch_counterpart_vals(self, template, company, balance):
        self.ensure_one()
        company_currency = self.company_currency_id
        pay_account, pay_partner, _bridge_account, _bridge_partner = self._kai_intercompany_sides(company)
        vals = dict(template)
        vals.update(
            {
                "name": _(
                    "Cuenta corriente %(branch)s · %(partner)s",
                    branch=company.name,
                    partner=self.partner_id.display_name,
                ),
                "account_id": pay_account.id,
                "partner_id": pay_partner.id,
                "balance": balance,
                "amount_currency": balance,
                "currency_id": company_currency.id,
                "kai_settled_company_id": company.id,
            }
        )
        return vals

    def _kai_add_advance_on_payer(self, new_lines, template, advance_balance):
        """El anticipo de quien paga queda en la cuenta a pagar o cobrar, sin puente."""
        currency = self.company_currency_id
        valid_types = set(self._get_valid_payment_account_types())
        for vals in new_lines:
            if vals.get("kai_settled_company_id"):
                continue
            account = self.env["account.account"].browse(vals.get("account_id"))
            if account.account_type in valid_types:
                balance = currency.round(vals["balance"] + advance_balance)
                vals["balance"] = balance
                vals["amount_currency"] = balance
                return
        account = self._kai_advance_account(self.company_id, self.env["account.move.line"])
        vals = dict(template)
        vals.update(
            {
                "name": _(
                    "Anticipo %(payment)s · %(partner)s",
                    payment=self.name or self.display_name,
                    partner=self.partner_id.display_name,
                ),
                "account_id": account.id,
                "partner_id": self.partner_id.commercial_partner_id.id,
                "balance": advance_balance,
                "amount_currency": advance_balance,
                "currency_id": currency.id,
            }
        )
        new_lines.append(vals)

    def _kai_add_advance_to_counterpart(self, new_lines, template, company, advance_balance):
        """Suma el anticipo a la contrapartida de esa compañía, o crea la línea."""
        if company == self.company_id:
            self._kai_add_advance_on_payer(new_lines, template, advance_balance)
            return
        currency = self.company_currency_id
        for vals in new_lines:
            if vals.get("kai_settled_company_id") == company.id:
                balance = currency.round(vals["balance"] + advance_balance)
                vals["balance"] = balance
                vals["amount_currency"] = balance
                return
        new_lines.append(self._kai_branch_counterpart_vals(template, company, advance_balance))

    def _kai_split_counterpart_lines(self, counterpart_lines):
        self.ensure_one()
        if len(counterpart_lines) != 1:
            raise UserError(
                _(
                    "No se pudo armar la contrapartida del pago multi-sucursal porque "
                    "el asiento ya trae más de una línea de contrapartida."
                )
            )
        template = dict(counterpart_lines[0])
        company_currency = self.company_currency_id
        total_balance = template["balance"]
        allocations = self._kai_debt_allocations()
        debt_balance = company_currency.round(sum(balance for _line, balance in allocations))
        kind, advance_balance = self._kai_classify_coverage(total_balance, debt_balance)
        if kind == "partial":
            raise UserError(self._kai_partial_payment_message(total_balance, debt_balance))
        advance_company = self.env["res.company"]
        if kind == "advance":
            advance_company = self._kai_require_advance_company(total_balance, debt_balance)

        grouped = {}
        for line, balance in allocations:
            grouped.setdefault(line.company_id, []).append((line, balance))

        new_lines = []
        companies = sorted(grouped, key=lambda company: (company != self.company_id, company.id))
        for company in companies:
            items = grouped[company]
            balance = company_currency.round(sum(amount for _line, amount in items))
            if company_currency.is_zero(balance):
                # La nota de crédito netea la factura de esa sucursal. El puente
                # igual las concilia, pero el pago no necesita línea por ese neto.
                continue
            if company == self.company_id:
                accounts = self.env["account.account"].browse({line.account_id.id for line, _amount in items})
                if len(accounts) > 1:
                    raise UserError(_("To Pay Lines must be of the same account!"))
                vals = dict(template)
                vals.update(
                    {
                        "account_id": accounts.id,
                        "partner_id": self.partner_id.commercial_partner_id.id,
                        "balance": balance,
                        "amount_currency": balance,
                        "currency_id": company_currency.id,
                    }
                )
                new_lines.append(vals)
                continue
            new_lines.append(self._kai_branch_counterpart_vals(template, company, balance))

        if advance_company:
            self._kai_add_advance_to_counterpart(new_lines, template, advance_company, advance_balance)

        split_total = company_currency.round(sum(line["balance"] for line in new_lines))
        if not company_currency.is_zero(split_total - total_balance):
            raise UserError(
                _(
                    "No se pudo repartir el pago entre sucursales: la contrapartida suma "
                    "%(split)s y el pago %(payment)s.",
                    split=self._kai_format_amount(split_total),
                    payment=self._kai_format_amount(total_balance),
                )
            )
        return new_lines

    def _prepare_move_lines_per_type(self, write_off_line_vals=None, force_balance=None):
        res = super()._prepare_move_lines_per_type(
            write_off_line_vals=write_off_line_vals,
            force_balance=force_balance,
        )
        if not self._kai_is_multi_branch_payment() or not self._kai_shares_legal_entity(
            self.to_pay_move_line_ids.company_id
        ):
            return res
        # La validación de configuración también corre en action_post. Acá se
        # repite porque el asiento se arma dentro del write de confirmación.
        self._kai_check_multi_branch_ready()
        res["counterpart_lines"] = self._kai_split_counterpart_lines(res.get("counterpart_lines") or [])
        return res

    def _seek_for_lines(self):
        liquidity, counterpart, writeoff = super()._seek_for_lines()
        branch_lines = writeoff.filtered("kai_settled_company_id")
        accounts = self.company_id.kai_branch_current_account_id | self.company_id.kai_central_current_account_id
        if not branch_lines and accounts and self._kai_is_multi_branch_payment():
            branch_lines = writeoff.filtered(lambda line: line.account_id in accounts)
        if branch_lines:
            counterpart |= branch_lines
            writeoff -= branch_lines
        return liquidity, counterpart, writeoff

    def _synchronize_to_moves(self, changed_fields):
        standard_triggers = set(self._get_trigger_fields_to_synchronize())
        if not any(
            field_name in changed_fields
            for field_name in standard_triggers | {"to_pay_move_line_ids", "kai_advance_company_id"}
        ):
            return

        def needs_multi_sync(payment):
            if not payment.move_id or payment.move_id.state == "posted":
                return False
            if payment.move_id.line_ids.filtered("kai_settled_company_id"):
                return True
            return payment._kai_is_multi_branch_payment() and payment._kai_shares_legal_entity(
                payment.to_pay_move_line_ids.company_id
            )

        multi = self.filtered(needs_multi_sync)
        others = self - multi
        if others and any(field_name in changed_fields for field_name in standard_triggers):
            super(AccountPayment, others)._synchronize_to_moves(changed_fields)
        for payment in multi:
            payment._kai_synchronize_multi_branch_move(changed_fields)

    def _kai_synchronize_multi_branch_move(self, changed_fields):
        """Keep every branch counterpart line. Core only writes the first one."""
        self.ensure_one()
        liquidity_lines, counterpart_lines, writeoff_lines = self._seek_for_lines()
        if "amount" in changed_fields and len(liquidity_lines) > 1:
            raise UserError(_("You cannot change the amount of a payment with multiple liquidity lines."))

        write_off_line_vals = []
        if liquidity_lines and counterpart_lines and writeoff_lines:
            write_off_line_vals.append(
                {
                    "name": writeoff_lines[0].name,
                    "account_id": writeoff_lines[0].account_id.id,
                    "partner_id": writeoff_lines[0].partner_id.id,
                    "currency_id": writeoff_lines[0].currency_id.id,
                    "amount_currency": sum(writeoff_lines.mapped("amount_currency")),
                    "balance": sum(writeoff_lines.mapped("balance")),
                }
            )
        line_vals_per_type = self._prepare_move_lines_per_type(write_off_line_vals=write_off_line_vals)
        commands = []
        for liquidity_line, newline_val in zip_longest(liquidity_lines, line_vals_per_type.get("liquidity_lines", [])):
            if liquidity_line and newline_val:
                commands.append(Command.update(liquidity_line.id, newline_val))
            elif not liquidity_line and newline_val:
                commands.append(Command.create(newline_val))
            elif liquidity_line and not newline_val:
                commands.append(Command.delete(liquidity_line.id))
        for line in counterpart_lines:
            commands.append(Command.delete(line.id))
        for newline_val in line_vals_per_type.get("counterpart_lines", []):
            commands.append(Command.create(newline_val))
        for line in writeoff_lines:
            commands.append(Command.delete(line.id))
        for extra_line_vals in line_vals_per_type.get("write_off_lines", []) + line_vals_per_type.get(
            "withholding_lines", []
        ):
            commands.append(Command.create(extra_line_vals))
        to_write = {
            "date": self.date,
            "partner_id": self.partner_id.id,
            "currency_id": self.currency_id.id,
            "partner_bank_id": self.partner_bank_id.id,
            "line_ids": commands,
        }
        if "journal_id" in changed_fields:
            to_write.update({"name": "/", "journal_id": self.journal_id.id})
        self.move_id.with_context(skip_invoice_sync=True).write(to_write)

    @api.constrains("partner_id", "to_pay_move_line_ids")
    def check_to_pay_lines(self):
        for rec in self:
            to_pay_partners = rec.to_pay_move_line_ids.mapped("partner_id")
            if len(to_pay_partners) > 1:
                raise ValidationError(_("All to pay lines must be of the same partner"))
            companies = rec.to_pay_move_line_ids.company_id
            if (companies - rec.company_id) and not rec._kai_shares_legal_entity(companies):
                raise ValidationError(_("You can't create payments for entries belonging to different companies."))
            if to_pay_partners and to_pay_partners != rec.partner_id.commercial_partner_id:
                raise ValidationError(
                    _("Payment is for partner %s but payment lines are of partner %s")
                    % (rec.partner_id.name, to_pay_partners.name)
                )

    def _check_to_pay_lines_account(self):
        regular = self.browse()
        for rec in self:
            companies = rec.to_pay_move_line_ids.company_id
            if len(companies) > 1 and rec._kai_shares_legal_entity(companies):
                if rec.partner_id and rec.state != "draft" and self.env.context.get("default_mode") != "check_balance":
                    own = rec.to_pay_move_line_ids.filtered(lambda line: line.company_id == rec.company_id)
                    if len(own.account_id) > 1:
                        raise ValidationError(_("To Pay Lines must be of the same account!"))
                continue
            regular |= rec
        if regular:
            super(AccountPayment, regular)._check_to_pay_lines_account()

    def _reconcile_after_post(self):
        multi = self.filtered(
            lambda payment: payment._kai_is_multi_branch_payment()
            and payment._kai_shares_legal_entity(payment.to_pay_move_line_ids.company_id)
        )
        super(AccountPayment, self - multi)._reconcile_after_post()
        for payment in multi:
            payment._kai_reconcile_own_company_debt()

    def _kai_reconcile_own_company_debt(self):
        """Reconcile only the debt that lives on the payment company.

        Branch invoices stay open until the bridge move, posted in their own
        company, clears them. Matching them here would debit the payable of the
        paying company and leave the branch payable inverted.
        """
        self.ensure_one()
        if not self.company_id.use_payment_pro or self.is_internal_transfer:
            return
        valid_types = set(self._get_valid_payment_account_types())
        counterpart = self.move_id.line_ids.filtered(
            lambda line: not line.reconciled and line.account_id.account_type in valid_types
        )
        if len(counterpart) != 1:
            return
        debt = self._kai_lines_to_pay().filtered(
            lambda line: not line.reconciled
            and line.company_id == self.company_id
            and line.account_id == counterpart.account_id
        )
        if not debt:
            return
        debt = self._kai_debt_lines_covered(counterpart, debt)
        if debt:
            (counterpart + debt).reconcile()

    def _kai_lines_to_pay(self):
        """Líneas que el pago se propone cancelar.

        account_payment_pro en el commit desplegado 766c676 las guarda en
        ``to_pay_move_line_ids``. Si una revisión las renombra, se usa el
        primer campo equivalente que exista.
        """
        self.ensure_one()
        for name in ("to_pay_move_line_ids", "debt_move_line_ids"):
            if name in self._fields:
                return self[name]
        return self.env["account.move.line"]

    def _kai_debt_lines_covered(self, counterpart, debt):
        """Deuda que el apunte del pago realmente puede conciliar.

        ``account_payment_pro`` reciente lo resuelve en ``_get_debt_lines_covered``.
        Esa revisión de producción no tiene el método y conciliaba
        ``to_pay_move_line_ids`` directo: el mismo plan de conciliación, en el
        mismo orden y por los mismos importes, sin depender de que el método exista.
        """
        self.ensure_one()
        if any("_get_debt_lines_covered" in cls.__dict__ for cls in type(self).mro()):
            return self._get_debt_lines_covered(counterpart, debt)
        amls = (counterpart + debt).with_context(
            no_exchange_difference=self.env.context.get("no_exchange_difference")
            or bool(
                self.env["ir.config_parameter"].sudo().get_param("account.disable_partial_exchange_diff")
            )
        )
        plan_list, all_amls = amls._optimize_reconciliation_plan([amls])
        values_map = {
            aml: {
                "aml": aml,
                "amount_residual": aml.amount_residual,
                "amount_residual_currency": aml.amount_residual_currency,
            }
            for aml in all_amls
        }
        matched_ids = set()
        for plan in plan_list:
            for result in amls._prepare_reconciliation_plan(plan, values_map):
                partial = result.get("partial_values") or {}
                matched_ids.add(partial.get("debit_move_id"))
                matched_ids.add(partial.get("credit_move_id"))
        # ``filtered`` conserva el orden de ``debt``, que es el de to_pay_move_line_ids.
        return debt.filtered(lambda line: line.id in matched_ids)

    def _kai_move_counterpart_balance(self):
        """Bruto ya asentado en la contrapartida del pago (deuda + anticipo)."""
        self.ensure_one()
        _liquidity, counterpart, _writeoff = self._seek_for_lines()
        return self.company_currency_id.round(sum(counterpart.mapped("balance")))

    def _kai_selected_debt_balance(self, allocations):
        """Deuda seleccionada, incluida la de la casa central ya conciliada.

        ``_reconcile_after_post`` cierra la deuda de la compañía del pago
        antes de crear los puentes y su residual queda en cero. Esa parte
        sigue en la línea a pagar del asiento. Si no se suma, el bruto menos
        el residual abierto de las sucursales parece un anticipo.
        """
        self.ensure_one()
        currency = self.company_currency_id
        foreign = currency.round(sum(
            balance for line, balance in allocations if line.company_id != self.company_id
        ))
        own_open = currency.round(sum(
            balance for line, balance in allocations if line.company_id == self.company_id
        ))
        if not currency.is_zero(own_open):
            return currency.round(foreign + own_open)
        valid_types = set(self._get_valid_payment_account_types())
        own_settled = currency.round(sum(self.move_id.line_ids.filtered(
            lambda line: line.account_id.account_type in valid_types and not line.kai_settled_company_id
        ).mapped("balance")))
        return currency.round(foreign + own_settled)

    def _kai_advance_account(self, company, debt_lines):
        """Cuenta a pagar o cobrar donde queda abierto el anticipo de la sucursal."""
        self.ensure_one()
        accounts = debt_lines.account_id
        if len(accounts) == 1:
            return accounts
        partner = self.partner_id.commercial_partner_id.with_company(company)
        if self.partner_type == "supplier":
            account = partner.property_account_payable_id
            kind = "pagar"
        else:
            account = partner.property_account_receivable_id
            kind = "cobrar"
        if not account:
            raise UserError(
                _(
                    "Falta la cuenta a %(kind)s de %(partner)s en %(company)s para dejar el anticipo.",
                    kind=kind,
                    partner=self.partner_id.display_name,
                    company=company.display_name,
                )
            )
        return account

    def _kai_create_bridge_moves(self):
        for payment in self:
            if payment.kai_bridge_move_ids or not payment._kai_is_multi_branch_payment():
                continue
            currency = payment.company_currency_id
            allocations = payment._kai_debt_allocations()
            debt_balance = payment._kai_selected_debt_balance(allocations)
            gross = payment._kai_move_counterpart_balance()
            kind, advance_balance = payment._kai_classify_coverage(gross, debt_balance)
            if kind == "partial":
                raise UserError(payment._kai_partial_payment_message(gross, debt_balance))
            advance_company = payment.env["res.company"]
            if kind == "advance":
                advance_company = payment._kai_require_advance_company(gross, debt_balance)
            grouped = {}
            for line, balance in allocations:
                if line.company_id == payment.company_id:
                    continue
                grouped.setdefault(line.company_id, []).append((line, balance))
            if advance_company and advance_company != payment.company_id:
                grouped.setdefault(advance_company, [])
            for company, items in grouped.items():
                company_advance = advance_balance if company == advance_company else 0.0
                payment._kai_create_bridge_move(company, items, advance_balance=company_advance)

    def _kai_create_bridge_move(self, company, items, date=None, advance_balance=0.0):
        self.ensure_one()
        company_currency = company.currency_id
        advance_balance = company_currency.round(advance_balance or 0.0)
        if not items and company_currency.is_zero(advance_balance):
            return
        journal = self._kai_bridge_journal(company)
        label = _("Cancelación %(payment)s", payment=self.name or self.display_name)
        commands = []
        for line, balance in items:
            commands.append(
                Command.create(
                    {
                        "name": label,
                        "account_id": line.account_id.id,
                        "partner_id": line.partner_id.id,
                        "currency_id": company_currency.id,
                        "amount_currency": balance,
                        "balance": balance,
                        "kai_debt_move_line_id": line.id,
                        "kai_settled_company_id": company.id,
                    }
                )
            )
        if not company_currency.is_zero(advance_balance):
            debt_lines = self.env["account.move.line"].browse([line.id for line, _balance in items])
            account = self._kai_advance_account(company, debt_lines)
            commands.append(
                Command.create(
                    {
                        "name": _(
                            "Anticipo %(payment)s · %(partner)s",
                            payment=self.name or self.display_name,
                            partner=self.partner_id.display_name,
                        ),
                        "account_id": account.id,
                        "partner_id": self.partner_id.commercial_partner_id.id,
                        "currency_id": company_currency.id,
                        "amount_currency": advance_balance,
                        "balance": advance_balance,
                        "kai_settled_company_id": company.id,
                    }
                )
            )
        _pay_account, _pay_partner, bridge_account, bridge_partner = self._kai_intercompany_sides(company)
        cc_balance = company_currency.round(
            -(sum(balance for _line, balance in items) + advance_balance)
        )
        if not company_currency.is_zero(cc_balance):
            commands.append(
                Command.create(
                    {
                        "name": _("Cuenta corriente %(central)s", central=bridge_partner.name or self.company_id.name),
                        "account_id": bridge_account.id,
                        "partner_id": bridge_partner.id,
                        "currency_id": company_currency.id,
                        "amount_currency": cc_balance,
                        "balance": cc_balance,
                    }
                )
            )
        allowed = list(set(self.env.companies.ids) | {company.id, self.company_id.id})
        move = (
            self.env["account.move"]
            .with_context(allowed_company_ids=allowed)
            .with_company(company)
            .create(
                {
                    "move_type": "entry",
                    "journal_id": journal.id,
                    "date": date or self.date,
                    "ref": self.name or self.display_name,
                    "company_id": company.id,
                    "kai_origin_payment_id": self.id,
                    "line_ids": commands,
                }
            )
        )
        move.action_post()
        for line, _balance in items:
            bridge_line = move.line_ids.filtered(lambda candidate, debt=line: candidate.kai_debt_move_line_id == debt)
            if len(bridge_line) != 1:
                raise UserError(
                    _(
                        "No se encontró la línea puente de %(company)s para conciliar %(move)s.",
                        company=company.display_name,
                        move=line.move_id.display_name,
                    )
                )
            (line + bridge_line).with_company(company).reconcile()
        self.env["account.move.line"].browse([line.id for line, _balance in items])._kai_refresh_settled_companies()

    def _kai_bridge_moves_to_drop(self):
        # sudo: el asiento vive en la sucursal. Si el usuario no la tiene
        # seleccionada, la regla de compañía lo escondería y el pago quedaría
        # cancelado con la deuda de la sucursal todavía conciliada.
        return self.env["account.move"].sudo().search([("kai_origin_payment_id", "in", self.ids)])

    def _kai_remove_bridge_moves(self):
        moves = self._kai_bridge_moves_to_drop()
        if not moves:
            return
        debt_lines = moves.line_ids.filtered("kai_debt_move_line_id").kai_debt_move_line_id
        partials = moves.line_ids.matched_debit_ids | moves.line_ids.matched_credit_ids
        if partials:
            partials.unlink()
        if debt_lines:
            debt_lines._kai_refresh_settled_companies()
        posted = moves.filtered(lambda move: move.state == "posted")
        if posted:
            posted.button_draft()
        cancelled = moves.filtered(lambda move: move.state == "cancel")
        if cancelled:
            cancelled.button_draft()
        moves.with_context(force_delete=True).unlink()

    def _kai_foreign_debt_lines(self):
        self.ensure_one()
        return self.to_pay_move_line_ids.filtered(lambda line: line.company_id and line.company_id != self.company_id)

    def _kai_regularize_block_reason(self):
        """Why this posted payment cannot be regularized, or False when it can."""
        self.ensure_one()
        if self.state in ("draft", "canceled", "rejected") or not self.move_id or self.move_id.state != "posted":
            return _("El pago %(payment)s no está publicado.", payment=self.display_name)
        if self.kai_bridge_move_ids:
            return _("El pago %(payment)s ya está regularizado.", payment=self.display_name)
        foreign = self._kai_foreign_debt_lines()
        if not foreign:
            return _(
                "El pago %(payment)s no concilia deuda de otra sucursal.",
                payment=self.display_name,
            )
        if not self._kai_shares_legal_entity(foreign.company_id | self.company_id):
            return _(
                "El pago %(payment)s mezcla compañías de otra entidad legal.",
                payment=self.display_name,
            )
        if self.is_internal_transfer:
            return _("Una transferencia interna no se regulariza.")
        if self.payment_method_code in CHECK_PAYMENT_CODES:
            return _(
                "El pago %(payment)s usa cheques. La regularización no los reparte entre sucursales.",
                payment=self.display_name,
            )
        if self._kai_has_withholdings():
            return _(
                "El pago %(payment)s tiene retenciones. La regularización no las reparte entre sucursales.",
                payment=self.display_name,
            )
        if self.currency_id != self.company_currency_id or foreign.filtered(
            lambda line: line.currency_id and line.currency_id != line.company_currency_id
        ):
            return _(
                "El pago %(payment)s está en moneda extranjera. La regularización no genera la diferencia de cambio.",
                payment=self.display_name,
            )
        try:
            self._kai_bridge_journal(self.company_id)
            for company in foreign.company_id:
                self._kai_check_intercompany_pair(company)
        except UserError as error:
            return str(error)
        return False

    def _kai_partials_between(self, foreign_lines):
        self.ensure_one()
        partials = self.env["account.partial.reconcile"]
        payment_lines = self.move_id.line_ids
        for line in foreign_lines:
            for partial in line.matched_debit_ids | line.matched_credit_ids:
                other = partial.debit_move_id if partial.credit_move_id == line else partial.credit_move_id
                if other in payment_lines or other in foreign_lines:
                    partials |= partial
        return partials

    def _kai_create_reclass_move(self, company, items, date):
        """En la compañía del pago, pasa a cuenta corriente la parte ya conciliada de la sucursal."""
        self.ensure_one()
        company_currency = self.company_currency_id
        total = company_currency.round(sum(balance for _line, balance in items))
        by_account = {}
        for line, balance in items:
            by_account.setdefault(line.account_id, []).append(balance)
        commands = []
        for account, balances in by_account.items():
            account_balance = company_currency.round(-sum(balances))
            commands.append(
                Command.create(
                    {
                        "name": _(
                            "Regularización %(payment)s · %(branch)s",
                            payment=self.name or self.display_name,
                            branch=company.name,
                        ),
                        "account_id": account.id,
                        "partner_id": self.partner_id.commercial_partner_id.id,
                        "currency_id": company_currency.id,
                        "amount_currency": account_balance,
                        "balance": account_balance,
                    }
                )
            )
        pay_account, pay_partner, _bridge_account, _bridge_partner = self._kai_intercompany_sides(company)
        commands.append(
            Command.create(
                {
                    "name": _(
                        "Cuenta corriente %(branch)s · %(partner)s",
                        branch=company.name,
                        partner=self.partner_id.display_name,
                    ),
                    "account_id": pay_account.id,
                    "partner_id": pay_partner.id,
                    "currency_id": company_currency.id,
                    "amount_currency": total,
                    "balance": total,
                    "kai_settled_company_id": company.id,
                }
            )
        )
        journal = self._kai_bridge_journal(self.company_id)
        allowed = list(set(self.env.companies.ids) | {company.id, self.company_id.id})
        move = (
            self.env["account.move"]
            .with_context(allowed_company_ids=allowed)
            .with_company(self.company_id)
            .create(
                {
                    "move_type": "entry",
                    "journal_id": journal.id,
                    "date": date,
                    "ref": _("Regularización %(payment)s", payment=self.name or self.display_name),
                    "company_id": self.company_id.id,
                    "kai_origin_payment_id": self.id,
                    "line_ids": commands,
                }
            )
        )
        move.action_post()
        debt_types = set(self._get_valid_payment_account_types())
        for account in move.line_ids.account_id:
            if account.account_type not in debt_types:
                continue
            counterparts = (self.move_id.line_ids + move.line_ids).filtered(
                lambda line, debt_account=account: line.account_id == debt_account and not line.reconciled
            )
            if counterparts:
                counterparts.with_company(self.company_id).reconcile()
        return move

    def _kai_regularize_existing(self, date=None):
        """Rearma la cuenta corriente y el puente de un pago ya publicado sin ellos.

        El asiento original del pago no se reescribe. En la compañía del pago se
        agrega el pase a la cuenta corriente con sucursales, conciliado contra la
        línea a pagar o cobrar de ese pago. En cada sucursal, el puente cancela
        la factura que ese pago había conciliado en forma cruzada.
        """
        for payment in self:
            reason = payment._kai_regularize_block_reason()
            if reason:
                raise UserError(reason)
            entry_date = date or payment.date
            foreign = payment._kai_foreign_debt_lines()
            partials = payment._kai_partials_between(foreign)
            if partials:
                partials.unlink()
            foreign.invalidate_recordset()
            payment.move_id.line_ids.invalidate_recordset()
            currency = payment.company_currency_id
            allocations = []
            for line in foreign.sorted(key=lambda item: (item.date_maturity or item.date, item.id)):
                balance = currency.round(-line.amount_residual)
                if not currency.is_zero(balance):
                    allocations.append((line, balance))
            if not allocations:
                raise UserError(
                    _(
                        "El pago %(payment)s no tiene saldo de otra sucursal para regularizar.",
                        payment=payment.display_name,
                    )
                )
            debt_types = set(payment._get_valid_payment_account_types())
            payment_residual = currency.round(
                sum(
                    payment.move_id.line_ids.filtered(
                        lambda line: line.account_id.account_type in debt_types
                    ).mapped("amount_residual")
                )
            )
            allocated = currency.round(sum(balance for _line, balance in allocations))
            if not currency.is_zero(payment_residual - allocated):
                raise UserError(
                    _(
                        "No se puede regularizar %(payment)s: el saldo abierto del pago es %(payment_amount)s "
                        "y la deuda de las sucursales es %(debt)s.",
                        payment=payment.display_name,
                        payment_amount=payment._kai_format_amount(payment_residual),
                        debt=payment._kai_format_amount(allocated),
                    )
                )
            grouped = {}
            for line, balance in allocations:
                grouped.setdefault(line.company_id, []).append((line, balance))
            for company, items in grouped.items():
                payment._kai_create_reclass_move(company, items, entry_date)
                payment._kai_create_bridge_move(company, items, date=entry_date)

    def action_post(self):
        if self.env.context.get("kai_skip_branch_bridge"):
            return super().action_post()
        multi = self.filtered(lambda payment: payment.state != "canceled" and payment._kai_is_multi_branch_payment())
        multi._kai_check_multi_branch_ready()
        # Un pago viejo vuelto a borrador sigue teniendo la contrapartida en
        # proveedores. Hay que rearmar las líneas antes de publicarlo de nuevo.
        for payment in multi.filtered(lambda item: item.move_id.state == "draft"):
            payment._kai_synchronize_multi_branch_move({"to_pay_move_line_ids"})
        res = super().action_post()
        posted = multi.filtered(lambda payment: payment.state != "canceled" and payment.move_id.state == "posted")
        posted._kai_create_bridge_moves()
        return res

    def action_draft(self):
        self._kai_remove_bridge_moves()
        return super().action_draft()

    def action_cancel(self):
        self._kai_remove_bridge_moves()
        return super().action_cancel()

    def unlink(self):
        self._kai_remove_bridge_moves()
        return super().unlink()

    def action_open_kai_bridge_moves(self):
        self.ensure_one()
        action = {
            "name": _("Asientos puente"),
            "type": "ir.actions.act_window",
            "res_model": "account.move",
            "view_mode": "list,form",
            "domain": [("id", "in", self.kai_bridge_move_ids.ids)],
            "context": {"create": False},
        }
        if len(self.kai_bridge_move_ids) == 1:
            action.update({"view_mode": "form", "res_id": self.kai_bridge_move_ids.id})
        return action

    @api.depends("move_id.line_ids", "kai_bridge_move_ids.line_ids")
    def _compute_matched_move_line_ids(self):
        super()._compute_matched_move_line_ids()
        valid_types = set(self._get_valid_payment_account_types())
        for payment in self.filtered("id"):
            # Adhoc already lists the reclass line of "Regularizar pago existente":
            # the payment reconciles it directly. Drop it before adding the invoice,
            # and also when this payment has no bridge lines yet.
            existing = payment.matched_move_line_ids.filtered(
                lambda line: line.move_id.kai_origin_payment_id != payment
            )
            payment.matched_move_line_ids = existing
            bridge_lines = payment.sudo().kai_bridge_move_ids.line_ids.filtered(
                lambda line: line.account_type in valid_types
            )
            if not bridge_lines:
                continue
            counterparts = (
                bridge_lines.matched_debit_ids.debit_move_id | bridge_lines.matched_credit_ids.credit_move_id
            )
            exchange_moves = bridge_lines.matched_debit_ids.exchange_move_id | bridge_lines.matched_credit_ids.exchange_move_id
            counterparts = counterparts.filtered(lambda line: line.move_id not in exchange_moves)
            counterparts -= bridge_lines
            counterparts = counterparts.filtered(
                lambda line: line.move_id.kai_origin_payment_id != payment and line.move_id != payment.move_id
            )
            existing = payment.matched_move_line_ids
            extra = counterparts - existing
            extra_with_date = extra.filtered("date_maturity").sorted(
                key=lambda line: (line.date_maturity, line.move_id.name or "")
            )
            extra_without_date = extra - extra_with_date
            payment.matched_move_line_ids = existing | extra_with_date | extra_without_date
