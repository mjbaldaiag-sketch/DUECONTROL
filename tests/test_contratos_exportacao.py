import tempfile
import unittest
from io import BytesIO
from pathlib import Path

import pandas as pd

import app


class ContractExportInvoiceTests(unittest.TestCase):
    def setUp(self):
        self.previous_db = app.DB
        self.db_path = Path(tempfile.mktemp(prefix="duecontrol_contract_export_", suffix=".db"))
        app.DB = self.db_path
        app.init_db()
        conn = app.db()
        conn.execute(
            "INSERT INTO empresas (razao_social, cnpj, apelido, prioridade) VALUES (?,?,?,?)",
            ("Empresa Exportadora", "12345678000190", "Exportadora", 1),
        )
        conn.execute(
            "INSERT INTO clientes (nome, pais) VALUES (?,?)",
            ("Trading Teste", "US"),
        )
        conn.executemany(
            "INSERT INTO contrapartes (nome) VALUES (?)",
            [("Banco Crédito",), ("Banco Liquidação",)],
        )
        conn.execute(
            """
            INSERT INTO contratos
                (numero_contrato, cnpj, cliente, cliente_id, moeda, valor_moeda,
                 data_contrato, status, categoria_cambio)
            VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                "C-OPEN", "12345678000190", "Trading Teste", 1, "USD", 200,
                "2026-09-01", app.STATUS_PENDENTE, app.CATEGORIA_CAMBIO_EXPORTACAO,
            ),
        )
        self.open_contract_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            """
            INSERT INTO contratos
                (numero_contrato, cnpj, cliente, cliente_id, moeda, valor_moeda,
                 data_contrato, status, categoria_cambio)
            VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                "C-ZERO", "12345678000190", "Trading Teste", 1, "USD", 50,
                "2026-09-02", app.STATUS_CONCLUIDO, app.CATEGORIA_CAMBIO_EXPORTACAO,
            ),
        )
        self.zero_contract_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            """
            INSERT INTO contratos
                (numero_contrato, cnpj, cliente, cliente_id, moeda, valor_moeda,
                 data_contrato, status, categoria_cambio)
            VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                "C-NO-INVOICE", "12345678000190", "Trading Teste", 1, "USD", 10,
                "2026-09-03", app.STATUS_PENDENTE, app.CATEGORIA_CAMBIO_EXPORTACAO,
            ),
        )
        self.no_invoice_contract_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        self.direct_invoice_id = self._insert_invoice(conn, "INV-DIRECT", 100)
        legacy_invoice_id = self._insert_invoice(conn, "INV-LEGACY", 25)
        central_a_id = self._insert_invoice(conn, "INV-CENTRAL-A", 50)
        central_b_id = self._insert_invoice(conn, "INV-CENTRAL-B", 50)
        zero_invoice_id = self._insert_invoice(conn, "INV-ZERO", 50)

        conn.execute(
            """
            INSERT INTO invoice_contrato_cambio
                (invoice_id, contrato_id, valor_alocado, observacao)
            VALUES (?,?,?,?)
            """,
            (self.direct_invoice_id, self.open_contract_id, 100, "Alocação direta"),
        )
        conn.execute(
            """
            INSERT INTO fechamentos_cambio
                (invoice_id, contrato_id, moeda, valor_moeda, data_fechamento, observacao)
            VALUES (?,?,?,?,?,?)
            """,
            (
                legacy_invoice_id, self.open_contract_id, "USD", 25,
                "2026-09-03", "Fechamento legado",
            ),
        )
        central_id = self._insert_central_header(conn, self.open_contract_id, "2026-09-04")
        for invoice_id in (central_a_id, central_b_id):
            conn.execute(
                """
                INSERT INTO fechamentos_cambio
                    (invoice_id, contrato_id, moeda, valor_moeda, data_fechamento,
                     observacao, fechamento_id)
                VALUES (?,?,?,?,?,?,?)
                """,
                (
                    invoice_id, None, "USD", 50, "2026-09-04",
                    "Fechamento central", central_id,
                ),
            )

        zero_central_id = self._insert_central_header(conn, self.zero_contract_id, "2026-09-05")
        conn.execute(
            """
            INSERT INTO fechamentos_cambio
                (invoice_id, contrato_id, moeda, valor_moeda, data_fechamento,
                 observacao, fechamento_id)
            VALUES (?,?,?,?,?,?,?)
            """,
            (
                zero_invoice_id, None, "USD", 50, "2026-09-05",
                "Fechamento do contrato zerado", zero_central_id,
            ),
        )

        conn.execute(
            """
            INSERT INTO dues
                (numero_due, chave_acesso, cnpj, cliente, cliente_id, moeda, valor_original)
            VALUES (?,?,?,?,?,?,?)
            """,
            (
                "DUE-ZERO", "12345678901234", "12345678000190",
                "Trading Teste", 1, "USD", 50,
            ),
        )
        due_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO due_contratos (due_id, contrato_id, valor_vinculado) VALUES (?,?,?)",
            (due_id, self.zero_contract_id, 50),
        )
        due_contract_link_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            """
            INSERT INTO due_movimentacoes
                (due_id, contrato_id, due_contrato_id, data_movimentacao, tipo, valor)
            VALUES (?,?,?,?,?,?)
            """,
            (due_id, self.zero_contract_id, due_contract_link_id, "2026-09-05", "VINCULACAO", 50),
        )
        conn.commit()
        conn.close()
        self.client = app.app.test_client()

    def tearDown(self):
        app.DB = self.previous_db
        self.db_path.unlink(missing_ok=True)

    def _insert_invoice(self, conn, number, value):
        conn.execute(
            """
            INSERT INTO invoices
                (empresa_id, numero_invoice, tipo_documento, cliente_id,
                 data_emissao, moeda, valor_moeda)
            VALUES (?,?,?,?,?,?,?)
            """,
            (1, number, "COMMERCIAL_INVOICE", 1, "2026-09-01", "USD", value),
        )
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    def _insert_central_header(self, conn, contract_id, closing_date):
        conn.execute(
            """
            INSERT INTO fechamentos
                (cliente_id, banco_credito_id, moeda, categoria_cambio,
                 data_fechamento, data_liquidacao, banco_liquidacao_id,
                 taxa_cambio, valor_brl, contrato_id)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                1, 1, "USD", app.CATEGORIA_CAMBIO_EXPORTACAO,
                closing_date, closing_date, 2, 5, 250, contract_id,
            ),
        )
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    def _workbook(self, query_string=""):
        response = self.client.get(f"/contratos/exportar{query_string}")
        self.assertEqual(response.status_code, 200)
        return response.data

    def test_export_includes_invoice_summary_and_detailed_links(self):
        workbook_data = self._workbook()
        workbook = pd.ExcelFile(BytesIO(workbook_data))

        self.assertEqual(workbook.sheet_names, ["Contratos", "Invoices", "Vínculos"])
        contracts = pd.read_excel(BytesIO(workbook_data), sheet_name="Contratos")
        invoices = pd.read_excel(BytesIO(workbook_data), sheet_name="Invoices")

        self.assertIn("Invoices relacionadas", contracts.columns)
        self.assertEqual(
            contracts.loc[
                contracts["Número do contrato"] == "C-OPEN", "Invoices relacionadas"
            ].iloc[0],
            "INV-DIRECT | INV-LEGACY | INV-CENTRAL-A | INV-CENTRAL-B",
        )
        self.assertEqual(
            contracts.loc[
                contracts["Número do contrato"] == "C-ZERO", "Invoices relacionadas"
            ].iloc[0],
            "INV-ZERO",
        )
        no_invoice = contracts.loc[
            contracts["Número do contrato"] == "C-NO-INVOICE", "Invoices relacionadas"
        ].iloc[0]
        self.assertTrue(pd.isna(no_invoice))
        zero_balance = contracts.loc[
            contracts["Número do contrato"] == "C-ZERO", "Saldo disponível"
        ].iloc[0]
        self.assertEqual(float(zero_balance), 0)

        self.assertEqual(len(invoices), 5)
        self.assertEqual(set(invoices["Invoice"]), {
            "INV-DIRECT", "INV-LEGACY", "INV-CENTRAL-A", "INV-CENTRAL-B", "INV-ZERO",
        })
        self.assertEqual(
            set(invoices.loc[invoices["Número do contrato"] == "C-OPEN", "Origem do vínculo"]),
            {"Vínculo direto", "Fechamento legado", "Fechamento centralizado"},
        )
        self.assertEqual(
            set(invoices.loc[invoices["Número do contrato"] == "C-OPEN", "Valor vinculado"]),
            {100, 50, 25},
        )
        self.assertEqual(
            int(invoices.loc[invoices["Invoice"] == "INV-ZERO", "Fechamento ID"].iloc[0]),
            2,
        )

    def test_export_filters_contracts_and_related_invoices_together(self):
        workbook_data = self._workbook("?numero_contrato=C-OPEN")
        contracts = pd.read_excel(BytesIO(workbook_data), sheet_name="Contratos")
        invoices = pd.read_excel(BytesIO(workbook_data), sheet_name="Invoices")

        self.assertEqual(set(contracts["Número do contrato"]), {"C-OPEN"})
        self.assertEqual(
            set(invoices["Número do contrato"]), {"C-OPEN"}
        )
        self.assertNotIn("INV-ZERO", set(invoices["Invoice"]))
        self.assertNotIn("C-ZERO", set(contracts["Número do contrato"]))


if __name__ == "__main__":
    unittest.main()
