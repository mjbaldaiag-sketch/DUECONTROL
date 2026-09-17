import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

import app


class ContractBalanceAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.previous_db = app.DB
        self.db_path = Path(tempfile.mktemp(prefix="duecontrol_balance_analysis_", suffix=".db"))
        app.DB = self.db_path
        app.init_db()
        conn = app.db()
        conn.execute(
            "INSERT INTO empresas (razao_social, cnpj, apelido, prioridade) VALUES (?,?,?,?)",
            ("Empresa Principal", "11222333000181", "Principal", 1),
        )
        conn.execute(
            "INSERT INTO empresas (razao_social, cnpj, apelido, prioridade) VALUES (?,?,?,?)",
            ("Empresa Secundária", "44555666000182", "Secundária", 2),
        )
        conn.execute("INSERT INTO clientes (nome, pais) VALUES (?,?)", ("Cliente Um", "BR"))
        conn.execute("INSERT INTO clientes (nome, pais) VALUES (?,?)", ("Cliente Dois", "US"))
        self.client_one = conn.execute(
            "SELECT id FROM clientes WHERE nome=?", ("Cliente Um",)
        ).fetchone()[0]
        self.client_two = conn.execute(
            "SELECT id FROM clientes WHERE nome=?", ("Cliente Dois",)
        ).fetchone()[0]

        self.due_main = self._due(
            conn, "DUE-MAIN", "11222333000181", self.client_one, "Cliente Um", "USD", 100
        )
        self.due_other_client = self._due(
            conn, "DUE-OTHER-CLIENT", "11222333000181", self.client_two, "Cliente Dois", "USD", 100
        )
        self.due_eur = self._due(
            conn, "DUE-EUR", "11222333000181", self.client_one, "Cliente Um", "EUR", 100
        )
        self.due_other_company = self._due(
            conn, "DUE-OTHER-COMPANY", "44555666000182", self.client_one, "Cliente Um", "USD", 100
        )
        self.due_linked = self._due(
            conn, "DUE-LINKED", "11222333000181", self.client_one, "Cliente Um", "USD", 100
        )
        self.due_zero = self._due(
            conn, "DUE-ZERO", "11222333000181", self.client_one, "Cliente Um", "USD", 100
        )
        self.due_legacy = self._due(
            conn, "DUE-LEGACY", "11222333000181", None, "Legacy Trading", "USD", 30
        )
        self.due_missing_id = self._due(
            conn, "DUE-MISSING-ID", "11222333000181", None, "Cliente Um", "USD", 30
        )

        self.contract_old = self._contract(
            conn, "C-OLD", "11222333000181", self.client_one, "Cliente Um", "USD", 50, "2026-01-01"
        )
        self.contract_new = self._contract(
            conn, "C-NEW", "11222333000181", self.client_one, "Cliente Um", "USD", 80, "2026-02-01"
        )
        self.contract_partial = self._contract(
            conn, "C-PARTIAL", "11222333000181", self.client_one, "Cliente Um", "USD", 70, "2026-03-01"
        )
        self.contract_eur = self._contract(
            conn, "C-EUR", "11222333000181", self.client_one, "Cliente Um", "USD", 90, "2026-04-01"
        )
        self.contract_linked = self._contract(
            conn, "C-LINKED", "11222333000181", self.client_one, "Cliente Um", "USD", 100, "2026-05-01"
        )
        self.contract_financial = self._contract(
            conn, "C-FINANCIAL", "11222333000181", self.client_one, "Cliente Um", "USD", 100, "2026-06-01",
            category=app.CATEGORIA_CAMBIO_FINANCEIRO,
        )
        self.contract_manual_zero = self._contract(
            conn, "C-MANUAL-ZERO", "11222333000181", self.client_one, "Cliente Um", "USD", 100, "2026-07-01",
            saldo_zerado_manual=1,
        )
        self.contract_legacy = self._contract(
            conn, "C-LEGACY", "11222333000181", None, " legacy  trading ", "USD", 30, "2026-08-01"
        )
        self.contract_missing_id = self._contract(
            conn, "C-MISSING-ID", "11222333000181", self.client_one, "Cliente Um", "USD", 30, "2026-09-01"
        )

        self._movement(conn, self.due_main, None, "UTILIZACAO", 20)
        self._movement(conn, self.due_zero, None, "UTILIZACAO", 100)
        self._movement(conn, self.due_other_client, self.contract_partial, "VINCULACAO", 10)
        self._link(conn, self.due_linked, self.contract_linked, 20)
        conn.commit()
        conn.close()
        self.client = app.app.test_client()

    def tearDown(self):
        app.DB = self.previous_db
        self.db_path.unlink(missing_ok=True)

    def _due(self, conn, number, cnpj, client_id, client, currency, value):
        conn.execute(
            """
            INSERT INTO dues
                (numero_due, cnpj, cliente, cliente_id, moeda, valor_original, status)
            VALUES (?,?,?,?,?,?,?)
            """,
            (number, cnpj, client, client_id, currency, value, app.STATUS_PENDENTE),
        )
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    def _contract(
        self, conn, number, cnpj, client_id, client, currency, value, contract_date,
        category=app.CATEGORIA_CAMBIO_EXPORTACAO, saldo_zerado_manual=0,
    ):
        conn.execute(
            """
            INSERT INTO contratos
                (numero_contrato, cnpj, cliente, cliente_id, moeda, valor_moeda,
                 data_contrato, status, categoria_cambio, saldo_zerado_manual)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                number, cnpj, client, client_id, currency, value, contract_date,
                app.STATUS_PENDENTE, category, saldo_zerado_manual,
            ),
        )
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    def _movement(self, conn, due_id, contract_id, kind, value):
        conn.execute(
            """
            INSERT INTO due_movimentacoes
                (due_id, contrato_id, data_movimentacao, tipo, valor)
            VALUES (?,?,?,?,?)
            """,
            (due_id, contract_id, "2026-09-01", kind, value),
        )

    def _link(self, conn, due_id, contract_id, value):
        conn.execute(
            "INSERT INTO due_contratos (due_id, contrato_id, valor_vinculado) VALUES (?,?,?)",
            (due_id, contract_id, value),
        )
        link_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            """
            INSERT INTO due_movimentacoes
                (due_id, contrato_id, due_contrato_id, data_movimentacao, tipo, valor)
            VALUES (?,?,?,?,?,?)
            """,
            (due_id, contract_id, link_id, "2026-09-01", "VINCULACAO", value),
        )

    def _suggestions(self):
        conn = app.db()
        try:
            with app.app.test_request_context("/contratos/analise-saldos"):
                app.g.global_context = {}
                return app.consulta_analise_saldos(conn)
        finally:
            conn.close()

    def test_analysis_applies_matching_rules_and_effective_balances(self):
        suggestions = self._suggestions()
        groups = {
            (item["cliente"], item["moeda"]): item
            for item in suggestions
        }

        self.assertEqual(len(suggestions), 2)
        self.assertIn(("Cliente Um", "USD"), groups)
        self.assertIn(("Legacy Trading", "USD"), groups)
        self.assertNotIn(("Cliente Dois", "USD"), groups)
        self.assertNotIn(("Cliente Um", "EUR"), groups)

        main = groups[("Cliente Um", "USD")]
        self.assertEqual(main["saldo_due"], Decimal("160"))
        self.assertEqual(main["saldo_contrato"], Decimal("390"))
        self.assertEqual(main["saldo_compativel"], Decimal("160"))
        self.assertNotIn("numero_due", main)
        self.assertNotIn("numero_contrato", main)

        legacy = groups[("Legacy Trading", "USD")]
        self.assertEqual(legacy["saldo_due"], Decimal("30"))
        self.assertEqual(legacy["saldo_contrato"], Decimal("30"))
        self.assertEqual(legacy["saldo_compativel"], Decimal("30"))

    def test_analysis_orders_by_compatible_balance_then_oldest_contract(self):
        suggestions = self._suggestions()
        self.assertEqual(
            [(item["cliente"], item["saldo_compativel"]) for item in suggestions],
            [("Cliente Um", Decimal("160")), ("Legacy Trading", Decimal("30"))],
        )

    def test_analysis_route_is_paginated_read_only_and_has_no_link_action(self):
        conn = app.db()
        before = conn.execute("SELECT COUNT(*) FROM due_contratos").fetchone()[0]
        conn.close()

        response = self.client.get("/contratos/analise-saldos")
        self.assertEqual(response.status_code, 200)
        html_bytes = response.data
        self.assertIn(b"An\xc3\xa1lise de saldos", html_bytes)
        self.assertIn(b"Imprimir relat\xc3\xb3rio", html_bytes)
        self.assertIn(b"Saldo a vincular", html_bytes)
        self.assertIn(b"Saldo contratos em aberto", html_bytes)
        self.assertIn(b"Saldo contratos fora da tabela", html_bytes)
        self.assertIn(b"Total geral dos grupos compat\xc3\xadveis", html_bytes)
        self.assertIn(b"saldo-analysis-table", html_bytes)
        self.assertIn(b"window.print()", html_bytes)
        self.assertNotIn(b"/vincular", html_bytes)
        self.assertNotIn(b"Vincular DUE", html_bytes)
        response = self.client.post("/contratos/analise-saldos")
        self.assertEqual(response.status_code, 405)
        conn = app.db()
        after = conn.execute("SELECT COUNT(*) FROM due_contratos").fetchone()[0]
        conn.close()
        self.assertEqual(before, after)

    def test_contract_list_exposes_analysis_button_and_pagination_works(self):
        conn = app.db()
        for index in range(21):
            client_name = f"Page Client {index + 1:02}"
            conn.execute(
                "INSERT INTO clientes (nome, pais) VALUES (?,?)",
                (client_name, "BR"),
            )
            client_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            self._due(
                conn, f"DUE-PAGE-{index + 1:02}", "11222333000181", client_id,
                client_name, "USD", 1,
            )
            self._contract(
                conn, f"C-PAGE-{index + 1:02}", "11222333000181", client_id,
                client_name, "USD", 1, f"2027-01-{index + 1:02}",
            )
        conn.commit()
        conn.close()

        response = self.client.get("/contratos")
        self.assertEqual(response.status_code, 200)
        self.assertIn("/contratos/analise-saldos", response.get_data(as_text=True))

        first_page = self.client.get("/contratos/analise-saldos?page=1")
        second_page = self.client.get("/contratos/analise-saldos?page=2")
        third_page = self.client.get("/contratos/analise-saldos?page=3")
        self.assertEqual(first_page.status_code, 200)
        self.assertEqual(second_page.status_code, 200)
        self.assertEqual(third_page.status_code, 200)
        self.assertIn(b'class="pagination erp-pagination"', first_page.data)
        self.assertIn(b"Page Client 21", second_page.data)
        self.assertNotIn(b"C-PAGE-21", first_page.data)


if __name__ == "__main__":
    unittest.main()
