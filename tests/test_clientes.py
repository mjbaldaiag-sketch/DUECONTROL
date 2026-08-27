import tempfile
import unittest
from pathlib import Path

import app


class ClienteFlowTests(unittest.TestCase):
    def setUp(self):
        self.previous_db = app.DB
        self.db_path = Path(tempfile.mktemp(prefix="duecontrol_cliente_test_", suffix=".db"))
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
        conn.commit()
        conn.close()
        self.client = app.app.test_client()

    def tearDown(self):
        app.DB = self.previous_db
        self.db_path.unlink(missing_ok=True)

    def _create_client(self, name, country="BR"):
        conn = app.db()
        cursor = conn.execute("INSERT INTO clientes (nome, pais) VALUES (?,?)", (name, country))
        conn.commit()
        client_id = cursor.lastrowid
        conn.close()
        return client_id

    def test_client_listing_has_edit_and_delete_actions(self):
        client_id = self._create_client("Cliente Livre")

        response = self.client.get("/configuracoes/clientes")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn(f"/configuracoes/clientes/{client_id}/editar", html)
        self.assertIn(f"/configuracoes/clientes/{client_id}/excluir", html)
        self.assertIn("Sem vínculos", html)
        conn = app.db()
        indexes = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        conn.close()
        self.assertTrue({
            "idx_invoices_cliente",
            "idx_contratos_cliente",
            "idx_ndfs_cliente",
        }.issubset(indexes))

    def test_edit_updates_client_and_related_contract_cache(self):
        client_id = self._create_client("Cliente Antigo", "BR")
        conn = app.db()
        conn.execute(
            """
            INSERT INTO invoices
                (empresa_id,numero_invoice,tipo_documento,competencia_id,cliente_id,
                 data_emissao,moeda,valor_moeda)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (1, "INV-CLIENTE-EDIT", "COMMERCIAL_INVOICE", 1, client_id, "2026-08-10", "USD", 100),
        )
        conn.execute(
            """
            INSERT INTO contratos
                (numero_contrato,cnpj,cliente_id,cliente,moeda,valor_moeda)
            VALUES (?,?,?,?,?,?)
            """,
            ("CONTRATO-CLIENTE-EDIT", "45765914000181", client_id, "Cliente Antigo", "USD", 100),
        )
        conn.execute(
            """
            INSERT INTO ndfs
                (numero_operacao,cnpj,cliente_id,contraparte,tipo,moeda,valor_contratado,
                 taxa_contratada,data_contratacao,data_vencimento,posicao)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            ("NDF-CLIENTE-EDIT", "45765914000181", client_id, "Banco", "BANCO", "USD", 100,
             5, "2026-08-01", "2026-08-31", "COMPRA"),
        )
        conn.commit()
        conn.close()

        response = self.client.post(
            f"/configuracoes/clientes/{client_id}/editar",
            data={"nome": "  Cliente Renomeado  ", "pais": "AR"},
        )

        self.assertEqual(response.status_code, 302)
        conn = app.db()
        client = conn.execute("SELECT nome, pais FROM clientes WHERE id=?", (client_id,)).fetchone()
        contract = conn.execute(
            "SELECT cliente FROM contratos WHERE numero_contrato=?", ("CONTRATO-CLIENTE-EDIT",)
        ).fetchone()
        related = conn.execute(
            """
            SELECT i.numero_invoice, c.nome AS cliente_invoice,
                   n.numero_operacao, c2.nome AS cliente_ndf
            FROM invoices i
            JOIN clientes c ON c.id=i.cliente_id
            JOIN ndfs n ON n.id=(SELECT id FROM ndfs WHERE numero_operacao='NDF-CLIENTE-EDIT')
            JOIN clientes c2 ON c2.id=n.cliente_id
            WHERE i.numero_invoice=?
            """,
            ("INV-CLIENTE-EDIT",),
        ).fetchone()
        conn.close()

        self.assertEqual(tuple(client), ("Cliente Renomeado", "AR"))
        self.assertEqual(contract["cliente"], "Cliente Renomeado")
        self.assertEqual(related["cliente_invoice"], "Cliente Renomeado")
        self.assertEqual(related["cliente_ndf"], "Cliente Renomeado")

        html = self.client.get("/configuracoes/clientes").get_data(as_text=True)
        self.assertIn("INV-CLIENTE-EDIT", html)
        self.assertIn("CONTRATO-CLIENTE-EDIT", html)
        self.assertIn("NDF-CLIENTE-EDIT", html)

    def test_edit_rejects_duplicate_normalized_name_and_country(self):
        first_id = self._create_client("Cliente Original", "BR")
        self._create_client("Cliente Existente", "BR")

        response = self.client.post(
            f"/configuracoes/clientes/{first_id}/editar",
            data={"nome": " CLIENTE   EXISTENTE ", "pais": "BR"},
        )

        self.assertEqual(response.status_code, 200)
        conn = app.db()
        client = conn.execute("SELECT nome, pais FROM clientes WHERE id=?", (first_id,)).fetchone()
        conn.close()
        self.assertEqual(tuple(client), ("Cliente Original", "BR"))
        self.assertIn("equivalente", response.get_data(as_text=True))

    def test_delete_succeeds_only_for_client_without_links(self):
        client_id = self._create_client("Cliente Livre")

        response = self.client.post(f"/configuracoes/clientes/{client_id}/excluir")

        self.assertEqual(response.status_code, 302)
        conn = app.db()
        self.assertIsNone(conn.execute("SELECT id FROM clientes WHERE id=?", (client_id,)).fetchone())
        conn.close()

    def test_delete_is_blocked_and_links_are_listed_for_each_link_type(self):
        invoice_client = self._create_client("Cliente Invoice")
        contract_client = self._create_client("Cliente Contrato")
        ndf_client = self._create_client("Cliente NDF")
        conn = app.db()
        invoice_cursor = conn.execute(
            """
            INSERT INTO invoices
                (empresa_id,numero_invoice,tipo_documento,competencia_id,cliente_id,
                 data_emissao,moeda,valor_moeda)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (1, "INV-CLIENTE-BLOQUEIO", "COMMERCIAL_INVOICE", 1, invoice_client,
             "2026-08-10", "USD", 100),
        )
        conn.execute(
            "INSERT INTO contratos (numero_contrato,cliente_id,cliente,moeda,valor_moeda) VALUES (?,?,?,?,?)",
            ("CONTRATO-CLIENTE-BLOQUEIO", contract_client, "Cliente Contrato", "USD", 100),
        )
        conn.execute(
            """
            INSERT INTO ndfs
                (numero_operacao,cnpj,cliente_id,contraparte,tipo,moeda,valor_contratado,
                 taxa_contratada,data_contratacao,data_vencimento,posicao)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            ("NDF-CLIENTE-BLOQUEIO", "45765914000181", ndf_client, "Banco", "BANCO", "USD", 100,
             5, "2026-08-01", "2026-08-31", "COMPRA"),
        )
        invoice_id = invoice_cursor.lastrowid
        conn.commit()
        conn.close()

        for client_id in (invoice_client, contract_client, ndf_client):
            with self.subTest(client_id=client_id):
                response = self.client.post(f"/configuracoes/clientes/{client_id}/excluir")
                self.assertEqual(response.status_code, 302)
                conn = app.db()
                self.assertIsNotNone(conn.execute("SELECT id FROM clientes WHERE id=?", (client_id,)).fetchone())
                conn.close()

        html = self.client.get("/configuracoes/clientes").get_data(as_text=True)
        self.assertIn("INV-CLIENTE-BLOQUEIO", html)
        self.assertIn("CONTRATO-CLIENTE-BLOQUEIO", html)
        self.assertIn("NDF-CLIENTE-BLOQUEIO", html)
        self.assertIn("Exclusão bloqueada", html)
        self.assertIn(f"/invoice/{invoice_id}", html)

    def test_edit_and_delete_return_not_found_for_unknown_client(self):
        self.assertEqual(
            self.client.get("/configuracoes/clientes/999999/editar").status_code,
            404,
        )
        self.assertEqual(
            self.client.post("/configuracoes/clientes/999999/excluir").status_code,
            404,
        )


if __name__ == "__main__":
    unittest.main()
