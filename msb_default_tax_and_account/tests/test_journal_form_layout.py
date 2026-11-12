# -*- coding: utf-8 -*-

from lxml import etree

from odoo.tests import TransactionCase, tagged


def _next_element(node):
    nxt = node.getnext()
    while nxt is not None and not isinstance(nxt.tag, str):
        nxt = nxt.getnext()
    return nxt


def _invisible(node, values):
    """Return True when this arch node is hidden for ``values``.

    Mirrors the form compiler: static invisible=\"1\" nodes are dropped, and
    dynamic modifiers are evaluated against the record. An unknown name in the
    expression is a test bug, not a reason to skip the node.
    """
    expr = node.get("invisible")
    if not expr:
        return False
    expr = expr.strip()
    if expr in ("1", "True", "true"):
        return True
    if expr in ("0", "False", "false"):
        return False
    return bool(eval(expr, {"__builtins__": {}}, values))


def _journal_entries_cells(arch, values):
    """Cells the Journal Entries inner group emits into the 2-column CSS grid.

    Odoo 19's inner group is ``display: grid`` with two columns. Invisible
    nodes are omitted. A labeled field contributes a label cell and a widget
    cell; a ``nolabel`` field or a ``<label>`` contributes one cell; a
    separator spans the row. ``getRows()`` does not wrap rows in the DOM, so
    a span that does not fit the remaining columns leaves a hole and the next
    cells slide into it.
    """
    if isinstance(arch, str):
        arch = etree.fromstring(arch.encode())
    inner = arch.find(".//page[@name='bank_account']/group/group")
    if inner is None:
        raise AssertionError("Journal Entries inner group not found")
    cells = []
    for node in inner:
        if not isinstance(node.tag, str) or _invisible(node, values):
            continue
        tag = node.tag
        if tag == "label":
            cells.append({
                "kind": "label",
                "name": node.get("for"),
                "span": int(node.get("colspan") or 1),
            })
        elif tag == "separator":
            cells.append({"kind": "separator", "name": node.get("string"), "span": 2})
        elif tag == "field":
            name = node.get("name")
            colspan = int(node.get("colspan") or 1)
            if node.get("nolabel") == "1":
                cells.append({"kind": "widget", "name": name, "span": colspan})
            else:
                item_span = colspan if colspan != 1 else 2
                cells.append({"kind": "label", "name": name, "span": 1})
                cells.append({"kind": "widget", "name": name, "span": item_span - 1})
        else:
            cells.append({
                "kind": tag,
                "name": node.get("name"),
                "span": int(node.get("colspan") or 1),
            })
    return cells


def _place_cells(cells):
    placed = []
    column = 0
    row = 0
    for cell in cells:
        span = cell["span"]
        if column + span > 2:
            row += 1
            column = 0
        placed.append({**cell, "row": row, "column": column})
        column += span
        if column >= 2:
            row += 1
            column = 0
    return placed


def _assert_labels_sit_with_widgets(placed):
    """Each label occupies column 0 and its widget the same row, column 1."""
    for index, cell in enumerate(placed):
        if cell["kind"] != "label":
            continue
        if cell["column"] != 0 or cell["span"] != 1:
            raise AssertionError(
                "Label %s is in column %s span %s (row %s), not beside its widget"
                % (cell["name"], cell["column"], cell["span"], cell["row"])
            )
        if index + 1 >= len(placed):
            raise AssertionError("Label %s has no following widget" % cell["name"])
        widget = placed[index + 1]
        if (
            widget["kind"] != "widget"
            or widget["name"] != cell["name"]
            or widget["row"] != cell["row"]
            or widget["column"] != 1
        ):
            raise AssertionError(
                "Label %s (row %s) is not paired with its widget: next cell is %s"
                % (cell["name"], cell["row"], widget)
            )


@tagged("post_install", "-at_install")
class TestJournalFormLayout(TransactionCase):
    """The journal form keeps the standard label/widget grid for every type."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.arch = cls.env.ref("account.view_account_journal_form").get_combined_arch()
        cls.client_view = cls.env["account.journal"].get_view(
            cls.env.ref("account.view_account_journal_form").id,
            "form",
        )

    def test_client_view_loads(self):
        arch = etree.fromstring(self.client_view["arch"].encode())
        names = arch.xpath("//field/@name")
        self.assertIn("usar_cuenta_defecto_facturas", names)
        self.assertIn("usar_cuenta_defecto_compras", names)
        self.assertIn("sale_tax_mapping_ids", names)
        self.assertIn("purchase_tax_mapping_ids", names)

    def test_fields_follow_the_sale_purchase_default_account(self):
        arch = etree.fromstring(self.arch.encode())
        defaults = arch.xpath("//page[@name='bank_account']//field[@name='default_account_id']")
        self.assertEqual(len(defaults), 2, "Odoo 19 renders two default_account_id widgets on the form")
        bank_field, sale_purchase_field = defaults
        # Bank/cash/credit widget is hidden on sale, purchase and general.
        # The sale/purchase/general widget is the second one and has no "bank"
        # in its modifier, which is why a bare //field[@name='default_account_id']
        # used to hit the wrong node.
        self.assertEqual(
            bank_field.get("invisible"),
            "not type or type in ('sale', 'purchase', 'general')",
        )
        self.assertEqual(
            sale_purchase_field.get("invisible"),
            "type not in ('sale', 'purchase', 'general')",
        )
        injected = _next_element(sale_purchase_field)
        self.assertEqual(injected.get("name"), "usar_cuenta_defecto_facturas")
        self.assertEqual(_next_element(injected).get("name"), "usar_cuenta_defecto_compras")
        # Nothing was inserted between the two default-account widgets, so the
        # visible label stays on the same row as the visible widget.
        self.assertEqual(_next_element(bank_field), sale_purchase_field)

    def test_grid_pairs_labels_for_every_journal_type(self):
        types = ("sale", "purchase", "bank", "cash", "credit", "general")
        expected_visible = {
            "sale": {"usar_cuenta_defecto_facturas", "sale_tax_mapping_ids"},
            "purchase": {"usar_cuenta_defecto_compras", "purchase_tax_mapping_ids"},
        }
        module_fields = {
            "usar_cuenta_defecto_facturas",
            "usar_cuenta_defecto_compras",
            "sale_tax_mapping_ids",
            "purchase_tax_mapping_ids",
        }
        for journal_type in types:
            values = {
                "type": journal_type,
                "id": 1,
                "display_invoice_template_pdf_report_id": False,
            }
            placed = _place_cells(_journal_entries_cells(self.arch, values))
            _assert_labels_sit_with_widgets(placed)
            visible = {cell["name"] for cell in placed if cell["kind"] == "widget"}
            self.assertEqual(
                visible & module_fields,
                expected_visible.get(journal_type, set()),
                "Unexpected module fields on a %s journal: %s" % (journal_type, visible & module_fields),
            )
            if journal_type in ("sale", "purchase"):
                self.assertIn("default_account_id", visible)
                self.assertIn("refund_sequence", visible)
                self.assertTrue(
                    any(cell["kind"] == "separator" and cell["name"] == "Mapeos de Impuestos" for cell in placed)
                )
            if journal_type == "purchase":
                self.assertIn("non_deductible_account_id", visible)
            else:
                self.assertNotIn("non_deductible_account_id", visible)
            if journal_type in ("bank", "cash", "credit"):
                self.assertIn("suspense_account_id", visible)
                self.assertIn("default_account_id", visible)
