import tempfile
import unittest
from pathlib import Path

import app


class ContractEditCategoryTests(unittest.TestCase):
    def setUp(self):
        self.previous_db = app.DB
        self.db_path = Path(tempfile.mktemp(prefix="duecontrol_contract_edit_", suffix=".db"))
        app.DB = self.db_path
        app.init_db()
        conn = app.db()
        conn.execute(
            "INSERT INTO empresas (razao_social, cnpj, apelido) VALUES (?,?,?)",
            ("Empresa Teste", "12345678000190", "Teste"),
        )
        conn.execute(
            "INSERT INTO clientes (nome, pais) VALUES (?,?)",
            ("Cliente Teste", "US"),
        )
        conn.executemany(
            "INSERT INTO contrapartes (nome) VALUES (?)",
            [("Banco Crédito",), ("Banco Liquidação",)],
        )
        conn.execute(
            """
            INSERT INTO contratos
                (numero_contrato, cnpj, cliente, cliente_id, moeda, valor_moeda,
                 data_contrato, status, categoria_cambio, previsao_embarque_dias)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "C-EDIT-CATEGORY", "12345678000190", "Cliente Teste", 1, "USD", 100,
                "2026-09-01", app.STATUS_CONCLUIDO,
                app.CATEGORIA_CAMBIO_FINANCEIRO, None,
            ),
        )
        self.contract_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            """
            INSERT INTO fechamentos
                (cliente_id, banco_credito_id, moeda, categoria_cambio,
                 data_fechamento, data_liquidacao, banco_liquidacao_id,
                 taxa_cambio, valor_brl, previsao_embarque_dias, contrato_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                1, 1, "USD", app.CATEGORIA_CAMBIO_FINANCEIRO,
                "2026-09-01", "2026-09-02", 2, 5, 500, None,
                self.contract_id,
            ),
        )
        conn.commit()
        conn.close()
        self.client = app.app.test_client()

    def tearDown(self):
        app.DB = self.previous_db
        self.db_path.unlink(missing_ok=True)

    def _edit_data(self, **overrides):
        data = {
            "derived_contract_form": "1",
            "numero_contrato": "C-EDIT-CATEGORY",
            "categoria_cambio": app.CATEGORIA_CAMBIO_EXPORTACAO,
            "banco_liquidacao_id": "2",
            "data_fechamento": "01/09/2026",
            "data_liquidacao": "02/09/2026",
            "taxa_cambio": "5,0000",
            "contrato_observacao": "",
            "previsao_embarque_dias": "90",
        }
        data.update(overrides)
        return data

    def test_edit_form_exposes_prediction_when_category_is_export(self):
        response = self.client.get(f"/contrato/{self.contract_id}/editar")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('name="previsao_embarque_dias"', html)
        self.assertIn('data-previsao-cambio-category', html)
        self.assertIn('data-previsao-embarque-label hidden', html)

    def test_financial_contract_can_be_returned_to_export(self):
        response = self.client.post(
            f"/contrato/{self.contract_id}/editar",
            data=self._edit_data(),
        )
        self.assertEqual(response.status_code, 302)

        conn = app.db()
        contract = conn.execute(
            "SELECT categoria_cambio, previsao_embarque_dias FROM contratos WHERE id=?",
            (self.contract_id,),
        ).fetchone()
        closing = conn.execute(
            "SELECT categoria_cambio, previsao_embarque_dias FROM fechamentos WHERE contrato_id=?",
            (self.contract_id,),
        ).fetchone()
        conn.close()
        self.assertEqual(contract["categoria_cambio"], app.CATEGORIA_CAMBIO_EXPORTACAO)
        self.assertEqual(contract["previsao_embarque_dias"], 90)
        self.assertEqual(closing["categoria_cambio"], app.CATEGORIA_CAMBIO_EXPORTACAO)
        self.assertEqual(closing["previsao_embarque_dias"], 90)

    def test_export_category_without_prediction_remains_rejected(self):
        response = self.client.post(
            f"/contrato/{self.contract_id}/editar",
            data=self._edit_data(previsao_embarque_dias=""),
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("PREVISÃO EMBARQUE é obrigatória", response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
