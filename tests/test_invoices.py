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

    def _create_invoice(self, number="INV-001", value="1000,00", commercial=None, client_id=1, currency="USD"):
        data = {
            "empresa_id": "1", "numero_invoice": number, "tipo_documento": "COMMERCIAL_INVOICE",
            "competencia_id": "1", "cliente_id": str(client_id), "data_emissao": "01/08/2026", "moeda": currency,
            "valor_moeda": value,
        }
        if commercial is not None:
            data["contrato_comercial"] = commercial
        response = self.client.post("/invoice/nova", data=data)
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        invoice = conn.execute("SELECT * FROM invoices WHERE numero_invoice=?", (number,)).fetchone()
        conn.close()
        return invoice["id"]

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
        for column in (
            "empresa", "invoice", "tipo", "banco_credito", "banco_liquidacao", "contrato_cambio",
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
            "SELECT id, status, status_manual, data_credito FROM invoices WHERE numero_invoice='INV-FULL-IMPORT'"
        ).fetchone()
        self.assertEqual(invoice["status"], app.INVOICE_STATUS_LIQUIDADA)
        self.assertEqual(invoice["status_manual"], 1)
        self.assertEqual(invoice["data_credito"], "2026-08-10")
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

    def test_schema_migrates_v1_to_v3_without_recreating_existing_contracts(self):
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
                INSERT INTO empresas(razao_social, cnpj, apelido) VALUES ('Empresa V1', '11111111000111', 'V1');
                INSERT INTO clientes(nome, pais) VALUES ('Cliente V1', 'BR');
                INSERT INTO dues(numero_due, moeda, valor_original) VALUES ('DUE-V1', 'USD', 10);
                INSERT INTO contratos(numero_contrato, moeda, valor_moeda) VALUES ('C-V1', 'USD', 10);
                INSERT INTO invoices(empresa_id, numero_invoice, tipo_documento, valor_moeda)
                    VALUES (1, 'INV-V1', 'COMMERCIAL_INVOICE', 10);
                PRAGMA user_version = 1;
            """)
            conn.commit()
            conn.close()

            app.init_db()
            conn = app.db()
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 3)
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


if __name__ == "__main__":
    unittest.main()
