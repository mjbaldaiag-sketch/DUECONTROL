import tempfile
import unittest
from io import BytesIO
from pathlib import Path
import sqlite3

import app


class InvoiceFlowTests(unittest.TestCase):
    def setUp(self):
        self.previous_db = app.DB
        self.db_path = Path(tempfile.mktemp(prefix="duecontrol_invoice_test_", suffix=".db"))
        app.DB = self.db_path
        app.init_db()
        conn = app.db()
        conn.execute("INSERT INTO empresas (razao_social, cnpj, apelido) VALUES (?,?,?)",
                     ("Empresa Teste", "45765914000181", "Teste"))
        conn.execute("INSERT INTO competencias (empresa_id, descricao, data_inicial, data_final) VALUES (?,?,?,?)",
                     (1, "Agosto/2026", "2026-08-01", "2026-08-31"))
        conn.execute("INSERT INTO clientes (nome, pais) VALUES (?,?)", ("Cliente Teste", "US"))
        conn.execute("INSERT INTO contrapartes (nome) VALUES (?)", ("Banco Teste",))
        conn.execute("INSERT INTO dues (numero_due, chave_acesso, moeda, valor_original) VALUES (?,?,?,?)",
                     ("DUE-001", "12345678901234", "USD", 1000))
        conn.commit()
        conn.close()
        self.client = app.app.test_client()

    def tearDown(self):
        app.DB = self.previous_db
        self.db_path.unlink(missing_ok=True)

    def _create_invoice(self, number="INV-001", value="1000,00", commercial=None, client_id=1,
                        currency="USD", banco_referenciado_id=None):
        data = {
            "empresa_id": "1", "numero_invoice": number, "tipo_documento": "COMMERCIAL_INVOICE",
            "competencia_id": "1", "cliente_id": str(client_id), "data_emissao": "01/08/2026", "moeda": currency,
            "valor_moeda": value,
        }
        if commercial is not None:
            data["contrato_comercial"] = commercial
        if banco_referenciado_id is not None:
            data["banco_referenciado_id"] = str(banco_referenciado_id)
        response = self.client.post("/invoice/nova", data=data)
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        invoice = conn.execute("SELECT * FROM invoices WHERE numero_invoice=?", (number,)).fetchone()
        conn.close()
        return invoice["id"]

    def test_invoice_reference_is_saved_and_receipt_defaults_are_editable(self):
        conn = app.db()
        conn.execute("INSERT INTO contrapartes (nome) VALUES (?)", ("Banco Alternativo",))
        bank_two_id = conn.execute(
            "SELECT id FROM contrapartes WHERE nome=?", ("Banco Alternativo",)
        ).fetchone()[0]
        conn.commit()
        conn.close()

        invoice_id = self._create_invoice(
            number="INV-REFERENCE", value="1000,00", banco_referenciado_id=1
        )
        response = self.client.get(f"/invoice/{invoice_id}")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        today_br = app.date.today().strftime("%d/%m/%Y")
        self.assertIn("Banco Referenciado", html)
        self.assertIn('<option value="1" selected>Banco Teste</option>', html)
        self.assertIn(f'name="data_credito" data-date-br value="{today_br}"', html)
        self.assertIn('name="valor_moeda" data-money value="1.000,00"', html)

        response = self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
            "banco_credito_id": str(bank_two_id), "data_credito": "20/08/2026",
            "valor_moeda": "900,00",
        })
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        receipt = conn.execute(
            "SELECT banco_credito_id, data_credito, valor_moeda FROM recebimentos_invoice WHERE invoice_id=?",
            (invoice_id,),
        ).fetchone()
        self.assertEqual((receipt["banco_credito_id"], receipt["data_credito"]), (bank_two_id, "2026-08-20"))
        self.assertEqual(app.Decimal(str(receipt["valor_moeda"])), app.Decimal("900"))
        conn.close()

        response = self.client.post(f"/invoice/{invoice_id}/editar", data={
            "empresa_id": "1", "numero_invoice": "INV-REFERENCE",
            "tipo_documento": "COMMERCIAL_INVOICE", "competencia_id": "1",
            "cliente_id": "1", "data_emissao": "01/08/2026", "moeda": "USD",
            "valor_moeda": "1000,00",
        })
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        self.assertEqual(conn.execute(
            "SELECT banco_referenciado_id FROM invoices WHERE id=?", (invoice_id,)
        ).fetchone()[0], 1)
        conn.close()

        response = self.client.post(f"/invoice/{invoice_id}/editar", data={
            "empresa_id": "1", "numero_invoice": "INV-REFERENCE",
            "tipo_documento": "COMMERCIAL_INVOICE", "competencia_id": "1",
            "cliente_id": "1", "banco_referenciado_id": "", "data_emissao": "01/08/2026",
            "moeda": "USD", "valor_moeda": "1000,00",
        })
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        self.assertIsNone(conn.execute(
            "SELECT banco_referenciado_id FROM invoices WHERE id=?", (invoice_id,)
        ).fetchone()[0])
        self.assertEqual(conn.execute(
            "SELECT banco_credito_id FROM recebimentos_invoice WHERE invoice_id=?", (invoice_id,)
        ).fetchone()[0], bank_two_id)
        conn.close()

        default_invoice_id = self._create_invoice(
            number="INV-REFERENCE-DEFAULT", value="500,00", banco_referenciado_id=1
        )
        response = self.client.post(f"/invoice/{default_invoice_id}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": today_br, "valor_moeda": "500,00",
        })
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        receipt = conn.execute(
            "SELECT banco_credito_id, data_credito, valor_moeda FROM recebimentos_invoice WHERE invoice_id=?",
            (default_invoice_id,),
        ).fetchone()
        self.assertEqual((receipt["banco_credito_id"], receipt["data_credito"]), (1, app.date.today().isoformat()))
        self.assertEqual(app.Decimal(str(receipt["valor_moeda"])), app.Decimal("500"))
        conn.close()

    def test_existing_reference_is_preserved_by_idempotent_schema_bootstrap(self):
        invoice_id = self._create_invoice(
            number="INV-REFERENCE-PRESERVED", banco_referenciado_id=1
        )
        conn = app.db()
        conn.execute(
            "INSERT INTO recebimentos_invoice(invoice_id,banco_credito_id,data_credito,moeda,valor_moeda) "
            "VALUES (?,?,?,?,?)", (invoice_id, 1, "2026-08-10", "USD", 1000)
        )
        conn.commit()
        conn.close()

        app.init_db()
        app.init_db()
        conn = app.db()
        self.assertEqual(conn.execute(
            "SELECT banco_referenciado_id FROM invoices WHERE id=?", (invoice_id,)
        ).fetchone()[0], 1)
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM recebimentos_invoice WHERE invoice_id=?", (invoice_id,)
        ).fetchone()[0], 1)
        conn.close()

    def test_invoice_report_consolidates_real_status_balances_and_copy_payloads(self):
        conn = app.db()
        conn.execute("INSERT INTO clientes (nome, pais) VALUES (?,?)", ("Cliente B", "BR"))
        client_b_id = conn.execute("SELECT id FROM clientes WHERE nome=?", ("Cliente B",)).fetchone()[0]
        conn.commit()
        conn.close()

        self._create_invoice(number="INV-REPORT-A", value="1000,00")
        self._create_invoice(number="INV-REPORT-B", value="300,00", client_id=client_b_id)
        received_a = self._create_invoice(number="INV-REPORT-C", value="1000,00")
        received_b = self._create_invoice(number="INV-REPORT-D", value="700,00", client_id=client_b_id)
        settled = self._create_invoice(number="INV-REPORT-E", value="50,00")
        self._create_invoice(number="INV-REPORT-EUR", value="900,00", currency="EUR")

        conn = app.db()
        conn.executemany(
            "INSERT INTO recebimentos_invoice (invoice_id,banco_credito_id,data_credito,moeda,valor_moeda) VALUES (?,?,?,?,?)",
            [(received_a, 1, "2026-08-10", "USD", 1000),
             (received_b, 1, "2026-08-11", "USD", 700),
             (settled, 1, "2026-08-12", "USD", 50)],
        )
        conn.executemany(
            "INSERT INTO contratos (numero_contrato,moeda,valor_moeda,status) VALUES (?,?,?,?)",
            [("C-REPORT-A", "USD", 600, "CONCLUIDO"),
             ("C-REPORT-B", "USD", 100, "CONCLUIDO"),
             ("C-REPORT-C", "USD", 50, "CONCLUIDO")],
        )
        contracts = conn.execute(
            "SELECT id FROM contratos WHERE numero_contrato LIKE 'C-REPORT-%' ORDER BY numero_contrato"
        ).fetchall()
        conn.executemany(
            "INSERT INTO invoice_contrato_cambio (invoice_id,contrato_id,valor_alocado) VALUES (?,?,?)",
            [(received_a, contracts[0][0], 600),
             (received_b, contracts[1][0], 100),
             (settled, contracts[2][0], 50)],
        )
        conn.commit()
        conn.close()

        response = self.client.get("/invoices")
        self.assertEqual(response.status_code, 200)
        self.assertIn("/invoices/relatorios", response.get_data(as_text=True))

        response = self.client.get("/invoices/relatorios")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Total de Invoices Recebidas", html)
        self.assertIn('/invoices/relatorios/imprimir/exchange', html)
        self.assertIn('/invoices/relatorios/imprimir/awaiting', html)
        self.assertIn("Imprimir / PDF", html)
        self.assertIn("USD 1.750,00", html)
        self.assertIn("USD 750,00", html)
        self.assertIn("USD 1.300,00", html)
        self.assertIn("USD 400,00", html)
        self.assertIn("USD 600,00", html)
        self.assertNotIn("INV-REPORT-EUR", html)
        self.assertEqual(html.count('data-copy-value='), 2)
        self.assertLess(html.index("Cliente B"), html.index("Cliente Teste"))
        self.assertIn("RECEBIDO AGUARDANDO CÂMBIO", html)
        self.assertIn("AGUARDANDO RECEBIMENTO", html)

        context = app.build_invoice_report_context()
        self.assertEqual(
            [row["cliente"] for row in context["tables"][0]["rows"]],
            ["Cliente B", "Cliente Teste"],
        )
        self.assertEqual(
            [row["cliente"] for row in context["tables"][1]["rows"]],
            ["Cliente Teste", "Cliente B"],
        )
        self.assertEqual(context["tables"][0]["total"], app.Decimal("1000"))
        self.assertEqual(context["tables"][1]["total"], app.Decimal("1300"))
        self.assertIn("TOTAL                                         USD 1.000,00", context["tables"][0]["copy_text"])
        self.assertIn("TOTAL                    USD 1.300,00", context["tables"][1]["copy_text"])
        self.assertIn("<table", context["tables"][0]["copy_html"])
        self.assertNotIn("RECEBIDO AGUARDANDO CÂMBIO", context["tables"][0]["copy_text"])
        self.assertIn("Cliente / Trading        Banco                USD", context["tables"][0]["copy_text"])
        self.assertIn("Banco", context["tables"][0]["copy_text"])
        self.assertEqual(context["tables"][0]["rows"][0]["banco"], "Banco Teste")
        self.assertIn("<thead", context["tables"][0]["copy_html"])
        self.assertIn("background:#f4fbf5", context["tables"][0]["copy_html"])
        self.assertIn("background:#fdf5f5", context["tables"][1]["copy_html"])

    def test_invoice_report_detail_groups_preserve_values_for_banks_and_statuses(self):
        conn = app.db()
        conn.execute("INSERT INTO contrapartes (nome) VALUES (?)", ("Banco Dois",))
        bank_two_id = conn.execute("SELECT id FROM contrapartes WHERE nome=?", ("Banco Dois",)).fetchone()[0]
        conn.commit()
        conn.close()

        awaiting_without_receipt = self._create_invoice("INV-DETAIL-AWAITING", "120,00")
        awaiting_partial = self._create_invoice("INV-DETAIL-PARTIAL", "500,00")
        received_multi_bank = self._create_invoice("INV-DETAIL-MULTI-BANK", "1000,00")

        conn = app.db()
        conn.executemany(
            "INSERT INTO recebimentos_invoice (invoice_id,banco_credito_id,data_credito,moeda,valor_moeda) VALUES (?,?,?,?,?)",
            [(awaiting_partial, 1, "2026-08-10", "USD", 200),
             (received_multi_bank, 1, "2026-08-11", "USD", 400),
             (received_multi_bank, bank_two_id, "2026-08-12", "USD", 600)],
        )
        conn.commit()
        conn.close()

        context = app.build_invoice_report_context()
        exchange = context["tables"][0]
        awaiting = context["tables"][1]

        self.assertEqual(len(exchange["detail_groups"]), 1)
        exchange_bank = exchange["detail_groups"][0]
        self.assertEqual(exchange_bank["banco"], "Banco Dois, Banco Teste")
        self.assertEqual(exchange_bank["subtotal"], app.Decimal("1000"))
        exchange_invoices = exchange_bank["empresas"][0]["clientes"][0]["invoices"]
        self.assertEqual([(row["numero"], row["valor"]) for row in exchange_invoices],
                         [("INV-DETAIL-MULTI-BANK", app.Decimal("1000"))])
        self.assertEqual(exchange["empresa_totals"], [{"empresa": "Teste", "valor": app.Decimal("1000")}])
        self.assertEqual(exchange["total"], app.Decimal("1000"))

        awaiting_groups = {group["banco"]: group for group in awaiting["detail_groups"]}
        self.assertEqual(set(awaiting_groups), {"-", "Banco Teste"})
        self.assertEqual(awaiting_groups["-"]["subtotal"], app.Decimal("120"))
        self.assertEqual(awaiting_groups["Banco Teste"]["subtotal"], app.Decimal("300"))
        self.assertEqual(
            [(row["numero"], row["valor"]) for row in awaiting_groups["-"]["empresas"][0]["clientes"][0]["invoices"]],
            [("INV-DETAIL-AWAITING", app.Decimal("120"))],
        )
        self.assertEqual(
            [(row["numero"], row["valor"]) for row in awaiting_groups["Banco Teste"]["empresas"][0]["clientes"][0]["invoices"]],
            [("INV-DETAIL-PARTIAL", app.Decimal("300"))],
        )
        self.assertEqual(awaiting["empresa_totals"], [{"empresa": "Teste", "valor": app.Decimal("420")}])
        self.assertEqual(awaiting["total"], app.Decimal("420"))

        response = self.client.get("/invoices/relatorios")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertEqual(html.count("data-report-details-toggle"), 2)
        self.assertIn('id="invoice-report-details-exchange"', html)
        self.assertIn('id="invoice-report-details-awaiting"', html)
        self.assertIn('aria-expanded="false"', html)
        self.assertIn("BANCO: Banco Dois, Banco Teste", html)
        self.assertIn("BANCO: -", html)
        self.assertIn("INVOICE INV-DETAIL-MULTI-BANK", html)
        self.assertIn("INVOICE INV-DETAIL-AWAITING", html)
        self.assertEqual(html.count("invoice-report-company-table"), 2)
        self.assertIn('<th>EMPRESA</th><th>USD</th><th class="invoice-report-percent-header">%</th>', html)
        self.assertIn("29%", html)
        self.assertIn("71%", html)
        self.assertIn("100%", html)
        self.assertEqual(html.count("invoice-report-detail-columns"), 2)
        self.assertEqual(html.count("invoice-report-detail-summary-table"), 6)
        self.assertEqual(html.count("invoice-report-detail-summary-title"), 8)
        self.assertEqual(html.count("TOTAL POR EMPRESA"), 2)
        self.assertEqual(html.count("TOTAL POR BANCO"), 2)
        self.assertEqual(html.count("TOTAL POR CLIENTE / TRADING"), 2)
        self.assertEqual(html.count("TOTAL POR INVOICES"), 2)
        self.assertNotIn("Banco / Empresa / Cliente / Invoice", html)
        self.assertNotIn("invoice-report-detail-hierarchy-header", html)
        self.assertEqual(html.count("invoice-report-bank-table"), 2)
        self.assertEqual(html.count("invoice-report-client-table"), 2)
        self.assertIn('<th>BANCO</th><th>USD</th><th class="invoice-report-percent-header">%</th>', html)
        self.assertIn('<th>CLIENTE / TRADING</th><th>USD</th><th class="invoice-report-percent-header">%</th>', html)
        self.assertEqual(html.count('class="compact-table invoice-report-table invoice-report-table--exchange">'), 1)
        self.assertEqual(html.count('class="compact-table invoice-report-table invoice-report-table--awaiting">'), 1)
        self.assertLess(html.index('class="compact-table invoice-report-table invoice-report-table--exchange">'), html.index('class="invoice-report-detail-columns"'))
        detail_html = html[html.index('class="invoice-report-detail-columns"'):]
        detail_html = detail_html[:detail_html.index("</section>")]
        self.assertEqual(detail_html.count("<tfoot><tr><th>TOTAL</th>"), 3)
        self.assertLess(html.index("invoice-report-company-table"), html.index("invoice-report-bank-table"))
        self.assertLess(html.index("invoice-report-bank-table"), html.index("invoice-report-client-table"))
        self.assertNotIn("CÃ", html)

    def test_invoice_report_print_view_renders_expanded_html_and_portrait_print_css(self):
        self._create_invoice("INV-PRINT-AWAITING", "120,00")
        received_invoice = self._create_invoice("INV-PRINT-EXCHANGE", "80,00")

        conn = app.db()
        conn.execute(
            "INSERT INTO recebimentos_invoice (invoice_id,banco_credito_id,data_credito,moeda,valor_moeda) VALUES (?,?,?,?,?)",
            (received_invoice, 1, "2026-08-20", "USD", 80),
        )
        conn.commit()
        conn.close()

        response = self.client.get("/invoices/relatorios/imprimir/awaiting")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content_type.startswith("text/html"))
        html = response.get_data(as_text=True)
        self.assertIn("Imprimir / Salvar PDF", html)
        self.assertIn("window.print()", html)
        self.assertNotIn("data-report-details-toggle", html)
        self.assertNotIn(" hidden", html)
        self.assertEqual(html.count("invoice-report-detail-table"), 1)
        self.assertNotIn("invoice-print-summary", html)
        self.assertNotIn("summary-cards", html)
        self.assertNotIn("<h1", html)
        self.assertNotIn("<h2", html)
        self.assertNotIn("Banco / Empresa / Cliente / Invoice", html)
        self.assertIn("<div class=\"invoice-report-print-title\">AGUARDANDO RECEBIMENTO</div>", html)
        self.assertEqual(html.count("invoice-report-company-table"), 1)
        self.assertIn('<th>EMPRESA</th><th>USD</th><th class="invoice-report-percent-header">%</th>', html)
        self.assertEqual(html.count("invoice-report-detail-columns"), 1)
        self.assertEqual(html.count("invoice-report-detail-summary-table"), 3)
        self.assertEqual(html.count("invoice-report-detail-summary-title"), 4)
        self.assertIn("TOTAL POR EMPRESA", html)
        self.assertIn("TOTAL POR BANCO", html)
        self.assertIn("TOTAL POR CLIENTE / TRADING", html)
        self.assertIn("TOTAL POR INVOICES", html)
        self.assertNotIn("invoice-report-detail-hierarchy-header", html)
        self.assertEqual(html.count("invoice-report-bank-table"), 1)
        self.assertEqual(html.count("invoice-report-client-table"), 1)
        self.assertIn('<th>BANCO</th><th>USD</th><th class="invoice-report-percent-header">%</th>', html)
        self.assertIn('<th>CLIENTE / TRADING</th><th>USD</th><th class="invoice-report-percent-header">%</th>', html)
        self.assertIn("BANCO: -", html)
        self.assertIn("INVOICE INV-PRINT-AWAITING", html)
        self.assertNotIn("INVOICE INV-PRINT-EXCHANGE", html)
        self.assertIn("invoice-detail-spacer", html)
        self.assertIn("invoice-detail-row--bank", html)
        self.assertIn("invoice-detail-row--company", html)
        self.assertIn("invoice-detail-row--client", html)

        response = self.client.get("/invoices/relatorios/imprimir/exchange")
        self.assertEqual(response.status_code, 200)
        exchange_html = response.get_data(as_text=True)
        self.assertEqual(exchange_html.count("invoice-report-detail-table"), 1)
        self.assertNotIn("summary-cards", exchange_html)
        self.assertNotIn("<h1", exchange_html)
        self.assertNotIn("<h2", exchange_html)
        self.assertIn("<div class=\"invoice-report-print-title\">RECEBIDO AGUARDANDO CÂMBIO</div>", exchange_html)
        self.assertEqual(exchange_html.count("invoice-report-company-table"), 1)
        self.assertIn('<th>EMPRESA</th><th>USD</th><th class="invoice-report-percent-header">%</th>', exchange_html)
        self.assertEqual(exchange_html.count("invoice-report-detail-columns"), 1)
        self.assertEqual(exchange_html.count("invoice-report-detail-summary-table"), 3)
        self.assertEqual(exchange_html.count("invoice-report-detail-summary-title"), 4)
        self.assertIn("TOTAL POR INVOICES", exchange_html)
        self.assertNotIn("Banco / Empresa / Cliente / Invoice", exchange_html)
        self.assertNotIn("invoice-report-detail-hierarchy-header", exchange_html)
        self.assertEqual(exchange_html.count("invoice-report-bank-table"), 1)
        self.assertEqual(exchange_html.count("invoice-report-client-table"), 1)
        self.assertIn("INVOICE INV-PRINT-EXCHANGE", exchange_html)
        self.assertNotIn("INVOICE INV-PRINT-AWAITING", exchange_html)

        css_response = self.client.get("/static/style.css")
        try:
            css = css_response.get_data(as_text=True)
        finally:
            css_response.close()
        self.assertIn("@page invoice-report{size:A4 portrait", css)
        self.assertIn("#dcecff", css)
        self.assertIn("#eee5f7", css)
        self.assertIn("#fff7d6", css)
        self.assertIn(".invoice-report-company-table th:last-child,.invoice-report-company-table td:last-child{text-align:right}", css)
        self.assertIn("size:A4 portrait", css)
        self.assertIn(".invoice-report-print .invoice-detail-spacer td", css)
        self.assertIn(
            ".invoice-report-detail .invoice-report-detail-columns{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px",
            css,
        )
        self.assertIn(
            ".invoice-report-print .invoice-report-detail .invoice-report-detail-columns{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}",
            css,
        )
        self.assertNotIn("grid-template-columns:minmax(0,1fr) minmax(0,1.35fr)", css)
        self.assertIn(".invoice-report-detail .invoice-report-detail-summary-title", css)
        self.assertIn(".invoice-report-detail .invoice-report-detail-summary-section{margin:0 0 24px}", css)
        self.assertIn(".invoice-report-print .invoice-report-detail .invoice-report-detail-summary-section{margin-bottom:12px}", css)
        self.assertIn(".invoice-report-detail .table-wrap{min-width:0;overflow:visible}", css)
        self.assertIn("invoice-report-percent-header", css)
        self.assertNotIn("padding-inline:2px", css)
        self.assertIn("width:88px;padding-left:5px;padding-right:7px", css)
        self.assertIn(".invoice-report-detail-summary{border-right:1px solid #d8e1e8;padding-right:0}", css)
        self.assertIn(".invoice-report-detail{padding-top:10px}", css)
        self.assertIn(".invoice-report-print .invoice-report-detail{padding-top:5px}", css)
        self.assertNotIn("text-overflow:ellipsis", css)
        self.assertIn("white-space:normal;overflow-wrap:anywhere", css)
        self.assertNotIn("invoice-print-summary", css)

    def test_invoice_competencies_are_scoped_to_company_and_cross_company_selection_is_rejected(self):
        conn = app.db()
        conn.execute("INSERT INTO empresas (razao_social, cnpj, apelido) VALUES (?,?,?)",
                     ("Outra Empresa", "12345678000199", "Outra"))
        conn.execute("INSERT INTO competencias (empresa_id, descricao, data_inicial, data_final) VALUES (?,?,?,?)",
                     (2, "Setembro/2026", "2026-09-01", "2026-09-30"))
        conn.commit()
        conn.close()

        form = self.client.get("/invoice/nova")
        self.assertEqual(form.status_code, 200)
        html = form.get_data(as_text=True)
        self.assertIn('name="competencia_id" data-competencia-select', html)
        self.assertIn('data-competencia-empresa="1"', html)
        self.assertIn('data-competencia-empresa="2"', html)

        response = self.client.post("/invoice/nova", data={
            "empresa_id": "1", "numero_invoice": "INV-WRONG-COMPETENCIA",
            "tipo_documento": "COMMERCIAL_INVOICE", "competencia_id": "2",
            "data_emissao": "01/08/2026", "moeda": "USD", "valor_moeda": "100,00",
        })
        self.assertEqual(response.status_code, 200)
        conn = app.db()
        self.assertIsNone(conn.execute(
            "SELECT id FROM invoices WHERE numero_invoice='INV-WRONG-COMPETENCIA'"
        ).fetchone())
        conn.close()

    def test_statuses_and_separate_balances(self):
        self.assertEqual(app.invoice_status_from_totals(1000, 0, 0), app.INVOICE_STATUS_AGUARDANDO_RECEBIMENTO)
        self.assertEqual(app.invoice_status_from_totals(1000, 800, 0), app.INVOICE_STATUS_PARCIAL)
        self.assertEqual(
            app.invoice_status_from_totals(1000, 1000, 400, 600),
            app.INVOICE_STATUS_AGUARDANDO_CONTRATO,
        )
        self.assertEqual(app.invoice_status_from_totals(1000, 1000, 600), app.INVOICE_STATUS_RECEBIDA_AGUARDANDO_CAMBIO)
        self.assertEqual(app.invoice_status_from_totals(1000, 1000, 1000), app.INVOICE_STATUS_LIQUIDADA)

    def test_invoice_status_can_be_assigned_and_is_preserved_after_movements(self):
        invoice_id = self._create_invoice()
        response = self.client.post(f"/invoice/{invoice_id}/editar", data={
            "empresa_id": "1", "numero_invoice": "INV-001", "tipo_documento": "COMMERCIAL_INVOICE",
            "competencia_id": "1", "cliente_id": "1", "data_emissao": "01/08/2026", "moeda": "USD",
            "valor_moeda": "1000,00", "status": "RECEBIDO AGUARDANDO CAMBIO", "data_credito": "10/08/2026",
        })
        self.assertEqual(response.status_code, 302)
        self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "10/08/2026", "valor_moeda": "1000,00",
        })
        conn = app.db()
        invoice = conn.execute("SELECT status, status_manual FROM invoices WHERE id=?", (invoice_id,)).fetchone()
        self.assertEqual(invoice["status"], app.INVOICE_STATUS_RECEBIDA_AGUARDANDO_CAMBIO)
        self.assertEqual(invoice["status_manual"], 1)
        conn.close()

    def test_saved_receipt_moves_awaiting_invoice_to_received_exchange_status(self):
        invoice_id = self._create_invoice("INV-RECEIPT-STATUS", "100,00")
        conn = app.db()
        conn.execute(
            "UPDATE invoices SET status=?, status_manual=1 WHERE id=?",
            (app.INVOICE_STATUS_AGUARDANDO_RECEBIMENTO, invoice_id),
        )
        conn.commit()
        conn.close()

        response = self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "20/08/2026", "valor_moeda": "100,00",
        })
        self.assertEqual(response.status_code, 302)

        conn = app.db()
        invoice = conn.execute(
            "SELECT status, data_credito FROM invoices WHERE id=?", (invoice_id,)
        ).fetchone()
        self.assertEqual(invoice["status"], app.INVOICE_STATUS_RECEBIDA_AGUARDANDO_CAMBIO)
        self.assertEqual(invoice["data_credito"], "2026-08-20")
        conn.close()

    def test_received_status_requires_credit_date_and_awaiting_clears_it(self):
        invoice_id = self._create_invoice(number="INV-CREDIT-DATE")
        base_data = {
            "empresa_id": "1", "numero_invoice": "INV-CREDIT-DATE", "tipo_documento": "COMMERCIAL_INVOICE",
            "competencia_id": "1", "cliente_id": "1", "data_emissao": "01/08/2026", "moeda": "USD",
            "valor_moeda": "1000,00",
        }
        response = self.client.post(
            f"/invoice/{invoice_id}/editar",
            data={**base_data, "status": "RECEBIDO AGUARDANDO CAMBIO"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("data do crédito", response.get_data(as_text=True).lower())
        conn = app.db()
        invoice = conn.execute("SELECT status, data_credito FROM invoices WHERE id=?", (invoice_id,)).fetchone()
        self.assertEqual(invoice["status"], app.INVOICE_STATUS_AGUARDANDO_RECEBIMENTO)
        self.assertIsNone(invoice["data_credito"])
        conn.close()

        response = self.client.post(
            f"/invoice/{invoice_id}/editar",
            data={**base_data, "status": "AGUARDANDO CONTRATO"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("data do crédito", response.get_data(as_text=True).lower())
        response = self.client.post(
            f"/invoice/{invoice_id}/editar",
            data={**base_data, "status": "AGUARDANDO CONTRATO", "data_credito": "16/08/2026"},
        )
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        invoice = conn.execute("SELECT status, data_credito FROM invoices WHERE id=?", (invoice_id,)).fetchone()
        self.assertEqual(invoice["status"], app.INVOICE_STATUS_AGUARDANDO_CONTRATO)
        self.assertEqual(invoice["data_credito"], "2026-08-16")
        conn.close()

        response = self.client.post(
            f"/invoice/{invoice_id}/editar",
            data={**base_data, "status": "RECEBIDO AGUARDANDO CAMBIO", "data_credito": "15/08/2026"},
        )
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        invoice = conn.execute("SELECT status, data_credito FROM invoices WHERE id=?", (invoice_id,)).fetchone()
        self.assertEqual(invoice["status"], app.INVOICE_STATUS_RECEBIDA_AGUARDANDO_CAMBIO)
        self.assertEqual(invoice["data_credito"], "2026-08-15")
        conn.close()

        response = self.client.post(
            f"/invoice/{invoice_id}/editar",
            data={**base_data, "status": "AGUARDANDO RECEBIMENTO", "data_credito": "15/08/2026"},
        )
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        invoice = conn.execute("SELECT status, data_credito FROM invoices WHERE id=?", (invoice_id,)).fetchone()
        self.assertEqual(invoice["status"], app.INVOICE_STATUS_AGUARDANDO_RECEBIMENTO)
        self.assertIsNone(invoice["data_credito"])
        conn.close()

    def test_invoice_status_is_supported_by_excel_model_and_import(self):
        import pandas as pd

        model = pd.read_excel(BytesIO(self.client.get("/invoices/modelo").data))
        self.assertIn("status", model.columns)
        self.assertIn("data_credito", model.columns)
        orientations = pd.read_excel(BytesIO(self.client.get("/invoices/modelo").data), sheet_name="Orientações")
        self.assertIn("AGUARDANDO CONTRATO", set(orientations["STATUS"].dropna()))
        for column in (
            "empresa", "invoice", "tipo", "banco_referenciado", "banco_credito", "banco_liquidacao", "contrato_cambio",
            "data_fechamento", "data_liquidacao", "valor_moeda", "taxa_cambio", "valor_brl",
        ):
            self.assertIn(column, model.columns)
        frame = pd.DataFrame([{
            "cnpj": "45.765.914/0001-81", "numero_invoice": "INV-STATUS-IMPORT",
            "tipo_documento": "COMMERCIAL INVOICE", "competencia": "Agosto/2026",
            "data_emissao": "01/08/2026", "moeda": "USD", "valor_invoice": "100,00",
            "status": "RECEBIDO AGUARDANDO CAMBIO", "data_credito": "10/08/2026",
        }])
        output = BytesIO()
        frame.to_excel(output, index=False)
        output.seek(0)
        response = self.client.post("/invoices/importar", data={"arquivo": (output, "status.xlsx")},
                                    content_type="multipart/form-data")
        self.assertEqual(response.status_code, 200)
        self.assertIn("RECEBIDO AGUARDANDO CAMBIO", response.get_data(as_text=True))
        with self.client.session_transaction() as session:
            token = session["invoice_import_stage"]
        self.assertEqual(self.client.post("/invoices/importar/confirmar", data={"stage_token": token}).status_code, 302)
        conn = app.db()
        invoice = conn.execute(
            "SELECT status, status_manual, data_credito FROM invoices WHERE numero_invoice='INV-STATUS-IMPORT'"
        ).fetchone()
        self.assertEqual(invoice["status"], app.INVOICE_STATUS_RECEBIDA_AGUARDANDO_CAMBIO)
        self.assertEqual(invoice["status_manual"], 1)
        self.assertEqual(invoice["data_credito"], "2026-08-10")
        conn.close()

    def test_excel_received_status_requires_credit_date(self):
        import pandas as pd

        frame = pd.DataFrame([{
            "cnpj": "45.765.914/0001-81", "numero_invoice": "INV-MISSING-CREDIT-DATE",
            "tipo_documento": "COMMERCIAL INVOICE", "competencia": "Agosto/2026",
            "valor_invoice": "100,00", "status": "RECEBIDO AGUARDANDO CAMBIO",
        }])
        with self.assertRaisesRegex(ValueError, "data_credito"):
            app.prepare_invoice_import_rows(frame, pd)

    def test_excel_awaiting_contract_status_is_imported_with_credit_date(self):
        import pandas as pd

        frame = pd.DataFrame([{
            "cnpj": "45.765.914/0001-81", "numero_invoice": "INV-CONTRACT-IMPORT",
            "tipo_documento": "COMMERCIAL INVOICE", "competencia": "Agosto/2026",
            "valor_invoice": "100,00", "status": "AGUARDANDO CONTRATO",
            "data_credito": "10/08/2026",
        }])
        output = BytesIO()
        frame.to_excel(output, index=False)
        output.seek(0)
        response = self.client.post(
            "/invoices/importar", data={"arquivo": (output, "awaiting-contract.xlsx")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 200)
        with self.client.session_transaction() as session:
            token = session["invoice_import_stage"]
        self.assertEqual(
            self.client.post("/invoices/importar/confirmar", data={"stage_token": token}).status_code,
            302,
        )
        conn = app.db()
        invoice = conn.execute(
            "SELECT status, status_manual, data_credito FROM invoices WHERE numero_invoice=?",
            ("INV-CONTRACT-IMPORT",),
        ).fetchone()
        self.assertEqual(invoice["status"], app.INVOICE_STATUS_AGUARDANDO_CONTRATO)
        self.assertEqual(invoice["status_manual"], 1)
        self.assertEqual(invoice["data_credito"], "2026-08-10")
        conn.close()

    def test_invoice_import_registers_receipt_and_closed_exchange(self):
        import pandas as pd

        conn = app.db()
        conn.execute("INSERT INTO contrapartes (nome) VALUES (?)", ("Banco Liquidação",))
        conn.commit()
        conn.close()
        frame = pd.DataFrame([{
            "Empresa": "Teste", "Invoice": "INV-FULL-IMPORT", "Contrato comercial": "COM-FULL",
            "Competência": "Agosto/2026", "Tipo": "COMMERCIAL INVOICE", "Banco Crédito": "Banco Teste",
            "Banco Liquidação": "Banco Liquidação", "Contrato Câmbio": "C-FULL",
            "Cliente": "Cliente Teste", "Emissão": "01/08/2026", "Data Crédito": "10/08/2026",
            "Data Fechamento": "11/08/2026", "Data Liquidação": "12/08/2026", "Moeda": "USD",
            "Valor Moeda": "1000,00", "Taxa Câmbio": "5,10", "Valor BRL": "5100,00",
            "Status": "LIQUIDADA",
        }])
        output = BytesIO()
        frame.to_excel(output, index=False)
        output.seek(0)
        response = self.client.post("/invoices/importar", data={"arquivo": (output, "full-invoice.xlsx")},
                                    content_type="multipart/form-data")
        self.assertEqual(response.status_code, 200)
        with self.client.session_transaction() as session:
            token = session["invoice_import_stage"]
        self.assertEqual(self.client.post("/invoices/importar/confirmar", data={"stage_token": token}).status_code, 302)

        conn = app.db()
        invoice = conn.execute(
            "SELECT id, status, status_manual, data_credito, banco_referenciado_id, desdobramento_habilitado "
            "FROM invoices WHERE numero_invoice='INV-FULL-IMPORT'"
        ).fetchone()
        self.assertEqual(invoice["status"], app.INVOICE_STATUS_LIQUIDADA)
        self.assertEqual(invoice["status_manual"], 1)
        self.assertEqual(invoice["data_credito"], "2026-08-10")
        self.assertEqual(invoice["banco_referenciado_id"], 1)
        self.assertEqual(invoice["desdobramento_habilitado"], 1)
        receipt = conn.execute(
            "SELECT banco_credito_id, data_credito, valor_moeda FROM recebimentos_invoice WHERE invoice_id=?",
            (invoice["id"],),
        ).fetchone()
        self.assertEqual((receipt["banco_credito_id"], receipt["data_credito"]), (1, "2026-08-10"))
        self.assertEqual(app.Decimal(str(receipt["valor_moeda"])), app.Decimal("1000"))
        contract = conn.execute(
            "SELECT id, banco_liquidacao_id, data_fechamento, data_liquidacao, taxa_cambio, valor_moeda, valor_reais "
            "FROM contratos WHERE numero_contrato='C-FULL'"
        ).fetchone()
        self.assertEqual(contract["banco_liquidacao_id"], 2)
        self.assertEqual((contract["data_fechamento"], contract["data_liquidacao"]), ("2026-08-11", "2026-08-12"))
        self.assertEqual(app.Decimal(str(contract["taxa_cambio"])), app.Decimal("5.1"))
        self.assertEqual(app.Decimal(str(contract["valor_moeda"])), app.Decimal("1000"))
        self.assertEqual(app.Decimal(str(contract["valor_reais"])), app.Decimal("5100"))
        link = conn.execute(
            "SELECT valor_alocado FROM invoice_contrato_cambio WHERE invoice_id=? AND contrato_id=?",
            (invoice["id"], contract["id"]),
        ).fetchone()
        self.assertEqual(app.Decimal(str(link["valor_alocado"])), app.Decimal("1000"))
        conn.close()

    def test_schema_bootstrap_is_idempotent_and_preserves_existing_domains(self):
        conn = app.db()
        before = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("empresas", "clientes", "dues", "ndfs", "ptax_cotacoes", "contratos")
        }
        conn.close()
        app.init_db()
        conn = app.db()
        after = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in before
        }
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(before, after)
        self.assertEqual(version, app.INVOICE_SCHEMA_VERSION)
        self.assertIn("contrato_comercial", {
            row[1] for row in conn.execute("PRAGMA table_info(invoices)").fetchall()
        })
        self.assertTrue(any(
            row[1] == "idx_invoices_contrato_comercial"
            for row in conn.execute("PRAGMA index_list(invoices)").fetchall()
        ))
        conn.close()

    def test_schema_migrates_v1_to_v6_without_recreating_existing_contracts(self):
        migration_path = Path(tempfile.mktemp(prefix="duecontrol_invoice_migration_", suffix=".db"))
        previous_db = app.DB
        try:
            app.DB = migration_path
            conn = sqlite3.connect(migration_path)
            conn.executescript("""
                CREATE TABLE empresas (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, razao_social TEXT NOT NULL,
                    cnpj TEXT NOT NULL UNIQUE, apelido TEXT, created_at TEXT
                );
                CREATE TABLE clientes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT NOT NULL,
                    pais TEXT NOT NULL, created_at TEXT, UNIQUE(nome, pais)
                );
                CREATE TABLE contrapartes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT NOT NULL UNIQUE,
                    created_at TEXT
                );
                CREATE TABLE dues (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, numero_due TEXT NOT NULL UNIQUE,
                    data_due TEXT, cnpj TEXT, cliente TEXT, moeda TEXT NOT NULL DEFAULT 'USD',
                    valor_original REAL NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'PENDENTE',
                    observacao TEXT, competencia_id INTEGER
                );
                CREATE TABLE contratos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, numero_contrato TEXT NOT NULL UNIQUE,
                    banco TEXT, banco_credito TEXT, banco_liquidacao TEXT, data_contrato TEXT,
                    data_fechamento TEXT,
                    data_recebimento TEXT, data_liquidacao TEXT, cnpj TEXT, cliente TEXT,
                    moeda TEXT NOT NULL DEFAULT 'USD', valor_moeda REAL NOT NULL DEFAULT 0,
                    taxa_cambio REAL, valor_reais REAL, status TEXT NOT NULL DEFAULT 'PENDENTE',
                    saldo_zerado_manual INTEGER NOT NULL DEFAULT 0, observacao TEXT,
                    created_at TEXT, competencia_id INTEGER
                );
                CREATE TABLE invoices (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, empresa_id INTEGER NOT NULL,
                    numero_invoice TEXT NOT NULL, tipo_documento TEXT NOT NULL,
                    cliente_id INTEGER, data_emissao TEXT, moeda TEXT NOT NULL DEFAULT 'USD',
                    valor_moeda REAL NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'AGUARDANDO_RECEBIMENTO',
                    observacao TEXT, created_at TEXT,
                    UNIQUE(empresa_id, numero_invoice, tipo_documento)
                );
                CREATE TABLE recebimentos_invoice (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, invoice_id INTEGER NOT NULL,
                    banco_credito_id INTEGER, data_credito TEXT NOT NULL,
                    moeda TEXT NOT NULL, valor_moeda REAL NOT NULL,
                    documento TEXT, observacao TEXT, created_at TEXT
                );
                INSERT INTO empresas(razao_social, cnpj, apelido) VALUES ('Empresa V1', '11111111000111', 'V1');
                INSERT INTO clientes(nome, pais) VALUES ('Cliente V1', 'BR');
                INSERT INTO contrapartes(nome) VALUES ('Banco Antigo'), ('Banco Mais Antigo');
                INSERT INTO dues(numero_due, moeda, valor_original) VALUES ('DUE-V1', 'USD', 10);
                INSERT INTO contratos(numero_contrato, moeda, valor_moeda) VALUES ('C-V1', 'USD', 10);
                INSERT INTO invoices(empresa_id, numero_invoice, tipo_documento, valor_moeda)
                    VALUES (1, 'INV-V1', 'COMMERCIAL_INVOICE', 10);
                INSERT INTO recebimentos_invoice(invoice_id,banco_credito_id,data_credito,moeda,valor_moeda)
                    VALUES (1,2,'2026-08-20','USD',10);
                INSERT INTO recebimentos_invoice(invoice_id,banco_credito_id,data_credito,moeda,valor_moeda)
                    VALUES (1,1,'2026-08-10','USD',10);
                PRAGMA user_version = 1;
            """)
            conn.commit()
            conn.close()

            app.init_db()
            conn = app.db()
            self.assertEqual(
                conn.execute("PRAGMA user_version").fetchone()[0],
                app.INVOICE_SCHEMA_VERSION,
            )
            self.assertIsNotNone(conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='fechamentos_cambio'"
            ).fetchone())
            self.assertIsNotNone(conn.execute(
                "SELECT id FROM invoices WHERE numero_invoice='INV-V1'"
            ).fetchone())
            self.assertEqual(conn.execute(
                "SELECT numero_contrato FROM contratos WHERE id=1"
            ).fetchone()[0], "C-V1")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM empresas").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM clientes").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM dues").fetchone()[0], 1)
            self.assertIn("contrato_comercial", {
                row[1] for row in conn.execute("PRAGMA table_info(invoices)").fetchall()
            })
            self.assertIn("competencia_id", {
                row[1] for row in conn.execute("PRAGMA table_info(invoices)").fetchall()
            })
            self.assertIn("banco_referenciado_id", {
                row[1] for row in conn.execute("PRAGMA table_info(invoices)").fetchall()
            })
            self.assertEqual(conn.execute(
                "SELECT banco_referenciado_id FROM invoices WHERE numero_invoice='INV-V1'"
            ).fetchone()[0], 1)
            self.assertEqual(conn.execute(
                "SELECT desdobramento_habilitado FROM invoices WHERE numero_invoice='INV-V1'"
            ).fetchone()[0], 0)
            invoice_schema = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='invoices'"
            ).fetchone()[0]
            self.assertIn("AGUARDANDO_CONTRATO", invoice_schema)
            self.assertTrue(any(
                row[1] == "idx_invoices_status"
                for row in conn.execute("PRAGMA index_list(invoices)").fetchall()
            ))
            conn.close()
        finally:
            app.DB = previous_db
            migration_path.unlink(missing_ok=True)

    def test_commercial_contract_is_optional_trimmed_reusable_and_visible(self):
        self.assertIsNone(app.normalize_contract_commercial("   "))
        self.assertEqual(app.normalize_contract_commercial("  COM-001  "), "COM-001")
        with self.assertRaises(ValueError):
            app.normalize_contract_commercial("x" * 121)
        with self.assertRaises(ValueError):
            app.normalize_contract_commercial("COM-001\nSECOND")

        invoice_id = self._create_invoice(commercial="  COM-001  ")
        second_id = self._create_invoice("INV-COM-002", "500,00", commercial="COM-001")
        conn = app.db()
        rows = conn.execute(
            "SELECT numero_invoice, contrato_comercial FROM invoices WHERE id IN (?,?) ORDER BY id",
            (invoice_id, second_id),
        ).fetchall()
        self.assertEqual([(row[0], row[1]) for row in rows], [(
            "INV-001", "COM-001"), ("INV-COM-002", "COM-001")])
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM contratos").fetchone()[0], 0)
        conn.close()

        detail = self.client.get(f"/invoice/{invoice_id}")
        self.assertEqual(detail.status_code, 200)
        self.assertIn("Contrato comercial", detail.get_data(as_text=True))
        self.assertIn("COM-001", detail.get_data(as_text=True))
        listing = self.client.get("/invoices?contrato_comercial=COM-001")
        self.assertEqual(listing.status_code, 200)
        listing_text = listing.get_data(as_text=True)
        self.assertIn("INV-001", listing_text)
        self.assertIn("INV-COM-002", listing_text)
        self.assertIn("Contrato Câmbio", listing_text)

        response = self.client.post(f"/invoice/{invoice_id}/editar", data={
            "empresa_id": "1", "numero_invoice": "INV-001", "tipo_documento": "COMMERCIAL_INVOICE",
            "cliente_id": "1", "data_emissao": "01/08/2026", "moeda": "USD",
            "valor_moeda": "1000,00", "contrato_comercial": "COM-UPDATED",
        })
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        self.assertEqual(conn.execute(
            "SELECT contrato_comercial FROM invoices WHERE id=?", (invoice_id,)
        ).fetchone()[0], "COM-UPDATED")
        conn.close()

    def test_invoice_import_normalizes_existing_clients_without_duplicates(self):
        import pandas as pd

        conn = app.db()
        conn.execute("INSERT INTO clientes (nome, pais) VALUES (?,?)", ("José da Silva", "BR"))
        conn.commit()
        conn.close()
        frame = pd.DataFrame([
            {"cnpj": "45.765.914/0001-81", "numero_invoice": "INV-CLIENT-1",
             "tipo_documento": "COMMERCIAL INVOICE", "cliente": "  CLIENTE   TESTE  ",
             "data_emissao": "01/08/2026", "moeda": "USD", "valor_invoice": "100,00"},
            {"cnpj": "45.765.914/0001-81", "numero_invoice": "INV-CLIENT-2",
            "tipo_documento": "COMMERCIAL INVOICE", "cliente": "JOSE   DA SILVA FILIAL",
             "cliente_pais": "Brasil", "data_emissao": "01/08/2026", "moeda": "USD",
             "valor_invoice": "100,00"},
        ])
        frame["competencia"] = "Agosto/2026"
        output = BytesIO()
        frame.to_excel(output, index=False)
        output.seek(0)
        response = self.client.post("/invoices/importar", data={"arquivo": (output, "normalized-clients.xlsx")},
                                    content_type="multipart/form-data")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Clientes não identificados", response.get_data(as_text=True))
        with self.client.session_transaction() as session:
            token = session["invoice_import_stage"]
        self.assertEqual(self.client.post("/invoices/importar/confirmar", data={"stage_token": token}).status_code, 302)
        conn = app.db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM clientes").fetchone()[0], 2)
        self.assertEqual(conn.execute("""
            SELECT i.cliente_id, c.nome, c.pais
            FROM invoices i JOIN clientes c ON c.id=i.cliente_id
            WHERE i.numero_invoice='INV-CLIENT-1'
        """).fetchone()[1:], ("Cliente Teste", "US"))
        self.assertEqual(conn.execute("""
            SELECT i.cliente_id, c.nome, c.pais
            FROM invoices i JOIN clientes c ON c.id=i.cliente_id
            WHERE i.numero_invoice='INV-CLIENT-2'
        """).fetchone()[1:], ("José da Silva", "BR"))
        conn.close()

    def test_invoice_import_suggests_new_client_and_country_before_creation(self):
        import pandas as pd

        frame = pd.DataFrame([{
            "cnpj": "45.765.914/0001-81", "numero_invoice": "INV-NEW-CLIENT",
            "tipo_documento": "COMMERCIAL INVOICE", "cliente": "  Novo   Cliente  ",
            "data_emissao": "01/08/2026", "moeda": "USD", "valor_invoice": "100,00",
        }])
        frame["competencia"] = "Agosto/2026"
        output = BytesIO()
        frame.to_excel(output, index=False)
        output.seek(0)
        response = self.client.post("/invoices/importar", data={"arquivo": (output, "new-client.xlsx")},
                                    content_type="multipart/form-data")
        self.assertEqual(response.status_code, 200)
        preview = response.get_data(as_text=True)
        self.assertIn("Clientes não identificados", preview)
        self.assertIn("Cliente novo sugerido: Novo Cliente", preview)
        self.assertIn("País do cliente", preview)
        conn = app.db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM clientes").fetchone()[0], 1)
        conn.close()
        with self.client.session_transaction() as session:
            token = session["invoice_import_stage"]
        response = self.client.post("/invoices/importar/confirmar", data={
            "stage_token": token, "cliente_novo_pais_c1": "US",
        })
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        client = conn.execute(
            "SELECT c.id, c.nome, c.pais FROM clientes c WHERE c.nome='Novo Cliente'"
        ).fetchone()
        self.assertIsNotNone(client)
        self.assertEqual(client["pais"], "US")
        invoice = conn.execute("""
            SELECT i.cliente_id, c.id AS client_id
            FROM invoices i JOIN clientes c ON c.id=i.cliente_id
            WHERE i.numero_invoice='INV-NEW-CLIENT'
        """).fetchone()
        self.assertEqual(invoice["cliente_id"], invoice["client_id"])
        conn.close()

    def test_invoice_import_can_replace_unknown_client_with_existing_client(self):
        import pandas as pd

        frame = pd.DataFrame([{
            "cnpj": "45.765.914/0001-81", "numero_invoice": "INV-EXISTING-CLIENT",
            "tipo_documento": "COMMERCIAL INVOICE", "cliente": "Nome divergente",
            "data_emissao": "01/08/2026", "moeda": "USD", "valor_invoice": "100,00",
            "competencia": "Agosto/2026",
        }])
        output = BytesIO()
        frame.to_excel(output, index=False)
        output.seek(0)
        response = self.client.post("/invoices/importar", data={"arquivo": (output, "existing-client.xlsx")},
                                    content_type="multipart/form-data")
        self.assertEqual(response.status_code, 200)
        preview = response.get_data(as_text=True)
        self.assertIn('name="cliente_existente_c1"', preview)
        self.assertIn("Cliente Teste", preview)
        with self.client.session_transaction() as session:
            token = session["invoice_import_stage"]
        response = self.client.post("/invoices/importar/confirmar", data={
            "stage_token": token, "cliente_existente_c1": "1",
        })
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM clientes").fetchone()[0], 1)
        invoice = conn.execute("""
            SELECT i.cliente_id, c.id AS client_id, c.nome
            FROM invoices i JOIN clientes c ON c.id=i.cliente_id
            WHERE i.numero_invoice='INV-EXISTING-CLIENT'
        """).fetchone()
        self.assertEqual(invoice["cliente_id"], invoice["client_id"])
        self.assertEqual(invoice["nome"], "Cliente Teste")
        conn.close()

    def test_invoice_import_suggests_registered_bank_for_credit_and_liquidation(self):
        import pandas as pd

        frame = pd.DataFrame([{
            "cnpj": "45.765.914/0001-81", "numero_invoice": "INV-BANK-SUGGESTION",
            "tipo_documento": "COMMERCIAL INVOICE", "cliente": "Cliente Teste",
            "data_emissao": "01/08/2026", "data_credito": "02/08/2026",
            "banco_credito": "BTG", "numero_contrato_cambio": "C-BANK-SUGGESTION",
            "banco_liquidacao": "C6 BANK", "valor_alocado": "100,00",
            "taxa_cambio": "5,00", "moeda": "USD", "valor_moeda": "100,00",
            "status": "RECEBIDO AGUARDANDO CAMBIO", "competencia": "Agosto/2026",
        }])
        output = BytesIO()
        frame.to_excel(output, index=False)
        output.seek(0)
        response = self.client.post("/invoices/importar", data={"arquivo": (output, "bank-suggestion.xlsx")},
                                    content_type="multipart/form-data")
        self.assertEqual(response.status_code, 200)
        preview = response.get_data(as_text=True)
        self.assertIn("Bancos não identificados", preview)
        self.assertIn("Banco de Crédito informado: BTG", preview)
        self.assertNotIn("Banco de CrÃ©dito informado", preview)
        self.assertIn('name="banco_existente_b1"', preview)
        self.assertIn('name="banco_existente_b2"', preview)
        self.assertIn("Banco Teste", preview)
        with self.client.session_transaction() as session:
            token = session["invoice_import_stage"]
        response = self.client.post("/invoices/importar/confirmar", data={
            "stage_token": token, "banco_existente_b1": "1", "banco_existente_b2": "1",
        })
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        receipt = conn.execute("""
            SELECT r.banco_credito_id
            FROM recebimentos_invoice r JOIN invoices i ON i.id=r.invoice_id
            WHERE i.numero_invoice='INV-BANK-SUGGESTION'
        """).fetchone()
        contract = conn.execute("""
            SELECT c.banco_liquidacao_id
            FROM contratos c JOIN invoice_contrato_cambio v ON v.contrato_id=c.id
            JOIN invoices i ON i.id=v.invoice_id
            WHERE i.numero_invoice='INV-BANK-SUGGESTION'
        """).fetchone()
        self.assertEqual(receipt["banco_credito_id"], 1)
        self.assertEqual(contract["banco_liquidacao_id"], 1)
        conn.close()

    def test_receipts_exchange_and_contract_grouping(self):
        invoice_id = self._create_invoice()
        for value in ("800,00", "200,00"):
            response = self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
                "banco_credito_id": "1", "data_credito": "10/08/2026", "valor_moeda": value,
            })
            self.assertEqual(response.status_code, 302)
        response = self.client.post(f"/invoice/{invoice_id}/cambio", data={
            "numero_contrato": "C001", "banco_liquidacao_id": "1", "data_fechamento": "12/08/2026",
            "taxa_cambio": "5,10", "valor_alocado": "600,00",
        })
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        summary = app.invoice_summary(conn, invoice_id)
        self.assertEqual(summary["saldo_recebimento"], app.Decimal("0"))
        self.assertEqual(summary["saldo_cambio"], app.Decimal("400"))
        self.assertEqual(summary["status"], app.INVOICE_STATUS_RECEBIDA_AGUARDANDO_CAMBIO)
        self.assertEqual(summary["taxa_cambio_media"], app.Decimal("5.10"))
        self.assertEqual(summary["valor_brl"], app.Decimal("3060"))
        contract = conn.execute("SELECT * FROM contratos WHERE numero_contrato='C001'").fetchone()
        self.assertEqual(app.Decimal(str(contract["valor_moeda"])), app.Decimal("600"))
        conn.close()
        self.assertEqual(self.client.get(f"/contrato/{contract['id']}").status_code, 200)
        self.assertEqual(self.client.get(f"/contrato/{contract['id']}/editar").status_code, 200)
        self.assertEqual(self.client.get(f"/invoice/{invoice_id}").status_code, 200)
        saldo_response = self.client.get(f"/invoices/{invoice_id}/saldo")
        self.assertEqual(saldo_response.status_code, 200)
        self.assertEqual(saldo_response.get_json()["saldo_cambio"], 400.0)

        invoice_2 = self._create_invoice("INV-002", "500,00")
        self.client.post(f"/invoice/{invoice_2}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "10/08/2026", "valor_moeda": "500,00",
        })
        self.client.post(f"/invoice/{invoice_2}/cambio", data={
            "contrato_id": str(contract["id"]), "valor_alocado": "300,00",
        })
        conn = app.db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM contratos WHERE numero_contrato='C001'").fetchone()[0], 1)
        self.assertEqual(app.Decimal(str(conn.execute("SELECT valor_moeda FROM contratos WHERE numero_contrato='C001'").fetchone()[0])), app.Decimal("900"))
        conn.close()

    def test_partial_closings_create_successive_parcels_without_duplicating_receipts(self):
        invoice_id = self._create_invoice("INV-CLOSINGS", "1000,00")
        for value, day in (("800,00", "10/08/2026"), ("200,00", "11/08/2026")):
            self.assertEqual(self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
                "banco_credito_id": "1", "data_credito": day, "valor_moeda": value,
            }).status_code, 302)

        self.assertEqual(self.client.post(f"/invoice/{invoice_id}/fechamentos", data={
            "valor_moeda": "300,00", "data_fechamento": "12/08/2026",
        }).status_code, 302)
        conn = app.db()
        first_child = conn.execute(
            "SELECT invoice_id FROM invoice_desdobramentos WHERE invoice_anterior_id=?",
            (invoice_id,),
        ).fetchone()[0]
        source = conn.execute("SELECT * FROM invoices WHERE id=?", (invoice_id,)).fetchone()
        child = conn.execute("SELECT * FROM invoices WHERE id=?", (first_child,)).fetchone()
        self.assertEqual(source["numero_invoice"], "INV-CLOSINGS - 1")
        self.assertEqual(app.Decimal(str(source["valor_moeda"])), app.Decimal("300"))
        self.assertEqual(app.Decimal(str(child["valor_moeda"])), app.Decimal("700"))
        self.assertEqual(child["status"], app.INVOICE_STATUS_RECEBIDA_AGUARDANDO_CAMBIO)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM recebimentos_invoice").fetchone()[0], 2)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM invoice_recebimento_alocacoes").fetchone()[0], 3)
        self.assertEqual(app.invoice_summary(conn, invoice_id)["total_recebido"], app.Decimal("300"))
        self.assertEqual(app.invoice_summary(conn, first_child)["total_recebido"], app.Decimal("700"))
        physical_receipt_id = conn.execute(
            "SELECT id FROM recebimentos_invoice ORDER BY id LIMIT 1"
        ).fetchone()[0]
        conn.close()
        response = self.client.post(
            f"/invoice/{invoice_id}/recebimentos/{physical_receipt_id}/editar",
            data={"valor_moeda": "999,00", "data_credito": "10/08/2026", "banco_credito_id": "1"},
            follow_redirects=True,
        )
        self.assertIn("compartilhado", response.get_data(as_text=True))
        response = self.client.post(f"/invoice/{first_child}/editar", data={
            "empresa_id": "1", "numero_invoice": "INV-CLOSINGS - 2",
            "tipo_documento": "COMMERCIAL_INVOICE", "competencia_id": "1", "cliente_id": "1",
            "data_emissao": "01/08/2026", "moeda": "USD", "valor_moeda": "701,00",
        })
        self.assertIn("não podem ser alterados", response.get_data(as_text=True))

        self.assertEqual(self.client.post(f"/invoice/{first_child}/fechamentos", data={
            "valor_moeda": "200,00", "data_fechamento": "13/08/2026",
        }).status_code, 302)
        conn = app.db()
        second_child = conn.execute(
            "SELECT invoice_id FROM invoice_desdobramentos WHERE invoice_anterior_id=?",
            (first_child,),
        ).fetchone()[0]
        self.assertEqual(app.invoice_summary(conn, first_child)["valor_moeda"], app.Decimal("200"))
        self.assertEqual(app.invoice_summary(conn, second_child)["valor_moeda"], app.Decimal("500"))
        self.assertEqual(app.invoice_summary(conn, second_child)["total_recebido"], app.Decimal("500"))
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM recebimentos_invoice").fetchone()[0], 2)
        conn.close()

        self.assertEqual(self.client.post(f"/invoice/{second_child}/fechamentos", data={
            "valor_moeda": "500,00", "data_fechamento": "14/08/2026",
            "numero_novo_contrato": "C-CLOSINGS",
        }).status_code, 302)
        conn = app.db()
        contract = conn.execute(
            "SELECT id, valor_moeda, data_contrato FROM contratos WHERE numero_contrato='C-CLOSINGS'"
        ).fetchone()
        self.assertEqual(app.Decimal(str(contract["valor_moeda"])), app.Decimal("500"))
        self.assertEqual(contract["data_contrato"], "2026-08-14")
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM invoice_desdobramentos WHERE invoice_raiz_id=?", (invoice_id,)
        ).fetchone()[0], 3)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM recebimentos_invoice").fetchone()[0], 2)
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM invoice_recebimento_alocacoes WHERE recebimento_id IN (1,2)"
        ).fetchone()[0], 4)
        self.assertEqual(conn.execute("SELECT status FROM invoices WHERE id=?", (second_child,)).fetchone()[0], app.INVOICE_STATUS_LIQUIDADA)
        conn.close()
        self.assertEqual(self.client.get(f"/contrato/{contract['id']}").status_code, 200)
        self.assertEqual(self.client.get(f"/contrato/{contract['id']}/relatorio").status_code, 200)

    def test_closing_status_is_persisted_after_successful_individual_closing(self):
        pending_invoice = self._create_invoice("INV-CLOSING-PENDING", "100,00")
        self.client.post(f"/invoice/{pending_invoice}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "10/08/2026", "valor_moeda": "100,00",
        })
        response = self.client.post(f"/invoice/{pending_invoice}/fechamentos", data={
            "valor_moeda": "100,00", "data_fechamento": "12/08/2026",
        })
        self.assertEqual(response.status_code, 302)

        conn = app.db()
        invoice = conn.execute(
            "SELECT status, status_manual FROM invoices WHERE id=?", (pending_invoice,)
        ).fetchone()
        self.assertEqual(invoice["status"], app.INVOICE_STATUS_AGUARDANDO_CONTRATO)
        self.assertEqual(invoice["status_manual"], 0)
        self.assertEqual(app.invoice_summary(conn, pending_invoice)["status"], app.INVOICE_STATUS_AGUARDANDO_CONTRATO)
        conn.close()

        manual_invoice = self._create_invoice("INV-CLOSING-MANUAL", "100,00")
        self.client.post(f"/invoice/{manual_invoice}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "10/08/2026", "valor_moeda": "100,00",
        })
        conn = app.db()
        conn.execute(
            "UPDATE invoices SET status=?, status_manual=1 WHERE id=?",
            (app.INVOICE_STATUS_LIQUIDADA, manual_invoice),
        )
        conn.commit()
        conn.close()
        self.assertEqual(self.client.post(f"/invoice/{manual_invoice}/fechamentos", data={
            "valor_moeda": "100,00", "data_fechamento": "12/08/2026",
        }).status_code, 302)
        conn = app.db()
        manual_status = conn.execute(
            "SELECT status, status_manual FROM invoices WHERE id=?", (manual_invoice,)
        ).fetchone()
        self.assertEqual(
            (manual_status["status"], manual_status["status_manual"]),
            (app.INVOICE_STATUS_LIQUIDADA, 1),
        )
        conn.close()

        listing = self.client.get("/invoices?status=AGUARDANDO_CONTRATO")
        self.assertEqual(listing.status_code, 200)
        listing_html = listing.get_data(as_text=True)
        self.assertIn("INV-CLOSING-PENDING", listing_html)
        self.assertIn("AGUARDANDO CONTRATO", listing_html)
        self.assertIn("Aguardando número de contrato", listing_html)
        detail = self.client.get(f"/invoice/{pending_invoice}")
        self.assertIn("Aguardando número de contrato.", detail.get_data(as_text=True))
        saldo = self.client.get(f"/invoices/{pending_invoice}/saldo").get_json()
        self.assertEqual(saldo["status"], app.INVOICE_STATUS_AGUARDANDO_CONTRATO)
        self.assertEqual(saldo["status_label"], "AGUARDANDO CONTRATO")
        report = app.build_invoice_report_context()
        self.assertEqual(report["tables"][0]["total"], app.Decimal("100"))

        liquidated_invoice = self._create_invoice("INV-CLOSING-LIQUIDATED", "100,00")
        self.client.post(f"/invoice/{liquidated_invoice}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "10/08/2026", "valor_moeda": "100,00",
        })
        response = self.client.post(f"/invoice/{liquidated_invoice}/fechamentos", data={
            "valor_moeda": "100,00", "data_fechamento": "12/08/2026",
            "numero_novo_contrato": "C-CLOSING-LIQUIDATED",
        })
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        invoice = conn.execute(
            "SELECT status, status_manual FROM invoices WHERE id=?", (liquidated_invoice,)
        ).fetchone()
        self.assertEqual(invoice["status"], app.INVOICE_STATUS_LIQUIDADA)
        self.assertEqual(invoice["status_manual"], 0)
        conn.close()

    def test_pending_closing_has_priority_until_all_received_amount_is_contracted(self):
        invoice_id = self._create_invoice("INV-CLOSING-MIXED", "1000,00")
        self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "10/08/2026", "valor_moeda": "1000,00",
        })
        self.assertEqual(self.client.post(f"/invoice/{invoice_id}/fechamentos", data={
            "valor_moeda": "600,00", "data_fechamento": "12/08/2026",
            "numero_novo_contrato": "C-CLOSING-MIXED",
        }).status_code, 302)
        conn = app.db()
        remainder_id = conn.execute(
            "SELECT invoice_id FROM invoice_desdobramentos WHERE invoice_anterior_id=?", (invoice_id,)
        ).fetchone()[0]
        conn.close()
        self.assertEqual(self.client.post(f"/invoice/{remainder_id}/fechamentos", data={
            "valor_moeda": "400,00", "data_fechamento": "13/08/2026",
        }).status_code, 302)
        conn = app.db()
        invoice = conn.execute("SELECT status FROM invoices WHERE id=?", (invoice_id,)).fetchone()
        remainder = conn.execute("SELECT status FROM invoices WHERE id=?", (remainder_id,)).fetchone()
        self.assertEqual(invoice["status"], app.INVOICE_STATUS_LIQUIDADA)
        self.assertEqual(remainder["status"], app.INVOICE_STATUS_AGUARDANDO_CONTRATO)
        self.assertEqual(app.invoice_summary(conn, invoice_id)["total_cambio"], app.Decimal("600"))
        self.assertEqual(app.invoice_summary(conn, remainder_id)["total_cambio"], app.Decimal("0"))
        conn.close()

        conn = app.db()
        pending_id = conn.execute(
            "SELECT id FROM fechamentos_cambio WHERE invoice_id=? AND contrato_id IS NULL", (remainder_id,)
        ).fetchone()[0]
        contract_id = conn.execute(
            "SELECT id FROM contratos WHERE numero_contrato='C-CLOSING-MIXED'"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(self.client.post(f"/contrato/{contract_id}/fechamentos/vincular", data={
            "fechamento_id": str(pending_id),
        }).status_code, 302)

        conn = app.db()
        invoice = conn.execute("SELECT status FROM invoices WHERE id=?", (invoice_id,)).fetchone()
        remainder = conn.execute("SELECT status FROM invoices WHERE id=?", (remainder_id,)).fetchone()
        self.assertEqual(invoice["status"], app.INVOICE_STATUS_LIQUIDADA)
        self.assertEqual(remainder["status"], app.INVOICE_STATUS_LIQUIDADA)
        self.assertEqual(app.invoice_summary(conn, remainder_id)["total_fechamentos_pendentes"], app.Decimal("0"))
        conn.close()

    def test_closing_and_legacy_allocation_respect_reserved_balance(self):
        invoice_id = self._create_invoice("INV-CLOSING-BALANCE", "500,00")
        self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "10/08/2026", "valor_moeda": "500,00",
        })
        self.assertEqual(self.client.post(f"/invoice/{invoice_id}/fechamentos", data={
            "valor_moeda": "400,00",
        }).status_code, 302)
        self.client.post(f"/invoice/{invoice_id}/fechamentos", data={"valor_moeda": "100,01"})
        self.client.post(f"/invoice/{invoice_id}/cambio", data={
            "numero_contrato": "C-RESERVED", "valor_alocado": "100,01",
        })
        conn = app.db()
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM fechamentos_cambio WHERE invoice_id=?", (invoice_id,)
        ).fetchone()[0], 1)
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM invoice_contrato_cambio WHERE invoice_id=?", (invoice_id,)
        ).fetchone()[0], 0)
        conn.close()

    def test_dates_default_to_today_and_manual_closing_date_is_preserved(self):
        invoice_id = self._create_invoice("INV-CLOSING-DATES", "200,00")
        self.client.post(f"/invoice/{invoice_id}/recebimentos", data={"valor_moeda": "200,00"})
        self.client.post(f"/invoice/{invoice_id}/fechamentos", data={"valor_moeda": "100,00"})
        conn = app.db()
        today = app.date.today().isoformat()
        self.assertEqual(conn.execute(
            "SELECT data_credito FROM recebimentos_invoice WHERE invoice_id=?", (invoice_id,)
        ).fetchone()[0], today)
        self.assertEqual(conn.execute(
            "SELECT data_fechamento FROM fechamentos_cambio WHERE invoice_id=?", (invoice_id,)
        ).fetchone()[0], today)
        conn.close()

        invoice_id = self._create_invoice("INV-CLOSING-CURRENCY", "100,00", currency="EUR")
        self.client.post(f"/invoice/{invoice_id}/recebimentos", data={"valor_moeda": "100,00"})
        self.client.post(f"/invoice/{invoice_id}/fechamentos", data={
            "valor_moeda": "100,00", "numero_novo_contrato": "C-EUR-CLOSING",
            "data_fechamento": "20/08/2026",
        })
        conn = app.db()
        contract = conn.execute(
            "SELECT moeda, data_contrato FROM contratos WHERE numero_contrato='C-EUR-CLOSING'"
        ).fetchone()
        self.assertEqual((contract["moeda"], contract["data_contrato"]), ("EUR", "2026-08-20"))
        conn.close()

    def test_closing_routes_reject_unknown_or_incompatible_contracts(self):
        invoice_id = self._create_invoice("INV-CLOSING-VALIDATION", "100,00")
        self.client.post(f"/invoice/{invoice_id}/recebimentos", data={"valor_moeda": "100,00"})
        self.client.post(f"/invoice/{invoice_id}/fechamentos", data={
            "valor_moeda": "50,00", "contrato_id": "999999",
        })
        conn = app.db()
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM fechamentos_cambio WHERE invoice_id=?", (invoice_id,)
        ).fetchone()[0], 0)
        conn.execute("INSERT INTO contratos(numero_contrato,moeda,valor_moeda) VALUES (?,?,?)", ("C-EUR", "EUR", 10))
        contract_id = conn.execute("SELECT id FROM contratos WHERE numero_contrato='C-EUR'").fetchone()[0]
        conn.commit()
        conn.close()
        other_invoice_id = self._create_invoice("INV-EUR-LINK", "10,00", currency="EUR")
        conn = app.db()
        conn.execute(
            "INSERT INTO invoice_contrato_cambio(invoice_id,contrato_id,valor_alocado) VALUES (?,?,?)",
            (other_invoice_id, contract_id, 10),
        )
        conn.commit()
        conn.close()
        self.client.post(f"/invoice/{invoice_id}/fechamentos", data={
            "valor_moeda": "50,00", "contrato_id": str(contract_id),
        })
        conn = app.db()
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM fechamentos_cambio WHERE invoice_id=?", (invoice_id,)
        ).fetchone()[0], 0)
        conn.close()

    def test_exchange_form_uses_exchange_contract_only(self):
        invoice_id = self._create_invoice(commercial="COM-ONLY")
        self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "10/08/2026", "valor_moeda": "1000,00",
        })
        response = self.client.post(f"/invoice/{invoice_id}/cambio", data={
            "numero_contrato_cambio": "C-EXCHANGE-ONLY", "valor_alocado": "1000,00",
        })
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        self.assertIsNotNone(conn.execute(
            "SELECT id FROM contratos WHERE numero_contrato='C-EXCHANGE-ONLY'"
        ).fetchone())
        self.assertIsNone(conn.execute(
            "SELECT id FROM contratos WHERE numero_contrato='COM-ONLY'"
        ).fetchone())
        self.assertEqual(conn.execute(
            "SELECT contrato_comercial FROM invoices WHERE id=?", (invoice_id,)
        ).fetchone()[0], "COM-ONLY")
        conn.close()

    def test_overallocation_is_rejected_and_due_traceability_is_limited_to_exchange(self):
        invoice_id = self._create_invoice()
        self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "10/08/2026", "valor_moeda": "1000,00",
        })
        response = self.client.post(f"/invoice/{invoice_id}/cambio", data={
            "numero_contrato": "C002", "valor_alocado": "1000,01",
        })
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM invoice_contrato_cambio").fetchone()[0], 0)
        conn.close()

        self.client.post(f"/invoice/{invoice_id}/cambio", data={
            "numero_contrato": "C002", "valor_alocado": "1000,00",
        })
        response = self.client.post(f"/invoice/{invoice_id}/due", data={
            "due_id": "1", "valor_vinculado": "1000,00",
        })
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM due_invoice").fetchone()[0], 1)
        conn.close()

        second_invoice = self._create_invoice("INV-DUE-2", "500,00")
        self.client.post(f"/invoice/{second_invoice}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "11/08/2026", "valor_moeda": "500,00",
        })
        self.client.post(f"/invoice/{second_invoice}/cambio", data={
            "numero_contrato": "C-DUE-2", "valor_alocado": "500,00",
        })
        self.client.post(f"/invoice/{second_invoice}/due", data={
            "due_id": "1", "valor_vinculado": "500,00",
        })
        conn = app.db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM due_invoice WHERE due_id=1").fetchone()[0], 2)
        conn.close()

    def test_weighted_exchange_rate_and_brl_for_multiple_contracts(self):
        invoice_id = self._create_invoice("INV-RATE", "1000,00")
        self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "10/08/2026", "valor_moeda": "1000,00",
        })
        for number, amount, rate in (("C-RATE-A", "600,00", "5,00"), ("C-RATE-B", "400,00", "6,00")):
            response = self.client.post(f"/invoice/{invoice_id}/cambio", data={
                "numero_contrato": number, "valor_alocado": amount, "taxa_cambio": rate,
            })
            self.assertEqual(response.status_code, 302)
        conn = app.db()
        summary = app.invoice_summary(conn, invoice_id)
        self.assertEqual(summary["taxa_cambio_media"], app.Decimal("5.4"))
        self.assertEqual(summary["valor_brl"], app.Decimal("5400"))
        self.assertEqual(summary["saldo_recebimento"], app.Decimal("0"))
        self.assertEqual(summary["saldo_cambio"], app.Decimal("0"))
        self.assertEqual(summary["status"], app.INVOICE_STATUS_LIQUIDADA)
        conn.close()
        self.assertEqual(self.client.get("/invoices?status=LIQUIDADA").status_code, 200)

    def test_import_splits_one_invoice_across_multiple_contracts(self):
        import pandas as pd

        invoice_id = self._create_invoice("INV-SPLIT", "1000,00")
        self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "10/08/2026", "valor_moeda": "1000,00",
        })
        frame = pd.DataFrame([
            {"cnpj": "45.765.914/0001-81", "numero_invoice": "INV-SPLIT", "tipo_documento": "COMMERCIAL INVOICE",
             "data_emissao": "01/08/2026", "moeda": "USD", "valor_invoice": "1000,00",
             "contrato_comercial": "COM-SPLIT", "numero_contrato_cambio": "C-SPLIT-A",
             "valor_alocado": "400,00", "taxa_cambio": "5,00"},
            {"cnpj": "45.765.914/0001-81", "numero_invoice": "INV-SPLIT", "tipo_documento": "COMMERCIAL INVOICE",
             "data_emissao": "01/08/2026", "moeda": "USD", "valor_invoice": "1000,00",
             "contrato_comercial": "COM-SPLIT", "numero_contrato_cambio": "C-SPLIT-B",
             "valor_alocado": "600,00", "taxa_cambio": "5,10"},
        ])
        frame["competencia"] = "Agosto/2026"
        output = BytesIO()
        frame.to_excel(output, index=False)
        output.seek(0)
        response = self.client.post("/invoices/importar", data={"arquivo": (output, "split.xlsx")},
                                    content_type="multipart/form-data")
        self.assertEqual(response.status_code, 200)
        with self.client.session_transaction() as session:
            token = session["invoice_import_stage"]
        self.assertEqual(self.client.post("/invoices/importar/confirmar", data={"stage_token": token}).status_code, 302)
        conn = app.db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM invoice_contrato_cambio WHERE invoice_id=?", (invoice_id,)).fetchone()[0], 2)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM contratos WHERE numero_contrato LIKE 'C-SPLIT-%'").fetchone()[0], 2)
        self.assertEqual(conn.execute(
            "SELECT contrato_comercial FROM invoices WHERE id=?", (invoice_id,)
        ).fetchone()[0], "COM-SPLIT")
        conn.close()

    def test_excel_groups_contracts_and_reimport_preserves_receipts(self):
        import pandas as pd

        for number, value in (("INV-A", "300,00"), ("INV-B", "200,00")):
            invoice_id = self._create_invoice(number, value)
            self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
                "banco_credito_id": "1", "data_credito": "10/08/2026", "valor_moeda": value,
            })
        conn = app.db()
        invoice_a = conn.execute("SELECT id FROM invoices WHERE numero_invoice='INV-A'").fetchone()[0]
        conn.close()
        self.client.post(f"/invoice/{invoice_a}/cambio", data={
            "numero_contrato": "C-OLD", "valor_alocado": "50,00", "taxa_cambio": "5,00",
        })
        self.client.post(f"/invoice/{invoice_a}/due", data={
            "due_id": "1", "valor_vinculado": "50,00",
        })

        frame = pd.DataFrame([
            {"cnpj": "45.765.914/0001-81", "numero_invoice": "INV-A", "tipo_documento": "COMMERCIAL INVOICE",
             "data_emissao": "01/08/2026", "moeda": "USD", "valor_invoice": "300,00",
             "contrato_comercial": "COM-GROUP", "numero_contrato_cambio": "C-GROUP",
             "valor_alocado": "300,00", "taxa_cambio": "5,10"},
            {"cnpj": "45.765.914/0001-81", "numero_invoice": "INV-B", "tipo_documento": "COMMERCIAL INVOICE",
             "data_emissao": "02/08/2026", "moeda": "USD", "valor_invoice": "200,00",
             "contrato_comercial": "COM-GROUP", "numero_contrato_cambio": "C-GROUP",
             "valor_alocado": "200,00", "taxa_cambio": "5,10"},
        ])
        frame["competencia"] = "Agosto/2026"
        output = BytesIO()
        frame.to_excel(output, index=False)
        output.seek(0)
        response = self.client.post("/invoices/importar", data={"arquivo": (output, "invoices.xlsx")},
                                    content_type="multipart/form-data")
        self.assertEqual(response.status_code, 200)
        with self.client.session_transaction() as session:
            token = session["invoice_import_stage"]
        response = self.client.post("/invoices/importar/confirmar", data={"stage_token": token})
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0], 2)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM contratos WHERE numero_contrato='C-GROUP'").fetchone()[0], 1)
        self.assertEqual(app.Decimal(str(conn.execute("SELECT valor_moeda FROM contratos WHERE numero_contrato='C-GROUP'").fetchone()[0])), app.Decimal("500"))
        self.assertEqual(conn.execute(
            "SELECT contrato_comercial FROM invoices WHERE numero_invoice='INV-A'"
        ).fetchone()[0], "COM-GROUP")
        receipt_count = conn.execute("SELECT COUNT(*) FROM recebimentos_invoice WHERE invoice_id=(SELECT id FROM invoices WHERE numero_invoice='INV-A')").fetchone()[0]
        due_link_count = conn.execute("SELECT COUNT(*) FROM due_invoice WHERE invoice_id=(SELECT id FROM invoices WHERE numero_invoice='INV-A')").fetchone()[0]
        conn.close()
        self.assertEqual(receipt_count, 1)
        self.assertEqual(due_link_count, 1)

        replacement = pd.DataFrame([{
            "cnpj": "45.765.914/0001-81", "numero_invoice": "INV-A", "tipo_documento": "COMMERCIAL INVOICE",
             "data_emissao": "01/08/2026", "moeda": "USD", "valor_invoice": "300,00",
            "contrato_comercial": "COM-REPROCESSED", "numero_contrato": "C-GROUP",
            "valor_alocado": "250,00", "taxa_cambio": "5,10",
        }])
        replacement["competencia"] = "Agosto/2026"
        replacement_output = BytesIO()
        replacement.to_excel(replacement_output, index=False)
        replacement_output.seek(0)
        response = self.client.post("/invoices/importar", data={"arquivo": (replacement_output, "replacement.xlsx")},
                                    content_type="multipart/form-data")
        self.assertEqual(response.status_code, 200)
        with self.client.session_transaction() as session:
            token = session["invoice_import_stage"]
        response = self.client.post("/invoices/importar/confirmar", data={"stage_token": token})
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM recebimentos_invoice WHERE invoice_id=(SELECT id FROM invoices WHERE numero_invoice='INV-A')").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM due_invoice WHERE invoice_id=(SELECT id FROM invoices WHERE numero_invoice='INV-A')").fetchone()[0], 1)
        self.assertEqual(app.Decimal(str(conn.execute("SELECT valor_alocado FROM invoice_contrato_cambio WHERE invoice_id=(SELECT id FROM invoices WHERE numero_invoice='INV-A')").fetchone()[0])), app.Decimal("250"))
        self.assertEqual(conn.execute(
            "SELECT contrato_comercial FROM invoices WHERE numero_invoice='INV-A'"
        ).fetchone()[0], "COM-REPROCESSED")
        conn.close()

    def test_invalid_receipt_exchange_edits_and_currency_change_are_rejected(self):
        invoice_id = self._create_invoice(value="1000,00")
        self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "10/08/2026", "valor_moeda": "1000,00",
        })
        self.client.post(f"/invoice/{invoice_id}/cambio", data={
            "numero_contrato": "C-VALID", "valor_alocado": "500,00",
        })
        conn = app.db()
        receipt = conn.execute("SELECT id FROM recebimentos_invoice WHERE invoice_id=?", (invoice_id,)).fetchone()
        link = conn.execute("SELECT id FROM invoice_contrato_cambio WHERE invoice_id=?", (invoice_id,)).fetchone()
        conn.close()

        self.client.post(f"/invoice/{invoice_id}/recebimentos/{receipt['id']}/editar", data={
            "data_credito": "10/08/2026", "valor_moeda": "400,00",
        })
        self.client.post(f"/invoice/{invoice_id}/recebimentos/{receipt['id']}/excluir")
        self.client.post(f"/invoice/{invoice_id}/cambio/{link['id']}/editar", data={
            "valor_alocado": "1000,01",
        })
        conn = app.db()
        self.assertEqual(app.Decimal(str(conn.execute("SELECT valor_moeda FROM recebimentos_invoice WHERE id=?", (receipt["id"],)).fetchone()[0])), app.Decimal("1000"))
        self.assertEqual(app.Decimal(str(conn.execute("SELECT valor_alocado FROM invoice_contrato_cambio WHERE id=?", (link["id"],)).fetchone()[0])), app.Decimal("500"))
        conn.close()

        response = self.client.post(f"/invoice/{invoice_id}/editar", data={
            "empresa_id": "1", "numero_invoice": "INV-001", "tipo_documento": "COMMERCIAL_INVOICE",
            "cliente_id": "1", "data_emissao": "01/08/2026", "moeda": "EUR", "valor_moeda": "1000,00",
        })
        self.assertEqual(response.status_code, 200)
        conn = app.db()
        self.assertEqual(conn.execute("SELECT moeda FROM invoices WHERE id=?", (invoice_id,)).fetchone()[0], "USD")
        conn.close()

    def test_derived_contract_cannot_be_deleted_from_contract_list(self):
        invoice_id = self._create_invoice(value="1000,00")
        self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "10/08/2026", "valor_moeda": "1000,00",
        })
        self.client.post(f"/invoice/{invoice_id}/cambio", data={
            "numero_contrato": "C-DERIVED", "valor_alocado": "1000,00",
        })
        conn = app.db()
        contract_id = conn.execute("SELECT id FROM contratos WHERE numero_contrato='C-DERIVED'").fetchone()[0]
        conn.close()
        self.client.post("/contratos/excluir-lote", data={"selected_ids": str(contract_id)})
        conn = app.db()
        self.assertIsNotNone(conn.execute("SELECT id FROM contratos WHERE id=?", (contract_id,)).fetchone())
        self.assertIsNotNone(conn.execute("SELECT id FROM invoice_contrato_cambio WHERE invoice_id=?", (invoice_id,)).fetchone())
        conn.close()

    def test_invoice_delete_succeeds_when_it_has_no_transactional_links(self):
        invoice_id = self._create_invoice(number="INV-DELETE-FREE", value="100,00")

        response = self.client.post(
            f"/invoice/{invoice_id}/excluir", follow_redirects=True
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("INV-DELETE-FREE", response.get_data(as_text=True))
        conn = app.db()
        self.assertIsNone(conn.execute(
            "SELECT id FROM invoices WHERE id=?", (invoice_id,)
        ).fetchone())
        conn.close()

    def test_invoice_delete_is_blocked_for_all_transactional_link_types(self):
        receipt_invoice = self._create_invoice("INV-BLOCK-RECEIPT", "100,00")
        allocation_invoice = self._create_invoice("INV-BLOCK-ALLOCATION", "100,00")
        closing_invoice = self._create_invoice("INV-BLOCK-CLOSING", "100,00")
        due_invoice = self._create_invoice("INV-BLOCK-DUE", "100,00")

        conn = app.db()
        conn.execute(
            "INSERT INTO recebimentos_invoice(invoice_id,data_credito,moeda,valor_moeda) "
            "VALUES (?,?,?,?)",
            (receipt_invoice, "2026-08-10", "USD", 100),
        )
        conn.execute(
            "INSERT INTO contratos(numero_contrato,moeda,valor_moeda) VALUES (?,?,?)",
            ("C-BLOCK-ALLOCATION", "USD", 100),
        )
        allocation_contract_id = conn.execute(
            "SELECT id FROM contratos WHERE numero_contrato=?",
            ("C-BLOCK-ALLOCATION",),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO invoice_contrato_cambio(invoice_id,contrato_id,valor_alocado) "
            "VALUES (?,?,?)",
            (allocation_invoice, allocation_contract_id, 50),
        )
        conn.execute(
            "INSERT INTO fechamentos_cambio(invoice_id,moeda,valor_moeda,data_fechamento) "
            "VALUES (?,?,?,?)",
            (closing_invoice, "USD", 50, "2026-08-11"),
        )
        conn.execute(
            "INSERT INTO due_invoice(due_id,invoice_id,valor_vinculado) VALUES (?,?,?)",
            (1, due_invoice, 50),
        )
        conn.commit()
        conn.close()

        expected = {
            receipt_invoice: ("INV-BLOCK-RECEIPT", "recebimento"),
            allocation_invoice: ("INV-BLOCK-ALLOCATION", "C-BLOCK-ALLOCATION"),
            closing_invoice: ("INV-BLOCK-CLOSING", "fechamento"),
            due_invoice: ("INV-BLOCK-DUE", "DUE-001"),
        }
        for invoice_id, (number, reason) in expected.items():
            response = self.client.post(
                f"/invoice/{invoice_id}/excluir", follow_redirects=True
            )
            self.assertEqual(response.status_code, 200)
            html = response.get_data(as_text=True)
            self.assertIn(number, html)
            self.assertIn(reason, html)

        conn = app.db()
        for invoice_id in expected:
            self.assertIsNotNone(conn.execute(
                "SELECT id FROM invoices WHERE id=?", (invoice_id,)
            ).fetchone())
        self.assertIsNotNone(conn.execute(
            "SELECT id FROM recebimentos_invoice WHERE invoice_id=?", (receipt_invoice,)
        ).fetchone())
        self.assertIsNotNone(conn.execute(
            "SELECT id FROM invoice_contrato_cambio WHERE invoice_id=?", (allocation_invoice,)
        ).fetchone())
        self.assertIsNotNone(conn.execute(
            "SELECT id FROM fechamentos_cambio WHERE invoice_id=?", (closing_invoice,)
        ).fetchone())
        self.assertIsNotNone(conn.execute(
            "SELECT id FROM due_invoice WHERE invoice_id=?", (due_invoice,)
        ).fetchone())
        conn.close()

    def test_invoice_batch_delete_removes_eligible_and_reports_blocked_invoices(self):
        free_id = self._create_invoice("INV-BATCH-FREE", "100,00")
        receipt_id = self._create_invoice("INV-BATCH-RECEIPT", "100,00")
        allocation_id = self._create_invoice("INV-BATCH-ALLOCATION", "100,00")

        conn = app.db()
        conn.execute(
            "INSERT INTO recebimentos_invoice(invoice_id,data_credito,moeda,valor_moeda) "
            "VALUES (?,?,?,?)",
            (receipt_id, "2026-08-10", "USD", 100),
        )
        conn.execute(
            "INSERT INTO contratos(numero_contrato,moeda,valor_moeda) VALUES (?,?,?)",
            ("C-BATCH-ALLOCATION", "USD", 100),
        )
        contract_id = conn.execute(
            "SELECT id FROM contratos WHERE numero_contrato=?",
            ("C-BATCH-ALLOCATION",),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO invoice_contrato_cambio(invoice_id,contrato_id,valor_alocado) "
            "VALUES (?,?,?)",
            (allocation_id, contract_id, 50),
        )
        conn.commit()
        conn.close()

        response = self.client.post(
            "/invoices/excluir-lote",
            data={
                "selected_ids": [str(free_id), str(receipt_id), str(allocation_id)],
                "numero_invoice": "INV-BATCH",
                "sort": "numero_invoice",
                "direction": "asc",
                "page": "2",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("numero_invoice=INV-BATCH", response.location)
        page = self.client.get(response.location)
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        self.assertIn("INV-BATCH-FREE", html)
        self.assertIn("INV-BATCH-RECEIPT", html)
        self.assertIn("INV-BATCH-ALLOCATION", html)
        self.assertIn("C-BATCH-ALLOCATION", html)
        self.assertIn("recebimento", html)
        self.assertIn("Invoice(s) excluída(s)", html)

        conn = app.db()
        self.assertIsNone(conn.execute(
            "SELECT id FROM invoices WHERE id=?", (free_id,)
        ).fetchone())
        self.assertIsNotNone(conn.execute(
            "SELECT id FROM invoices WHERE id=?", (receipt_id,)
        ).fetchone())
        self.assertIsNotNone(conn.execute(
            "SELECT id FROM invoices WHERE id=?", (allocation_id,)
        ).fetchone())
        self.assertIsNotNone(conn.execute(
            "SELECT id FROM contratos WHERE id=?", (contract_id,)
        ).fetchone())
        conn.close()

    def test_invoice_batch_delete_does_not_partially_delete_when_an_id_is_missing(self):
        invoice_id = self._create_invoice("INV-BATCH-STALE", "100,00")

        response = self.client.post(
            "/invoices/excluir-lote",
            data={"selected_ids": [str(invoice_id), "999999999"]},
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("não foram encontrados", response.get_data(as_text=True))
        conn = app.db()
        self.assertIsNotNone(conn.execute(
            "SELECT id FROM invoices WHERE id=?", (invoice_id,)
        ).fetchone())
        conn.close()

    def test_invoice_list_and_detail_render_delete_controls(self):
        invoice_id = self._create_invoice("INV-DELETE-CONTROLS", "100,00")

        listing = self.client.get("/invoices?numero_invoice=INV-DELETE-CONTROLS")
        self.assertEqual(listing.status_code, 200)
        listing_html = listing.get_data(as_text=True)
        self.assertIn('data-batch-form', listing_html)
        self.assertIn('data-batch-select-all', listing_html)
        self.assertIn(f'name="selected_ids" value="{invoice_id}"', listing_html)
        self.assertIn('data-invoice-single-delete', listing_html)
        self.assertIn(f'formaction="/invoice/{invoice_id}/excluir"', listing_html)
        self.assertIn("Excluir as {count} Invoice(s)", listing_html)

        detail = self.client.get(f"/invoice/{invoice_id}")
        self.assertEqual(detail.status_code, 200)
        detail_html = detail.get_data(as_text=True)
        self.assertIn(f'action="/invoice/{invoice_id}/excluir"', detail_html)
        self.assertIn("Excluir Invoice", detail_html)

    def test_invoice_list_renders_batch_receipt_controls_and_banks(self):
        invoice_id = self._create_invoice("INV-BATCH-CONTROLS", "100,00")

        response = self.client.get(f"/invoices?numero_invoice=INV-BATCH-CONTROLS")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('data-batch-receipt-open', html)
        self.assertIn('action="/invoices/recebimentos"', html)
        self.assertIn('<option value="1">Banco Teste</option>', html)
        self.assertIn("Registrar recebimentos em lote", html)
        self.assertIn(f'data-batch-checkbox aria-label="Selecionar Invoice INV-BATCH-CONTROLS"', html)
        self.assertIsNotNone(invoice_id)

    def test_batch_receipts_register_remaining_balance_for_multiple_invoices(self):
        invoice_a = self._create_invoice("INV-BATCH-A", "100,00")
        invoice_b = self._create_invoice("INV-BATCH-B", "50,00", currency="EUR")
        conn = app.db()
        conn.execute(
            "UPDATE invoices SET status=?, status_manual=1 WHERE id IN (?,?)",
            (app.INVOICE_STATUS_AGUARDANDO_RECEBIMENTO, invoice_a, invoice_b),
        )
        conn.commit()
        conn.close()

        response = self.client.post("/invoices/recebimentos", data={
            "selected_ids": [str(invoice_a), str(invoice_b)],
            "banco_credito_id": "1", "data_credito": "20/08/2026",
            "numero_invoice": "INV-BATCH", "sort": "numero_invoice",
            "direction": "asc", "page": "2",
        })

        self.assertEqual(response.status_code, 302)
        self.assertIn("numero_invoice=INV-BATCH", response.location)
        self.assertIn("sort=numero_invoice", response.location)
        self.assertNotIn("banco_credito_id", response.location)
        self.assertNotIn("data_credito", response.location)
        conn = app.db()
        rows = conn.execute("""
            SELECT invoice_id, banco_credito_id, data_credito, moeda, valor_moeda
            FROM recebimentos_invoice WHERE invoice_id IN (?,?) ORDER BY invoice_id
        """, (invoice_a, invoice_b)).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual([(row["invoice_id"], row["moeda"]) for row in rows],
                         [(invoice_a, "USD"), (invoice_b, "EUR")])
        self.assertEqual({row["banco_credito_id"] for row in rows}, {1})
        self.assertEqual({row["data_credito"] for row in rows}, {"2026-08-20"})
        self.assertEqual([app.Decimal(str(row["valor_moeda"])) for row in rows],
                         [app.Decimal("100"), app.Decimal("50")])
        statuses = conn.execute(
            "SELECT status FROM invoices WHERE id IN (?,?) ORDER BY id",
            (invoice_a, invoice_b),
        ).fetchall()
        self.assertEqual(
            [row["status"] for row in statuses],
            [app.INVOICE_STATUS_RECEBIDA_AGUARDANDO_CAMBIO] * 2,
        )
        self.assertEqual(app.invoice_summary(conn, invoice_a)["status"],
                         app.INVOICE_STATUS_RECEBIDA_AGUARDANDO_CAMBIO)
        self.assertEqual(app.invoice_summary(conn, invoice_b)["status"],
                         app.INVOICE_STATUS_RECEBIDA_AGUARDANDO_CAMBIO)
        conn.close()
        listing = self.client.get(response.location)
        self.assertEqual(listing.status_code, 200)
        self.assertGreaterEqual(listing.get_data(as_text=True).count("RECEBIDO AGUARDANDO CAMBIO"), 2)

    def test_batch_receipts_only_register_remaining_balance_for_partial_invoice(self):
        invoice_id = self._create_invoice("INV-BATCH-PARTIAL", "1000,00")
        self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "10/08/2026",
            "valor_moeda": "400,00",
        })

        response = self.client.post("/invoices/recebimentos", data={
            "selected_ids": str(invoice_id), "banco_credito_id": "1",
            "data_credito": "20/08/2026",
        })

        self.assertEqual(response.status_code, 302)
        conn = app.db()
        receipts = conn.execute(
            "SELECT valor_moeda, data_credito FROM recebimentos_invoice WHERE invoice_id=? ORDER BY id",
            (invoice_id,),
        ).fetchall()
        self.assertEqual([app.Decimal(str(row["valor_moeda"])) for row in receipts],
                         [app.Decimal("400"), app.Decimal("600")])
        self.assertEqual(receipts[-1]["data_credito"], "2026-08-20")
        self.assertEqual(app.invoice_summary(conn, invoice_id)["saldo_recebimento"], app.Decimal("0"))
        conn.close()

    def test_batch_receipts_reject_invalid_selection_without_partial_write(self):
        invoice_id = self._create_invoice("INV-BATCH-STALE-RECEIPT", "100,00")

        response = self.client.post("/invoices/recebimentos", data={
            "selected_ids": [str(invoice_id), "999999999"],
            "banco_credito_id": "1", "data_credito": "20/08/2026",
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("não foram encontrados", response.get_data(as_text=True))
        conn = app.db()
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM recebimentos_invoice WHERE invoice_id=?", (invoice_id,)
        ).fetchone()[0], 0)
        conn.close()

    def test_batch_receipts_roll_back_when_one_invoice_has_no_remaining_balance(self):
        invoice_a = self._create_invoice("INV-BATCH-ROLLBACK-A", "100,00")
        invoice_b = self._create_invoice("INV-BATCH-ROLLBACK-B", "100,00")
        self.client.post(f"/invoice/{invoice_b}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "10/08/2026",
            "valor_moeda": "100,00",
        })

        response = self.client.post("/invoices/recebimentos", data={
            "selected_ids": [str(invoice_a), str(invoice_b)],
            "banco_credito_id": "1", "data_credito": "20/08/2026",
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("não possui saldo de recebimento", response.get_data(as_text=True))
        conn = app.db()
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM recebimentos_invoice WHERE invoice_id=?", (invoice_a,)
        ).fetchone()[0], 0)
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM recebimentos_invoice WHERE invoice_id=?", (invoice_b,)
        ).fetchone()[0], 1)
        conn.close()

    def test_batch_receipts_reject_invalid_bank_or_date_without_write(self):
        invoice_id = self._create_invoice("INV-BATCH-INVALID-FORM", "100,00")

        response = self.client.post("/invoices/recebimentos", data={
            "selected_ids": str(invoice_id), "banco_credito_id": "1",
            "data_credito": "31/02/2026",
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Data inválida", response.get_data(as_text=True))
        response = self.client.post("/invoices/recebimentos", data={
            "selected_ids": str(invoice_id), "banco_credito_id": "999999",
            "data_credito": "20/08/2026",
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("não foi encontrado", response.get_data(as_text=True))
        conn = app.db()
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM recebimentos_invoice WHERE invoice_id=?", (invoice_id,)
        ).fetchone()[0], 0)
        conn.close()

    def test_batch_receipts_keep_centralized_closing_bank_rule(self):
        conn = app.db()
        conn.execute("INSERT INTO contrapartes (nome) VALUES (?)", ("Banco Alternativo",))
        bank_two = conn.execute(
            "SELECT id FROM contrapartes WHERE nome='Banco Alternativo'"
        ).fetchone()[0]
        conn.commit()
        conn.close()
        invoice_id = self._create_invoice("INV-BATCH-CENTRAL-BANK", "100,00")
        self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "10/08/2026",
            "valor_moeda": "50,00",
        })
        self.client.post("/invoices/fechamentos", data={
            "selected_ids": str(invoice_id), "data_fechamento": "20/08/2026",
            "data_liquidacao": "20/08/2026", "taxa_cambio": "5,0000",
            "banco_liquidacao_id": "1",
        })

        response = self.client.post("/invoices/recebimentos", data={
            "selected_ids": str(invoice_id), "banco_credito_id": str(bank_two),
            "data_credito": "21/08/2026",
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Banco de Crédito deve permanecer igual", response.get_data(as_text=True))
        conn = app.db()
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM recebimentos_invoice WHERE invoice_id=?", (invoice_id,)
        ).fetchone()[0], 1)
        conn.close()

    def test_import_rejects_conflicting_contract_metadata(self):
        import pandas as pd

        frame = pd.DataFrame([
            {"cnpj": "45.765.914/0001-81", "numero_invoice": "INV-CONFLICT", "tipo_documento": "COMMERCIAL INVOICE",
             "data_emissao": "01/08/2026", "moeda": "USD", "valor_invoice": "1000,00",
             "numero_contrato": "C-CONFLICT", "valor_alocado": "500,00", "taxa_cambio": "5,10"},
            {"cnpj": "45.765.914/0001-81", "numero_invoice": "INV-CONFLICT", "tipo_documento": "COMMERCIAL INVOICE",
             "data_emissao": "01/08/2026", "moeda": "USD", "valor_invoice": "1000,00",
             "numero_contrato": "C-CONFLICT", "valor_alocado": "500,00", "taxa_cambio": "5,20"},
        ])
        frame["competencia"] = "Agosto/2026"
        output = BytesIO()
        frame.to_excel(output, index=False)
        output.seek(0)
        response = self.client.post("/invoices/importar", data={"arquivo": (output, "conflict.xlsx")},
                                    content_type="multipart/form-data")
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM invoices WHERE numero_invoice='INV-CONFLICT'").fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM contratos WHERE numero_contrato='C-CONFLICT'").fetchone()[0], 0)
        conn.close()

    def test_import_rejects_conflicting_commercial_contract(self):
        import pandas as pd

        frame = pd.DataFrame([
            {"cnpj": "45.765.914/0001-81", "numero_invoice": "INV-COM-CONFLICT", "tipo_documento": "COMMERCIAL INVOICE",
             "data_emissao": "01/08/2026", "moeda": "USD", "valor_invoice": "1000,00",
             "contrato_comercial": "COM-A", "numero_contrato_cambio": "C-COM-CONFLICT",
             "valor_alocado": "500,00", "taxa_cambio": "5,10"},
            {"cnpj": "45.765.914/0001-81", "numero_invoice": "INV-COM-CONFLICT", "tipo_documento": "COMMERCIAL INVOICE",
             "data_emissao": "01/08/2026", "moeda": "USD", "valor_invoice": "1000,00",
             "contrato_comercial": "COM-B", "numero_contrato_cambio": "C-COM-CONFLICT",
             "valor_alocado": "500,00", "taxa_cambio": "5,10"},
        ])
        frame["competencia"] = "Agosto/2026"
        output = BytesIO()
        frame.to_excel(output, index=False)
        output.seek(0)
        response = self.client.post("/invoices/importar", data={"arquivo": (output, "commercial-conflict.xlsx")},
                                    content_type="multipart/form-data")
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM invoices WHERE numero_invoice='INV-COM-CONFLICT'"
        ).fetchone()[0], 0)
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM contratos WHERE numero_contrato='C-COM-CONFLICT'"
        ).fetchone()[0], 0)
        conn.close()

    def test_invoice_import_model_requires_competencia_and_removes_unused_columns(self):
        import pandas as pd

        response = self.client.get("/invoices/modelo")
        self.assertEqual(response.status_code, 200)
        model = pd.read_excel(BytesIO(response.data))
        self.assertIn("competencia", model.columns)
        self.assertNotIn("cliente_pais", model.columns)
        self.assertNotIn("valor_alocado", model.columns)
        rows = app.prepare_invoice_import_rows(pd.DataFrame([{
            "cnpj": "45.765.914/0001-81", "numero_invoice": "INV-MODEL-CHECK",
            "tipo_documento": "COMMERCIAL INVOICE", "competencia": "Agosto/2026",
            "valor_invoice": "100,00",
        }]), pd)
        self.assertNotIn("cliente_pais", rows[0])
        self.assertNotIn("valor_alocado", rows[0])

        frame = pd.DataFrame([{
            "cnpj": "45.765.914/0001-81", "numero_invoice": "INV-WITHOUT-COMPETENCE",
            "tipo_documento": "COMMERCIAL INVOICE", "data_emissao": "01/08/2026",
            "moeda": "USD", "valor_invoice": "100,00",
        }])
        output = BytesIO()
        frame.to_excel(output, index=False)
        output.seek(0)
        response = self.client.post("/invoices/importar", data={"arquivo": (output, "missing-competence.xlsx")},
                                    content_type="multipart/form-data")
        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as session:
            self.assertNotIn("invoice_import_stage", session)

    def test_invoice_import_recovers_leading_zero_from_numeric_cnpj(self):
        import pandas as pd

        conn = app.db()
        conn.execute("INSERT INTO empresas (razao_social, cnpj, apelido) VALUES (?,?,?)",
                     ("Empresa CNPJ com zero", "04171382000177", "Empresa Zero"))
        conn.execute("INSERT INTO competencias (empresa_id, descricao, data_inicial, data_final) VALUES (?,?,?,?)",
                     (2, "Agosto/2026", "2026-08-01", "2026-08-31"))
        conn.commit()
        conn.close()

        frame = pd.DataFrame([{
            # Simula a célula numérica do Excel: o zero inicial é removido.
            "empresa": 4171382000177, "invoice": "INV-CNPJ-ZERO",
            "tipo": "COMMERCIAL INVOICE", "competencia": "Agosto/2026",
            "moeda": "USD", "valor_moeda": "100,00",
        }])
        output = BytesIO()
        frame.to_excel(output, index=False)
        output.seek(0)
        response = self.client.post("/invoices/importar", data={"arquivo": (output, "numeric-cnpj.xlsx")},
                                    content_type="multipart/form-data")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("não está cadastrada", response.get_data(as_text=True))
        with self.client.session_transaction() as session:
            token = session["invoice_import_stage"]
        self.assertEqual(self.client.post("/invoices/importar/confirmar", data={"stage_token": token}).status_code, 302)
        conn = app.db()
        invoice = conn.execute("SELECT empresa_id FROM invoices WHERE numero_invoice='INV-CNPJ-ZERO'").fetchone()
        self.assertEqual(invoice["empresa_id"], 2)
        conn.close()

    def test_invoice_import_and_export_support_referenced_bank_without_breaking_legacy_rows(self):
        import pandas as pd

        frame = pd.DataFrame([{
            "cnpj": "45.765.914/0001-81", "numero_invoice": "INV-REFERENCE-IMPORT",
            "tipo_documento": "COMMERCIAL INVOICE", "competencia": "Agosto/2026",
            "moeda": "USD", "valor_moeda": "100,00", "banco_referenciado": "Banco Teste",
        }])
        output = BytesIO()
        frame.to_excel(output, index=False)
        output.seek(0)
        response = self.client.post(
            "/invoices/importar", data={"arquivo": (output, "reference.xlsx")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 200)
        with self.client.session_transaction() as session:
            token = session["invoice_import_stage"]
        self.assertEqual(
            self.client.post("/invoices/importar/confirmar", data={"stage_token": token}).status_code,
            302,
        )
        conn = app.db()
        imported = conn.execute(
            "SELECT banco_referenciado_id FROM invoices WHERE numero_invoice='INV-REFERENCE-IMPORT'"
        ).fetchone()
        self.assertEqual(imported["banco_referenciado_id"], 1)
        conn.close()

        existing_id = self._create_invoice(
            number="INV-REFERENCE-REIMPORT", value="200,00", banco_referenciado_id=1
        )
        legacy_frame = pd.DataFrame([{
            "cnpj": "45.765.914/0001-81", "numero_invoice": "INV-REFERENCE-REIMPORT",
            "tipo_documento": "COMMERCIAL INVOICE", "competencia": "Agosto/2026",
            "moeda": "USD", "valor_moeda": "200,00",
        }])
        legacy_output = BytesIO()
        legacy_frame.to_excel(legacy_output, index=False)
        legacy_output.seek(0)
        response = self.client.post(
            "/invoices/importar", data={"arquivo": (legacy_output, "legacy-reference.xlsx")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 200)
        with self.client.session_transaction() as session:
            token = session["invoice_import_stage"]
        self.assertEqual(
            self.client.post("/invoices/importar/confirmar", data={"stage_token": token}).status_code,
            302,
        )
        conn = app.db()
        self.assertEqual(conn.execute(
            "SELECT banco_referenciado_id FROM invoices WHERE id=?", (existing_id,)
        ).fetchone()[0], 1)
        conn.close()

        response = self.client.get("/invoices/exportar?numero_invoice=INV-REFERENCE-IMPORT")
        self.assertEqual(response.status_code, 200)
        exported = pd.read_excel(BytesIO(response.data), sheet_name="Invoices")
        self.assertIn("Banco referenciado", exported.columns)
        self.assertEqual(exported.iloc[0]["Banco referenciado"], "Banco Teste")

    def test_invoice_import_accepts_proforma_invoice_type(self):
        import pandas as pd

        frame = pd.DataFrame([{
            "empresa": "45.765.914/0001-81", "invoice": "INV-PROFORMA",
            "tipo": "PROFORMA INVOICE", "competencia": "Agosto/2026",
            "moeda": "USD", "valor_moeda": "100,00",
        }])
        frame.columns = app.normalize_invoice_import_columns(frame.columns)
        rows = app.prepare_invoice_import_rows(frame, pd)

        self.assertEqual(rows[0]["tipo_documento"], "PROFORMA")

    def test_invoice_import_associates_competence_by_period_and_preserves_invoice_only_model(self):
        import pandas as pd

        frame = pd.DataFrame([{
            "cnpj": "45.765.914/0001-81", "numero_invoice": "INV-COMPETENCE",
            "tipo_documento": "COMMERCIAL INVOICE", "cliente": "Cliente Teste",
            "contrato_comercial": "COM-001", "competencia": "08/2026",
            "data_emissao": "10/08/2026", "moeda": "USD", "valor_invoice": "100,00",
        }])
        output = BytesIO()
        frame.to_excel(output, index=False)
        output.seek(0)
        response = self.client.post("/invoices/importar", data={"arquivo": (output, "competence.xlsx")},
                                    content_type="multipart/form-data")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Competências não cadastradas", response.get_data(as_text=True))
        with self.client.session_transaction() as session:
            token = session["invoice_import_stage"]
        response = self.client.post("/invoices/importar/confirmar", data={"stage_token": token})
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        invoice = conn.execute("SELECT competencia_id FROM invoices WHERE numero_invoice='INV-COMPETENCE'").fetchone()
        self.assertEqual(invoice["competencia_id"], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM contratos").fetchone()[0], 0)
        conn.close()

    def test_invoice_import_suggests_new_competence_by_company_and_creates_on_confirmation(self):
        import pandas as pd

        frame = pd.DataFrame([{
            "cnpj": "45.765.914/0001-81", "numero_invoice": "INV-NEW-COMPETENCE",
            "tipo_documento": "COMMERCIAL INVOICE", "cliente": "Cliente Teste",
            "competencia": "Janeiro/2032", "data_emissao": "01/01/2032",
            "moeda": "USD", "valor_invoice": "100,00",
        }])
        output = BytesIO()
        frame.to_excel(output, index=False)
        output.seek(0)
        response = self.client.post("/invoices/importar", data={"arquivo": (output, "new-competence.xlsx")},
                                    content_type="multipart/form-data")
        self.assertEqual(response.status_code, 200)
        preview = response.get_data(as_text=True)
        self.assertIn("Competências não cadastradas", preview)
        self.assertIn("Janeiro/2032", preview)
        with self.client.session_transaction() as session:
            token = session["invoice_import_stage"]
        response = self.client.post("/invoices/importar/confirmar", data={
            "stage_token": token, "competencia_nova_confirmar_p1": "1",
            "competencia_nova_descricao_p1": "Janeiro/2032",
            "competencia_nova_data_inicial_p1": "01/01/2032",
            "competencia_nova_data_final_p1": "31/01/2032",
        })
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        competence = conn.execute(
            "SELECT id FROM competencias WHERE empresa_id=1 AND descricao='Janeiro/2032'"
        ).fetchone()
        self.assertIsNotNone(competence)
        self.assertEqual(conn.execute(
            "SELECT competencia_id FROM invoices WHERE numero_invoice='INV-NEW-COMPETENCE'"
        ).fetchone()[0], competence["id"])
        conn.close()

    def test_invoice_export_preserves_filters_and_includes_related_data(self):
        import pandas as pd

        invoice_id = self._create_invoice(number="INV-EXPORT-A", value="500,00")
        self._create_invoice(number="INV-EXPORT-B", value="250,00")
        conn = app.db()
        conn.execute("""
            INSERT INTO recebimentos_invoice
                (invoice_id,banco_credito_id,data_credito,moeda,valor_moeda,documento,observacao)
            VALUES (?,?,?,?,?,?,?)
        """, (invoice_id, 1, "2026-08-10", "USD", 500, "DOC-EXP", "Recebimento exportado"))
        conn.execute("""
            INSERT INTO contratos
                (numero_contrato,banco_liquidacao,data_fechamento,data_liquidacao,moeda,
                 taxa_cambio,valor_moeda,valor_reais,status,observacao)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, ("C-EXPORT", "Banco Teste", "2026-08-11", "2026-08-12", "USD",
              5.1, 500, 2550, "CONCLUIDO", "Contrato exportado"))
        contrato_id = conn.execute("SELECT id FROM contratos WHERE numero_contrato='C-EXPORT'").fetchone()[0]
        conn.execute("""
            INSERT INTO invoice_contrato_cambio(invoice_id,contrato_id,valor_alocado,observacao)
            VALUES (?,?,?,?)
        """, (invoice_id, contrato_id, 500, "Cambio exportado"))
        due_id = conn.execute("SELECT id FROM dues WHERE numero_due='DUE-001'").fetchone()[0]
        conn.execute("""
            INSERT INTO due_invoice(due_id,invoice_id,valor_vinculado,observacao)
            VALUES (?,?,?,?)
        """, (due_id, invoice_id, 500, "DU-E exportada"))
        conn.commit()
        conn.close()

        response = self.client.get("/invoices/exportar?numero_invoice=INV-EXPORT-A")
        self.assertEqual(response.status_code, 200)
        workbook = pd.ExcelFile(BytesIO(response.data))
        self.assertEqual(workbook.sheet_names, ["Invoices", "Recebimentos", "Cambios", "DU-Es"])
        invoices = pd.read_excel(BytesIO(response.data), sheet_name="Invoices")
        receipts = pd.read_excel(BytesIO(response.data), sheet_name="Recebimentos")
        changes = pd.read_excel(BytesIO(response.data), sheet_name="Cambios")
        dues = pd.read_excel(BytesIO(response.data), sheet_name="DU-Es")
        self.assertEqual(len(invoices), 1)
        self.assertEqual(invoices.iloc[0]["Numero da Invoice"], "INV-EXPORT-A")
        self.assertEqual(len(receipts), 1)
        self.assertEqual(len(changes), 1)
        self.assertEqual(len(dues), 1)
        self.assertIn("Saldo de recebimento", invoices.columns)
        self.assertIn("Banco de credito", receipts.columns)
        self.assertIn("Taxa de cambio", changes.columns)
        self.assertIn("Chave de acesso", dues.columns)

    def test_invoice_export_includes_new_closings_in_cambios(self):
        import pandas as pd

        invoice_id = self._create_invoice("INV-EXPORT-CLOSING", "100,00")
        self.client.post(f"/invoice/{invoice_id}/recebimentos", data={"valor_moeda": "100,00"})
        self.client.post(f"/invoice/{invoice_id}/fechamentos", data={
            "valor_moeda": "40,00", "data_fechamento": "20/08/2026",
        })
        response = self.client.get("/invoices/exportar?numero_invoice=INV-EXPORT-CLOSING")
        self.assertEqual(response.status_code, 200)
        changes = pd.read_excel(BytesIO(response.data), sheet_name="Cambios")
        invoices = pd.read_excel(BytesIO(response.data), sheet_name="Invoices")
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes.iloc[0]["Tipo de registro"], "Fechamento")
        self.assertEqual(changes.iloc[0]["Valor alocado"], 40)
        self.assertTrue(pd.isna(changes.iloc[0]["Numero do Contrato Cambio"]))
        self.assertEqual(len(invoices), 2)
        source_row = invoices[invoices["Numero da Invoice"] == "INV-EXPORT-CLOSING - 1"].iloc[0]
        remainder_row = invoices[invoices["Numero da Invoice"] == "INV-EXPORT-CLOSING - 2"].iloc[0]
        self.assertEqual(source_row["Status"], "AGUARDANDO CONTRATO")
        self.assertEqual(remainder_row["Status"], "RECEBIDO AGUARDANDO CAMBIO")

    def test_contract_list_filters_by_partial_contract_number(self):
        conn = app.db()
        conn.executemany(
            "INSERT INTO contratos(numero_contrato,cnpj,moeda,valor_moeda) VALUES (?,?,?,?)",
            [("C-ABC-001", "45765914000181", "USD", 100),
             ("C-ABC-002", "45765914000181", "USD", 200),
             ("C-XYZ-001", "45765914000181", "USD", 300)],
        )
        conn.commit()
        conn.close()

        response = self.client.get("/contratos?numero_contrato=ABC")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('name="numero_contrato"', html)
        self.assertIn('value="ABC"', html)
        self.assertIn("C-ABC-001", html)
        self.assertIn("C-ABC-002", html)
        self.assertNotIn("C-XYZ-001", html)

    def test_central_closing_groups_same_client_and_links_one_contract(self):
        invoice_a = self._create_invoice("INV-CENTRAL-A", "100,00")
        invoice_b = self._create_invoice("INV-CENTRAL-B", "50,00")
        for invoice_id, value in ((invoice_a, "100,00"), (invoice_b, "50,00")):
            response = self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
                "banco_credito_id": "1", "data_credito": "20/08/2026", "valor_moeda": value,
            })
            self.assertEqual(response.status_code, 302)

        response = self.client.post("/invoices/fechamentos/preview", data={
            "selected_ids": [str(invoice_a), str(invoice_b)],
            "data_fechamento": "2026-08-21", "data_liquidacao": "2026-08-25",
            "taxa_cambio": "5,1234", "banco_liquidacao_id": "1",
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn("Grupo 1", response.get_data(as_text=True))
        self.assertIn("5,1234", response.get_data(as_text=True))
        self.assertIn("768,51", response.get_data(as_text=True))
        conn = app.db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM fechamentos").fetchone()[0], 0)
        conn.close()

        response = self.client.post("/invoices/fechamentos", data={
            "selected_ids": [str(invoice_a), str(invoice_b)],
            "data_fechamento": "2026-08-21", "data_liquidacao": "2026-08-25",
            "taxa_cambio": "5,1234", "banco_liquidacao_id": "1", "numero_contrato_0": "CENTRAL-001",
        })
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        header = conn.execute("SELECT * FROM fechamentos").fetchone()
        self.assertIsNotNone(header)
        self.assertEqual(header["contrato_id"], 1)
        self.assertEqual(app.Decimal(str(header["taxa_cambio"])), app.Decimal("5.1234"))
        self.assertEqual(app.Decimal(str(header["valor_brl"])), app.Decimal("768.51"))
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM fechamentos_cambio WHERE fechamento_id=?", (header["id"],)).fetchone()[0], 2)
        contract = conn.execute("SELECT valor_moeda, taxa_cambio, valor_reais FROM contratos WHERE id=?", (header["contrato_id"],)).fetchone()
        self.assertEqual(app.Decimal(str(contract["valor_moeda"])), app.Decimal("150"))
        self.assertEqual(app.Decimal(str(contract["taxa_cambio"])), app.Decimal("5.1234"))
        self.assertEqual(app.Decimal(str(contract["valor_reais"])), app.Decimal("768.51"))
        self.assertEqual(app.invoice_summary(conn, invoice_a)["saldo_fechamentos"], app.Decimal("0"))
        self.assertEqual(
            conn.execute("SELECT status FROM invoices WHERE id=?", (invoice_a,)).fetchone()[0],
            app.INVOICE_STATUS_LIQUIDADA,
        )
        self.assertEqual(
            conn.execute("SELECT status FROM invoices WHERE id=?", (invoice_b,)).fetchone()[0],
            app.INVOICE_STATUS_LIQUIDADA,
        )
        conn.close()

        detail = self.client.get(f"/invoices/fechamentos/{header['id']}")
        self.assertEqual(detail.status_code, 200)
        self.assertIn("CENTRAL-001", detail.get_data(as_text=True))
        self.assertIn(f"Fechamento {header['id']}", detail.get_data(as_text=True))
        self.assertIn("5,1234", detail.get_data(as_text=True))
        self.assertIn("768,51", detail.get_data(as_text=True))
        listing = self.client.get("/invoices/fechamentos")
        self.assertEqual(listing.status_code, 200)
        self.assertIn("5,1234", listing.get_data(as_text=True))
        self.assertIn("768,51", listing.get_data(as_text=True))
        export = self.client.get(f"/invoices/exportar?numero_invoice=INV-CENTRAL-A")
        self.assertEqual(export.status_code, 200)
        contract_detail = self.client.get(f"/contrato/{header['contrato_id']}")
        self.assertEqual(contract_detail.status_code, 200)
        self.assertIn(f"href=\"/invoices/fechamentos/{header['id']}\"", contract_detail.get_data(as_text=True))

        response = self.client.post(f"/invoices/fechamentos/{header['id']}/editar", data={
            "data_fechamento": "2026-08-22", "data_liquidacao": "2026-08-26", "taxa_cambio": "5,2000", "banco_liquidacao_id": "1",
        })
        self.assertEqual(response.status_code, 302)
        response = self.client.post(f"/invoices/fechamentos/{header['id']}/excluir")
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM fechamentos").fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM contratos WHERE numero_contrato='CENTRAL-001'").fetchone()[0], 0)
        conn.close()

    def test_central_closing_without_contract_persists_awaiting_contract(self):
        invoice_id = self._create_invoice("INV-CENTRAL-PENDING", "100,00")
        self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "20/08/2026", "valor_moeda": "100,00",
        })
        response = self.client.post("/invoices/fechamentos", data={
            "selected_ids": [str(invoice_id)],
            "data_fechamento": "2026-08-21", "data_liquidacao": "2026-08-25",
            "taxa_cambio": "5,0000", "banco_liquidacao_id": "1",
        })
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        invoice = conn.execute(
            "SELECT status, status_manual FROM invoices WHERE id=?", (invoice_id,)
        ).fetchone()
        self.assertEqual(invoice["status"], app.INVOICE_STATUS_AGUARDANDO_CONTRATO)
        self.assertEqual(invoice["status_manual"], 0)
        self.assertEqual(conn.execute(
            "SELECT contrato_id FROM fechamentos WHERE id=(SELECT MAX(id) FROM fechamentos)"
        ).fetchone()[0], None)
        conn.close()

    def test_central_closing_accepts_different_amounts_and_splits_only_partial_invoice(self):
        partial_id = self._create_invoice("INV-CENTRAL-SPLIT", "1000,00")
        integral_id = self._create_invoice("INV-CENTRAL-INTEGRAL", "500,00")
        for invoice_id, value in ((partial_id, "1000,00"), (integral_id, "500,00")):
            self.assertEqual(self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
                "banco_credito_id": "1", "data_credito": "20/08/2026", "valor_moeda": value,
            }).status_code, 302)

        response = self.client.post("/invoices/fechamentos", data={
            "selected_ids": [str(partial_id), str(integral_id)],
            "valor_fechamento_%d" % partial_id: "400,00",
            "valor_fechamento_%d" % integral_id: "500,00",
            "data_fechamento": "2026-08-21", "data_liquidacao": "2026-08-25",
            "taxa_cambio": "5,0000", "banco_liquidacao_id": "1",
            "numero_contrato_grupo_1_USD": "CENTRAL-SPLIT-001",
        })
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        remainder_id = conn.execute(
            "SELECT invoice_id FROM invoice_desdobramentos WHERE invoice_anterior_id=?", (partial_id,)
        ).fetchone()[0]
        source = app.invoice_summary(conn, partial_id)
        remainder = app.invoice_summary(conn, remainder_id)
        integral = app.invoice_summary(conn, integral_id)
        self.assertEqual(source["numero_invoice"], "INV-CENTRAL-SPLIT - 1")
        self.assertEqual(source["status"], app.INVOICE_STATUS_LIQUIDADA)
        self.assertEqual(source["total_fechamentos"], app.Decimal("400"))
        self.assertEqual(remainder["valor_moeda"], app.Decimal("600"))
        self.assertEqual(remainder["total_fechamentos"], app.Decimal("0"))
        self.assertEqual(remainder["status"], app.INVOICE_STATUS_RECEBIDA_AGUARDANDO_CAMBIO)
        self.assertEqual(integral["numero_invoice"], "INV-CENTRAL-INTEGRAL")
        self.assertEqual(integral["status"], app.INVOICE_STATUS_LIQUIDADA)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM fechamentos").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM fechamentos_cambio").fetchone()[0], 2)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM recebimentos_invoice").fetchone()[0], 2)
        self.assertEqual(conn.execute(
            "SELECT valor_moeda FROM contratos WHERE numero_contrato='CENTRAL-SPLIT-001'"
        ).fetchone()[0], 900)
        conn.close()

    def test_partial_split_is_blocked_for_legacy_and_incomplete_received_invoice(self):
        conn = app.db()
        conn.execute("""
            INSERT INTO invoices
                (empresa_id,numero_invoice,tipo_documento,competencia_id,cliente_id,
                 data_emissao,moeda,valor_moeda,status,status_manual)
            VALUES (1,'INV-LEGACY-SPLIT','COMMERCIAL_INVOICE',1,1,'2026-08-01',
                    'USD',1000,'RECEBIDA_AGUARDANDO_CAMBIO',0)
        """)
        legacy_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute("""
            INSERT INTO recebimentos_invoice
                (invoice_id,banco_credito_id,data_credito,moeda,valor_moeda)
            VALUES (?,1,'2026-08-10','USD',1000)
        """, (legacy_id,))
        conn.commit()
        conn.close()
        response = self.client.post(f"/invoice/{legacy_id}/fechamentos", data={
            "valor_moeda": "400,00", "data_fechamento": "12/08/2026",
        }, follow_redirects=True)
        self.assertIn("legada", response.get_data(as_text=True))

        incomplete_id = self._create_invoice("INV-INCOMPLETE-SPLIT", "1000,00")
        self.client.post(f"/invoice/{incomplete_id}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "10/08/2026", "valor_moeda": "500,00",
        })
        response = self.client.post(f"/invoice/{incomplete_id}/fechamentos", data={
            "valor_moeda": "400,00", "data_fechamento": "12/08/2026",
        }, follow_redirects=True)
        self.assertIn("totalmente recebida", response.get_data(as_text=True))
        conn = app.db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM invoice_desdobramentos").fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM fechamentos_cambio").fetchone()[0], 0)
        self.assertEqual(conn.execute(
            "SELECT valor_moeda FROM invoices WHERE id=?", (incomplete_id,)
        ).fetchone()[0], 1000)
        conn.close()

    def test_partial_split_validates_amounts_and_rolls_back_name_collision(self):
        invoice_id = self._create_invoice("INV-SPLIT-COLLISION", "1000,00")
        self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "10/08/2026", "valor_moeda": "1000,00",
        })
        conn = app.db()
        conn.execute("""
            INSERT INTO invoices
                (empresa_id,numero_invoice,tipo_documento,competencia_id,cliente_id,
                 data_emissao,moeda,valor_moeda,status,status_manual)
            VALUES (1,'INV-SPLIT-COLLISION - 1','COMMERCIAL_INVOICE',1,1,
                    '2026-08-01','USD',100,'RECEBIDA_AGUARDANDO_CAMBIO',0)
        """)
        conn.commit()
        conn.close()
        response = self.client.post(f"/invoice/{invoice_id}/fechamentos", data={
            "valor_moeda": "400,00", "data_fechamento": "12/08/2026",
        }, follow_redirects=True)
        self.assertIn("renomear", response.get_data(as_text=True))
        conn = app.db()
        source = conn.execute(
            "SELECT numero_invoice, valor_moeda FROM invoices WHERE id=?", (invoice_id,)
        ).fetchone()
        self.assertEqual(source["numero_invoice"], "INV-SPLIT-COLLISION")
        self.assertEqual(source["valor_moeda"], 1000)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM fechamentos_cambio").fetchone()[0], 0)
        conn.close()

        conn = app.db()
        conn.execute("DELETE FROM invoices WHERE numero_invoice='INV-SPLIT-COLLISION - 1'")
        conn.commit()
        conn.close()
        for value in ("0,00", "1000,01"):
            response = self.client.post("/invoices/fechamentos", data={
                "selected_ids": [str(invoice_id)],
                "valor_fechamento_%d" % invoice_id: value,
                "data_fechamento": "2026-08-21", "data_liquidacao": "2026-08-25",
                "taxa_cambio": "5,0000", "banco_liquidacao_id": "1",
            }, follow_redirects=True)
            self.assertIn("valor", response.get_data(as_text=True))
        conn = app.db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM fechamentos").fetchone()[0], 0)
        conn.close()

    def test_central_closing_rejects_different_clients_without_partial_write(self):
        conn = app.db()
        conn.execute("INSERT INTO clientes (nome, pais) VALUES (?, ?)", ("Outro Cliente", "BR"))
        conn.commit()
        conn.close()
        invoice_a = self._create_invoice("INV-CENTRAL-CLIENT-A", "100,00")
        invoice_b = self._create_invoice("INV-CENTRAL-CLIENT-B", "100,00", client_id=2)
        for invoice_id in (invoice_a, invoice_b):
            self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
                "banco_credito_id": "1", "data_credito": "20/08/2026", "valor_moeda": "100,00",
            })
        response = self.client.post("/invoices/fechamentos", data={
            "selected_ids": [str(invoice_a), str(invoice_b)],
            "data_fechamento": "2026-08-21", "data_liquidacao": "2026-08-25",
            "taxa_cambio": "5,0000", "banco_liquidacao_id": "1", "numero_contrato_0": "SHOULD-ROLLBACK",
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("mesmo Cliente", response.get_data(as_text=True))
        conn = app.db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM fechamentos").fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM contratos WHERE numero_contrato='SHOULD-ROLLBACK'").fetchone()[0], 0)
        conn.close()

    def test_central_closing_splits_bank_and_rejects_duplicate_contract_atomically(self):
        conn = app.db()
        conn.execute("INSERT INTO contrapartes (nome) VALUES (?)", ("Banco Alternativo",))
        bank_two = conn.execute("SELECT id FROM contrapartes WHERE nome='Banco Alternativo'").fetchone()[0]
        conn.commit()
        conn.close()
        invoice_a = self._create_invoice("INV-CENTRAL-BANK-A", "100,00")
        invoice_b = self._create_invoice("INV-CENTRAL-BANK-B", "100,00")
        self.client.post(f"/invoice/{invoice_a}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "20/08/2026", "valor_moeda": "100,00",
        })
        self.client.post(f"/invoice/{invoice_b}/recebimentos", data={
            "banco_credito_id": str(bank_two), "data_credito": "20/08/2026", "valor_moeda": "100,00",
        })
        response = self.client.post("/invoices/fechamentos", data={
            "selected_ids": [str(invoice_a), str(invoice_b)],
            "data_fechamento": "2026-08-21", "data_liquidacao": "2026-08-25",
            "taxa_cambio": "5,0000", "banco_liquidacao_id": "1", "numero_contrato_0": "DUPLICADO", "numero_contrato_1": "DUPLICADO",
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("número DUPLICADO", response.get_data(as_text=True))
        conn = app.db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM fechamentos").fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM contratos WHERE numero_contrato='DUPLICADO'").fetchone()[0], 0)
        conn.close()

        response = self.client.post("/invoices/fechamentos/preview", data={
            "selected_ids": [str(invoice_a), str(invoice_b)],
            "data_fechamento": "2026-08-21", "data_liquidacao": "2026-08-25",
            "taxa_cambio": "5,0000", "banco_liquidacao_id": "1",
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn("Grupo 2", response.get_data(as_text=True))

    def test_central_closing_rejects_invoice_without_credit_bank(self):
        invoice_id = self._create_invoice("INV-CENTRAL-NO-BANK", "100,00")
        self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
            "banco_credito_id": "", "data_credito": "20/08/2026", "valor_moeda": "100,00",
        })
        response = self.client.post("/invoices/fechamentos", data={
            "selected_ids": [str(invoice_id)],
            "data_fechamento": "2026-08-21", "data_liquidacao": "2026-08-25",
            "taxa_cambio": "5,0000", "banco_liquidacao_id": "1",
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("exatamente um Banco de Crédito", response.get_data(as_text=True))
        conn = app.db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM fechamentos").fetchone()[0], 0)
        conn.close()

    def test_central_closing_requires_rate_with_at_most_four_decimals(self):
        invoice_id = self._create_invoice("INV-CENTRAL-RATE", "100,00")
        self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "20/08/2026", "valor_moeda": "100,00",
        })
        base = {
            "selected_ids": [str(invoice_id)],
            "data_fechamento": "2026-08-21", "data_liquidacao": "2026-08-25",
            "banco_liquidacao_id": "1",
        }
        response = self.client.post("/invoices/fechamentos", data=base, follow_redirects=True)
        self.assertIn("Taxa é obrigatória", response.get_data(as_text=True))
        response = self.client.post("/invoices/fechamentos", data={**base, "taxa_cambio": "5,12345"}, follow_redirects=True)
        self.assertIn("no máximo 4 casas", response.get_data(as_text=True))
        conn = app.db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM fechamentos").fetchone()[0], 0)
        conn.close()


if __name__ == "__main__":
    unittest.main()
