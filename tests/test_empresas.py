import tempfile
import unittest
from pathlib import Path

import app


class EmpresaPriorityTests(unittest.TestCase):
    def setUp(self):
        self.previous_db = app.DB
        self.db_path = Path(tempfile.mktemp(prefix="duecontrol_empresa_test_", suffix=".db"))
        app.DB = self.db_path
        app.init_db()
        self.client = app.app.test_client()

    def tearDown(self):
        app.DB = self.previous_db
        self.db_path.unlink(missing_ok=True)

    def _insert_company(self, name, cnpj, priority):
        conn = app.db()
        cursor = conn.execute(
            "INSERT INTO empresas (razao_social, cnpj, apelido, prioridade) VALUES (?,?,?,?)",
            (name, cnpj, name, priority),
        )
        conn.commit()
        conn.close()
        return cursor.lastrowid

    def test_company_priority_is_saved_edited_and_used_in_company_lists(self):
        response = self.client.post("/configuracoes/empresas", data={
            "razao_social": "CEM", "cnpj": "45.765.914/0001-81",
            "apelido": "CEM", "prioridade": "3",
        })
        self.assertEqual(response.status_code, 302)
        cemma_id = self._insert_company("CEMMA", "04171382000177", 1)
        self._insert_company("COPLASA", "11222333000181", 2)

        response = self.client.get("/configuracoes/empresas")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertLess(html.index(">CEMMA<"), html.index(">COPLASA<"))
        self.assertLess(html.index(">COPLASA<"), html.index(">CEM<"))

        response = self.client.post(f"/configuracoes/empresas/{cemma_id}/editar", data={
            "razao_social": "CEMMA", "cnpj": "04171382000177",
            "apelido": "CEMMA", "prioridade": "4",
        })
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        self.assertEqual(conn.execute(
            "SELECT prioridade FROM empresas WHERE razao_social=?", ("CEMMA",)
        ).fetchone()[0], 4)
        conn.close()

    def test_invoice_and_contract_report_company_groups_use_priority(self):
        companies = [
            ("CEM", "45000000000100", 1),
            ("CEMMA", "45000000000200", 2),
            ("COPLASA", "45000000000300", 3),
        ]
        ids = [self._insert_company(*company) for company in reversed(companies)]
        conn = app.db()
        for company_id, company in zip(ids, reversed(companies)):
            conn.execute(
                "INSERT INTO invoices (empresa_id, numero_invoice, tipo_documento, moeda, valor_moeda) "
                "VALUES (?,?,?,?,?)",
                (company_id, f"INV-{company[0]}", "COMMERCIAL_INVOICE", "USD", 100),
            )
        conn.executemany(
            "INSERT INTO contratos (numero_contrato, cnpj, moeda, valor_moeda, data_contrato) VALUES (?,?,?,?,?)",
            [(f"CON-{name}", cnpj, "USD", 100, "2026-08-01") for name, cnpj, _ in companies],
        )
        conn.commit()
        conn.close()

        invoice_context = app.build_invoice_report_context()
        self.assertEqual(
            [row["empresa"] for row in invoice_context["tables"][1]["empresa_totals"]],
            ["CEM", "CEMMA", "COPLASA"],
        )
        hierarchy = invoice_context["tables"][1]["detail_groups"][0]
        self.assertEqual(
            [row["empresa"] for row in hierarchy["empresas"]],
            ["CEM", "CEMMA", "COPLASA"],
        )

        contract_context = app.build_contract_report_context({})
        self.assertEqual(
            [row["nome"] for row in contract_context["por_empresa"]],
            ["CEM", "CEMMA", "COPLASA"],
        )


if __name__ == "__main__":
    unittest.main()
