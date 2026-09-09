import tempfile
import unittest
from io import BytesIO
from pathlib import Path

import pandas as pd

import app


class ContractLinkReportTests(unittest.TestCase):
    def setUp(self):
        self.previous_db = app.DB
        self.db_path = Path(tempfile.mktemp(prefix="duecontrol_contract_link_report_", suffix=".db"))
        app.DB = self.db_path
        app.init_db()
        conn = app.db()
        conn.execute(
            "INSERT INTO empresas (razao_social, cnpj, apelido, prioridade) VALUES (?,?,?,?)",
            ("Empresa Exportadora", "45.765.914/0001-81", "Exportadora", 1),
        )
        conn.execute(
            "INSERT INTO empresas (razao_social, cnpj, apelido, prioridade) VALUES (?,?,?,?)",
            ("Outra Empresa", "12.345.678/0001-90", "Outra", 2),
        )
        conn.execute(
            "INSERT INTO competencias (empresa_id, descricao, data_inicial, data_final) VALUES (?,?,?,?)",
            (1, "Agosto/2026", "2026-08-01", "2026-08-31"),
        )
        conn.execute("INSERT INTO clientes (nome, pais) VALUES (?,?)", ("Trading Um", "US"))
        conn.execute("INSERT INTO clientes (nome, pais) VALUES (?,?)", ("Trading Dois", "BR"))
        self.client_one = conn.execute(
            "SELECT id FROM clientes WHERE nome=?", ("Trading Um",)
        ).fetchone()[0]
        self.client_two = conn.execute(
            "SELECT id FROM clientes WHERE nome=?", ("Trading Dois",)
        ).fetchone()[0]
        contracts = [
            (
                "EXP-GOOD", "45765914000181", self.client_one, "Trading Um", "2026-08-20",
                150, app.STATUS_CONCLUIDO, app.CATEGORIA_CAMBIO_EXPORTACAO,
            ),
            (
                "EXP-OTHER", "12345678000190", self.client_two, "Trading Dois", "2026-07-01",
                75, app.STATUS_CONCLUIDO, app.CATEGORIA_CAMBIO_EXPORTACAO,
            ),
            (
                "PEND-001", "45765914000181", self.client_one, "Trading Um", "2026-08-20",
                40, app.STATUS_PENDENTE, app.CATEGORIA_CAMBIO_EXPORTACAO,
            ),
            (
                "FIN-001", "45765914000181", self.client_one, "Trading Um", "2026-08-20",
                60, app.STATUS_CONCLUIDO, app.CATEGORIA_CAMBIO_FINANCEIRO,
            ),
        ]
        self.contract_ids = {}
        for number, cnpj, client_id, client, contract_date, value, status, category in contracts:
            conn.execute(
                """
                INSERT INTO contratos
                    (numero_contrato, banco_credito, banco_liquidacao, data_contrato,
                     cnpj, cliente, cliente_id, moeda, valor_moeda, status,
                     categoria_cambio, competencia_id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    number, "Banco Crédito", "Banco Liquidação", contract_date,
                    cnpj, client, client_id, "USD", value, status, category, 1,
                ),
            )
            self.contract_ids[number] = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        dues = [
            ("DUE-GOOD-1", "12345678901234", "45765914000181", "100"),
            ("DUE-GOOD-2", "22345678901234", "45765914000181", "50"),
            ("DUE-OTHER", "32345678901234", "12345678000190", "75"),
            ("DUE-PENDING", "42345678901234", "45765914000181", "40"),
            ("DUE-FIN", "52345678901234", "45765914000181", "60"),
        ]
        due_ids = {}
        for number, key, cnpj, value in dues:
            conn.execute(
                "INSERT INTO dues (numero_due, chave_acesso, cnpj, moeda, valor_original) VALUES (?,?,?,?,?)",
                (number, key, cnpj, "USD", float(value)),
            )
            due_ids[number] = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        self._link(conn, "EXP-GOOD", "DUE-GOOD-1", due_ids, 100, with_movement=True)
        self._link(conn, "EXP-GOOD", "DUE-GOOD-2", due_ids, 50, with_movement=False)
        self._link(conn, "EXP-OTHER", "DUE-OTHER", due_ids, 75, with_movement=True)
        self._link(conn, "PEND-001", "DUE-PENDING", due_ids, 40, with_movement=True)
        self._link(conn, "FIN-001", "DUE-FIN", due_ids, 60, with_movement=True)
        conn.commit()
        conn.close()
        self.client = app.app.test_client()

    def tearDown(self):
        app.DB = self.previous_db
        self.db_path.unlink(missing_ok=True)

    def _link(self, conn, contract_number, due_number, due_ids, amount, with_movement):
        contract_id = self.contract_ids[contract_number]
        due_id = due_ids[due_number]
        conn.execute(
            "INSERT INTO due_contratos (due_id, contrato_id, valor_vinculado) VALUES (?,?,?)",
            (due_id, contract_id, amount),
        )
        link_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        if with_movement:
            conn.execute(
                """
                INSERT INTO due_movimentacoes
                    (due_id, contrato_id, due_contrato_id, data_movimentacao,
                     tipo, documento, valor)
                VALUES (?,?,?,?,?,?,?)
                """,
                (due_id, contract_id, link_id, "2026-08-20", "VINCULACAO", contract_number, amount),
            )
            if contract_number == "EXP-GOOD" and due_number == "DUE-GOOD-1":
                conn.execute(
                    """
                    INSERT INTO due_movimentacoes
                        (due_id, contrato_id, due_contrato_id, data_movimentacao,
                         tipo, documento, valor)
                    VALUES (?,?,?,?,?,?,?)
                    """,
                    (due_id, contract_id, link_id, "2026-08-25", "VINCULACAO", contract_number, 0),
                )

    def test_report_filters_contracts_and_maps_each_due_link(self):
        response = self.client.get("/contratos/relatorio-vinculos")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)

        for column in app.CONTRACT_LINK_REPORT_COLUMNS:
            self.assertIn(f"<th>{column}</th>", html)
        self.assertIn("Relatório de vínculos", html)
        self.assertIn("window.print()", html)
        self.assertIn("Exportadora", html)
        self.assertIn("45.765.914/0001-81", html)
        self.assertIn("Banco Liquidação", html)
        self.assertIn("Trading Um", html)
        self.assertIn("DUE-GOOD-1", html)
        self.assertIn("DUE-GOOD-2", html)
        self.assertIn("25/08/2026", html)
        self.assertIn("12345678901234", html)
        self.assertIn("100,00", html)
        self.assertIn("50,00", html)
        css_response = self.client.get("/static/style.css")
        css = css_response.get_data(as_text=True)
        css_response.close()
        self.assertIn("@page contract-link-report{size:A4 landscape", css)
        self.assertNotIn("PEND-001", html)
        self.assertNotIn("FIN-001", html)
        self.assertIn("EXP-OTHER", html)

    def test_report_reuses_filters_and_excel_has_expected_columns(self):
        filtered = self.client.get(
            "/contratos/relatorio-vinculos?numero_contrato=EXP-OTHER"
        )
        self.assertEqual(filtered.status_code, 200)
        filtered_html = filtered.get_data(as_text=True)
        self.assertIn("EXP-OTHER", filtered_html)
        self.assertNotIn("EXP-GOOD", filtered_html)

        response = self.client.get(
            "/contratos/relatorio-vinculos/exportar?numero_contrato=EXP-GOOD"
        )
        self.assertEqual(response.status_code, 200)
        workbook = pd.ExcelFile(BytesIO(response.data))
        self.assertEqual(workbook.sheet_names, ["Vínculos"])
        frame = pd.read_excel(BytesIO(response.data), sheet_name="Vínculos")
        self.assertEqual(frame.columns.tolist(), list(app.CONTRACT_LINK_REPORT_COLUMNS))
        self.assertEqual(len(frame), 2)
        self.assertEqual(frame.iloc[0]["CONTRATO"], "EXP-GOOD")
        self.assertEqual(frame.iloc[0]["DATA LANÇAMENTO"].strftime("%d/%m/%Y"), "25/08/2026")
        self.assertEqual(frame.iloc[0]["EMPRESA"], "Exportadora")
        self.assertEqual(frame.iloc[0]["CNPJ"], "45.765.914/0001-81")
        self.assertEqual(frame.iloc[0]["VALOR UTILIZADO"], 100)
        self.assertEqual(frame.iloc[1]["VALOR UTILIZADO"], 50)

    def test_report_period_filter_can_return_empty_result(self):
        response = self.client.get(
            "/contratos/relatorio-vinculos?data_de=01/09/2026"
        )
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Nenhum vínculo encontrado", html)
        self.assertNotIn("EXP-GOOD", html)

    def test_contract_list_exposes_link_report_button(self):
        response = self.client.get("/contratos?numero_contrato=EXP-GOOD")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Relat&oacute;rio de v&iacute;nculos", html)
        self.assertIn("/contratos/relatorio-vinculos", html)


if __name__ == "__main__":
    unittest.main()
