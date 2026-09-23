import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

import app


class ConversionRuleTests(unittest.TestCase):
    def setUp(self):
        self.previous_db = app.DB
        self.db_path = Path(tempfile.mktemp(prefix="duecontrol_conversion_test_", suffix=".db"))
        app.DB = self.db_path
        app.init_db()
        conn = app.db()
        conn.execute(
            "INSERT INTO empresas (razao_social, cnpj, apelido) VALUES (?,?,?)",
            ("Empresa Teste", "45765914000181", "Teste"),
        )
        conn.execute(
            "INSERT INTO competencias (empresa_id, descricao, data_inicial, data_final) VALUES (?,?,?,?)",
            (1, "Agosto/2026", "2026-08-01", "2026-08-31"),
        )
        conn.execute("INSERT INTO clientes (nome, pais) VALUES (?,?)", ("Cliente Teste", "US"))
        conn.executemany(
            "INSERT INTO contrapartes (nome, regra_conversao_brl) VALUES (?,?)",
            [("Banco Half-Up", app.CONVERSION_RULE_HALF_UP),
             ("Banco Half-Down", app.CONVERSION_RULE_HALF_UP)],
        )
        conn.commit()
        conn.close()
        self.client = app.app.test_client()

    def tearDown(self):
        app.DB = self.previous_db
        self.db_path.unlink(missing_ok=True)

    def _create_invoice(self, number, value):
        response = self.client.post("/invoice/nova", data={
            "empresa_id": "1",
            "numero_invoice": number,
            "tipo_documento": "COMMERCIAL_INVOICE",
            "competencia_id": "1",
            "cliente_id": "1",
            "data_emissao": "01/08/2026",
            "moeda": "USD",
            "valor_moeda": value,
        })
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        invoice_id = conn.execute(
            "SELECT id FROM invoices WHERE numero_invoice=?", (number,)
        ).fetchone()[0]
        conn.close()
        return invoice_id

    def test_decimal_examples_use_the_selected_rounding_rule(self):
        self.assertEqual(
            app.closing_brl_value(
                Decimal("269787.50"), Decimal("5.1180"), app.CONVERSION_RULE_HALF_DOWN
            ),
            Decimal("1380772.42"),
        )
        self.assertEqual(
            app.closing_brl_value(
                Decimal("269787.50"), Decimal("5.1180"), app.CONVERSION_RULE_HALF_UP
            ),
            Decimal("1380772.43"),
        )
        # O valor informado 269.787,51 produz 1.380.772,47618 exatamente.
        self.assertEqual(
            app.closing_brl_value(
                Decimal("269787.51"), Decimal("5.1180"), app.CONVERSION_RULE_HALF_DOWN
            ),
            Decimal("1380772.48"),
        )

    def test_bank_rule_is_saved_and_used_by_central_closing(self):
        page = self.client.get("/configuracoes/contrapartes")
        self.assertEqual(page.status_code, 200)
        self.assertIn('name="regra_conversao_brl"', page.get_data(as_text=True))

        self.assertEqual(self.client.post(
            "/configuracoes/contrapartes",
            data={"nome": "Banco Padrão"},
        ).status_code, 302)
        conn = app.db()
        default_rule = conn.execute(
            "SELECT regra_conversao_brl FROM contrapartes WHERE nome=?",
            ("Banco Padrão",),
        ).fetchone()[0]
        conn.close()
        self.assertEqual(default_rule, app.CONVERSION_RULE_HALF_UP)

        self.assertEqual(self.client.post(
            "/configuracoes/contrapartes/2/editar",
            data={
                "nome": "Banco Half-Down",
                "regra_conversao_brl": app.CONVERSION_RULE_HALF_DOWN,
            },
        ).status_code, 302)

        invoice_id = self._create_invoice("INV-RULE-DOWN", "269.787,50")
        self.assertEqual(self.client.post(
            f"/invoice/{invoice_id}/recebimentos",
            data={
                "banco_credito_id": "1",
                "data_credito": "01/08/2026",
                "valor_moeda": "269.787,50",
            },
        ).status_code, 302)
        response = self.client.post("/invoices/fechamentos", data={
            "selected_ids": [str(invoice_id)],
            f"valor_fechamento_{invoice_id}": "269.787,50",
            "data_fechamento": "2026-08-10",
            "data_liquidacao": "2026-08-11",
            "taxa_cambio": "5,1180",
            "banco_liquidacao_id": "2",
            "categoria_cambio": app.CATEGORIA_CAMBIO_FINANCEIRO,
        })
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        fechamento = conn.execute(
            "SELECT valor_brl FROM fechamentos ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
        self.assertEqual(Decimal(str(fechamento[0])), Decimal("1380772.42"))

    def test_changing_bank_configuration_does_not_change_existing_value(self):
        invoice_id = self._create_invoice("INV-RULE-HISTORY", "269.787,50")
        self.client.post(
            "/configuracoes/contrapartes/2/editar",
            data={
                "nome": "Banco Half-Down",
                "regra_conversao_brl": app.CONVERSION_RULE_HALF_DOWN,
            },
        )
        self.client.post(
            f"/invoice/{invoice_id}/recebimentos",
            data={
                "banco_credito_id": "1",
                "data_credito": "01/08/2026",
                "valor_moeda": "269.787,50",
            },
        )
        self.client.post("/invoices/fechamentos", data={
            "selected_ids": [str(invoice_id)],
            f"valor_fechamento_{invoice_id}": "269.787,50",
            "data_fechamento": "2026-08-10",
            "data_liquidacao": "2026-08-11",
            "taxa_cambio": "5,1180",
            "banco_liquidacao_id": "2",
            "categoria_cambio": app.CATEGORIA_CAMBIO_FINANCEIRO,
        })
        conn = app.db()
        fechamento_id = conn.execute(
            "SELECT id FROM fechamentos ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
        conn.execute(
            "UPDATE contrapartes SET regra_conversao_brl=? WHERE id=2",
            (app.CONVERSION_RULE_HALF_UP,),
        )
        conn.commit()
        conn.close()

        response = self.client.post(
            f"/invoices/fechamentos/{fechamento_id}/editar",
            data={
                "categoria_cambio": app.CATEGORIA_CAMBIO_FINANCEIRO,
                "data_fechamento": "2026-08-10",
                "data_liquidacao": "2026-08-11",
                "taxa_cambio": "5,1180",
                "banco_liquidacao_id": "2",
            },
        )
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        value = conn.execute(
            "SELECT valor_brl FROM fechamentos WHERE id=?", (fechamento_id,)
        ).fetchone()[0]
        conn.close()
        self.assertEqual(Decimal(str(value)), Decimal("1380772.42"))

    def test_counterparty_edit_is_visible_and_delete_is_blocked_by_links(self):
        edit_page = self.client.get("/configuracoes/contrapartes/2/editar")
        self.assertEqual(edit_page.status_code, 200)
        self.assertIn('value="HALF_UP" selected', edit_page.get_data(as_text=True))

        response = self.client.post(
            "/configuracoes/contrapartes/2/editar",
            data={
                "nome": "Banco Half-Down",
                "regra_conversao_brl": app.CONVERSION_RULE_HALF_DOWN,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("#contraparte-2", response.headers["Location"])
        conn = app.db()
        self.assertEqual(
            conn.execute(
                "SELECT regra_conversao_brl FROM contrapartes WHERE id=2"
            ).fetchone()[0],
            app.CONVERSION_RULE_HALF_DOWN,
        )
        conn.close()
        edited_page = self.client.get("/configuracoes/contrapartes/2/editar")
        self.assertIn('value="HALF_DOWN" selected', edited_page.get_data(as_text=True))

        conn = app.db()
        conn.execute(
            "INSERT INTO configuracoes_padrao (empresa_id, banco_liquidacao_id) VALUES (?,?)",
            (1, 1),
        )
        conn.commit()
        conn.close()
        response = self.client.post(
            "/configuracoes/contrapartes/1/excluir", follow_redirects=True
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("Não é possível excluir", response.get_data(as_text=True))
        conn = app.db()
        self.assertIsNotNone(
            conn.execute("SELECT id FROM contrapartes WHERE id=1").fetchone()
        )
        conn.close()

    def test_unused_counterparty_can_be_deleted(self):
        conn = app.db()
        conn.execute(
            "INSERT INTO contrapartes (nome, regra_conversao_brl) VALUES (?,?)",
            ("Banco Livre", app.CONVERSION_RULE_DEFAULT),
        )
        free_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        conn.close()

        response = self.client.post(
            f"/configuracoes/contrapartes/{free_id}/excluir", follow_redirects=True
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("excluído com sucesso", response.get_data(as_text=True))
        conn = app.db()
        self.assertIsNone(
            conn.execute("SELECT id FROM contrapartes WHERE id=?", (free_id,)).fetchone()
        )
        conn.close()


if __name__ == "__main__":
    unittest.main()
