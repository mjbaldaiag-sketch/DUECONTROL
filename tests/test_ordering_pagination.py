import re
import tempfile
import unittest
from html import unescape
from pathlib import Path

import app


class OrderingPaginationTests(unittest.TestCase):
    def setUp(self):
        self.previous_db = app.DB
        self.db_path = Path(tempfile.mktemp(prefix="duecontrol_ordering_", suffix=".db"))
        app.DB = self.db_path
        app.init_db()
        conn = app.db()
        conn.execute(
            "INSERT INTO empresas (razao_social, cnpj, apelido, prioridade) VALUES (?,?,?,?)",
            ("Empresa Teste", "12345678901234", "Teste", 1),
        )
        conn.execute("INSERT INTO clientes (nome, pais) VALUES (?,?)", ("Cliente Teste", "BR"))
        conn.execute("INSERT INTO contrapartes (nome) VALUES (?)", ("Banco Teste",))
        conn.commit()
        conn.close()
        self.client = app.app.test_client()

    def tearDown(self):
        app.DB = self.previous_db
        self.db_path.unlink(missing_ok=True)

    def _create_rows(self, count=45):
        conn = app.db()
        conn.executemany(
            """INSERT INTO invoices
               (empresa_id, numero_invoice, tipo_documento, cliente_id,
                data_emissao, moeda, valor_moeda)
               VALUES (?,?,?,?,?,?,?)""",
            [
                (1, f"INV-{index:03}", "COMMERCIAL_INVOICE", 1,
                 "2026-01-01", "USD", index)
                for index in range(count, 0, -1)
            ],
        )
        conn.executemany(
            """INSERT INTO contratos
               (numero_contrato, cnpj, cliente_id, cliente, moeda, valor_moeda)
               VALUES (?,?,?,?,?,?)""",
            [
                (f"CON-{index:03}", "12345678901234", 1, "Cliente Teste", "USD", index)
                for index in range(count, 0, -1)
            ],
        )
        conn.commit()
        conn.close()

    @staticmethod
    def _row_values(html, prefix):
        return re.findall(rf'data-sort-value="({prefix}-\d+)"', html)

    def test_invoice_and_contract_sorting_crosses_page_boundaries(self):
        self._create_rows()

        invoice_values = []
        contract_values = []
        for page in range(1, 4):
            invoice_html = self.client.get(
                f"/invoices?sort=numero_invoice&direction=asc&page={page}"
            ).get_data(as_text=True)
            contract_html = self.client.get(
                f"/contratos?sort=numero_contrato&direction=asc&page={page}"
            ).get_data(as_text=True)
            invoice_values.extend(self._row_values(invoice_html, "INV"))
            contract_values.extend(self._row_values(contract_html, "CON"))

        self.assertEqual(invoice_values, [f"INV-{index:03}" for index in range(1, 46)])
        self.assertEqual(contract_values, [f"CON-{index:03}" for index in range(1, 46)])

        invoice_values = []
        contract_values = []
        for page in range(1, 4):
            invoice_html = self.client.get(
                f"/invoices?sort=numero_invoice&direction=desc&page={page}"
            ).get_data(as_text=True)
            contract_html = self.client.get(
                f"/contratos?sort=numero_contrato&direction=desc&page={page}"
            ).get_data(as_text=True)
            invoice_values.extend(self._row_values(invoice_html, "INV"))
            contract_values.extend(self._row_values(contract_html, "CON"))

        self.assertEqual(invoice_values, [f"INV-{index:03}" for index in range(45, 0, -1)])
        self.assertEqual(contract_values, [f"CON-{index:03}" for index in range(45, 0, -1)])

    def test_closing_listing_and_navigation_share_selected_order(self):
        conn = app.db()
        invoice_ids = []
        for index in range(25):
            invoice = conn.execute(
                """INSERT INTO invoices
                   (empresa_id, numero_invoice, tipo_documento, cliente_id,
                    data_emissao, moeda, valor_moeda)
                   VALUES (?,?,?,?,?,?,?)""",
                (1, f"CLOSE-{index:03}", "COMMERCIAL_INVOICE", 1,
                 "2026-01-01", "USD", 100),
            )
            invoice_ids.append(invoice.lastrowid)
            closing = conn.execute(
                """INSERT INTO fechamentos
                   (cliente_id, banco_credito_id, moeda, data_fechamento,
                    data_liquidacao, banco_liquidacao_id, taxa_cambio, valor_brl)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (1, 1, "USD", "2026-01-01", "2026-01-02", 1, 5, index // 2),
            )
            conn.execute(
                """INSERT INTO fechamentos_cambio
                   (invoice_id, moeda, valor_moeda, data_fechamento, fechamento_id)
                   VALUES (?,?,?,?,?)""",
                (invoice.lastrowid, "USD", 100, "2026-01-01", closing.lastrowid),
            )
        conn.commit()
        expected = [row["id"] for row in app.central_closing_headers(
            conn, sort="valor_brl", direction="ASC"
        )]
        conn.close()

        listed = []
        for page in (1, 2):
            html = self.client.get(
                "/invoices/fechamentos?closing_sort=valor_brl&"
                f"closing_direction=asc&fechamentos_page={page}"
            ).get_data(as_text=True)
            listed.extend(int(value) for value in re.findall(
                r'<td data-sort-value="(\d+)"><a href="/invoices/fechamentos/\d+',
                html,
            ))
        self.assertEqual(listed, expected)

        middle = expected[12]
        detail = self.client.get(
            f"/invoices/fechamentos/{middle}?closing_sort=valor_brl&closing_direction=asc"
        ).get_data(as_text=True)
        detail = unescape(detail)
        self.assertIn(
            f"/invoices/fechamentos/{expected[11]}?closing_sort=valor_brl&closing_direction=asc",
            detail,
        )
        self.assertIn(
            f"/invoices/fechamentos/{expected[13]}?closing_sort=valor_brl&closing_direction=asc",
            detail,
        )

        first_detail = unescape(self.client.get(
            f"/invoices/fechamentos/{expected[0]}?closing_sort=valor_brl&"
            "closing_direction=asc&fechamento_cliente_id=1"
        ).get_data(as_text=True))
        last_detail = unescape(self.client.get(
            f"/invoices/fechamentos/{expected[-1]}?closing_sort=valor_brl&"
            "closing_direction=asc&fechamento_cliente_id=1"
        ).get_data(as_text=True))
        self.assertNotIn('aria-label="Fechamento anterior"', first_detail)
        self.assertNotIn('aria-label="Próximo fechamento"', last_detail)
        self.assertIn("fechamento_cliente_id=1", first_detail)

    def test_eligible_invoice_sorting_is_deterministic(self):
        items = [
            {"id": 2, "numero_invoice": "B", "valor_fechamento": 10},
            {"id": 1, "numero_invoice": "A", "valor_fechamento": 10},
            {"id": 3, "numero_invoice": "C", "valor_fechamento": ""},
        ]
        self.assertEqual(
            [item["id"] for item in app.sort_eligible_invoices(items, "valor_fechamento", "ASC")],
            [1, 2, 3],
        )
        self.assertEqual(
            [item["id"] for item in app.sort_eligible_invoices(items, "valor_fechamento", "DESC")],
            [2, 1, 3],
        )


if __name__ == "__main__":
    unittest.main()
