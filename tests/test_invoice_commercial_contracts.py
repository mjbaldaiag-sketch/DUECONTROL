import tempfile
import unittest
from io import BytesIO
from pathlib import Path

import app


class InvoiceCommercialContractTests(unittest.TestCase):
    def setUp(self):
        self.previous_db = app.DB
        self.db_path = Path(tempfile.mktemp(prefix="duecontrol_invoice_contract_test_", suffix=".db"))
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
        conn.executemany(
            "INSERT INTO clientes (nome, pais) VALUES (?,?)",
            [("Cliente Um", "BR"), ("Cliente Dois", "US")],
        )
        conn.commit()
        conn.close()
        self.client = app.app.test_client()

    def tearDown(self):
        app.DB = self.previous_db
        self.db_path.unlink(missing_ok=True)

    def _create_contract(self, client_id="1", name="CONTRATO A"):
        response = self.client.post(
            "/configuracoes/contratos-comerciais",
            data={"cliente_id": client_id, "nome": name},
        )
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        normalized_name = app.normalize_invoice_commercial_contract_name(name, required=True)
        contract_id = conn.execute(
            "SELECT id FROM invoice_contratos_comerciais WHERE cliente_id=? AND nome=?",
            (client_id, normalized_name),
        ).fetchone()["id"]
        conn.close()
        return contract_id

    def _invoice_data(self, number="INV-001", client_id="1", contract_id="1"):
        return {
            "empresa_id": "1",
            "numero_invoice": number,
            "tipo_documento": "COMMERCIAL_INVOICE",
            "competencia_id": "1",
            "cliente_id": client_id,
            "contrato_comercial_id": contract_id,
            "data_emissao": "01/08/2026",
            "moeda": "USD",
            "valor_moeda": "100,00",
        }

    def test_master_normalizes_and_scopes_duplicate_by_client(self):
        contract_id = self._create_contract(name="  contrato   alfa  ")
        conn = app.db()
        self.assertEqual(
            tuple(conn.execute(
                "SELECT cliente_id, nome FROM invoice_contratos_comerciais WHERE id=?",
                (contract_id,),
            ).fetchone()),
            (1, "CONTRATO ALFA"),
        )
        conn.close()

        duplicate = self.client.post(
            "/configuracoes/contratos-comerciais",
            data={"cliente_id": "1", "nome": "contrato alfa"},
        )
        self.assertEqual(duplicate.status_code, 200)
        self.assertIn("Já existe", duplicate.get_data(as_text=True))
        self.assertEqual(self._create_contract("2", "contrato alfa"), 2)

    def test_new_invoice_requires_owned_master_contract(self):
        contract_id = self._create_contract()
        page = self.client.get("/invoice/nova")
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        self.assertIn('name="contrato_comercial_id"', html)
        self.assertIn('data-uppercase', self.client.get("/configuracoes/contratos-comerciais").get_data(as_text=True))
        self.assertIn("disabled", html)
        self.assertEqual(
            self.client.get("/configuracoes/contratos-comerciais/opcoes?cliente_id=1").json,
            [{"id": contract_id, "nome": "CONTRATO A"}],
        )

        missing = self.client.post("/invoice/nova", data=self._invoice_data(contract_id=""))
        self.assertEqual(missing.status_code, 200)
        self.assertIn("Contrato comercial", missing.get_data(as_text=True))

        wrong_client = self.client.post(
            "/invoice/nova", data=self._invoice_data(number="INV-WRONG", client_id="2")
        )
        self.assertEqual(wrong_client.status_code, 200)
        self.assertIn("não pertence", wrong_client.get_data(as_text=True))

        created = self.client.post(
            "/invoice/nova", data=self._invoice_data(number="INV-OK")
        )
        self.assertEqual(created.status_code, 302)
        conn = app.db()
        invoice = conn.execute(
            "SELECT cliente_id, contrato_comercial, contrato_comercial_id "
            "FROM invoices WHERE numero_invoice='INV-OK'"
        ).fetchone()
        self.assertEqual(tuple(invoice), (1, "CONTRATO A", contract_id))
        conn.close()

    def test_historical_invoice_preserves_legacy_text_until_explicit_change(self):
        contract_id = self._create_contract()
        conn = app.db()
        conn.execute(
            """INSERT INTO invoices
               (empresa_id, numero_invoice, tipo_documento, competencia_id, cliente_id,
                data_emissao, moeda, valor_moeda, contrato_comercial)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (1, "INV-LEGACY", "COMMERCIAL_INVOICE", 1, 1, "2026-08-01", "USD", 100, "Legacy raw"),
        )
        conn.commit()
        invoice_id = conn.execute(
            "SELECT id FROM invoices WHERE numero_invoice='INV-LEGACY'"
        ).fetchone()["id"]
        conn.close()

        edit_data = self._invoice_data(number="INV-LEGACY", contract_id="")
        response = self.client.post(f"/invoice/{invoice_id}/editar", data=edit_data)
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        self.assertEqual(
            tuple(conn.execute(
                "SELECT contrato_comercial, contrato_comercial_id FROM invoices WHERE id=?",
                (invoice_id,),
            ).fetchone()),
            ("Legacy raw", None),
        )
        conn.close()

        edit_data["contrato_comercial_id"] = str(contract_id)
        response = self.client.post(f"/invoice/{invoice_id}/editar", data=edit_data)
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        self.assertEqual(
            tuple(conn.execute(
                "SELECT contrato_comercial, contrato_comercial_id FROM invoices WHERE id=?",
                (invoice_id,),
            ).fetchone()),
            ("CONTRATO A", contract_id),
        )
        conn.close()

        renamed = self.client.post(
            f"/configuracoes/contratos-comerciais/{contract_id}/editar",
            data={"cliente_id": "1", "nome": "contrato renomeado"},
        )
        self.assertEqual(renamed.status_code, 302)
        conn = app.db()
        self.assertEqual(
            conn.execute(
                "SELECT contrato_comercial FROM invoices WHERE id=?", (invoice_id,)
            ).fetchone()["contrato_comercial"],
            "CONTRATO RENOMEADO",
        )
        conn.close()

    def test_deletion_is_blocked_for_linked_contract_and_client(self):
        contract_id = self._create_contract()
        response = self.client.post(
            "/invoice/nova", data=self._invoice_data(number="INV-LINKED")
        )
        self.assertEqual(response.status_code, 302)

        response = self.client.post(
            f"/configuracoes/contratos-comerciais/{contract_id}/excluir",
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("não é possível excluir", response.get_data(as_text=True).lower())

        response = self.client.post(
            "/configuracoes/clientes/1/excluir", follow_redirects=True
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("vínculo", response.get_data(as_text=True).lower())

    def test_migration_links_text_without_rewriting_history(self):
        conn = app.db()
        conn.execute(
            """INSERT INTO invoices
               (empresa_id, numero_invoice, tipo_documento, competencia_id, cliente_id,
                data_emissao, moeda, valor_moeda, contrato_comercial)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (1, "INV-BACKFILL", "COMMERCIAL_INVOICE", 1, 1, "2026-08-01", "USD", 100, "  backfill   ref "),
        )
        conn.execute(
            """INSERT INTO invoices
               (empresa_id, numero_invoice, tipo_documento, competencia_id, cliente_id,
                data_emissao, moeda, valor_moeda, contrato_comercial)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (1, "INV-NONE", "COMMERCIAL_INVOICE", 1, 1, "2026-08-01", "USD", 100, "None"),
        )
        conn.commit()
        app.migrate_invoice_commercial_contracts_schema(conn)
        app.migrate_invoice_commercial_contracts_schema(conn)
        linked = conn.execute(
            "SELECT contrato_comercial, contrato_comercial_id FROM invoices WHERE numero_invoice='INV-BACKFILL'"
        ).fetchone()
        none = conn.execute(
            "SELECT contrato_comercial, contrato_comercial_id FROM invoices WHERE numero_invoice='INV-NONE'"
        ).fetchone()
        self.assertEqual(linked["contrato_comercial"], "  backfill   ref ")
        self.assertIsNotNone(linked["contrato_comercial_id"])
        self.assertEqual(tuple(none), ("None", None))
        self.assertEqual(
            conn.execute(
                "SELECT COUNT(*) FROM invoice_contratos_comerciais WHERE cliente_id=1 AND nome='BACKFILL REF'"
            ).fetchone()[0],
            1,
        )
        conn.close()

    def test_import_requires_existing_master_and_links_normalized_value(self):
        self._create_contract()
        import pandas as pd

        def upload(contract_name, number):
            workbook = BytesIO()
            pd.DataFrame([{
                "empresa": "45.765.914/0001-81",
                "invoice": number,
                "contrato_comercial": contract_name,
                "competencia": "Agosto/2026",
                "tipo": "COMMERCIAL_INVOICE",
                "cliente": "Cliente Um",
                "emissao": "01/08/2026",
                "moeda": "USD",
                "valor_moeda": 100,
            }]).to_excel(workbook, index=False)
            workbook.seek(0)
            return self.client.post(
                "/invoices/importar",
                data={"arquivo": (workbook, "invoices.xlsx")},
                content_type="multipart/form-data",
            )

        preview = upload(" contrato a ", "INV-IMPORT")
        self.assertEqual(preview.status_code, 200)
        with self.client.session_transaction() as session:
            stage_token = session["invoice_import_stage"]
        self.assertEqual(
            self.client.post(
                "/invoices/importar/confirmar",
                data={"stage_token": stage_token},
            ).status_code,
            302,
        )

        conn = app.db()
        self.assertEqual(
            tuple(conn.execute(
                "SELECT contrato_comercial, contrato_comercial_id "
                "FROM invoices WHERE numero_invoice='INV-IMPORT'"
            ).fetchone()),
            ("CONTRATO A", 1),
        )
        conn.close()

        preview = upload("NÃO CADASTRADO", "INV-IMPORT-INVALID")
        self.assertEqual(preview.status_code, 200)
        with self.client.session_transaction() as session:
            stage_token = session["invoice_import_stage"]
        rejected = self.client.post(
            "/invoices/importar/confirmar",
            data={"stage_token": stage_token},
        )
        self.assertEqual(rejected.status_code, 400)


if __name__ == "__main__":
    unittest.main()
