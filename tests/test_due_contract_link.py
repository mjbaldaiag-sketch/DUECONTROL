import tempfile
import unittest
from pathlib import Path

import app


class DueContractLinkTests(unittest.TestCase):
    def setUp(self):
        self.previous_db = app.DB
        self.db_path = Path(tempfile.mktemp(prefix="duecontrol_due_contract_test_", suffix=".db"))
        app.DB = self.db_path
        app.init_db()
        conn = app.db()
        conn.execute(
            "INSERT INTO empresas (razao_social, cnpj, apelido) VALUES (?,?,?)",
            ("Empresa Teste", "04171382000177", "Teste"),
        )
        conn.execute(
            "INSERT INTO dues (numero_due, cnpj, moeda, valor_original, status) VALUES (?,?,?,?,?)",
            ("26BR000871281-4", "04171382000177", "USD", 100000, app.STATUS_PARCIAL),
        )
        first_due_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO dues (numero_due, cnpj, moeda, valor_original, status) VALUES (?,?,?,?,?)",
            ("26BR000871280-6", "04171382000177", "USD", 269672.64, app.STATUS_PENDENTE),
        )
        self.due_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO contratos (numero_contrato, cnpj, moeda, valor_moeda, status) VALUES (?,?,?,?,?)",
            ("598615276", "04171382000177", "USD", 339345.32999999996, app.STATUS_PARCIAL),
        )
        self.contract_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO due_contratos (due_id, contrato_id, valor_vinculado) VALUES (?,?,?)",
            (first_due_id, self.contract_id, 82498.55),
        )
        link_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO due_movimentacoes "
            "(due_id, contrato_id, due_contrato_id, data_movimentacao, tipo, valor) "
            "VALUES (?,?,?,?,?,?)",
            (first_due_id, self.contract_id, link_id, "2026-09-17", "VINCULACAO", 82498.55),
        )
        conn.commit()
        conn.close()
        self.client = app.app.test_client()

    def tearDown(self):
        app.DB = self.previous_db
        self.db_path.unlink(missing_ok=True)

    def test_displayed_contract_balance_can_be_linked_without_float_error(self):
        response = self.client.get(f"/contratos/{self.contract_id}/saldo")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["saldo_disponivel"], 256846.78)

        response = self.client.post(
            f"/due/{self.due_id}/vincular",
            data={"contrato_id": str(self.contract_id), "valor_vinculado": "256.846,78"},
        )
        self.assertEqual(response.status_code, 302)

        conn = app.db()
        link = conn.execute(
            "SELECT valor_vinculado FROM due_contratos WHERE due_id=? AND contrato_id=?",
            (self.due_id, self.contract_id),
        ).fetchone()
        summary = app.contract_summary(conn, self.contract_id)
        conn.close()
        self.assertAlmostEqual(link["valor_vinculado"], 256846.78, places=2)
        self.assertEqual(summary["saldo"], app.Decimal("0"))
        self.assertEqual(summary["status"], app.STATUS_CONCLUIDO)

    def test_link_rejects_amount_above_effective_contract_balance(self):
        response = self.client.post(
            f"/due/{self.due_id}/vincular",
            data={"contrato_id": str(self.contract_id), "valor_vinculado": "256.846,79"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("Valor maior que o saldo", response.get_data(as_text=True))

        conn = app.db()
        count = conn.execute(
            "SELECT COUNT(*) FROM due_contratos WHERE due_id=? AND contrato_id=?",
            (self.due_id, self.contract_id),
        ).fetchone()[0]
        conn.close()
        self.assertEqual(count, 0)

    def test_full_due_value_reports_contract_limit_instead_of_generic_failure(self):
        response = self.client.post(
            f"/due/{self.due_id}/vincular",
            data={"contrato_id": str(self.contract_id), "valor_vinculado": "269.672,64"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Valor maior que o saldo", html)
        self.assertIn("256.846,78", html)

    def test_contract_link_form_rounds_balances_and_limits_default_value(self):
        response = self.client.get(f"/contrato/{self.contract_id}")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('data-contract-total="339345.33"', html)
        self.assertIn('data-contract-available="256846.78"', html)
        self.assertIn('data-saldo="269672.64"', html)


if __name__ == "__main__":
    unittest.main()
