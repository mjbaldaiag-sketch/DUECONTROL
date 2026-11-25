import tempfile
import unittest
from pathlib import Path

import app


class FinanciamentoExportacaoTests(unittest.TestCase):
    def setUp(self):
        self.previous_db = app.DB
        self.db_path = Path(tempfile.mktemp(prefix="duecontrol_financiamento_test_", suffix=".db"))
        app.DB = self.db_path
        app.init_db()
        conn = app.db()
        conn.execute(
            "INSERT INTO empresas (razao_social, cnpj, apelido) VALUES (?,?,?)",
            ("Empresa Financiamento", "45765914000181", "Financiadora"),
        )
        conn.execute(
            "INSERT INTO competencias (empresa_id, descricao, data_inicial, data_final) VALUES (?,?,?,?)",
            (1, "Novembro/2026", "2026-11-01", "2026-11-30"),
        )
        conn.execute("INSERT INTO clientes (nome, pais) VALUES (?,?)", ("Cliente Exportação", "US"))
        conn.execute("INSERT INTO contrapartes (nome, regra_conversao_brl) VALUES (?,?)", ("Banco Financiador", "HALF_UP"))
        conn.execute(
            "INSERT INTO dues (numero_due, chave_acesso, cnpj, moeda, valor_original) VALUES (?,?,?,?,?)",
            ("DUE-FIN-001", "123456789012345", "45765914000181", "USD", 500),
        )
        conn.execute(
            """INSERT INTO contratos
                (numero_contrato, cnpj, moeda, valor_moeda, categoria_cambio)
               VALUES (?,?,?,?,?)""",
            ("CAMBIO-FIN-001", "45765914000181", "USD", 500, app.CATEGORIA_CAMBIO_EXPORTACAO),
        )
        conn.commit()
        conn.close()
        self.client = app.app.test_client()

    def tearDown(self):
        app.DB = self.previous_db
        self.db_path.unlink(missing_ok=True)

    def _operation(self, amount="1000,00", modality="ACC", number=""):
        response = self.client.post(
            "/financiamento-exportacoes/nova",
            data={
                "categoria_operacao": modality,
                "empresa_id": "1",
                "banco_credito_id": "1",
                "numero_contrato_cambio": number,
                "data_contratacao": "25/11/2026",
                "data_limite_liquidacao": "30/11/2026",
                "moeda": "USD",
                "valor_contratado_moeda": amount,
                "taxa_contratada": "5,20",
            },
        )
        self.assertEqual(response.status_code, 302)
        return int(response.location.rsplit("/", 1)[1])

    def _invoice_with_receipt(self, number="INV-FIN-001", amount=1000):
        conn = app.db()
        cursor = conn.execute(
            """INSERT INTO invoices
                (empresa_id, numero_invoice, tipo_documento, competencia_id,
                 cliente_id, data_emissao, moeda, valor_moeda, status)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (1, number, "COMMERCIAL_INVOICE", 1, 1, "2026-11-01", "USD", amount,
             app.INVOICE_STATUS_RECEBIDA_AGUARDANDO_CAMBIO),
        )
        invoice_id = cursor.lastrowid
        conn.execute(
            """INSERT INTO recebimentos_invoice
                (invoice_id,banco_credito_id,data_credito,moeda,valor_moeda)
               VALUES (?,?,?,?,?)""",
            (invoice_id, 1, "2026-11-02", "USD", amount),
        )
        conn.commit()
        conn.close()
        return invoice_id

    def test_independent_registration_rounding_status_and_excess(self):
        operation_id = self._operation("1000,00", modality="PPE", number="TXT-001")
        conn = app.db()
        operation = conn.execute(
            "SELECT * FROM operacoes_financeiras WHERE id=?", (operation_id,)
        ).fetchone()
        self.assertEqual(operation["valor_contratado_brl"], 5200)
        self.assertEqual(operation["data_limite_liquidacao"], "2026-11-30")
        self.assertEqual(operation["status"], app.FINANCIAMENTO_STATUS_ABERTA)
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM operacoes_financeiras_invoices WHERE operacao_financeira_id=?",
            (operation_id,),
        ).fetchone()[0], 0)
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM operacoes_financeiras_liquidacoes WHERE operacao_financeira_id=?",
            (operation_id,),
        ).fetchone()[0], 0)
        conn.close()

        response = self.client.post(
            f"/financiamento-exportacoes/{operation_id}/liquidacoes",
            data={"data_liquidacao": "26/11/2026", "taxa_liquidacao": "5,00",
                  "valor_liquidado_moeda": "400,00"},
        )
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        self.assertEqual(conn.execute(
            "SELECT status FROM operacoes_financeiras WHERE id=?", (operation_id,)
        ).fetchone()[0], app.FINANCIAMENTO_STATUS_PARCIAL)
        conn.close()

        response = self.client.post(
            f"/financiamento-exportacoes/{operation_id}/liquidacoes",
            data={"data_liquidacao": "27/11/2026", "taxa_liquidacao": "5,10",
                  "valor_liquidado_moeda": "600,00"},
        )
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        self.assertEqual(conn.execute(
            "SELECT status FROM operacoes_financeiras WHERE id=?", (operation_id,)
        ).fetchone()[0], app.FINANCIAMENTO_STATUS_LIQUIDADA)
        self.assertEqual(conn.execute(
            "SELECT ROUND(SUM(valor_liquidado_brl), 2) FROM operacoes_financeiras_liquidacoes WHERE operacao_financeira_id=?",
            (operation_id,),
        ).fetchone()[0], 5060)
        self.assertGreaterEqual(conn.execute(
            "SELECT COUNT(*) FROM auditoria_eventos WHERE entidade_id=?", (operation_id,)
        ).fetchone()[0], 3)
        conn.close()

        response = self.client.post(
            f"/financiamento-exportacoes/{operation_id}/liquidacoes",
            data={"data_liquidacao": "28/11/2026", "taxa_liquidacao": "5,00",
                  "valor_liquidado_moeda": "0,01"}, follow_redirects=True,
        )
        self.assertIn("ultrapassar o valor contratado", response.get_data(as_text=True))

    def test_rejects_unsupported_modality_and_accepts_textual_contract_number(self):
        response = self.client.post(
            "/financiamento-exportacoes/nova",
            data={"categoria_operacao": "NCE", "empresa_id": "1", "banco_credito_id": "1",
                  "data_contratacao": "25/11/2026", "moeda": "USD",
                  "valor_contratado_moeda": "10,00", "taxa_contratada": "5,00"},
        )
        self.assertEqual(response.status_code, 400)
        conn = app.db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM operacoes_financeiras").fetchone()[0], 0)
        conn.close()
        operation_id = self._operation("10,00", number="SEM-CADASTRO-001")
        conn = app.db()
        self.assertEqual(conn.execute(
            "SELECT numero_contrato_cambio FROM operacoes_financeiras WHERE id=?", (operation_id,)
        ).fetchone()[0], "SEM-CADASTRO-001")
        conn.close()

    def test_invoice_due_and_contract_links_preserve_separate_balances(self):
        operation_id = self._operation("1000,00")
        invoice_id = self._invoice_with_receipt()
        response = self.client.post(
            f"/financiamento-exportacoes/{operation_id}/invoices",
            data={"invoice_id": str(invoice_id), "valor_vinculado": "600,00"},
        )
        self.assertEqual(response.status_code, 302)

        response = self.client.post(
            f"/financiamento-exportacoes/{operation_id}/dues",
            data={"due_id": "1", "valor_vinculado": "300,00"},
        )
        self.assertEqual(response.status_code, 302)
        response = self.client.post(
            f"/financiamento-exportacoes/{operation_id}/contratos",
            data={"contrato_id": "1", "valor_vinculado": "300,00"},
        )
        self.assertEqual(response.status_code, 302)

        conn = app.db()
        self.assertEqual(conn.execute(
            "SELECT valor_moeda FROM operacoes_financeiras_invoices WHERE operacao_financeira_id=?",
            (operation_id,),
        ).fetchone()[0], 600)
        self.assertEqual(conn.execute(
            "SELECT valor_vinculado FROM operacoes_financeiras_dues WHERE operacao_financeira_id=?",
            (operation_id,),
        ).fetchone()[0], 300)
        self.assertEqual(conn.execute(
            "SELECT valor_vinculado FROM operacoes_financeiras_contratos WHERE operacao_financeira_id=?",
            (operation_id,),
        ).fetchone()[0], 300)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM due_movimentacoes").fetchone()[0], 0)
        conn.close()

        response = self.client.post(
            f"/financiamento-exportacoes/{operation_id}/invoices/1/excluir",
        )
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM operacoes_financeiras_invoices WHERE operacao_financeira_id=?",
            (operation_id,),
        ).fetchone()[0], 0)
        conn.close()

    def _register_invoice_operation(self, financing_id, invoice_id, amount,
                                    category="ACC", manual_number="MANUAL-IGNORED"):
        return self.client.post(
            "/invoices/operacoes-financeiras",
            data={
                "selected_ids": str(invoice_id),
                f"valor_operacao_{invoice_id}": amount,
                "categoria_operacao": category,
                "data_liquidacao": "27/11/2026",
                "taxa_cambio": "5,2000",
                "banco_liquidacao_id": "1",
                "financiamento_id_grupo_1_USD": str(financing_id),
                "numero_operacao_grupo_1_USD": manual_number,
            },
            follow_redirects=True,
        )

    def test_invoice_operation_persists_financing_link_and_releases_balance(self):
        financing_id = self._operation("1000,00", number="ACC-CONTRATO-001")
        invoice_id = self._invoice_with_receipt("INV-LINK-001", 250)

        response = self.client.get("/invoices/fechamentos")
        html = response.get_data(as_text=True)
        self.assertIn('data-financing-options', html)
        self.assertIn('data-financing-number="ACC-CONTRATO-001"', html)
        self.assertIn('data-financing-balance="1000', html)

        response = self._register_invoice_operation(financing_id, invoice_id, "250,00")
        self.assertEqual(response.status_code, 200)
        conn = app.db()
        operation = conn.execute(
            "SELECT * FROM operacoes_financeiras "
            "WHERE financiamento_exportacao_id=?",
            (financing_id,),
        ).fetchone()
        self.assertIsNotNone(operation)
        self.assertEqual(operation["numero_operacao"], "ACC-CONTRATO-001")
        self.assertEqual(operation["financiamento_exportacao_id"], financing_id)
        self.assertEqual(
            conn.execute(
                "SELECT invoice_id FROM operacoes_financeiras_invoices "
                "WHERE operacao_financeira_id=?", (operation["id"],)
            ).fetchone()[0],
            invoice_id,
        )
        self.assertEqual(
            app.financiamento_registro_available(conn, financing_id),
            app.Decimal("750"),
        )
        financing_detail = app.financiamento_operation_detail(conn, financing_id)
        self.assertEqual(financing_detail["saldo_operacoes_moeda"], app.Decimal("750"))
        self.assertEqual(
            financing_detail["invoices_operacoes_financeiras"][0]["invoice_id"],
            invoice_id,
        )
        self.assertEqual(
            financing_detail["operacoes_financeiras_vinculadas"][0]["operacao_id"],
            operation["id"],
        )
        conn.close()

        financing_page = self.client.get(
            f"/financiamento-exportacoes/{financing_id}"
        )
        financing_html = financing_page.get_data(as_text=True)
        self.assertEqual(financing_page.status_code, 200)
        self.assertIn("INV-LINK-001", financing_html)
        self.assertIn("Saldo para Operações Financeiras", financing_html)
        self.assertIn("750,00", financing_html)
        self.assertIn("Operações Financeiras vinculadas", financing_html)

        detail = self.client.get(
            f"/invoices/operacoes-financeiras/{operation['id']}"
        )
        detail_html = detail.get_data(as_text=True)
        self.assertIn("ACC-CONTRATO-001", detail_html)
        self.assertIn("data-financing-linked=", detail_html)
        self.assertIn("readonly", detail_html)
        self.assertIn("disabled", detail_html)

        response = self.client.post(
            f"/invoices/operacoes-financeiras/{operation['id']}/editar",
            data={
                "numero_operacao": "MANUAL-ALTERADO",
                "categoria_operacao": "ACE",
                "data_liquidacao": "28/11/2026",
                "taxa_cambio": "6,0000",
                "banco_liquidacao_id": "1",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        conn = app.db()
        unchanged = conn.execute(
            "SELECT numero_operacao,categoria_operacao FROM operacoes_financeiras "
            "WHERE id=?", (operation["id"],)
        ).fetchone()
        self.assertEqual(unchanged["numero_operacao"], "ACC-CONTRATO-001")
        self.assertEqual(unchanged["categoria_operacao"], "ACC")
        conn.close()

        financing_list = self.client.get("/financiamento-exportacoes")
        self.assertIn("ACC-CONTRATO-001", financing_list.get_data(as_text=True))
        self.assertNotIn(
            f"/financiamento-exportacoes/{operation['id']}/editar",
            financing_list.get_data(as_text=True),
        )

        response = self.client.post(
            f"/financiamento-exportacoes/{financing_id}/excluir"
        )
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        self.assertIsNotNone(conn.execute(
            "SELECT id FROM operacoes_financeiras WHERE id=?", (financing_id,)
        ).fetchone())
        conn.close()

        response = self.client.post(
            f"/invoices/operacoes-financeiras/{operation['id']}/excluir"
        )
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        self.assertEqual(
            app.financiamento_registro_available(conn, financing_id),
            app.Decimal("1000"),
        )
        conn.close()
        response = self.client.post(
            f"/financiamento-exportacoes/{financing_id}/excluir"
        )
        self.assertEqual(response.status_code, 302)

    def test_invoice_operation_uses_financing_id_when_contract_number_is_empty(self):
        financing_id = self._operation("100,00", number="")
        invoice_id = self._invoice_with_receipt("INV-LINK-FALLBACK", 100)
        response = self._register_invoice_operation(financing_id, invoice_id, "100,00")
        self.assertEqual(response.status_code, 200)
        conn = app.db()
        operation = conn.execute(
            "SELECT numero_operacao FROM operacoes_financeiras "
            "WHERE financiamento_exportacao_id=?", (financing_id,)
        ).fetchone()
        self.assertEqual(operation["numero_operacao"], str(financing_id))
        conn.close()

    def test_invoice_operation_rejects_incompatible_financing_atomically(self):
        acc_id = self._operation("100,00", modality="ACC", number="ACC-VALID")
        invoice_id = self._invoice_with_receipt("INV-LINK-INVALID", 100)
        response = self._register_invoice_operation(
            acc_id, invoice_id, "100,00", category="ACE"
        )
        self.assertEqual(response.status_code, 200)
        conn = app.db()
        self.assertEqual(
            conn.execute(
                "SELECT COUNT(*) FROM operacoes_financeiras "
                "WHERE origem=? AND financiamento_exportacao_id IS NOT NULL",
                (app.FINANCIAMENTO_ORIGEM_LEGADO,),
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            app.financiamento_registro_available(conn, acc_id), app.Decimal("100")
        )
        conn.close()

    def test_invoice_operation_rejects_multi_company_group(self):
        conn = app.db()
        conn.execute(
            "INSERT INTO empresas (razao_social, cnpj, apelido) VALUES (?,?,?)",
            ("Empresa Dois", "98765432000199", "Dois"),
        )
        conn.execute(
            "INSERT INTO competencias (empresa_id, descricao, data_inicial, data_final) "
            "VALUES (?,?,?,?)", (2, "Novembro/2026 Dois", "2026-11-01", "2026-11-30")
        )
        conn.commit()
        conn.close()
        first = self._invoice_with_receipt("INV-MULTI-ONE", 100)
        second = self._invoice_with_receipt("INV-MULTI-TWO", 100)
        conn = app.db()
        conn.execute("UPDATE invoices SET empresa_id=?, competencia_id=? WHERE id=?", (2, 2, second))
        conn.commit()
        conn.close()
        financing_id = self._operation("200,00", number="ACC-MULTI")
        response = self.client.post(
            "/invoices/operacoes-financeiras",
            data={
                "selected_ids": [str(first), str(second)],
                f"valor_operacao_{first}": "100,00",
                f"valor_operacao_{second}": "100,00",
                "categoria_operacao": "ACC",
                "data_liquidacao": "27/11/2026",
                "taxa_cambio": "5,2000",
                "banco_liquidacao_id": "1",
                "financiamento_id_grupo_1_USD": str(financing_id),
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        conn = app.db()
        self.assertEqual(
            conn.execute(
                "SELECT COUNT(*) FROM operacoes_financeiras "
                "WHERE financiamento_exportacao_id IS NOT NULL"
            ).fetchone()[0],
            0,
        )
        conn.close()

    def test_legacy_invoice_operation_remains_editable_without_financing_link(self):
        invoice_id = self._invoice_with_receipt("INV-LEGACY-EDIT", 100)
        conn = app.db()
        cursor = conn.execute(
            """
            INSERT INTO operacoes_financeiras
                (numero_operacao,cliente_id,empresa_id,banco_credito_id,moeda,
                 categoria_operacao,data_fechamento,data_liquidacao,
                 banco_liquidacao_id,taxa_cambio,valor_brl,status,origem)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "LEGACY-MANUAL", 1, 1, 1, "USD", "ACC", "2026-11-27",
                "2026-11-27", 1, 5, 500, app.FINANCIAMENTO_STATUS_LIQUIDADA,
                app.FINANCIAMENTO_ORIGEM_LEGADO,
            ),
        )
        operation_id = cursor.lastrowid
        conn.execute(
            """
            INSERT INTO operacoes_financeiras_liquidacoes
                (operacao_financeira_id,data_liquidacao,taxa_liquidacao,
                 valor_liquidado_moeda,valor_liquidado_brl)
            VALUES (?,?,?,?,?)
            """,
            (operation_id, "2026-11-27", 5, 100, 500),
        )
        conn.execute(
            """
            INSERT INTO operacoes_financeiras_invoices
                (operacao_financeira_id,invoice_id,moeda,valor_moeda)
            VALUES (?,?,?,?)
            """,
            (operation_id, invoice_id, "USD", 100),
        )
        conn.commit()
        conn.close()

        response = self.client.post(
            f"/invoices/operacoes-financeiras/{operation_id}/editar",
            data={
                "numero_operacao": "LEGACY-EDITADA",
                "categoria_operacao": "ACE",
                "data_liquidacao": "28/11/2026",
                "taxa_cambio": "6,0000",
                "banco_liquidacao_id": "1",
            },
        )
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        operation = conn.execute(
            "SELECT numero_operacao,categoria_operacao,financiamento_exportacao_id "
            "FROM operacoes_financeiras WHERE id=?", (operation_id,)
        ).fetchone()
        self.assertEqual(operation["numero_operacao"], "LEGACY-EDITADA")
        self.assertEqual(operation["categoria_operacao"], "ACE")
        self.assertIsNone(operation["financiamento_exportacao_id"])
        conn.close()


if __name__ == "__main__":
    unittest.main()
