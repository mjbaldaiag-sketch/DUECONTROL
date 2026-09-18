import tempfile
import unittest
from io import BytesIO
from pathlib import Path
import sqlite3
import app


class InvoiceRecompositionTestsMixin:
    def _create_two_partial_parcels(self, number="INV-RECOMPOSE"):
        invoice_id = self._create_invoice(number, "1000,00")
        self.assertEqual(self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "10/08/2026", "valor_moeda": "1000,00",
        }).status_code, 302)
        self.assertEqual(self.client.post(f"/invoice/{invoice_id}/fechamentos", data={
            "valor_moeda": "300,00", "data_fechamento": "11/08/2026",
        }).status_code, 302)
        conn = app.db()
        first_child = conn.execute(
            "SELECT invoice_id FROM invoice_desdobramentos WHERE invoice_anterior_id=?",
            (invoice_id,),
        ).fetchone()[0]
        conn.close()
        self.assertEqual(self.client.post(f"/invoice/{first_child}/fechamentos", data={
            "valor_moeda": "200,00", "data_fechamento": "12/08/2026",
        }).status_code, 302)
        conn = app.db()
        second_child = conn.execute(
            "SELECT invoice_id FROM invoice_desdobramentos WHERE invoice_anterior_id=?",
            (first_child,),
        ).fetchone()[0]
        conn.close()
        return invoice_id, first_child, second_child

    def test_deleting_partial_closing_then_leaf_recomposes_without_deleting_receipt(self):
        invoice_id = self._create_invoice("INV-RECOMPOSE-INDIVIDUAL", "1000,00")
        self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "10/08/2026", "valor_moeda": "1000,00",
        })
        self.client.post(f"/invoice/{invoice_id}/fechamentos", data={
            "valor_moeda": "300,00", "data_fechamento": "11/08/2026",
        })
        conn = app.db()
        child_id = conn.execute(
            "SELECT invoice_id FROM invoice_desdobramentos WHERE invoice_anterior_id=?",
            (invoice_id,),
        ).fetchone()[0]
        closing_id = conn.execute(
            "SELECT id FROM fechamentos_cambio WHERE invoice_id=?", (invoice_id,)
        ).fetchone()[0]
        receipt = conn.execute(
            "SELECT id, valor_moeda, data_credito FROM recebimentos_invoice WHERE invoice_id=?",
            (invoice_id,),
        ).fetchone()
        conn.close()

        self.assertEqual(self.client.post(
            f"/invoice/{invoice_id}/fechamentos/{closing_id}/excluir"
        ).status_code, 302)
        response = self.client.post(f"/invoice/{child_id}/excluir", follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("excluída", response.get_data(as_text=True))

        conn = app.db()
        root = conn.execute(
            "SELECT numero_invoice, valor_moeda FROM invoices WHERE id=?", (invoice_id,)
        ).fetchone()
        restored_receipt = conn.execute(
            "SELECT id, valor_moeda, data_credito FROM recebimentos_invoice WHERE id=?",
            (receipt["id"],),
        ).fetchone()
        allocation = conn.execute(
            "SELECT invoice_id, valor_moeda FROM invoice_recebimento_alocacoes WHERE recebimento_id=?",
            (receipt["id"],),
        ).fetchall()
        self.assertEqual((root["numero_invoice"], app.Decimal(str(root["valor_moeda"]))),
                         ("INV-RECOMPOSE-INDIVIDUAL", app.Decimal("1000")))
        self.assertEqual(dict(restored_receipt), dict(receipt))
        self.assertEqual(len(allocation), 0)
        self.assertFalse(app.receipt_is_shared(conn, receipt["id"]))
        conn.close()

        blocked_root = self.client.post(f"/invoice/{invoice_id}/excluir", follow_redirects=True)
        self.assertIn("recebimento(s) registrado(s)", blocked_root.get_data(as_text=True))
        self.assertEqual(self.client.post(
            f"/invoice/{invoice_id}/recebimentos/{receipt['id']}/excluir"
        ).status_code, 302)
        self.assertEqual(self.client.post(f"/invoice/{invoice_id}/excluir").status_code, 302)

    def test_central_partial_closing_can_delete_leaf_after_group_deletion(self):
        invoice_id = self._create_invoice("INV-RECOMPOSE-CENTRAL", "1000,00")
        self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "10/08/2026", "valor_moeda": "1000,00",
        })
        response = self.client.post("/invoices/fechamentos", data={
            "selected_ids": str(invoice_id),
            f"valor_fechamento_{invoice_id}": "400,00",
            "data_fechamento": "2026-08-11", "data_liquidacao": "2026-08-12",
            "taxa_cambio": "5,0000", "banco_liquidacao_id": "1",
            "categoria_cambio": app.CATEGORIA_CAMBIO_EXPORTACAO,
            "previsao_embarque_dias": "120",
        })
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        header_id = conn.execute("SELECT id FROM fechamentos ORDER BY id DESC LIMIT 1").fetchone()[0]
        child_id = conn.execute(
            "SELECT invoice_id FROM invoice_desdobramentos WHERE invoice_anterior_id=?",
            (invoice_id,),
        ).fetchone()[0]
        conn.close()

        self.assertEqual(self.client.post(
            f"/invoices/fechamentos/{header_id}/excluir"
        ).status_code, 302)
        self.assertEqual(self.client.post(f"/invoice/{child_id}/excluir").status_code, 302)
        conn = app.db()
        self.assertIsNone(conn.execute("SELECT id FROM invoices WHERE id=?", (child_id,)).fetchone())
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM fechamentos").fetchone()[0], 0)
        self.assertEqual(app.Decimal(str(conn.execute(
            "SELECT valor_moeda FROM invoices WHERE id=?", (invoice_id,)
        ).fetchone()[0])), app.Decimal("1000"))
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM recebimentos_invoice").fetchone()[0], 1)
        conn.close()

    def test_partial_parcel_batch_deletion_is_leaf_first_and_blocks_non_leaf(self):
        invoice_id, first_child, second_child = self._create_two_partial_parcels()
        conn = app.db()
        closing_ids = [row[0] for row in conn.execute(
            "SELECT id FROM fechamentos_cambio WHERE invoice_id IN (?,?)",
            (invoice_id, first_child),
        ).fetchall()]
        conn.close()
        for closing_id in closing_ids:
            self.assertEqual(self.client.post(
                f"/invoice/{invoice_id if closing_id == closing_ids[0] else first_child}/fechamentos/{closing_id}/excluir"
            ).status_code, 302)

        blocked = self.client.post(f"/invoice/{first_child}/excluir", follow_redirects=True)
        self.assertIn("parcela(s) dependente(s)", blocked.get_data(as_text=True))

        response = self.client.post("/invoices/excluir-lote", data={
            "selected_ids": [str(first_child), str(second_child)],
        })
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM invoices WHERE id IN (?,?)", (first_child, second_child)
        ).fetchone()[0], 0)
        root = conn.execute(
            "SELECT numero_invoice, valor_moeda FROM invoices WHERE id=?", (invoice_id,)
        ).fetchone()
        self.assertEqual(root["numero_invoice"], "INV-RECOMPOSE")
        self.assertEqual(app.Decimal(str(root["valor_moeda"])), app.Decimal("1000"))
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM invoice_recebimento_alocacoes WHERE invoice_id=?", (invoice_id,)
        ).fetchone()[0], 0)
        conn.close()

    def test_partial_parcel_with_own_receipt_remains_blocked(self):
        invoice_id = self._create_invoice("INV-RECOMPOSE-PROTECTED", "1000,00")
        self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "10/08/2026", "valor_moeda": "1000,00",
        })
        self.client.post(f"/invoice/{invoice_id}/fechamentos", data={
            "valor_moeda": "400,00", "data_fechamento": "11/08/2026",
        })
        conn = app.db()
        child_id = conn.execute(
            "SELECT invoice_id FROM invoice_desdobramentos WHERE invoice_anterior_id=?",
            (invoice_id,),
        ).fetchone()[0]
        conn.execute("""
            INSERT INTO recebimentos_invoice
                (invoice_id,banco_credito_id,data_credito,moeda,valor_moeda)
            VALUES (?,1,'2026-08-12','USD',10)
        """, (child_id,))
        closing_id = conn.execute(
            "SELECT id FROM fechamentos_cambio WHERE invoice_id=?", (invoice_id,)
        ).fetchone()[0]
        conn.commit()
        conn.close()
        self.client.post(f"/invoice/{invoice_id}/fechamentos/{closing_id}/excluir")

        response = self.client.post(f"/invoice/{child_id}/excluir", follow_redirects=True)
        self.assertIn("recebimento(s) registrado(s)", response.get_data(as_text=True))
        conn = app.db()
        self.assertIsNotNone(conn.execute("SELECT id FROM invoices WHERE id=?", (child_id,)).fetchone())
        conn.close()

    def test_unidentified_receipt_allocation_remains_blocked(self):
        root_id = self._create_invoice("INV-UNIDENTIFIED-ROOT", "100,00")
        allocation_id = self._create_invoice("INV-UNIDENTIFIED-ALLOCATION", "100,00")
        self.client.post(f"/invoice/{root_id}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "10/08/2026", "valor_moeda": "100,00",
        })
        conn = app.db()
        receipt_id = conn.execute(
            "SELECT id FROM recebimentos_invoice WHERE invoice_id=?", (root_id,)
        ).fetchone()[0]
        conn.execute("""
            INSERT INTO invoice_recebimento_alocacoes
                (invoice_id,recebimento_id,valor_moeda)
            VALUES (?,?,?)
        """, (allocation_id, receipt_id, 100))
        conn.commit()
        conn.close()

        response = self.client.post(f"/invoice/{allocation_id}/excluir", follow_redirects=True)
        self.assertIn("aloca", response.get_data(as_text=True))
        conn = app.db()
        self.assertIsNotNone(conn.execute(
            "SELECT id FROM invoice_recebimento_alocacoes WHERE invoice_id=?", (allocation_id,)
        ).fetchone())
        self.assertIsNotNone(conn.execute(
            "SELECT id FROM invoices WHERE id=?", (allocation_id,)
        ).fetchone())
        conn.close()

class InvoiceFlowTests(InvoiceRecompositionTestsMixin, unittest.TestCase):
    def setUp(self):
        self.previous_db = app.DB
        self.db_path = Path(tempfile.mktemp(prefix="duecontrol_invoice_test_", suffix=".db"))
        app.DB = self.db_path
        app.init_db()
        conn = app.db()
        conn.execute("INSERT INTO empresas (razao_social, cnpj, apelido) VALUES (?,?,?)",
                     ("Empresa Teste", "45765914000181", "Teste"))
        conn.execute("INSERT INTO competencias (empresa_id, descricao, data_inicial, data_final) VALUES (?,?,?,?)",
                     (1, "Agosto/2026", "2026-08-01", "2026-08-31"))
        conn.execute("INSERT INTO clientes (nome, pais) VALUES (?,?)", ("Cliente Teste", "US"))
        conn.execute("INSERT INTO contrapartes (nome) VALUES (?)", ("Banco Teste",))
        conn.execute("INSERT INTO dues (numero_due, chave_acesso, moeda, valor_original) VALUES (?,?,?,?)",
                     ("DUE-001", "12345678901234", "USD", 1000))
        conn.commit()
        conn.close()
        self.client = app.app.test_client()

    def tearDown(self):
        app.DB = self.previous_db
        self.db_path.unlink(missing_ok=True)

    def _create_invoice(self, number="INV-001", value="1000,00", commercial=None, client_id=1,
                        currency="USD", banco_referenciado_id=None, empresa_id=1,
                        competencia_id=1):
        data = {
            "empresa_id": str(empresa_id), "numero_invoice": number, "tipo_documento": "COMMERCIAL_INVOICE",
            "competencia_id": str(competencia_id), "cliente_id": str(client_id),
            "data_emissao": "01/08/2026", "moeda": currency,
            "valor_moeda": value,
        }
        if commercial is not None:
            data["contrato_comercial"] = commercial
        if banco_referenciado_id is not None:
            data["banco_referenciado_id"] = str(banco_referenciado_id)
        response = self.client.post("/invoice/nova", data=data)
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        invoice = conn.execute("SELECT * FROM invoices WHERE numero_invoice=?", (number,)).fetchone()
        conn.close()
        return invoice["id"]

    def _register_central_closing(self, invoice_id, value, data_fechamento,
                                  data_liquidacao, banco_liquidacao_id="1",
                                  taxa_cambio="5,0000", numero_contrato=None,
                                  categoria_cambio=app.CATEGORIA_CAMBIO_EXPORTACAO):
        received = self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
            "banco_credito_id": "1",
            "data_credito": "01/08/2026",
            "valor_moeda": value,
        })
        self.assertEqual(received.status_code, 302)
        closing_data = {
            "selected_ids": [str(invoice_id)],
            "data_fechamento": data_fechamento,
            "data_liquidacao": data_liquidacao,
            "taxa_cambio": taxa_cambio,
            "banco_liquidacao_id": banco_liquidacao_id,
            "categoria_cambio": categoria_cambio,
        }
        if categoria_cambio == app.CATEGORIA_CAMBIO_EXPORTACAO:
            closing_data["previsao_embarque_dias"] = "120"
        if numero_contrato:
            closing_data["numero_contrato_0"] = numero_contrato
        response = self.client.post("/invoices/fechamentos", data=closing_data)
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        row = conn.execute(
            "SELECT * FROM fechamentos WHERE id=(SELECT MAX(id) FROM fechamentos)"
        ).fetchone()
        conn.close()
        return row

    def test_invoice_reference_is_saved_and_receipt_defaults_are_editable(self):
        conn = app.db()
        conn.execute("INSERT INTO contrapartes (nome) VALUES (?)", ("Banco Alternativo",))
        bank_two_id = conn.execute(
            "SELECT id FROM contrapartes WHERE nome=?", ("Banco Alternativo",)
        ).fetchone()[0]
        conn.commit()
        conn.close()

        invoice_id = self._create_invoice(
            number="INV-REFERENCE", value="1000,00", banco_referenciado_id=1
        )
        response = self.client.get(f"/invoice/{invoice_id}")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        today_br = app.date.today().strftime("%d/%m/%Y")
        self.assertIn("Banco Referenciado", html)
        self.assertIn('<option value="1" selected>Banco Teste</option>', html)
        self.assertIn(f'name="data_credito" data-date-br value="{today_br}"', html)
        self.assertIn('name="valor_moeda" data-money value="1.000,00"', html)

        response = self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
            "banco_credito_id": str(bank_two_id), "data_credito": "20/08/2026",
            "valor_moeda": "900,00",
        })
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        receipt = conn.execute(
            "SELECT banco_credito_id, data_credito, valor_moeda FROM recebimentos_invoice WHERE invoice_id=?",
            (invoice_id,),
        ).fetchone()
        self.assertEqual((receipt["banco_credito_id"], receipt["data_credito"]), (bank_two_id, "2026-08-20"))
        self.assertEqual(app.Decimal(str(receipt["valor_moeda"])), app.Decimal("900"))
        conn.close()

        response = self.client.post(f"/invoice/{invoice_id}/editar", data={
            "empresa_id": "1", "numero_invoice": "INV-REFERENCE",
            "tipo_documento": "COMMERCIAL_INVOICE", "competencia_id": "1",
            "cliente_id": "1", "data_emissao": "01/08/2026", "moeda": "USD",
            "valor_moeda": "1000,00",
        })
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        self.assertEqual(conn.execute(
            "SELECT banco_referenciado_id FROM invoices WHERE id=?", (invoice_id,)
        ).fetchone()[0], 1)
        conn.close()

        response = self.client.post(f"/invoice/{invoice_id}/editar", data={
            "empresa_id": "1", "numero_invoice": "INV-REFERENCE",
            "tipo_documento": "COMMERCIAL_INVOICE", "competencia_id": "1",
            "cliente_id": "1", "banco_referenciado_id": "", "data_emissao": "01/08/2026",
            "moeda": "USD", "valor_moeda": "1000,00",
        })
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        self.assertIsNone(conn.execute(
            "SELECT banco_referenciado_id FROM invoices WHERE id=?", (invoice_id,)
        ).fetchone()[0])
        self.assertEqual(conn.execute(
            "SELECT banco_credito_id FROM recebimentos_invoice WHERE invoice_id=?", (invoice_id,)
        ).fetchone()[0], bank_two_id)
        conn.close()

        default_invoice_id = self._create_invoice(
            number="INV-REFERENCE-DEFAULT", value="500,00", banco_referenciado_id=1
        )
        response = self.client.post(f"/invoice/{default_invoice_id}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": today_br, "valor_moeda": "500,00",
        })
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        receipt = conn.execute(
            "SELECT banco_credito_id, data_credito, valor_moeda FROM recebimentos_invoice WHERE invoice_id=?",
            (default_invoice_id,),
        ).fetchone()
        self.assertEqual((receipt["banco_credito_id"], receipt["data_credito"]), (1, app.date.today().isoformat()))
        self.assertEqual(app.Decimal(str(receipt["valor_moeda"])), app.Decimal("500"))
        conn.close()

    def test_existing_reference_is_preserved_by_idempotent_schema_bootstrap(self):
        invoice_id = self._create_invoice(
            number="INV-REFERENCE-PRESERVED", banco_referenciado_id=1
        )
        conn = app.db()
        conn.execute(
            "INSERT INTO recebimentos_invoice(invoice_id,banco_credito_id,data_credito,moeda,valor_moeda) "
            "VALUES (?,?,?,?,?)", (invoice_id, 1, "2026-08-10", "USD", 1000)
        )
        conn.commit()
        conn.close()

        app.init_db()
        app.init_db()
        conn = app.db()
        self.assertEqual(conn.execute(
            "SELECT banco_referenciado_id FROM invoices WHERE id=?", (invoice_id,)
        ).fetchone()[0], 1)
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM recebimentos_invoice WHERE invoice_id=?", (invoice_id,)
        ).fetchone()[0], 1)
        conn.close()

    def test_invoice_report_consolidates_real_status_balances_and_copy_payloads(self):
        conn = app.db()
        conn.execute("INSERT INTO clientes (nome, pais) VALUES (?,?)", ("Cliente B", "BR"))
        client_b_id = conn.execute("SELECT id FROM clientes WHERE nome=?", ("Cliente B",)).fetchone()[0]
        conn.commit()
        conn.close()

        self._create_invoice(number="INV-REPORT-A", value="1000,00")
        self._create_invoice(number="INV-REPORT-B", value="300,00", client_id=client_b_id)
        received_a = self._create_invoice(number="INV-REPORT-C", value="1000,00")
        received_b = self._create_invoice(number="INV-REPORT-D", value="700,00", client_id=client_b_id)
        settled = self._create_invoice(number="INV-REPORT-E", value="50,00")
        self._create_invoice(number="INV-REPORT-EUR", value="900,00", currency="EUR")

        conn = app.db()
        conn.executemany(
            "INSERT INTO recebimentos_invoice (invoice_id,banco_credito_id,data_credito,moeda,valor_moeda) VALUES (?,?,?,?,?)",
            [(received_a, 1, "2026-08-10", "USD", 1000),
             (received_b, 1, "2026-08-11", "USD", 700),
             (settled, 1, "2026-08-12", "USD", 50)],
        )
        conn.executemany(
            "INSERT INTO contratos (numero_contrato,moeda,valor_moeda,status) VALUES (?,?,?,?)",
            [("C-REPORT-A", "USD", 600, "CONCLUIDO"),
             ("C-REPORT-B", "USD", 100, "CONCLUIDO"),
             ("C-REPORT-C", "USD", 50, "CONCLUIDO")],
        )
        contracts = conn.execute(
            "SELECT id FROM contratos WHERE numero_contrato LIKE 'C-REPORT-%' ORDER BY numero_contrato"
        ).fetchall()
        conn.executemany(
            "INSERT INTO invoice_contrato_cambio (invoice_id,contrato_id,valor_alocado) VALUES (?,?,?)",
            [(received_a, contracts[0][0], 600),
             (received_b, contracts[1][0], 100),
             (settled, contracts[2][0], 50)],
        )
        conn.commit()
        conn.close()

        response = self.client.get("/invoices")
        self.assertEqual(response.status_code, 200)
        self.assertIn("/invoices/relatorios", response.get_data(as_text=True))

        response = self.client.get("/invoices/relatorios")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Total de Invoices Recebidas", html)
        self.assertIn('/invoices/relatorios/imprimir/exchange', html)
        self.assertIn('/invoices/relatorios/imprimir/awaiting', html)
        self.assertIn("Imprimir / PDF", html)
        self.assertIn("USD 1.750,00", html)
        self.assertIn("USD 750,00", html)
        self.assertIn("USD 1.300,00", html)
        self.assertIn("USD 400,00", html)
        self.assertIn("USD 600,00", html)
        self.assertNotIn("INV-REPORT-EUR", html)
        self.assertEqual(html.count('data-copy-value='), 2)
        self.assertLess(html.index("Cliente B"), html.index("Cliente Teste"))
        self.assertIn("RECEBIDO AGUARDANDO CÂMBIO", html)
        self.assertIn("AGUARDANDO RECEBIMENTO", html)

        context = app.build_invoice_report_context()
        self.assertEqual(
            [row["cliente"] for row in context["tables"][0]["rows"]],
            ["Cliente B", "Cliente Teste"],
        )
        self.assertEqual(
            [row["cliente"] for row in context["tables"][1]["rows"]],
            ["Cliente Teste", "Cliente B"],
        )
        self.assertEqual(context["tables"][0]["total"], app.Decimal("1000"))
        self.assertEqual(context["tables"][1]["total"], app.Decimal("1300"))
        self.assertIn("TOTAL                                         USD 1.000,00", context["tables"][0]["copy_text"])
        self.assertIn("TOTAL                    USD 1.300,00", context["tables"][1]["copy_text"])
        self.assertIn("<table", context["tables"][0]["copy_html"])
        self.assertNotIn("RECEBIDO AGUARDANDO CÂMBIO", context["tables"][0]["copy_text"])
        self.assertIn("Cliente / Trading        Banco                USD", context["tables"][0]["copy_text"])
        self.assertIn("Banco", context["tables"][0]["copy_text"])
        self.assertEqual(context["tables"][0]["rows"][0]["banco"], "Banco Teste")
        self.assertIn("<thead", context["tables"][0]["copy_html"])
        self.assertIn("background:#f4fbf5", context["tables"][0]["copy_html"])
        self.assertIn("background:#fdf5f5", context["tables"][1]["copy_html"])

    def test_invoice_report_detail_groups_preserve_values_for_banks_and_statuses(self):
        conn = app.db()
        conn.execute("INSERT INTO contrapartes (nome) VALUES (?)", ("Banco Dois",))
        bank_two_id = conn.execute("SELECT id FROM contrapartes WHERE nome=?", ("Banco Dois",)).fetchone()[0]
        conn.commit()
        conn.close()

        awaiting_without_receipt = self._create_invoice("INV-DETAIL-AWAITING", "120,00")
        awaiting_partial = self._create_invoice("INV-DETAIL-PARTIAL", "500,00")
        received_multi_bank = self._create_invoice("INV-DETAIL-MULTI-BANK", "1000,00")

        conn = app.db()
        conn.executemany(
            "INSERT INTO recebimentos_invoice (invoice_id,banco_credito_id,data_credito,moeda,valor_moeda) VALUES (?,?,?,?,?)",
            [(awaiting_partial, 1, "2026-08-10", "USD", 200),
             (received_multi_bank, 1, "2026-08-11", "USD", 400),
             (received_multi_bank, bank_two_id, "2026-08-12", "USD", 600)],
        )
        conn.commit()
        conn.close()

        context = app.build_invoice_report_context()
        exchange = context["tables"][0]
        awaiting = context["tables"][1]

        self.assertEqual(len(exchange["detail_groups"]), 1)
        exchange_bank = exchange["detail_groups"][0]
        self.assertEqual(exchange_bank["banco"], "Banco Dois, Banco Teste")
        self.assertEqual(exchange_bank["subtotal"], app.Decimal("1000"))
        exchange_invoices = exchange_bank["empresas"][0]["clientes"][0]["invoices"]
        self.assertEqual([(row["numero"], row["valor"]) for row in exchange_invoices],
                         [("INV-DETAIL-MULTI-BANK", app.Decimal("1000"))])
        self.assertEqual(exchange["empresa_totals"], [{"empresa": "Teste", "valor": app.Decimal("1000")}])
        self.assertEqual(exchange["total"], app.Decimal("1000"))

        awaiting_groups = {group["banco"]: group for group in awaiting["detail_groups"]}
        self.assertEqual(set(awaiting_groups), {"-", "Banco Teste"})
        self.assertEqual(awaiting_groups["-"]["subtotal"], app.Decimal("120"))
        self.assertEqual(awaiting_groups["Banco Teste"]["subtotal"], app.Decimal("300"))
        self.assertEqual(
            [(row["numero"], row["valor"]) for row in awaiting_groups["-"]["empresas"][0]["clientes"][0]["invoices"]],
            [("INV-DETAIL-AWAITING", app.Decimal("120"))],
        )
        self.assertEqual(
            [(row["numero"], row["valor"]) for row in awaiting_groups["Banco Teste"]["empresas"][0]["clientes"][0]["invoices"]],
            [("INV-DETAIL-PARTIAL", app.Decimal("300"))],
        )
        self.assertEqual(awaiting["empresa_totals"], [{"empresa": "Teste", "valor": app.Decimal("420")}])
        self.assertEqual(awaiting["total"], app.Decimal("420"))

        response = self.client.get("/invoices/relatorios")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertEqual(html.count("data-report-details-toggle"), 2)
        self.assertIn('id="invoice-report-details-exchange"', html)
        self.assertIn('id="invoice-report-details-awaiting"', html)
        self.assertIn('aria-expanded="false"', html)
        self.assertIn("BANCO: Banco Dois, Banco Teste", html)
        self.assertIn("BANCO: -", html)
        self.assertIn("INVOICE INV-DETAIL-MULTI-BANK", html)
        self.assertIn("INVOICE INV-DETAIL-AWAITING", html)
        self.assertEqual(html.count("invoice-report-company-table"), 2)
        self.assertIn('<th>EMPRESA</th><th>USD</th><th class="invoice-report-percent-header">%</th>', html)
        self.assertIn("29%", html)
        self.assertIn("71%", html)
        self.assertIn("100%", html)
        self.assertEqual(html.count("invoice-report-detail-columns"), 2)
        self.assertEqual(html.count("invoice-report-detail-summary-table"), 6)
        self.assertEqual(html.count("invoice-report-detail-summary-title"), 8)
        self.assertEqual(html.count("TOTAL POR EMPRESA"), 2)
        self.assertEqual(html.count("TOTAL POR BANCO"), 2)
        self.assertEqual(html.count("TOTAL POR CLIENTE / TRADING"), 2)
        self.assertEqual(html.count("TOTAL POR INVOICES"), 2)
        self.assertNotIn("Banco / Empresa / Cliente / Invoice", html)
        self.assertNotIn("invoice-report-detail-hierarchy-header", html)
        self.assertEqual(html.count("invoice-report-bank-table"), 2)
        self.assertEqual(html.count("invoice-report-client-table"), 2)
        self.assertIn('<th>BANCO</th><th>USD</th><th class="invoice-report-percent-header">%</th>', html)
        self.assertIn('<th>CLIENTE / TRADING</th><th>USD</th><th class="invoice-report-percent-header">%</th>', html)
        self.assertEqual(html.count('class="compact-table invoice-report-table invoice-report-table--exchange">'), 1)
        self.assertEqual(html.count('class="compact-table invoice-report-table invoice-report-table--awaiting">'), 1)
        self.assertLess(html.index('class="compact-table invoice-report-table invoice-report-table--exchange">'), html.index('class="invoice-report-detail-columns"'))
        detail_html = html[html.index('class="invoice-report-detail-columns"'):]
        detail_html = detail_html[:detail_html.index("</section>")]
        self.assertEqual(detail_html.count("<tfoot><tr><th>TOTAL</th>"), 3)
        self.assertLess(html.index("invoice-report-company-table"), html.index("invoice-report-bank-table"))
        self.assertLess(html.index("invoice-report-bank-table"), html.index("invoice-report-client-table"))
        self.assertNotIn("CÃ", html)

    def test_invoice_report_print_view_renders_expanded_html_and_portrait_print_css(self):
        self._create_invoice("INV-PRINT-AWAITING", "120,00")
        received_invoice = self._create_invoice("INV-PRINT-EXCHANGE", "80,00")

        conn = app.db()
        conn.execute(
            "INSERT INTO recebimentos_invoice (invoice_id,banco_credito_id,data_credito,moeda,valor_moeda) VALUES (?,?,?,?,?)",
            (received_invoice, 1, "2026-08-20", "USD", 80),
        )
        conn.commit()
        conn.close()

        response = self.client.get("/invoices/relatorios/imprimir/awaiting")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content_type.startswith("text/html"))
        html = response.get_data(as_text=True)
        self.assertIn("Imprimir / Salvar PDF", html)
        self.assertIn("window.print()", html)
        self.assertNotIn("data-report-details-toggle", html)
        self.assertNotIn(" hidden", html)
        self.assertEqual(html.count("invoice-report-detail-table"), 1)
        self.assertNotIn("invoice-print-summary", html)
        self.assertNotIn("summary-cards", html)
        self.assertNotIn("<h1", html)
        self.assertNotIn("<h2", html)
        self.assertNotIn("Banco / Empresa / Cliente / Invoice", html)
        self.assertIn("<div class=\"invoice-report-print-title\">AGUARDANDO RECEBIMENTO</div>", html)
        self.assertEqual(html.count("invoice-report-company-table"), 1)
        self.assertIn('<th>EMPRESA</th><th>USD</th><th class="invoice-report-percent-header">%</th>', html)
        self.assertEqual(html.count("invoice-report-detail-columns"), 1)
        self.assertEqual(html.count("invoice-report-detail-summary-table"), 3)
        self.assertEqual(html.count("invoice-report-detail-summary-title"), 4)
        self.assertIn("TOTAL POR EMPRESA", html)
        self.assertIn("TOTAL POR BANCO", html)
        self.assertIn("TOTAL POR CLIENTE / TRADING", html)
        self.assertIn("TOTAL POR INVOICES", html)
        self.assertNotIn("invoice-report-detail-hierarchy-header", html)
        self.assertEqual(html.count("invoice-report-bank-table"), 1)
        self.assertEqual(html.count("invoice-report-client-table"), 1)
        self.assertIn('<th>BANCO</th><th>USD</th><th class="invoice-report-percent-header">%</th>', html)
        self.assertIn('<th>CLIENTE / TRADING</th><th>USD</th><th class="invoice-report-percent-header">%</th>', html)
        self.assertIn("BANCO: -", html)
        self.assertIn("INVOICE INV-PRINT-AWAITING", html)
        self.assertNotIn("INVOICE INV-PRINT-EXCHANGE", html)
        self.assertIn("invoice-detail-spacer", html)
        self.assertIn("invoice-detail-row--bank", html)
        self.assertIn("invoice-detail-row--company", html)
        self.assertIn("invoice-detail-row--client", html)

        response = self.client.get("/invoices/relatorios/imprimir/exchange")
        self.assertEqual(response.status_code, 200)
        exchange_html = response.get_data(as_text=True)
        self.assertEqual(exchange_html.count("invoice-report-detail-table"), 1)
        self.assertNotIn("summary-cards", exchange_html)
        self.assertNotIn("<h1", exchange_html)
        self.assertNotIn("<h2", exchange_html)
        self.assertIn("<div class=\"invoice-report-print-title\">RECEBIDO AGUARDANDO CÂMBIO</div>", exchange_html)
        self.assertEqual(exchange_html.count("invoice-report-company-table"), 1)
        self.assertIn('<th>EMPRESA</th><th>USD</th><th class="invoice-report-percent-header">%</th>', exchange_html)
        self.assertEqual(exchange_html.count("invoice-report-detail-columns"), 1)
        self.assertEqual(exchange_html.count("invoice-report-detail-summary-table"), 3)
        self.assertEqual(exchange_html.count("invoice-report-detail-summary-title"), 4)
        self.assertIn("TOTAL POR INVOICES", exchange_html)
        self.assertNotIn("Banco / Empresa / Cliente / Invoice", exchange_html)
        self.assertNotIn("invoice-report-detail-hierarchy-header", exchange_html)
        self.assertEqual(exchange_html.count("invoice-report-bank-table"), 1)
        self.assertEqual(exchange_html.count("invoice-report-client-table"), 1)
        self.assertIn("INVOICE INV-PRINT-EXCHANGE", exchange_html)
        self.assertNotIn("INVOICE INV-PRINT-AWAITING", exchange_html)

        css_response = self.client.get("/static/style.css")
        try:
            css = css_response.get_data(as_text=True)
        finally:
            css_response.close()
        self.assertIn("@page invoice-report{size:A4 portrait", css)
        self.assertIn("#dcecff", css)
        self.assertIn("#eee5f7", css)
        self.assertIn("#fff7d6", css)
        self.assertIn(".invoice-report-company-table th:last-child,.invoice-report-company-table td:last-child{text-align:right}", css)
        self.assertIn("size:A4 portrait", css)
        self.assertIn(".invoice-report-print .invoice-detail-spacer td", css)
        self.assertIn(
            ".invoice-report-detail .invoice-report-detail-columns{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px",
            css,
        )
        self.assertIn(
            ".invoice-report-print .invoice-report-detail .invoice-report-detail-columns{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}",
            css,
        )
        self.assertNotIn("grid-template-columns:minmax(0,1fr) minmax(0,1.35fr)", css)
        self.assertIn(".invoice-report-detail .invoice-report-detail-summary-title", css)
        self.assertIn(".invoice-report-detail .invoice-report-detail-summary-section{margin:0 0 24px}", css)
        self.assertIn(".invoice-report-print .invoice-report-detail .invoice-report-detail-summary-section{margin-bottom:12px}", css)
        self.assertIn(".invoice-report-detail .table-wrap{min-width:0;overflow:visible}", css)
        self.assertIn("invoice-report-percent-header", css)
        self.assertNotIn("padding-inline:2px", css)
        self.assertIn("width:88px;padding-left:5px;padding-right:7px", css)
        self.assertIn(".invoice-report-detail-summary{border-right:1px solid #d8e1e8;padding-right:0}", css)
        self.assertIn(".invoice-report-detail{padding-top:10px}", css)
        self.assertIn(".invoice-report-print .invoice-report-detail{padding-top:5px}", css)
        self.assertNotIn("text-overflow:ellipsis", css)
        self.assertIn("white-space:normal;overflow-wrap:anywhere", css)
        self.assertNotIn("invoice-print-summary", css)

    def test_invoice_competencies_are_scoped_to_company_and_cross_company_selection_is_rejected(self):
        conn = app.db()
        conn.execute("INSERT INTO empresas (razao_social, cnpj, apelido) VALUES (?,?,?)",
                     ("Outra Empresa", "12345678000199", "Outra"))
        conn.execute("INSERT INTO competencias (empresa_id, descricao, data_inicial, data_final) VALUES (?,?,?,?)",
                     (2, "Setembro/2026", "2026-09-01", "2026-09-30"))
        conn.commit()
        conn.close()

        form = self.client.get("/invoice/nova")
        self.assertEqual(form.status_code, 200)
        html = form.get_data(as_text=True)
        self.assertIn('name="competencia_id" data-competencia-select', html)
        self.assertIn('data-competencia-empresa="1"', html)
        self.assertIn('data-competencia-empresa="2"', html)

        response = self.client.post("/invoice/nova", data={
            "empresa_id": "1", "numero_invoice": "INV-WRONG-COMPETENCIA",
            "tipo_documento": "COMMERCIAL_INVOICE", "competencia_id": "2",
            "data_emissao": "01/08/2026", "moeda": "USD", "valor_moeda": "100,00",
        })
        self.assertEqual(response.status_code, 200)
        conn = app.db()
        self.assertIsNone(conn.execute(
            "SELECT id FROM invoices WHERE numero_invoice='INV-WRONG-COMPETENCIA'"
        ).fetchone())
        conn.close()

    def test_statuses_and_separate_balances(self):
        self.assertEqual(app.invoice_status_from_totals(1000, 0, 0), app.INVOICE_STATUS_AGUARDANDO_RECEBIMENTO)
        self.assertEqual(app.invoice_status_from_totals(1000, 800, 0), app.INVOICE_STATUS_PARCIAL)
        self.assertEqual(
            app.invoice_status_from_totals(1000, 1000, 400, 600),
            app.INVOICE_STATUS_AGUARDANDO_CONTRATO,
        )
        self.assertEqual(app.invoice_status_from_totals(1000, 1000, 600), app.INVOICE_STATUS_RECEBIDA_AGUARDANDO_CAMBIO)
        self.assertEqual(app.invoice_status_from_totals(1000, 1000, 1000), app.INVOICE_STATUS_LIQUIDADA)

    def test_invoice_status_can_be_assigned_and_is_preserved_after_movements(self):
        invoice_id = self._create_invoice()
        response = self.client.post(f"/invoice/{invoice_id}/editar", data={
            "empresa_id": "1", "numero_invoice": "INV-001", "tipo_documento": "COMMERCIAL_INVOICE",
            "competencia_id": "1", "cliente_id": "1", "data_emissao": "01/08/2026", "moeda": "USD",
            "valor_moeda": "1000,00", "status": "RECEBIDO AGUARDANDO CAMBIO", "data_credito": "10/08/2026",
        })
        self.assertEqual(response.status_code, 302)
        self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "10/08/2026", "valor_moeda": "1000,00",
        })
        conn = app.db()
        invoice = conn.execute("SELECT status, status_manual FROM invoices WHERE id=?", (invoice_id,)).fetchone()
        self.assertEqual(invoice["status"], app.INVOICE_STATUS_RECEBIDA_AGUARDANDO_CAMBIO)
        self.assertEqual(invoice["status_manual"], 1)
        conn.close()

    def test_saved_receipt_moves_awaiting_invoice_to_received_exchange_status(self):
        invoice_id = self._create_invoice("INV-RECEIPT-STATUS", "100,00")
        conn = app.db()
        conn.execute(
            "UPDATE invoices SET status=?, status_manual=1 WHERE id=?",
            (app.INVOICE_STATUS_AGUARDANDO_RECEBIMENTO, invoice_id),
        )
        conn.commit()
        conn.close()

        response = self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "20/08/2026", "valor_moeda": "100,00",
        })
        self.assertEqual(response.status_code, 302)

        conn = app.db()
        invoice = conn.execute(
            "SELECT status, data_credito FROM invoices WHERE id=?", (invoice_id,)
        ).fetchone()
        self.assertEqual(invoice["status"], app.INVOICE_STATUS_RECEBIDA_AGUARDANDO_CAMBIO)
        self.assertEqual(invoice["data_credito"], "2026-08-20")
        conn.close()

    def test_received_status_requires_credit_date_and_awaiting_clears_it(self):
        invoice_id = self._create_invoice(number="INV-CREDIT-DATE")
        base_data = {
            "empresa_id": "1", "numero_invoice": "INV-CREDIT-DATE", "tipo_documento": "COMMERCIAL_INVOICE",
            "competencia_id": "1", "cliente_id": "1", "data_emissao": "01/08/2026", "moeda": "USD",
            "valor_moeda": "1000,00",
        }
        response = self.client.post(
            f"/invoice/{invoice_id}/editar",
            data={**base_data, "status": "RECEBIDO AGUARDANDO CAMBIO"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("data do crédito", response.get_data(as_text=True).lower())
        conn = app.db()
        invoice = conn.execute("SELECT status, data_credito FROM invoices WHERE id=?", (invoice_id,)).fetchone()
        self.assertEqual(invoice["status"], app.INVOICE_STATUS_AGUARDANDO_RECEBIMENTO)
        self.assertIsNone(invoice["data_credito"])
        conn.close()

        response = self.client.post(
            f"/invoice/{invoice_id}/editar",
            data={**base_data, "status": "AGUARDANDO CONTRATO"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("data do crédito", response.get_data(as_text=True).lower())
        response = self.client.post(
            f"/invoice/{invoice_id}/editar",
            data={**base_data, "status": "AGUARDANDO CONTRATO", "data_credito": "16/08/2026"},
        )
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        invoice = conn.execute("SELECT status, data_credito FROM invoices WHERE id=?", (invoice_id,)).fetchone()
        self.assertEqual(invoice["status"], app.INVOICE_STATUS_AGUARDANDO_CONTRATO)
        self.assertEqual(invoice["data_credito"], "2026-08-16")
        conn.close()

        response = self.client.post(
            f"/invoice/{invoice_id}/editar",
            data={**base_data, "status": "RECEBIDO AGUARDANDO CAMBIO", "data_credito": "15/08/2026"},
        )
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        invoice = conn.execute("SELECT status, data_credito FROM invoices WHERE id=?", (invoice_id,)).fetchone()
        self.assertEqual(invoice["status"], app.INVOICE_STATUS_RECEBIDA_AGUARDANDO_CAMBIO)
        self.assertEqual(invoice["data_credito"], "2026-08-15")
        conn.close()

        response = self.client.post(
            f"/invoice/{invoice_id}/editar",
            data={**base_data, "status": "AGUARDANDO RECEBIMENTO", "data_credito": "15/08/2026"},
        )
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        invoice = conn.execute("SELECT status, data_credito FROM invoices WHERE id=?", (invoice_id,)).fetchone()
        self.assertEqual(invoice["status"], app.INVOICE_STATUS_AGUARDANDO_RECEBIMENTO)
        self.assertIsNone(invoice["data_credito"])
        conn.close()

    def test_invoice_status_is_supported_by_excel_model_and_import(self):
        import pandas as pd

        model = pd.read_excel(BytesIO(self.client.get("/invoices/modelo").data))
        self.assertIn("status", model.columns)
        self.assertIn("data_credito", model.columns)
        orientations = pd.read_excel(BytesIO(self.client.get("/invoices/modelo").data), sheet_name="Orientações")
        self.assertIn("AGUARDANDO CONTRATO", set(orientations["STATUS"].dropna()))
        for column in (
            "empresa", "invoice", "tipo", "banco_referenciado", "banco_credito", "banco_liquidacao", "contrato_cambio",
            "data_fechamento", "data_liquidacao", "valor_moeda", "taxa_cambio", "valor_brl",
        ):
            self.assertIn(column, model.columns)
        frame = pd.DataFrame([{
            "cnpj": "45.765.914/0001-81", "numero_invoice": "INV-STATUS-IMPORT",
            "tipo_documento": "COMMERCIAL INVOICE", "competencia": "Agosto/2026",
            "data_emissao": "01/08/2026", "moeda": "USD", "valor_invoice": "100,00",
            "status": "RECEBIDO AGUARDANDO CAMBIO", "data_credito": "10/08/2026",
        }])
        output = BytesIO()
        frame.to_excel(output, index=False)
        output.seek(0)
        response = self.client.post("/invoices/importar", data={"arquivo": (output, "status.xlsx")},
                                    content_type="multipart/form-data")
        self.assertEqual(response.status_code, 200)
        self.assertIn("RECEBIDO AGUARDANDO CAMBIO", response.get_data(as_text=True))
        with self.client.session_transaction() as session:
            token = session["invoice_import_stage"]
        self.assertEqual(self.client.post("/invoices/importar/confirmar", data={"stage_token": token}).status_code, 302)
        conn = app.db()
        invoice = conn.execute(
            "SELECT status, status_manual, data_credito FROM invoices WHERE numero_invoice='INV-STATUS-IMPORT'"
        ).fetchone()
        self.assertEqual(invoice["status"], app.INVOICE_STATUS_RECEBIDA_AGUARDANDO_CAMBIO)
        self.assertEqual(invoice["status_manual"], 1)
        self.assertEqual(invoice["data_credito"], "2026-08-10")
        conn.close()

    def test_excel_received_status_requires_credit_date(self):
        import pandas as pd

        frame = pd.DataFrame([{
            "cnpj": "45.765.914/0001-81", "numero_invoice": "INV-MISSING-CREDIT-DATE",
            "tipo_documento": "COMMERCIAL INVOICE", "competencia": "Agosto/2026",
            "valor_invoice": "100,00", "status": "RECEBIDO AGUARDANDO CAMBIO",
        }])
        with self.assertRaisesRegex(ValueError, "data_credito"):
            app.prepare_invoice_import_rows(frame, pd)

    def test_excel_awaiting_contract_status_is_imported_with_credit_date(self):
        import pandas as pd

        frame = pd.DataFrame([{
            "cnpj": "45.765.914/0001-81", "numero_invoice": "INV-CONTRACT-IMPORT",
            "tipo_documento": "COMMERCIAL INVOICE", "competencia": "Agosto/2026",
            "valor_invoice": "100,00", "status": "AGUARDANDO CONTRATO",
            "data_credito": "10/08/2026",
        }])
        output = BytesIO()
        frame.to_excel(output, index=False)
        output.seek(0)
        response = self.client.post(
            "/invoices/importar", data={"arquivo": (output, "awaiting-contract.xlsx")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 200)
        with self.client.session_transaction() as session:
            token = session["invoice_import_stage"]
        self.assertEqual(
            self.client.post("/invoices/importar/confirmar", data={"stage_token": token}).status_code,
            302,
        )
        conn = app.db()
        invoice = conn.execute(
            "SELECT status, status_manual, data_credito FROM invoices WHERE numero_invoice=?",
            ("INV-CONTRACT-IMPORT",),
        ).fetchone()
        self.assertEqual(invoice["status"], app.INVOICE_STATUS_AGUARDANDO_CONTRATO)
        self.assertEqual(invoice["status_manual"], 1)
        self.assertEqual(invoice["data_credito"], "2026-08-10")
        conn.close()

    def test_invoice_import_registers_receipt_and_closed_exchange(self):
        import pandas as pd

        conn = app.db()
        conn.execute("INSERT INTO contrapartes (nome) VALUES (?)", ("Banco Liquidação",))
        conn.commit()
        conn.close()
        frame = pd.DataFrame([{
            "Empresa": "Teste", "Invoice": "INV-FULL-IMPORT", "Contrato comercial": "COM-FULL",
            "Competência": "Agosto/2026", "Tipo": "COMMERCIAL INVOICE", "Banco Crédito": "Banco Teste",
            "Banco Liquidação": "Banco Liquidação", "Contrato Câmbio": "C-FULL",
            "Cliente": "Cliente Teste", "Emissão": "01/08/2026", "Data Crédito": "10/08/2026",
            "Data Fechamento": "11/08/2026", "Data Liquidação": "12/08/2026", "Moeda": "USD",
            "Valor Moeda": "1000,00", "Taxa Câmbio": "5,10", "Valor BRL": "5100,00",
            "Status": "LIQUIDADA",
        }])
        output = BytesIO()
        frame.to_excel(output, index=False)
        output.seek(0)
        response = self.client.post("/invoices/importar", data={"arquivo": (output, "full-invoice.xlsx")},
                                    content_type="multipart/form-data")
        self.assertEqual(response.status_code, 200)
        with self.client.session_transaction() as session:
            token = session["invoice_import_stage"]
        self.assertEqual(self.client.post("/invoices/importar/confirmar", data={"stage_token": token}).status_code, 302)

        conn = app.db()
        invoice = conn.execute(
            "SELECT id, status, status_manual, data_credito, banco_referenciado_id, desdobramento_habilitado "
            "FROM invoices WHERE numero_invoice='INV-FULL-IMPORT'"
        ).fetchone()
        self.assertEqual(invoice["status"], app.INVOICE_STATUS_LIQUIDADA)
        self.assertEqual(invoice["status_manual"], 1)
        self.assertEqual(invoice["data_credito"], "2026-08-10")
        self.assertEqual(invoice["banco_referenciado_id"], 1)
        self.assertEqual(invoice["desdobramento_habilitado"], 1)
        receipt = conn.execute(
            "SELECT banco_credito_id, data_credito, valor_moeda FROM recebimentos_invoice WHERE invoice_id=?",
            (invoice["id"],),
        ).fetchone()
        self.assertEqual((receipt["banco_credito_id"], receipt["data_credito"]), (1, "2026-08-10"))
        self.assertEqual(app.Decimal(str(receipt["valor_moeda"])), app.Decimal("1000"))
        contract = conn.execute(
            "SELECT id, banco_liquidacao_id, data_fechamento, data_liquidacao, taxa_cambio, valor_moeda, valor_reais "
            "FROM contratos WHERE numero_contrato='C-FULL'"
        ).fetchone()
        self.assertEqual(contract["banco_liquidacao_id"], 2)
        self.assertEqual((contract["data_fechamento"], contract["data_liquidacao"]), ("2026-08-11", "2026-08-12"))
        self.assertEqual(app.Decimal(str(contract["taxa_cambio"])), app.Decimal("5.1"))
        self.assertEqual(app.Decimal(str(contract["valor_moeda"])), app.Decimal("1000"))
        self.assertEqual(app.Decimal(str(contract["valor_reais"])), app.Decimal("5100"))
        link = conn.execute(
            "SELECT valor_alocado FROM invoice_contrato_cambio WHERE invoice_id=? AND contrato_id=?",
            (invoice["id"], contract["id"]),
        ).fetchone()
        self.assertEqual(app.Decimal(str(link["valor_alocado"])), app.Decimal("1000"))
        conn.close()

    def test_schema_bootstrap_is_idempotent_and_preserves_existing_domains(self):
        conn = app.db()
        before = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("empresas", "clientes", "dues", "ndfs", "ptax_cotacoes", "contratos")
        }
        conn.close()
        app.init_db()
        conn = app.db()
        after = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in before
        }
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(before, after)
        self.assertEqual(version, app.INVOICE_SCHEMA_VERSION)
        self.assertIn("contrato_comercial", {
            row[1] for row in conn.execute("PRAGMA table_info(invoices)").fetchall()
        })
        self.assertTrue(any(
            row[1] == "idx_invoices_contrato_comercial"
            for row in conn.execute("PRAGMA index_list(invoices)").fetchall()
        ))
        conn.close()

    def test_schema_migrates_v1_to_v6_without_recreating_existing_contracts(self):
        migration_path = Path(tempfile.mktemp(prefix="duecontrol_invoice_migration_", suffix=".db"))
        previous_db = app.DB
        try:
            app.DB = migration_path
            conn = sqlite3.connect(migration_path)
            conn.executescript("""
                CREATE TABLE empresas (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, razao_social TEXT NOT NULL,
                    cnpj TEXT NOT NULL UNIQUE, apelido TEXT, created_at TEXT
                );
                CREATE TABLE clientes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT NOT NULL,
                    pais TEXT NOT NULL, created_at TEXT, UNIQUE(nome, pais)
                );
                CREATE TABLE contrapartes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT NOT NULL UNIQUE,
                    created_at TEXT
                );
                CREATE TABLE dues (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, numero_due TEXT NOT NULL UNIQUE,
                    data_due TEXT, cnpj TEXT, cliente TEXT, moeda TEXT NOT NULL DEFAULT 'USD',
                    valor_original REAL NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'PENDENTE',
                    observacao TEXT, competencia_id INTEGER
                );
                CREATE TABLE contratos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, numero_contrato TEXT NOT NULL UNIQUE,
                    banco TEXT, banco_credito TEXT, banco_liquidacao TEXT, data_contrato TEXT,
                    data_fechamento TEXT,
                    data_recebimento TEXT, data_liquidacao TEXT, cnpj TEXT, cliente TEXT,
                    moeda TEXT NOT NULL DEFAULT 'USD', valor_moeda REAL NOT NULL DEFAULT 0,
                    taxa_cambio REAL, valor_reais REAL, status TEXT NOT NULL DEFAULT 'PENDENTE',
                    saldo_zerado_manual INTEGER NOT NULL DEFAULT 0, observacao TEXT,
                    created_at TEXT, competencia_id INTEGER
                );
                CREATE TABLE invoices (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, empresa_id INTEGER NOT NULL,
                    numero_invoice TEXT NOT NULL, tipo_documento TEXT NOT NULL,
                    cliente_id INTEGER, data_emissao TEXT, moeda TEXT NOT NULL DEFAULT 'USD',
                    valor_moeda REAL NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'AGUARDANDO_RECEBIMENTO',
                    observacao TEXT, created_at TEXT,
                    UNIQUE(empresa_id, numero_invoice, tipo_documento)
                );
                CREATE TABLE recebimentos_invoice (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, invoice_id INTEGER NOT NULL,
                    banco_credito_id INTEGER, data_credito TEXT NOT NULL,
                    moeda TEXT NOT NULL, valor_moeda REAL NOT NULL,
                    documento TEXT, observacao TEXT, created_at TEXT
                );
                INSERT INTO empresas(razao_social, cnpj, apelido) VALUES ('Empresa V1', '11111111000111', 'V1');
                INSERT INTO clientes(nome, pais) VALUES ('Cliente V1', 'BR');
                INSERT INTO contrapartes(nome) VALUES ('Banco Antigo'), ('Banco Mais Antigo');
                INSERT INTO dues(numero_due, moeda, valor_original) VALUES ('DUE-V1', 'USD', 10);
                INSERT INTO contratos(numero_contrato, moeda, valor_moeda) VALUES ('C-V1', 'USD', 10);
                INSERT INTO invoices(empresa_id, numero_invoice, tipo_documento, valor_moeda)
                    VALUES (1, 'INV-V1', 'COMMERCIAL_INVOICE', 10);
                INSERT INTO recebimentos_invoice(invoice_id,banco_credito_id,data_credito,moeda,valor_moeda)
                    VALUES (1,2,'2026-08-20','USD',10);
                INSERT INTO recebimentos_invoice(invoice_id,banco_credito_id,data_credito,moeda,valor_moeda)
                    VALUES (1,1,'2026-08-10','USD',10);
                PRAGMA user_version = 1;
            """)
            conn.commit()
            conn.close()

            app.init_db()
            conn = app.db()
            self.assertEqual(
                conn.execute("PRAGMA user_version").fetchone()[0],
                app.INVOICE_SCHEMA_VERSION,
            )
            self.assertIsNotNone(conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='fechamentos_cambio'"
            ).fetchone())
            self.assertIsNotNone(conn.execute(
                "SELECT id FROM invoices WHERE numero_invoice='INV-V1'"
            ).fetchone())
            self.assertEqual(conn.execute(
                "SELECT numero_contrato FROM contratos WHERE id=1"
            ).fetchone()[0], "C-V1")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM empresas").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM clientes").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM dues").fetchone()[0], 1)
            self.assertIn("contrato_comercial", {
                row[1] for row in conn.execute("PRAGMA table_info(invoices)").fetchall()
            })
            self.assertIn("competencia_id", {
                row[1] for row in conn.execute("PRAGMA table_info(invoices)").fetchall()
            })
            self.assertIn("banco_referenciado_id", {
                row[1] for row in conn.execute("PRAGMA table_info(invoices)").fetchall()
            })
            self.assertEqual(conn.execute(
                "SELECT banco_referenciado_id FROM invoices WHERE numero_invoice='INV-V1'"
            ).fetchone()[0], 1)
            self.assertEqual(conn.execute(
                "SELECT desdobramento_habilitado FROM invoices WHERE numero_invoice='INV-V1'"
            ).fetchone()[0], 0)
            invoice_schema = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='invoices'"
            ).fetchone()[0]
            self.assertIn("AGUARDANDO_CONTRATO", invoice_schema)
            self.assertTrue(any(
                row[1] == "idx_invoices_status"
                for row in conn.execute("PRAGMA index_list(invoices)").fetchall()
            ))
            conn.close()
        finally:
            app.DB = previous_db
            migration_path.unlink(missing_ok=True)

    def test_commercial_contract_is_optional_trimmed_reusable_and_visible(self):
        self.assertIsNone(app.normalize_contract_commercial("   "))
        self.assertEqual(app.normalize_contract_commercial("  COM-001  "), "COM-001")
        with self.assertRaises(ValueError):
            app.normalize_contract_commercial("x" * 121)
        with self.assertRaises(ValueError):
            app.normalize_contract_commercial("COM-001\nSECOND")

        invoice_id = self._create_invoice(commercial="  COM-001  ")
        second_id = self._create_invoice("INV-COM-002", "500,00", commercial="COM-001")
        conn = app.db()
        rows = conn.execute(
            "SELECT numero_invoice, contrato_comercial FROM invoices WHERE id IN (?,?) ORDER BY id",
            (invoice_id, second_id),
        ).fetchall()
        self.assertEqual([(row[0], row[1]) for row in rows], [(
            "INV-001", "COM-001"), ("INV-COM-002", "COM-001")])
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM contratos").fetchone()[0], 0)
        conn.close()

        detail = self.client.get(f"/invoice/{invoice_id}")
        self.assertEqual(detail.status_code, 200)
        self.assertIn("Contrato comercial", detail.get_data(as_text=True))
        self.assertIn("COM-001", detail.get_data(as_text=True))
        listing = self.client.get("/invoices?contrato_comercial=COM-001")
        self.assertEqual(listing.status_code, 200)
        listing_text = listing.get_data(as_text=True)
        self.assertIn("INV-001", listing_text)
        self.assertIn("INV-COM-002", listing_text)
        self.assertIn("Contrato Câmbio", listing_text)

        response = self.client.post(f"/invoice/{invoice_id}/editar", data={
            "empresa_id": "1", "numero_invoice": "INV-001", "tipo_documento": "COMMERCIAL_INVOICE",
            "cliente_id": "1", "data_emissao": "01/08/2026", "moeda": "USD",
            "valor_moeda": "1000,00", "contrato_comercial": "COM-UPDATED",
        })
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        self.assertEqual(conn.execute(
            "SELECT contrato_comercial FROM invoices WHERE id=?", (invoice_id,)
        ).fetchone()[0], "COM-UPDATED")
        conn.close()

    def test_invoice_import_normalizes_existing_clients_without_duplicates(self):
        import pandas as pd

        conn = app.db()
        conn.execute("INSERT INTO clientes (nome, pais) VALUES (?,?)", ("José da Silva", "BR"))
        conn.commit()
        conn.close()
        frame = pd.DataFrame([
            {"cnpj": "45.765.914/0001-81", "numero_invoice": "INV-CLIENT-1",
             "tipo_documento": "COMMERCIAL INVOICE", "cliente": "  CLIENTE   TESTE  ",
             "data_emissao": "01/08/2026", "moeda": "USD", "valor_invoice": "100,00"},
            {"cnpj": "45.765.914/0001-81", "numero_invoice": "INV-CLIENT-2",
            "tipo_documento": "COMMERCIAL INVOICE", "cliente": "JOSE   DA SILVA FILIAL",
             "cliente_pais": "Brasil", "data_emissao": "01/08/2026", "moeda": "USD",
             "valor_invoice": "100,00"},
        ])
        frame["competencia"] = "Agosto/2026"
        output = BytesIO()
        frame.to_excel(output, index=False)
        output.seek(0)
        response = self.client.post("/invoices/importar", data={"arquivo": (output, "normalized-clients.xlsx")},
                                    content_type="multipart/form-data")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Clientes não identificados", response.get_data(as_text=True))
        with self.client.session_transaction() as session:
            token = session["invoice_import_stage"]
        self.assertEqual(self.client.post("/invoices/importar/confirmar", data={"stage_token": token}).status_code, 302)
        conn = app.db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM clientes").fetchone()[0], 2)
        self.assertEqual(conn.execute("""
            SELECT i.cliente_id, c.nome, c.pais
            FROM invoices i JOIN clientes c ON c.id=i.cliente_id
            WHERE i.numero_invoice='INV-CLIENT-1'
        """).fetchone()[1:], ("Cliente Teste", "US"))
        self.assertEqual(conn.execute("""
            SELECT i.cliente_id, c.nome, c.pais
            FROM invoices i JOIN clientes c ON c.id=i.cliente_id
            WHERE i.numero_invoice='INV-CLIENT-2'
        """).fetchone()[1:], ("José da Silva", "BR"))
        conn.close()

    def test_invoice_import_suggests_new_client_and_country_before_creation(self):
        import pandas as pd

        frame = pd.DataFrame([{
            "cnpj": "45.765.914/0001-81", "numero_invoice": "INV-NEW-CLIENT",
            "tipo_documento": "COMMERCIAL INVOICE", "cliente": "  Novo   Cliente  ",
            "data_emissao": "01/08/2026", "moeda": "USD", "valor_invoice": "100,00",
        }])
        frame["competencia"] = "Agosto/2026"
        output = BytesIO()
        frame.to_excel(output, index=False)
        output.seek(0)
        response = self.client.post("/invoices/importar", data={"arquivo": (output, "new-client.xlsx")},
                                    content_type="multipart/form-data")
        self.assertEqual(response.status_code, 200)
        preview = response.get_data(as_text=True)
        self.assertIn("Clientes não identificados", preview)
        self.assertIn("Cliente novo sugerido: Novo Cliente", preview)
        self.assertIn("País do cliente", preview)
        conn = app.db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM clientes").fetchone()[0], 1)
        conn.close()
        with self.client.session_transaction() as session:
            token = session["invoice_import_stage"]
        response = self.client.post("/invoices/importar/confirmar", data={
            "stage_token": token, "cliente_novo_pais_c1": "US",
        })
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        client = conn.execute(
            "SELECT c.id, c.nome, c.pais FROM clientes c WHERE c.nome='Novo Cliente'"
        ).fetchone()
        self.assertIsNotNone(client)
        self.assertEqual(client["pais"], "US")
        invoice = conn.execute("""
            SELECT i.cliente_id, c.id AS client_id
            FROM invoices i JOIN clientes c ON c.id=i.cliente_id
            WHERE i.numero_invoice='INV-NEW-CLIENT'
        """).fetchone()
        self.assertEqual(invoice["cliente_id"], invoice["client_id"])
        conn.close()

    def test_invoice_import_can_replace_unknown_client_with_existing_client(self):
        import pandas as pd

        frame = pd.DataFrame([{
            "cnpj": "45.765.914/0001-81", "numero_invoice": "INV-EXISTING-CLIENT",
            "tipo_documento": "COMMERCIAL INVOICE", "cliente": "Nome divergente",
            "data_emissao": "01/08/2026", "moeda": "USD", "valor_invoice": "100,00",
            "competencia": "Agosto/2026",
        }])
        output = BytesIO()
        frame.to_excel(output, index=False)
        output.seek(0)
        response = self.client.post("/invoices/importar", data={"arquivo": (output, "existing-client.xlsx")},
                                    content_type="multipart/form-data")
        self.assertEqual(response.status_code, 200)
        preview = response.get_data(as_text=True)
        self.assertIn('name="cliente_existente_c1"', preview)
        self.assertIn("Cliente Teste", preview)
        with self.client.session_transaction() as session:
            token = session["invoice_import_stage"]
        response = self.client.post("/invoices/importar/confirmar", data={
            "stage_token": token, "cliente_existente_c1": "1",
        })
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM clientes").fetchone()[0], 1)
        invoice = conn.execute("""
            SELECT i.cliente_id, c.id AS client_id, c.nome
            FROM invoices i JOIN clientes c ON c.id=i.cliente_id
            WHERE i.numero_invoice='INV-EXISTING-CLIENT'
        """).fetchone()
        self.assertEqual(invoice["cliente_id"], invoice["client_id"])
        self.assertEqual(invoice["nome"], "Cliente Teste")
        conn.close()

    def test_invoice_import_suggests_registered_bank_for_credit_and_liquidation(self):
        import pandas as pd

        frame = pd.DataFrame([{
            "cnpj": "45.765.914/0001-81", "numero_invoice": "INV-BANK-SUGGESTION",
            "tipo_documento": "COMMERCIAL INVOICE", "cliente": "Cliente Teste",
            "data_emissao": "01/08/2026", "data_credito": "02/08/2026",
            "banco_credito": "BTG", "numero_contrato_cambio": "C-BANK-SUGGESTION",
            "banco_liquidacao": "C6 BANK", "valor_alocado": "100,00",
            "taxa_cambio": "5,00", "moeda": "USD", "valor_moeda": "100,00",
            "status": "RECEBIDO AGUARDANDO CAMBIO", "competencia": "Agosto/2026",
        }])
        output = BytesIO()
        frame.to_excel(output, index=False)
        output.seek(0)
        response = self.client.post("/invoices/importar", data={"arquivo": (output, "bank-suggestion.xlsx")},
                                    content_type="multipart/form-data")
        self.assertEqual(response.status_code, 200)
        preview = response.get_data(as_text=True)
        self.assertIn("Bancos não identificados", preview)
        self.assertIn("Banco de Crédito informado: BTG", preview)
        self.assertNotIn("Banco de CrÃ©dito informado", preview)
        self.assertIn('name="banco_existente_b1"', preview)
        self.assertIn('name="banco_existente_b2"', preview)
        self.assertIn("Banco Teste", preview)
        with self.client.session_transaction() as session:
            token = session["invoice_import_stage"]
        response = self.client.post("/invoices/importar/confirmar", data={
            "stage_token": token, "banco_existente_b1": "1", "banco_existente_b2": "1",
        })
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        receipt = conn.execute("""
            SELECT r.banco_credito_id
            FROM recebimentos_invoice r JOIN invoices i ON i.id=r.invoice_id
            WHERE i.numero_invoice='INV-BANK-SUGGESTION'
        """).fetchone()
        contract = conn.execute("""
            SELECT c.banco_liquidacao_id
            FROM contratos c JOIN invoice_contrato_cambio v ON v.contrato_id=c.id
            JOIN invoices i ON i.id=v.invoice_id
            WHERE i.numero_invoice='INV-BANK-SUGGESTION'
        """).fetchone()
        self.assertEqual(receipt["banco_credito_id"], 1)
        self.assertEqual(contract["banco_liquidacao_id"], 1)
        conn.close()

    def test_receipts_exchange_and_contract_grouping(self):
        invoice_id = self._create_invoice()
        for value in ("800,00", "200,00"):
            response = self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
                "banco_credito_id": "1", "data_credito": "10/08/2026", "valor_moeda": value,
            })
            self.assertEqual(response.status_code, 302)
        response = self.client.post(f"/invoice/{invoice_id}/cambio", data={
            "numero_contrato": "C001", "banco_liquidacao_id": "1", "data_fechamento": "12/08/2026",
            "taxa_cambio": "5,10", "valor_alocado": "600,00",
        })
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        summary = app.invoice_summary(conn, invoice_id)
        self.assertEqual(summary["saldo_recebimento"], app.Decimal("0"))
        self.assertEqual(summary["saldo_cambio"], app.Decimal("400"))
        self.assertEqual(summary["status"], app.INVOICE_STATUS_RECEBIDA_AGUARDANDO_CAMBIO)
        self.assertEqual(summary["taxa_cambio_media"], app.Decimal("5.10"))
        self.assertEqual(summary["valor_brl"], app.Decimal("3060"))
        contract = conn.execute("SELECT * FROM contratos WHERE numero_contrato='C001'").fetchone()
        self.assertEqual(app.Decimal(str(contract["valor_moeda"])), app.Decimal("600"))
        conn.close()
        self.assertEqual(self.client.get(f"/contrato/{contract['id']}").status_code, 200)
        self.assertEqual(self.client.get(f"/contrato/{contract['id']}/editar").status_code, 200)
        self.assertEqual(self.client.get(f"/invoice/{invoice_id}").status_code, 200)
        saldo_response = self.client.get(f"/invoices/{invoice_id}/saldo")
        self.assertEqual(saldo_response.status_code, 200)
        self.assertEqual(saldo_response.get_json()["saldo_cambio"], 400.0)

        invoice_2 = self._create_invoice("INV-002", "500,00")
        self.client.post(f"/invoice/{invoice_2}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "10/08/2026", "valor_moeda": "500,00",
        })
        self.client.post(f"/invoice/{invoice_2}/cambio", data={
            "contrato_id": str(contract["id"]), "valor_alocado": "300,00",
        })
        conn = app.db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM contratos WHERE numero_contrato='C001'").fetchone()[0], 1)
        self.assertEqual(app.Decimal(str(conn.execute("SELECT valor_moeda FROM contratos WHERE numero_contrato='C001'").fetchone()[0])), app.Decimal("900"))
        conn.close()

    def test_partial_closings_create_successive_parcels_without_duplicating_receipts(self):
        invoice_id = self._create_invoice("INV-CLOSINGS", "1000,00")
        for value, day in (("800,00", "10/08/2026"), ("200,00", "11/08/2026")):
            self.assertEqual(self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
                "banco_credito_id": "1", "data_credito": day, "valor_moeda": value,
            }).status_code, 302)

        self.assertEqual(self.client.post(f"/invoice/{invoice_id}/fechamentos", data={
            "valor_moeda": "300,00", "data_fechamento": "12/08/2026",
        }).status_code, 302)
        conn = app.db()
        first_child = conn.execute(
            "SELECT invoice_id FROM invoice_desdobramentos WHERE invoice_anterior_id=?",
            (invoice_id,),
        ).fetchone()[0]
        source = conn.execute("SELECT * FROM invoices WHERE id=?", (invoice_id,)).fetchone()
        child = conn.execute("SELECT * FROM invoices WHERE id=?", (first_child,)).fetchone()
        self.assertEqual(source["numero_invoice"], "INV-CLOSINGS - 1")
        self.assertEqual(app.Decimal(str(source["valor_moeda"])), app.Decimal("300"))
        self.assertEqual(app.Decimal(str(child["valor_moeda"])), app.Decimal("700"))
        self.assertEqual(child["status"], app.INVOICE_STATUS_RECEBIDA_AGUARDANDO_CAMBIO)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM recebimentos_invoice").fetchone()[0], 2)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM invoice_recebimento_alocacoes").fetchone()[0], 3)
        self.assertEqual(app.invoice_summary(conn, invoice_id)["total_recebido"], app.Decimal("300"))
        self.assertEqual(app.invoice_summary(conn, first_child)["total_recebido"], app.Decimal("700"))
        physical_receipt_id = conn.execute(
            "SELECT id FROM recebimentos_invoice ORDER BY id LIMIT 1"
        ).fetchone()[0]
        conn.close()
        response = self.client.post(
            f"/invoice/{invoice_id}/recebimentos/{physical_receipt_id}/editar",
            data={"valor_moeda": "999,00", "data_credito": "10/08/2026", "banco_credito_id": "1"},
            follow_redirects=True,
        )
        self.assertIn("compartilhado", response.get_data(as_text=True))
        response = self.client.post(f"/invoice/{first_child}/editar", data={
            "empresa_id": "1", "numero_invoice": "INV-CLOSINGS - 2",
            "tipo_documento": "COMMERCIAL_INVOICE", "competencia_id": "1", "cliente_id": "1",
            "data_emissao": "01/08/2026", "moeda": "USD", "valor_moeda": "701,00",
        })
        self.assertIn("não podem ser alterados", response.get_data(as_text=True))

        self.assertEqual(self.client.post(f"/invoice/{first_child}/fechamentos", data={
            "valor_moeda": "200,00", "data_fechamento": "13/08/2026",
        }).status_code, 302)
        conn = app.db()
        second_child = conn.execute(
            "SELECT invoice_id FROM invoice_desdobramentos WHERE invoice_anterior_id=?",
            (first_child,),
        ).fetchone()[0]
        self.assertEqual(app.invoice_summary(conn, first_child)["valor_moeda"], app.Decimal("200"))
        self.assertEqual(app.invoice_summary(conn, second_child)["valor_moeda"], app.Decimal("500"))
        self.assertEqual(app.invoice_summary(conn, second_child)["total_recebido"], app.Decimal("500"))
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM recebimentos_invoice").fetchone()[0], 2)
        conn.close()

        self.assertEqual(self.client.post(f"/invoice/{second_child}/fechamentos", data={
            "valor_moeda": "500,00", "data_fechamento": "14/08/2026",
            "numero_novo_contrato": "C-CLOSINGS",
        }).status_code, 302)
        conn = app.db()
        contract = conn.execute(
            "SELECT id, valor_moeda, data_contrato FROM contratos WHERE numero_contrato='C-CLOSINGS'"
        ).fetchone()
        self.assertEqual(app.Decimal(str(contract["valor_moeda"])), app.Decimal("500"))
        self.assertEqual(contract["data_contrato"], "2026-08-14")
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM invoice_desdobramentos WHERE invoice_raiz_id=?", (invoice_id,)
        ).fetchone()[0], 3)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM recebimentos_invoice").fetchone()[0], 2)
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM invoice_recebimento_alocacoes WHERE recebimento_id IN (1,2)"
        ).fetchone()[0], 4)
        self.assertEqual(conn.execute("SELECT status FROM invoices WHERE id=?", (second_child,)).fetchone()[0], app.INVOICE_STATUS_LIQUIDADA)
        conn.close()
        self.assertEqual(self.client.get(f"/contrato/{contract['id']}").status_code, 200)
        self.assertEqual(self.client.get(f"/contrato/{contract['id']}/relatorio").status_code, 200)

    def test_closing_status_is_persisted_after_successful_individual_closing(self):
        pending_invoice = self._create_invoice("INV-CLOSING-PENDING", "100,00")
        self.client.post(f"/invoice/{pending_invoice}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "10/08/2026", "valor_moeda": "100,00",
        })
        response = self.client.post(f"/invoice/{pending_invoice}/fechamentos", data={
            "valor_moeda": "100,00", "data_fechamento": "12/08/2026",
        })
        self.assertEqual(response.status_code, 302)

        conn = app.db()
        invoice = conn.execute(
            "SELECT status, status_manual FROM invoices WHERE id=?", (pending_invoice,)
        ).fetchone()
        self.assertEqual(invoice["status"], app.INVOICE_STATUS_AGUARDANDO_CONTRATO)
        self.assertEqual(invoice["status_manual"], 0)
        self.assertEqual(app.invoice_summary(conn, pending_invoice)["status"], app.INVOICE_STATUS_AGUARDANDO_CONTRATO)
        conn.close()

        manual_invoice = self._create_invoice("INV-CLOSING-MANUAL", "100,00")
        self.client.post(f"/invoice/{manual_invoice}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "10/08/2026", "valor_moeda": "100,00",
        })
        conn = app.db()
        conn.execute(
            "UPDATE invoices SET status=?, status_manual=1 WHERE id=?",
            (app.INVOICE_STATUS_LIQUIDADA, manual_invoice),
        )
        conn.commit()
        conn.close()
        self.assertEqual(self.client.post(f"/invoice/{manual_invoice}/fechamentos", data={
            "valor_moeda": "100,00", "data_fechamento": "12/08/2026",
        }).status_code, 302)
        conn = app.db()
        manual_status = conn.execute(
            "SELECT status, status_manual FROM invoices WHERE id=?", (manual_invoice,)
        ).fetchone()
        self.assertEqual(
            (manual_status["status"], manual_status["status_manual"]),
            (app.INVOICE_STATUS_LIQUIDADA, 1),
        )
        conn.close()

        listing = self.client.get("/invoices?status=AGUARDANDO_CONTRATO")
        self.assertEqual(listing.status_code, 200)
        listing_html = listing.get_data(as_text=True)
        self.assertIn("INV-CLOSING-PENDING", listing_html)
        self.assertIn("AGUARDANDO CONTRATO", listing_html)
        self.assertIn("Aguardando número de contrato", listing_html)
        detail = self.client.get(f"/invoice/{pending_invoice}")
        self.assertIn("Aguardando número de contrato.", detail.get_data(as_text=True))
        saldo = self.client.get(f"/invoices/{pending_invoice}/saldo").get_json()
        self.assertEqual(saldo["status"], app.INVOICE_STATUS_AGUARDANDO_CONTRATO)
        self.assertEqual(saldo["status_label"], "AGUARDANDO CONTRATO")
        report = app.build_invoice_report_context()
        self.assertEqual(report["tables"][0]["total"], app.Decimal("0"))
        self.assertEqual(report["tables"][0]["rows"], [])
        report_html = self.client.get("/invoices/relatorios").get_data(as_text=True)
        self.assertNotIn("INV-CLOSING-PENDING", report_html)

        liquidated_invoice = self._create_invoice("INV-CLOSING-LIQUIDATED", "100,00")
        self.client.post(f"/invoice/{liquidated_invoice}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "10/08/2026", "valor_moeda": "100,00",
        })
        response = self.client.post(f"/invoice/{liquidated_invoice}/fechamentos", data={
            "valor_moeda": "100,00", "data_fechamento": "12/08/2026",
            "numero_novo_contrato": "C-CLOSING-LIQUIDATED",
        })
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        invoice = conn.execute(
            "SELECT status, status_manual FROM invoices WHERE id=?", (liquidated_invoice,)
        ).fetchone()
        self.assertEqual(invoice["status"], app.INVOICE_STATUS_LIQUIDADA)
        self.assertEqual(invoice["status_manual"], 0)
        conn.close()

    def test_pending_closing_has_priority_until_all_received_amount_is_contracted(self):
        invoice_id = self._create_invoice("INV-CLOSING-MIXED", "1000,00")
        self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "10/08/2026", "valor_moeda": "1000,00",
        })
        self.assertEqual(self.client.post(f"/invoice/{invoice_id}/fechamentos", data={
            "valor_moeda": "600,00", "data_fechamento": "12/08/2026",
            "numero_novo_contrato": "C-CLOSING-MIXED",
        }).status_code, 302)
        conn = app.db()
        remainder_id = conn.execute(
            "SELECT invoice_id FROM invoice_desdobramentos WHERE invoice_anterior_id=?", (invoice_id,)
        ).fetchone()[0]
        conn.close()
        self.assertEqual(self.client.post(f"/invoice/{remainder_id}/fechamentos", data={
            "valor_moeda": "400,00", "data_fechamento": "13/08/2026",
        }).status_code, 302)
        conn = app.db()
        invoice = conn.execute("SELECT status FROM invoices WHERE id=?", (invoice_id,)).fetchone()
        remainder = conn.execute("SELECT status FROM invoices WHERE id=?", (remainder_id,)).fetchone()
        self.assertEqual(invoice["status"], app.INVOICE_STATUS_LIQUIDADA)
        self.assertEqual(remainder["status"], app.INVOICE_STATUS_AGUARDANDO_CONTRATO)
        self.assertEqual(app.invoice_summary(conn, invoice_id)["total_cambio"], app.Decimal("600"))
        self.assertEqual(app.invoice_summary(conn, remainder_id)["total_cambio"], app.Decimal("0"))
        conn.close()

        conn = app.db()
        pending_id = conn.execute(
            "SELECT id FROM fechamentos_cambio WHERE invoice_id=? AND contrato_id IS NULL", (remainder_id,)
        ).fetchone()[0]
        contract_id = conn.execute(
            "SELECT id FROM contratos WHERE numero_contrato='C-CLOSING-MIXED'"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(self.client.post(f"/contrato/{contract_id}/fechamentos/vincular", data={
            "fechamento_id": str(pending_id),
        }).status_code, 302)

        conn = app.db()
        invoice = conn.execute("SELECT status FROM invoices WHERE id=?", (invoice_id,)).fetchone()
        remainder = conn.execute("SELECT status FROM invoices WHERE id=?", (remainder_id,)).fetchone()
        self.assertEqual(invoice["status"], app.INVOICE_STATUS_LIQUIDADA)
        self.assertEqual(remainder["status"], app.INVOICE_STATUS_LIQUIDADA)
        self.assertEqual(app.invoice_summary(conn, remainder_id)["total_fechamentos_pendentes"], app.Decimal("0"))
        conn.close()

    def test_closing_and_legacy_allocation_respect_reserved_balance(self):
        invoice_id = self._create_invoice("INV-CLOSING-BALANCE", "500,00")
        self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "10/08/2026", "valor_moeda": "500,00",
        })
        self.assertEqual(self.client.post(f"/invoice/{invoice_id}/fechamentos", data={
            "valor_moeda": "400,00",
        }).status_code, 302)
        self.client.post(f"/invoice/{invoice_id}/fechamentos", data={"valor_moeda": "100,01"})
        self.client.post(f"/invoice/{invoice_id}/cambio", data={
            "numero_contrato": "C-RESERVED", "valor_alocado": "100,01",
        })
        conn = app.db()
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM fechamentos_cambio WHERE invoice_id=?", (invoice_id,)
        ).fetchone()[0], 1)
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM invoice_contrato_cambio WHERE invoice_id=?", (invoice_id,)
        ).fetchone()[0], 0)
        conn.close()

    def test_dates_default_to_today_and_manual_closing_date_is_preserved(self):
        invoice_id = self._create_invoice("INV-CLOSING-DATES", "200,00")
        self.client.post(f"/invoice/{invoice_id}/recebimentos", data={"valor_moeda": "200,00"})
        self.client.post(f"/invoice/{invoice_id}/fechamentos", data={"valor_moeda": "100,00"})
        conn = app.db()
        today = app.date.today().isoformat()
        self.assertEqual(conn.execute(
            "SELECT data_credito FROM recebimentos_invoice WHERE invoice_id=?", (invoice_id,)
        ).fetchone()[0], today)
        self.assertEqual(conn.execute(
            "SELECT data_fechamento FROM fechamentos_cambio WHERE invoice_id=?", (invoice_id,)
        ).fetchone()[0], today)
        conn.close()

        invoice_id = self._create_invoice("INV-CLOSING-CURRENCY", "100,00", currency="EUR")
        self.client.post(f"/invoice/{invoice_id}/recebimentos", data={"valor_moeda": "100,00"})
        self.client.post(f"/invoice/{invoice_id}/fechamentos", data={
            "valor_moeda": "100,00", "numero_novo_contrato": "C-EUR-CLOSING",
            "data_fechamento": "20/08/2026",
        })
        conn = app.db()
        contract = conn.execute(
            "SELECT moeda, data_contrato FROM contratos WHERE numero_contrato='C-EUR-CLOSING'"
        ).fetchone()
        self.assertEqual((contract["moeda"], contract["data_contrato"]), ("EUR", "2026-08-20"))
        conn.close()

    def test_closing_routes_reject_unknown_or_incompatible_contracts(self):
        invoice_id = self._create_invoice("INV-CLOSING-VALIDATION", "100,00")
        self.client.post(f"/invoice/{invoice_id}/recebimentos", data={"valor_moeda": "100,00"})
        self.client.post(f"/invoice/{invoice_id}/fechamentos", data={
            "valor_moeda": "50,00", "contrato_id": "999999",
        })
        conn = app.db()
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM fechamentos_cambio WHERE invoice_id=?", (invoice_id,)
        ).fetchone()[0], 0)
        conn.execute("INSERT INTO contratos(numero_contrato,moeda,valor_moeda) VALUES (?,?,?)", ("C-EUR", "EUR", 10))
        contract_id = conn.execute("SELECT id FROM contratos WHERE numero_contrato='C-EUR'").fetchone()[0]
        conn.commit()
        conn.close()
        other_invoice_id = self._create_invoice("INV-EUR-LINK", "10,00", currency="EUR")
        conn = app.db()
        conn.execute(
            "INSERT INTO invoice_contrato_cambio(invoice_id,contrato_id,valor_alocado) VALUES (?,?,?)",
            (other_invoice_id, contract_id, 10),
        )
        conn.commit()
        conn.close()
        self.client.post(f"/invoice/{invoice_id}/fechamentos", data={
            "valor_moeda": "50,00", "contrato_id": str(contract_id),
        })
        conn = app.db()
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM fechamentos_cambio WHERE invoice_id=?", (invoice_id,)
        ).fetchone()[0], 0)
        conn.close()

    def test_exchange_form_uses_exchange_contract_only(self):
        invoice_id = self._create_invoice(commercial="COM-ONLY")
        self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "10/08/2026", "valor_moeda": "1000,00",
        })
        response = self.client.post(f"/invoice/{invoice_id}/cambio", data={
            "numero_contrato_cambio": "C-EXCHANGE-ONLY", "valor_alocado": "1000,00",
        })
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        self.assertIsNotNone(conn.execute(
            "SELECT id FROM contratos WHERE numero_contrato='C-EXCHANGE-ONLY'"
        ).fetchone())
        self.assertIsNone(conn.execute(
            "SELECT id FROM contratos WHERE numero_contrato='COM-ONLY'"
        ).fetchone())
        self.assertEqual(conn.execute(
            "SELECT contrato_comercial FROM invoices WHERE id=?", (invoice_id,)
        ).fetchone()[0], "COM-ONLY")
        conn.close()

    def test_overallocation_is_rejected_and_due_traceability_is_limited_to_exchange(self):
        invoice_id = self._create_invoice()
        self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "10/08/2026", "valor_moeda": "1000,00",
        })
        response = self.client.post(f"/invoice/{invoice_id}/cambio", data={
            "numero_contrato": "C002", "valor_alocado": "1000,01",
        })
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM invoice_contrato_cambio").fetchone()[0], 0)
        conn.close()

        self.client.post(f"/invoice/{invoice_id}/cambio", data={
            "numero_contrato": "C002", "valor_alocado": "1000,00",
        })
        response = self.client.post(f"/invoice/{invoice_id}/due", data={
            "due_id": "1", "valor_vinculado": "1000,00",
        })
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM due_invoice").fetchone()[0], 1)
        conn.close()

        second_invoice = self._create_invoice("INV-DUE-2", "500,00")
        self.client.post(f"/invoice/{second_invoice}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "11/08/2026", "valor_moeda": "500,00",
        })
        self.client.post(f"/invoice/{second_invoice}/cambio", data={
            "numero_contrato": "C-DUE-2", "valor_alocado": "500,00",
        })
        self.client.post(f"/invoice/{second_invoice}/due", data={
            "due_id": "1", "valor_vinculado": "500,00",
        })
        conn = app.db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM due_invoice WHERE due_id=1").fetchone()[0], 2)
        conn.close()

    def test_weighted_exchange_rate_and_brl_for_multiple_contracts(self):
        invoice_id = self._create_invoice("INV-RATE", "1000,00")
        self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "10/08/2026", "valor_moeda": "1000,00",
        })
        for number, amount, rate in (("C-RATE-A", "600,00", "5,00"), ("C-RATE-B", "400,00", "6,00")):
            response = self.client.post(f"/invoice/{invoice_id}/cambio", data={
                "numero_contrato": number, "valor_alocado": amount, "taxa_cambio": rate,
            })
            self.assertEqual(response.status_code, 302)
        conn = app.db()
        summary = app.invoice_summary(conn, invoice_id)
        self.assertEqual(summary["taxa_cambio_media"], app.Decimal("5.4"))
        self.assertEqual(summary["valor_brl"], app.Decimal("5400"))
        self.assertEqual(summary["saldo_recebimento"], app.Decimal("0"))
        self.assertEqual(summary["saldo_cambio"], app.Decimal("0"))
        self.assertEqual(summary["status"], app.INVOICE_STATUS_LIQUIDADA)
        conn.close()
        self.assertEqual(self.client.get("/invoices?status=LIQUIDADA").status_code, 200)

    def test_import_splits_one_invoice_across_multiple_contracts(self):
        import pandas as pd

        invoice_id = self._create_invoice("INV-SPLIT", "1000,00")
        self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "10/08/2026", "valor_moeda": "1000,00",
        })
        frame = pd.DataFrame([
            {"cnpj": "45.765.914/0001-81", "numero_invoice": "INV-SPLIT", "tipo_documento": "COMMERCIAL INVOICE",
             "data_emissao": "01/08/2026", "moeda": "USD", "valor_invoice": "1000,00",
             "contrato_comercial": "COM-SPLIT", "numero_contrato_cambio": "C-SPLIT-A",
             "valor_alocado": "400,00", "taxa_cambio": "5,00"},
            {"cnpj": "45.765.914/0001-81", "numero_invoice": "INV-SPLIT", "tipo_documento": "COMMERCIAL INVOICE",
             "data_emissao": "01/08/2026", "moeda": "USD", "valor_invoice": "1000,00",
             "contrato_comercial": "COM-SPLIT", "numero_contrato_cambio": "C-SPLIT-B",
             "valor_alocado": "600,00", "taxa_cambio": "5,10"},
        ])
        frame["competencia"] = "Agosto/2026"
        output = BytesIO()
        frame.to_excel(output, index=False)
        output.seek(0)
        response = self.client.post("/invoices/importar", data={"arquivo": (output, "split.xlsx")},
                                    content_type="multipart/form-data")
        self.assertEqual(response.status_code, 200)
        with self.client.session_transaction() as session:
            token = session["invoice_import_stage"]
        self.assertEqual(self.client.post("/invoices/importar/confirmar", data={"stage_token": token}).status_code, 302)
        conn = app.db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM invoice_contrato_cambio WHERE invoice_id=?", (invoice_id,)).fetchone()[0], 2)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM contratos WHERE numero_contrato LIKE 'C-SPLIT-%'").fetchone()[0], 2)
        self.assertEqual(conn.execute(
            "SELECT contrato_comercial FROM invoices WHERE id=?", (invoice_id,)
        ).fetchone()[0], "COM-SPLIT")
        conn.close()

    def test_excel_groups_contracts_and_reimport_preserves_receipts(self):
        import pandas as pd

        for number, value in (("INV-A", "300,00"), ("INV-B", "200,00")):
            invoice_id = self._create_invoice(number, value)
            self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
                "banco_credito_id": "1", "data_credito": "10/08/2026", "valor_moeda": value,
            })
        conn = app.db()
        invoice_a = conn.execute("SELECT id FROM invoices WHERE numero_invoice='INV-A'").fetchone()[0]
        conn.close()
        self.client.post(f"/invoice/{invoice_a}/cambio", data={
            "numero_contrato": "C-OLD", "valor_alocado": "50,00", "taxa_cambio": "5,00",
        })
        self.client.post(f"/invoice/{invoice_a}/due", data={
            "due_id": "1", "valor_vinculado": "50,00",
        })

        frame = pd.DataFrame([
            {"cnpj": "45.765.914/0001-81", "numero_invoice": "INV-A", "tipo_documento": "COMMERCIAL INVOICE",
             "data_emissao": "01/08/2026", "moeda": "USD", "valor_invoice": "300,00",
             "contrato_comercial": "COM-GROUP", "numero_contrato_cambio": "C-GROUP",
             "valor_alocado": "300,00", "taxa_cambio": "5,10"},
            {"cnpj": "45.765.914/0001-81", "numero_invoice": "INV-B", "tipo_documento": "COMMERCIAL INVOICE",
             "data_emissao": "02/08/2026", "moeda": "USD", "valor_invoice": "200,00",
             "contrato_comercial": "COM-GROUP", "numero_contrato_cambio": "C-GROUP",
             "valor_alocado": "200,00", "taxa_cambio": "5,10"},
        ])
        frame["competencia"] = "Agosto/2026"
        output = BytesIO()
        frame.to_excel(output, index=False)
        output.seek(0)
        response = self.client.post("/invoices/importar", data={"arquivo": (output, "invoices.xlsx")},
                                    content_type="multipart/form-data")
        self.assertEqual(response.status_code, 200)
        with self.client.session_transaction() as session:
            token = session["invoice_import_stage"]
        response = self.client.post("/invoices/importar/confirmar", data={"stage_token": token})
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0], 2)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM contratos WHERE numero_contrato='C-GROUP'").fetchone()[0], 1)
        self.assertEqual(app.Decimal(str(conn.execute("SELECT valor_moeda FROM contratos WHERE numero_contrato='C-GROUP'").fetchone()[0])), app.Decimal("500"))
        self.assertEqual(conn.execute(
            "SELECT contrato_comercial FROM invoices WHERE numero_invoice='INV-A'"
        ).fetchone()[0], "COM-GROUP")
        receipt_count = conn.execute("SELECT COUNT(*) FROM recebimentos_invoice WHERE invoice_id=(SELECT id FROM invoices WHERE numero_invoice='INV-A')").fetchone()[0]
        due_link_count = conn.execute("SELECT COUNT(*) FROM due_invoice WHERE invoice_id=(SELECT id FROM invoices WHERE numero_invoice='INV-A')").fetchone()[0]
        conn.close()
        self.assertEqual(receipt_count, 1)
        self.assertEqual(due_link_count, 1)

        replacement = pd.DataFrame([{
            "cnpj": "45.765.914/0001-81", "numero_invoice": "INV-A", "tipo_documento": "COMMERCIAL INVOICE",
             "data_emissao": "01/08/2026", "moeda": "USD", "valor_invoice": "300,00",
            "contrato_comercial": "COM-REPROCESSED", "numero_contrato": "C-GROUP",
            "valor_alocado": "250,00", "taxa_cambio": "5,10",
        }])
        replacement["competencia"] = "Agosto/2026"
        replacement_output = BytesIO()
        replacement.to_excel(replacement_output, index=False)
        replacement_output.seek(0)
        response = self.client.post("/invoices/importar", data={"arquivo": (replacement_output, "replacement.xlsx")},
                                    content_type="multipart/form-data")
        self.assertEqual(response.status_code, 200)
        with self.client.session_transaction() as session:
            token = session["invoice_import_stage"]
        response = self.client.post("/invoices/importar/confirmar", data={"stage_token": token})
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM recebimentos_invoice WHERE invoice_id=(SELECT id FROM invoices WHERE numero_invoice='INV-A')").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM due_invoice WHERE invoice_id=(SELECT id FROM invoices WHERE numero_invoice='INV-A')").fetchone()[0], 1)
        self.assertEqual(app.Decimal(str(conn.execute("SELECT valor_alocado FROM invoice_contrato_cambio WHERE invoice_id=(SELECT id FROM invoices WHERE numero_invoice='INV-A')").fetchone()[0])), app.Decimal("250"))
        self.assertEqual(conn.execute(
            "SELECT contrato_comercial FROM invoices WHERE numero_invoice='INV-A'"
        ).fetchone()[0], "COM-REPROCESSED")
        conn.close()

    def test_invalid_receipt_exchange_edits_and_currency_change_are_rejected(self):
        invoice_id = self._create_invoice(value="1000,00")
        self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "10/08/2026", "valor_moeda": "1000,00",
        })
        self.client.post(f"/invoice/{invoice_id}/cambio", data={
            "numero_contrato": "C-VALID", "valor_alocado": "500,00",
        })
        conn = app.db()
        receipt = conn.execute("SELECT id FROM recebimentos_invoice WHERE invoice_id=?", (invoice_id,)).fetchone()
        link = conn.execute("SELECT id FROM invoice_contrato_cambio WHERE invoice_id=?", (invoice_id,)).fetchone()
        conn.close()

        self.client.post(f"/invoice/{invoice_id}/recebimentos/{receipt['id']}/editar", data={
            "data_credito": "10/08/2026", "valor_moeda": "400,00",
        })
        self.client.post(f"/invoice/{invoice_id}/recebimentos/{receipt['id']}/excluir")
        self.client.post(f"/invoice/{invoice_id}/cambio/{link['id']}/editar", data={
            "valor_alocado": "1000,01",
        })
        conn = app.db()
        self.assertEqual(app.Decimal(str(conn.execute("SELECT valor_moeda FROM recebimentos_invoice WHERE id=?", (receipt["id"],)).fetchone()[0])), app.Decimal("1000"))
        self.assertEqual(app.Decimal(str(conn.execute("SELECT valor_alocado FROM invoice_contrato_cambio WHERE id=?", (link["id"],)).fetchone()[0])), app.Decimal("500"))
        conn.close()

        response = self.client.post(f"/invoice/{invoice_id}/editar", data={
            "empresa_id": "1", "numero_invoice": "INV-001", "tipo_documento": "COMMERCIAL_INVOICE",
            "cliente_id": "1", "data_emissao": "01/08/2026", "moeda": "EUR", "valor_moeda": "1000,00",
        })
        self.assertEqual(response.status_code, 200)
        conn = app.db()
        self.assertEqual(conn.execute("SELECT moeda FROM invoices WHERE id=?", (invoice_id,)).fetchone()[0], "USD")
        conn.close()

    def test_derived_contract_cannot_be_deleted_from_contract_list(self):
        invoice_id = self._create_invoice(value="1000,00")
        self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "10/08/2026", "valor_moeda": "1000,00",
        })
        self.client.post(f"/invoice/{invoice_id}/cambio", data={
            "numero_contrato": "C-DERIVED", "valor_alocado": "1000,00",
        })
        conn = app.db()
        contract_id = conn.execute("SELECT id FROM contratos WHERE numero_contrato='C-DERIVED'").fetchone()[0]
        conn.close()
        self.client.post("/contratos/excluir-lote", data={"selected_ids": str(contract_id)})
        conn = app.db()
        self.assertIsNotNone(conn.execute("SELECT id FROM contratos WHERE id=?", (contract_id,)).fetchone())
        self.assertIsNotNone(conn.execute("SELECT id FROM invoice_contrato_cambio WHERE invoice_id=?", (invoice_id,)).fetchone())
        conn.close()

    def test_invoice_delete_succeeds_when_it_has_no_transactional_links(self):
        invoice_id = self._create_invoice(number="INV-DELETE-FREE", value="100,00")

        response = self.client.post(
            f"/invoice/{invoice_id}/excluir", follow_redirects=True
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("INV-DELETE-FREE", response.get_data(as_text=True))
        conn = app.db()
        self.assertIsNone(conn.execute(
            "SELECT id FROM invoices WHERE id=?", (invoice_id,)
        ).fetchone())
        conn.close()

    def test_invoice_delete_is_blocked_for_all_transactional_link_types(self):
        receipt_invoice = self._create_invoice("INV-BLOCK-RECEIPT", "100,00")
        allocation_invoice = self._create_invoice("INV-BLOCK-ALLOCATION", "100,00")
        closing_invoice = self._create_invoice("INV-BLOCK-CLOSING", "100,00")
        due_invoice = self._create_invoice("INV-BLOCK-DUE", "100,00")

        conn = app.db()
        conn.execute(
            "INSERT INTO recebimentos_invoice(invoice_id,data_credito,moeda,valor_moeda) "
            "VALUES (?,?,?,?)",
            (receipt_invoice, "2026-08-10", "USD", 100),
        )
        conn.execute(
            "INSERT INTO contratos(numero_contrato,moeda,valor_moeda) VALUES (?,?,?)",
            ("C-BLOCK-ALLOCATION", "USD", 100),
        )
        allocation_contract_id = conn.execute(
            "SELECT id FROM contratos WHERE numero_contrato=?",
            ("C-BLOCK-ALLOCATION",),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO invoice_contrato_cambio(invoice_id,contrato_id,valor_alocado) "
            "VALUES (?,?,?)",
            (allocation_invoice, allocation_contract_id, 50),
        )
        conn.execute(
            "INSERT INTO fechamentos_cambio(invoice_id,moeda,valor_moeda,data_fechamento) "
            "VALUES (?,?,?,?)",
            (closing_invoice, "USD", 50, "2026-08-11"),
        )
        conn.execute(
            "INSERT INTO due_invoice(due_id,invoice_id,valor_vinculado) VALUES (?,?,?)",
            (1, due_invoice, 50),
        )
        conn.commit()
        conn.close()

        expected = {
            receipt_invoice: ("INV-BLOCK-RECEIPT", "recebimento"),
            allocation_invoice: ("INV-BLOCK-ALLOCATION", "C-BLOCK-ALLOCATION"),
            closing_invoice: ("INV-BLOCK-CLOSING", "fechamento"),
            due_invoice: ("INV-BLOCK-DUE", "DUE-001"),
        }
        for invoice_id, (number, reason) in expected.items():
            response = self.client.post(
                f"/invoice/{invoice_id}/excluir", follow_redirects=True
            )
            self.assertEqual(response.status_code, 200)
            html = response.get_data(as_text=True)
            self.assertIn(number, html)
            self.assertIn(reason, html)

        conn = app.db()
        for invoice_id in expected:
            self.assertIsNotNone(conn.execute(
                "SELECT id FROM invoices WHERE id=?", (invoice_id,)
            ).fetchone())
        self.assertIsNotNone(conn.execute(
            "SELECT id FROM recebimentos_invoice WHERE invoice_id=?", (receipt_invoice,)
        ).fetchone())
        self.assertIsNotNone(conn.execute(
            "SELECT id FROM invoice_contrato_cambio WHERE invoice_id=?", (allocation_invoice,)
        ).fetchone())
        self.assertIsNotNone(conn.execute(
            "SELECT id FROM fechamentos_cambio WHERE invoice_id=?", (closing_invoice,)
        ).fetchone())
        self.assertIsNotNone(conn.execute(
            "SELECT id FROM due_invoice WHERE invoice_id=?", (due_invoice,)
        ).fetchone())
        conn.close()

    def test_invoice_batch_delete_removes_eligible_and_reports_blocked_invoices(self):
        free_id = self._create_invoice("INV-BATCH-FREE", "100,00")
        receipt_id = self._create_invoice("INV-BATCH-RECEIPT", "100,00")
        allocation_id = self._create_invoice("INV-BATCH-ALLOCATION", "100,00")

        conn = app.db()
        conn.execute(
            "INSERT INTO recebimentos_invoice(invoice_id,data_credito,moeda,valor_moeda) "
            "VALUES (?,?,?,?)",
            (receipt_id, "2026-08-10", "USD", 100),
        )
        conn.execute(
            "INSERT INTO contratos(numero_contrato,moeda,valor_moeda) VALUES (?,?,?)",
            ("C-BATCH-ALLOCATION", "USD", 100),
        )
        contract_id = conn.execute(
            "SELECT id FROM contratos WHERE numero_contrato=?",
            ("C-BATCH-ALLOCATION",),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO invoice_contrato_cambio(invoice_id,contrato_id,valor_alocado) "
            "VALUES (?,?,?)",
            (allocation_id, contract_id, 50),
        )
        conn.commit()
        conn.close()

        response = self.client.post(
            "/invoices/excluir-lote",
            data={
                "selected_ids": [str(free_id), str(receipt_id), str(allocation_id)],
                "numero_invoice": "INV-BATCH",
                "sort": "numero_invoice",
                "direction": "asc",
                "page": "2",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("numero_invoice=INV-BATCH", response.location)
        page = self.client.get(response.location)
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        self.assertIn("INV-BATCH-FREE", html)
        self.assertIn("INV-BATCH-RECEIPT", html)
        self.assertIn("INV-BATCH-ALLOCATION", html)
        self.assertIn("C-BATCH-ALLOCATION", html)
        self.assertIn("recebimento", html)
        self.assertIn("Invoice(s) excluída(s)", html)

        conn = app.db()
        self.assertIsNone(conn.execute(
            "SELECT id FROM invoices WHERE id=?", (free_id,)
        ).fetchone())
        self.assertIsNotNone(conn.execute(
            "SELECT id FROM invoices WHERE id=?", (receipt_id,)
        ).fetchone())
        self.assertIsNotNone(conn.execute(
            "SELECT id FROM invoices WHERE id=?", (allocation_id,)
        ).fetchone())
        self.assertIsNotNone(conn.execute(
            "SELECT id FROM contratos WHERE id=?", (contract_id,)
        ).fetchone())
        conn.close()

    def test_invoice_batch_delete_does_not_partially_delete_when_an_id_is_missing(self):
        invoice_id = self._create_invoice("INV-BATCH-STALE", "100,00")

        response = self.client.post(
            "/invoices/excluir-lote",
            data={"selected_ids": [str(invoice_id), "999999999"]},
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("não foram encontrados", response.get_data(as_text=True))
        conn = app.db()
        self.assertIsNotNone(conn.execute(
            "SELECT id FROM invoices WHERE id=?", (invoice_id,)
        ).fetchone())
        conn.close()

    def test_invoice_list_and_detail_render_delete_controls(self):
        invoice_id = self._create_invoice("INV-DELETE-CONTROLS", "100,00")

        listing = self.client.get("/invoices?numero_invoice=INV-DELETE-CONTROLS")
        self.assertEqual(listing.status_code, 200)
        listing_html = listing.get_data(as_text=True)
        self.assertIn('data-batch-form', listing_html)
        self.assertIn('data-batch-select-all', listing_html)
        self.assertIn(f'name="selected_ids" value="{invoice_id}"', listing_html)
        self.assertIn('data-invoice-single-delete', listing_html)
        self.assertIn(f'formaction="/invoice/{invoice_id}/excluir"', listing_html)
        self.assertIn("Excluir as {count} Invoice(s)", listing_html)

        detail = self.client.get(f"/invoice/{invoice_id}")
        self.assertEqual(detail.status_code, 200)
        detail_html = detail.get_data(as_text=True)
        self.assertIn(f'action="/invoice/{invoice_id}/excluir"', detail_html)
        self.assertIn("Excluir Invoice", detail_html)
        self.assertIn('href="/invoice/nova"', detail_html)
        self.assertIn("+ Nova invoice", detail_html)

    def test_invoice_detail_shows_central_closing_in_contract_table(self):
        invoice_id = self._create_invoice("INV-CONTRACT-CLOSING-DETAIL", "100,00")
        closing = self._register_central_closing(
            invoice_id,
            "100,00",
            "20/08/2026",
            "25/08/2026",
            taxa_cambio="5,1000",
            numero_contrato="CONTRACT-DETAIL-001",
        )

        response = self.client.get(f"/invoice/{invoice_id}")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        start = html.index("<th>Contrato Câmbio</th>")
        end = html.index("<h2>DU-Es vinculadas", start)
        table = html[start:end]
        contract_id = closing["contrato_id"]

        self.assertIn(f'href="/contrato/{contract_id}"', table)
        self.assertIn("CONTRACT-DETAIL-001", table)
        self.assertIn("Banco Teste", table)
        self.assertIn("20/08/2026", table)
        self.assertIn("25/08/2026", table)
        self.assertIn("5,1000", table)
        self.assertIn("100,00", table)
        self.assertIn(f'href="/invoices/fechamentos/{closing["id"]}"', table)

    def test_invoice_list_renders_batch_receipt_controls_and_banks(self):
        invoice_id = self._create_invoice("INV-BATCH-CONTROLS", "100,00")

        response = self.client.get(f"/invoices?numero_invoice=INV-BATCH-CONTROLS")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('data-batch-receipt-open', html)
        self.assertIn('action="/invoices/recebimentos"', html)
        self.assertIn('<option value="1">Banco Teste</option>', html)
        self.assertIn("Registrar recebimentos em lote", html)
        self.assertIn(f'data-batch-checkbox aria-label="Selecionar Invoice INV-BATCH-CONTROLS"', html)
        self.assertIsNotNone(invoice_id)

    def test_batch_receipts_register_remaining_balance_for_multiple_invoices(self):
        invoice_a = self._create_invoice("INV-BATCH-A", "100,00")
        invoice_b = self._create_invoice("INV-BATCH-B", "50,00", currency="EUR")
        conn = app.db()
        conn.execute(
            "UPDATE invoices SET status=?, status_manual=1 WHERE id IN (?,?)",
            (app.INVOICE_STATUS_AGUARDANDO_RECEBIMENTO, invoice_a, invoice_b),
        )
        conn.commit()
        conn.close()

        response = self.client.post("/invoices/recebimentos", data={
            "selected_ids": [str(invoice_a), str(invoice_b)],
            "banco_credito_id": "1", "data_credito": "20/08/2026",
            "numero_invoice": "INV-BATCH", "sort": "numero_invoice",
            "direction": "asc", "page": "2",
        })

        self.assertEqual(response.status_code, 302)
        self.assertIn("numero_invoice=INV-BATCH", response.location)
        self.assertIn("sort=numero_invoice", response.location)
        self.assertNotIn("banco_credito_id", response.location)
        self.assertNotIn("data_credito", response.location)
        conn = app.db()
        rows = conn.execute("""
            SELECT invoice_id, banco_credito_id, data_credito, moeda, valor_moeda
            FROM recebimentos_invoice WHERE invoice_id IN (?,?) ORDER BY invoice_id
        """, (invoice_a, invoice_b)).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual([(row["invoice_id"], row["moeda"]) for row in rows],
                         [(invoice_a, "USD"), (invoice_b, "EUR")])
        self.assertEqual({row["banco_credito_id"] for row in rows}, {1})
        self.assertEqual({row["data_credito"] for row in rows}, {"2026-08-20"})
        self.assertEqual([app.Decimal(str(row["valor_moeda"])) for row in rows],
                         [app.Decimal("100"), app.Decimal("50")])
        statuses = conn.execute(
            "SELECT status FROM invoices WHERE id IN (?,?) ORDER BY id",
            (invoice_a, invoice_b),
        ).fetchall()
        self.assertEqual(
            [row["status"] for row in statuses],
            [app.INVOICE_STATUS_RECEBIDA_AGUARDANDO_CAMBIO] * 2,
        )
        self.assertEqual(app.invoice_summary(conn, invoice_a)["status"],
                         app.INVOICE_STATUS_RECEBIDA_AGUARDANDO_CAMBIO)
        self.assertEqual(app.invoice_summary(conn, invoice_b)["status"],
                         app.INVOICE_STATUS_RECEBIDA_AGUARDANDO_CAMBIO)
        conn.close()
        listing = self.client.get(response.location)
        self.assertEqual(listing.status_code, 200)
        self.assertGreaterEqual(listing.get_data(as_text=True).count("RECEBIDO AGUARDANDO CAMBIO"), 2)

    def test_batch_receipts_only_register_remaining_balance_for_partial_invoice(self):
        invoice_id = self._create_invoice("INV-BATCH-PARTIAL", "1000,00")
        self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "10/08/2026",
            "valor_moeda": "400,00",
        })

        response = self.client.post("/invoices/recebimentos", data={
            "selected_ids": str(invoice_id), "banco_credito_id": "1",
            "data_credito": "20/08/2026",
        })

        self.assertEqual(response.status_code, 302)
        conn = app.db()
        receipts = conn.execute(
            "SELECT valor_moeda, data_credito FROM recebimentos_invoice WHERE invoice_id=? ORDER BY id",
            (invoice_id,),
        ).fetchall()
        self.assertEqual([app.Decimal(str(row["valor_moeda"])) for row in receipts],
                         [app.Decimal("400"), app.Decimal("600")])
        self.assertEqual(receipts[-1]["data_credito"], "2026-08-20")
        self.assertEqual(app.invoice_summary(conn, invoice_id)["saldo_recebimento"], app.Decimal("0"))
        conn.close()

    def test_batch_receipts_reject_invalid_selection_without_partial_write(self):
        invoice_id = self._create_invoice("INV-BATCH-STALE-RECEIPT", "100,00")

        response = self.client.post("/invoices/recebimentos", data={
            "selected_ids": [str(invoice_id), "999999999"],
            "banco_credito_id": "1", "data_credito": "20/08/2026",
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("não foram encontrados", response.get_data(as_text=True))
        conn = app.db()
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM recebimentos_invoice WHERE invoice_id=?", (invoice_id,)
        ).fetchone()[0], 0)
        conn.close()

    def test_batch_receipts_roll_back_when_one_invoice_has_no_remaining_balance(self):
        invoice_a = self._create_invoice("INV-BATCH-ROLLBACK-A", "100,00")
        invoice_b = self._create_invoice("INV-BATCH-ROLLBACK-B", "100,00")
        self.client.post(f"/invoice/{invoice_b}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "10/08/2026",
            "valor_moeda": "100,00",
        })

        response = self.client.post("/invoices/recebimentos", data={
            "selected_ids": [str(invoice_a), str(invoice_b)],
            "banco_credito_id": "1", "data_credito": "20/08/2026",
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("não possui saldo de recebimento", response.get_data(as_text=True))
        conn = app.db()
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM recebimentos_invoice WHERE invoice_id=?", (invoice_a,)
        ).fetchone()[0], 0)
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM recebimentos_invoice WHERE invoice_id=?", (invoice_b,)
        ).fetchone()[0], 1)
        conn.close()

    def test_batch_receipts_reject_invalid_bank_or_date_without_write(self):
        invoice_id = self._create_invoice("INV-BATCH-INVALID-FORM", "100,00")

        response = self.client.post("/invoices/recebimentos", data={
            "selected_ids": str(invoice_id), "banco_credito_id": "1",
            "data_credito": "31/02/2026",
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Data inválida", response.get_data(as_text=True))
        response = self.client.post("/invoices/recebimentos", data={
            "selected_ids": str(invoice_id), "banco_credito_id": "999999",
            "data_credito": "20/08/2026",
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("não foi encontrado", response.get_data(as_text=True))
        conn = app.db()
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM recebimentos_invoice WHERE invoice_id=?", (invoice_id,)
        ).fetchone()[0], 0)
        conn.close()

    def test_batch_receipts_keep_centralized_closing_bank_rule(self):
        conn = app.db()
        conn.execute("INSERT INTO contrapartes (nome) VALUES (?)", ("Banco Alternativo",))
        bank_two = conn.execute(
            "SELECT id FROM contrapartes WHERE nome='Banco Alternativo'"
        ).fetchone()[0]
        conn.commit()
        conn.close()
        invoice_id = self._create_invoice("INV-BATCH-CENTRAL-BANK", "100,00")
        self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "10/08/2026",
            "valor_moeda": "50,00",
        })
        self.client.post("/invoices/fechamentos", data={
            "selected_ids": str(invoice_id), "data_fechamento": "20/08/2026",
            "data_liquidacao": "20/08/2026", "taxa_cambio": "5,0000",
            "banco_liquidacao_id": "1", "categoria_cambio": app.CATEGORIA_CAMBIO_EXPORTACAO,
        })

        response = self.client.post("/invoices/recebimentos", data={
            "selected_ids": str(invoice_id), "banco_credito_id": str(bank_two),
            "data_credito": "21/08/2026",
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Banco de Crédito deve permanecer igual", response.get_data(as_text=True))
        conn = app.db()
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM recebimentos_invoice WHERE invoice_id=?", (invoice_id,)
        ).fetchone()[0], 1)
        conn.close()

    def test_import_rejects_conflicting_contract_metadata(self):
        import pandas as pd

        frame = pd.DataFrame([
            {"cnpj": "45.765.914/0001-81", "numero_invoice": "INV-CONFLICT", "tipo_documento": "COMMERCIAL INVOICE",
             "data_emissao": "01/08/2026", "moeda": "USD", "valor_invoice": "1000,00",
             "numero_contrato": "C-CONFLICT", "valor_alocado": "500,00", "taxa_cambio": "5,10"},
            {"cnpj": "45.765.914/0001-81", "numero_invoice": "INV-CONFLICT", "tipo_documento": "COMMERCIAL INVOICE",
             "data_emissao": "01/08/2026", "moeda": "USD", "valor_invoice": "1000,00",
             "numero_contrato": "C-CONFLICT", "valor_alocado": "500,00", "taxa_cambio": "5,20"},
        ])
        frame["competencia"] = "Agosto/2026"
        output = BytesIO()
        frame.to_excel(output, index=False)
        output.seek(0)
        response = self.client.post("/invoices/importar", data={"arquivo": (output, "conflict.xlsx")},
                                    content_type="multipart/form-data")
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM invoices WHERE numero_invoice='INV-CONFLICT'").fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM contratos WHERE numero_contrato='C-CONFLICT'").fetchone()[0], 0)
        conn.close()

    def test_import_rejects_conflicting_commercial_contract(self):
        import pandas as pd

        frame = pd.DataFrame([
            {"cnpj": "45.765.914/0001-81", "numero_invoice": "INV-COM-CONFLICT", "tipo_documento": "COMMERCIAL INVOICE",
             "data_emissao": "01/08/2026", "moeda": "USD", "valor_invoice": "1000,00",
             "contrato_comercial": "COM-A", "numero_contrato_cambio": "C-COM-CONFLICT",
             "valor_alocado": "500,00", "taxa_cambio": "5,10"},
            {"cnpj": "45.765.914/0001-81", "numero_invoice": "INV-COM-CONFLICT", "tipo_documento": "COMMERCIAL INVOICE",
             "data_emissao": "01/08/2026", "moeda": "USD", "valor_invoice": "1000,00",
             "contrato_comercial": "COM-B", "numero_contrato_cambio": "C-COM-CONFLICT",
             "valor_alocado": "500,00", "taxa_cambio": "5,10"},
        ])
        frame["competencia"] = "Agosto/2026"
        output = BytesIO()
        frame.to_excel(output, index=False)
        output.seek(0)
        response = self.client.post("/invoices/importar", data={"arquivo": (output, "commercial-conflict.xlsx")},
                                    content_type="multipart/form-data")
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM invoices WHERE numero_invoice='INV-COM-CONFLICT'"
        ).fetchone()[0], 0)
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM contratos WHERE numero_contrato='C-COM-CONFLICT'"
        ).fetchone()[0], 0)
        conn.close()

    def test_invoice_import_model_requires_competencia_and_removes_unused_columns(self):
        import pandas as pd

        response = self.client.get("/invoices/modelo")
        self.assertEqual(response.status_code, 200)
        model = pd.read_excel(BytesIO(response.data))
        self.assertIn("competencia", model.columns)
        self.assertNotIn("cliente_pais", model.columns)
        self.assertNotIn("valor_alocado", model.columns)
        rows = app.prepare_invoice_import_rows(pd.DataFrame([{
            "cnpj": "45.765.914/0001-81", "numero_invoice": "INV-MODEL-CHECK",
            "tipo_documento": "COMMERCIAL INVOICE", "competencia": "Agosto/2026",
            "valor_invoice": "100,00",
        }]), pd)
        self.assertNotIn("cliente_pais", rows[0])
        self.assertNotIn("valor_alocado", rows[0])

        frame = pd.DataFrame([{
            "cnpj": "45.765.914/0001-81", "numero_invoice": "INV-WITHOUT-COMPETENCE",
            "tipo_documento": "COMMERCIAL INVOICE", "data_emissao": "01/08/2026",
            "moeda": "USD", "valor_invoice": "100,00",
        }])
        output = BytesIO()
        frame.to_excel(output, index=False)
        output.seek(0)
        response = self.client.post("/invoices/importar", data={"arquivo": (output, "missing-competence.xlsx")},
                                    content_type="multipart/form-data")
        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as session:
            self.assertNotIn("invoice_import_stage", session)

    def test_invoice_import_recovers_leading_zero_from_numeric_cnpj(self):
        import pandas as pd

        conn = app.db()
        conn.execute("INSERT INTO empresas (razao_social, cnpj, apelido) VALUES (?,?,?)",
                     ("Empresa CNPJ com zero", "04171382000177", "Empresa Zero"))
        conn.execute("INSERT INTO competencias (empresa_id, descricao, data_inicial, data_final) VALUES (?,?,?,?)",
                     (2, "Agosto/2026", "2026-08-01", "2026-08-31"))
        conn.commit()
        conn.close()

        frame = pd.DataFrame([{
            # Simula a célula numérica do Excel: o zero inicial é removido.
            "empresa": 4171382000177, "invoice": "INV-CNPJ-ZERO",
            "tipo": "COMMERCIAL INVOICE", "competencia": "Agosto/2026",
            "moeda": "USD", "valor_moeda": "100,00",
        }])
        output = BytesIO()
        frame.to_excel(output, index=False)
        output.seek(0)
        response = self.client.post("/invoices/importar", data={"arquivo": (output, "numeric-cnpj.xlsx")},
                                    content_type="multipart/form-data")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("não está cadastrada", response.get_data(as_text=True))
        with self.client.session_transaction() as session:
            token = session["invoice_import_stage"]
        self.assertEqual(self.client.post("/invoices/importar/confirmar", data={"stage_token": token}).status_code, 302)
        conn = app.db()
        invoice = conn.execute("SELECT empresa_id FROM invoices WHERE numero_invoice='INV-CNPJ-ZERO'").fetchone()
        self.assertEqual(invoice["empresa_id"], 2)
        conn.close()

    def test_invoice_import_and_export_support_referenced_bank_without_breaking_legacy_rows(self):
        import pandas as pd

        frame = pd.DataFrame([{
            "cnpj": "45.765.914/0001-81", "numero_invoice": "INV-REFERENCE-IMPORT",
            "tipo_documento": "COMMERCIAL INVOICE", "competencia": "Agosto/2026",
            "moeda": "USD", "valor_moeda": "100,00", "banco_referenciado": "Banco Teste",
        }])
        output = BytesIO()
        frame.to_excel(output, index=False)
        output.seek(0)
        response = self.client.post(
            "/invoices/importar", data={"arquivo": (output, "reference.xlsx")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 200)
        with self.client.session_transaction() as session:
            token = session["invoice_import_stage"]
        self.assertEqual(
            self.client.post("/invoices/importar/confirmar", data={"stage_token": token}).status_code,
            302,
        )
        conn = app.db()
        imported = conn.execute(
            "SELECT banco_referenciado_id FROM invoices WHERE numero_invoice='INV-REFERENCE-IMPORT'"
        ).fetchone()
        self.assertEqual(imported["banco_referenciado_id"], 1)
        conn.close()

        existing_id = self._create_invoice(
            number="INV-REFERENCE-REIMPORT", value="200,00", banco_referenciado_id=1
        )
        legacy_frame = pd.DataFrame([{
            "cnpj": "45.765.914/0001-81", "numero_invoice": "INV-REFERENCE-REIMPORT",
            "tipo_documento": "COMMERCIAL INVOICE", "competencia": "Agosto/2026",
            "moeda": "USD", "valor_moeda": "200,00",
        }])
        legacy_output = BytesIO()
        legacy_frame.to_excel(legacy_output, index=False)
        legacy_output.seek(0)
        response = self.client.post(
            "/invoices/importar", data={"arquivo": (legacy_output, "legacy-reference.xlsx")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 200)
        with self.client.session_transaction() as session:
            token = session["invoice_import_stage"]
        self.assertEqual(
            self.client.post("/invoices/importar/confirmar", data={"stage_token": token}).status_code,
            302,
        )
        conn = app.db()
        self.assertEqual(conn.execute(
            "SELECT banco_referenciado_id FROM invoices WHERE id=?", (existing_id,)
        ).fetchone()[0], 1)
        conn.close()

        response = self.client.get("/invoices/exportar?numero_invoice=INV-REFERENCE-IMPORT")
        self.assertEqual(response.status_code, 200)
        exported = pd.read_excel(BytesIO(response.data), sheet_name="Invoices")
        self.assertIn("Banco referenciado", exported.columns)
        self.assertEqual(exported.iloc[0]["Banco referenciado"], "Banco Teste")

    def test_invoice_import_accepts_proforma_invoice_type(self):
        import pandas as pd

        frame = pd.DataFrame([{
            "empresa": "45.765.914/0001-81", "invoice": "INV-PROFORMA",
            "tipo": "PROFORMA INVOICE", "competencia": "Agosto/2026",
            "moeda": "USD", "valor_moeda": "100,00",
        }])
        frame.columns = app.normalize_invoice_import_columns(frame.columns)
        rows = app.prepare_invoice_import_rows(frame, pd)

        self.assertEqual(rows[0]["tipo_documento"], "PROFORMA")

    def test_invoice_import_associates_competence_by_period_and_preserves_invoice_only_model(self):
        import pandas as pd

        frame = pd.DataFrame([{
            "cnpj": "45.765.914/0001-81", "numero_invoice": "INV-COMPETENCE",
            "tipo_documento": "COMMERCIAL INVOICE", "cliente": "Cliente Teste",
            "contrato_comercial": "COM-001", "competencia": "08/2026",
            "data_emissao": "10/08/2026", "moeda": "USD", "valor_invoice": "100,00",
        }])
        output = BytesIO()
        frame.to_excel(output, index=False)
        output.seek(0)
        response = self.client.post("/invoices/importar", data={"arquivo": (output, "competence.xlsx")},
                                    content_type="multipart/form-data")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Competências não cadastradas", response.get_data(as_text=True))
        with self.client.session_transaction() as session:
            token = session["invoice_import_stage"]
        response = self.client.post("/invoices/importar/confirmar", data={"stage_token": token})
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        invoice = conn.execute("SELECT competencia_id FROM invoices WHERE numero_invoice='INV-COMPETENCE'").fetchone()
        self.assertEqual(invoice["competencia_id"], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM contratos").fetchone()[0], 0)
        conn.close()

    def test_invoice_import_suggests_new_competence_by_company_and_creates_on_confirmation(self):
        import pandas as pd

        frame = pd.DataFrame([{
            "cnpj": "45.765.914/0001-81", "numero_invoice": "INV-NEW-COMPETENCE",
            "tipo_documento": "COMMERCIAL INVOICE", "cliente": "Cliente Teste",
            "competencia": "Janeiro/2032", "data_emissao": "01/01/2032",
            "moeda": "USD", "valor_invoice": "100,00",
        }])
        output = BytesIO()
        frame.to_excel(output, index=False)
        output.seek(0)
        response = self.client.post("/invoices/importar", data={"arquivo": (output, "new-competence.xlsx")},
                                    content_type="multipart/form-data")
        self.assertEqual(response.status_code, 200)
        preview = response.get_data(as_text=True)
        self.assertIn("Competências não cadastradas", preview)
        self.assertIn("Janeiro/2032", preview)
        with self.client.session_transaction() as session:
            token = session["invoice_import_stage"]
        response = self.client.post("/invoices/importar/confirmar", data={
            "stage_token": token, "competencia_nova_confirmar_p1": "1",
            "competencia_nova_descricao_p1": "Janeiro/2032",
            "competencia_nova_data_inicial_p1": "01/01/2032",
            "competencia_nova_data_final_p1": "31/01/2032",
        })
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        competence = conn.execute(
            "SELECT id FROM competencias WHERE empresa_id=1 AND descricao='Janeiro/2032'"
        ).fetchone()
        self.assertIsNotNone(competence)
        self.assertEqual(conn.execute(
            "SELECT competencia_id FROM invoices WHERE numero_invoice='INV-NEW-COMPETENCE'"
        ).fetchone()[0], competence["id"])
        conn.close()

    def test_invoice_export_preserves_filters_and_includes_related_data(self):
        import pandas as pd

        invoice_id = self._create_invoice(number="INV-EXPORT-A", value="500,00")
        self._create_invoice(number="INV-EXPORT-B", value="250,00")
        conn = app.db()
        conn.execute("""
            INSERT INTO recebimentos_invoice
                (invoice_id,banco_credito_id,data_credito,moeda,valor_moeda,documento,observacao)
            VALUES (?,?,?,?,?,?,?)
        """, (invoice_id, 1, "2026-08-10", "USD", 500, "DOC-EXP", "Recebimento exportado"))
        conn.execute("""
            INSERT INTO contratos
                (numero_contrato,banco_liquidacao,data_fechamento,data_liquidacao,moeda,
                 taxa_cambio,valor_moeda,valor_reais,status,observacao)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, ("C-EXPORT", "Banco Teste", "2026-08-11", "2026-08-12", "USD",
              5.1, 500, 2550, "CONCLUIDO", "Contrato exportado"))
        contrato_id = conn.execute("SELECT id FROM contratos WHERE numero_contrato='C-EXPORT'").fetchone()[0]
        conn.execute("""
            INSERT INTO invoice_contrato_cambio(invoice_id,contrato_id,valor_alocado,observacao)
            VALUES (?,?,?,?)
        """, (invoice_id, contrato_id, 500, "Cambio exportado"))
        due_id = conn.execute("SELECT id FROM dues WHERE numero_due='DUE-001'").fetchone()[0]
        conn.execute("""
            INSERT INTO due_invoice(due_id,invoice_id,valor_vinculado,observacao)
            VALUES (?,?,?,?)
        """, (due_id, invoice_id, 500, "DU-E exportada"))
        conn.commit()
        conn.close()

        response = self.client.get("/invoices/exportar?numero_invoice=INV-EXPORT-A")
        self.assertEqual(response.status_code, 200)
        workbook = pd.ExcelFile(BytesIO(response.data))
        self.assertEqual(workbook.sheet_names, ["Invoices", "Recebimentos", "Cambios", "DU-Es"])
        invoices = pd.read_excel(BytesIO(response.data), sheet_name="Invoices")
        receipts = pd.read_excel(BytesIO(response.data), sheet_name="Recebimentos")
        changes = pd.read_excel(BytesIO(response.data), sheet_name="Cambios")
        dues = pd.read_excel(BytesIO(response.data), sheet_name="DU-Es")
        self.assertEqual(len(invoices), 1)
        self.assertEqual(invoices.iloc[0]["Numero da Invoice"], "INV-EXPORT-A")
        self.assertEqual(len(receipts), 1)
        self.assertEqual(len(changes), 1)
        self.assertEqual(len(dues), 1)
        self.assertIn("Saldo de recebimento", invoices.columns)
        self.assertIn("Banco de credito", receipts.columns)
        self.assertIn("Taxa de cambio", changes.columns)
        self.assertIn("Chave de acesso", dues.columns)

    def test_invoice_export_includes_new_closings_in_cambios(self):
        import pandas as pd

        invoice_id = self._create_invoice("INV-EXPORT-CLOSING", "100,00")
        self.client.post(f"/invoice/{invoice_id}/recebimentos", data={"valor_moeda": "100,00"})
        self.client.post(f"/invoice/{invoice_id}/fechamentos", data={
            "valor_moeda": "40,00", "data_fechamento": "20/08/2026",
        })
        response = self.client.get("/invoices/exportar?numero_invoice=INV-EXPORT-CLOSING")
        self.assertEqual(response.status_code, 200)
        changes = pd.read_excel(BytesIO(response.data), sheet_name="Cambios")
        invoices = pd.read_excel(BytesIO(response.data), sheet_name="Invoices")
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes.iloc[0]["Tipo de registro"], "Fechamento")
        self.assertEqual(changes.iloc[0]["Valor alocado"], 40)
        self.assertTrue(pd.isna(changes.iloc[0]["Numero do Contrato Cambio"]))
        self.assertEqual(len(invoices), 2)
        source_row = invoices[invoices["Numero da Invoice"] == "INV-EXPORT-CLOSING - 1"].iloc[0]
        remainder_row = invoices[invoices["Numero da Invoice"] == "INV-EXPORT-CLOSING - 2"].iloc[0]
        self.assertEqual(source_row["Status"], "AGUARDANDO CONTRATO")
        self.assertEqual(remainder_row["Status"], "RECEBIDO AGUARDANDO CAMBIO")

    def test_contract_list_filters_by_partial_contract_number(self):
        conn = app.db()
        conn.executemany(
            "INSERT INTO contratos(numero_contrato,cnpj,moeda,valor_moeda) VALUES (?,?,?,?)",
            [("C-ABC-001", "45765914000181", "USD", 100),
             ("C-ABC-002", "45765914000181", "USD", 200),
             ("C-XYZ-001", "45765914000181", "USD", 300)],
        )
        conn.commit()
        conn.close()

        response = self.client.get("/contratos?numero_contrato=ABC")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('name="numero_contrato"', html)
        self.assertIn('value="ABC"', html)
        self.assertIn("C-ABC-001", html)
        self.assertIn("C-ABC-002", html)
        self.assertNotIn("C-XYZ-001", html)

    def test_central_closing_groups_same_client_and_links_one_contract(self):
        invoice_a = self._create_invoice("INV-CENTRAL-A", "100,00")
        invoice_b = self._create_invoice("INV-CENTRAL-B", "50,00")
        for invoice_id, value in ((invoice_a, "100,00"), (invoice_b, "50,00")):
            response = self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
                "banco_credito_id": "1", "data_credito": "20/08/2026", "valor_moeda": value,
            })
            self.assertEqual(response.status_code, 302)

        response = self.client.post("/invoices/fechamentos/preview", data={
            "selected_ids": [str(invoice_a), str(invoice_b)],
            "data_fechamento": "2026-08-21", "data_liquidacao": "2026-08-25",
            "taxa_cambio": "5,1234", "banco_liquidacao_id": "1",
            "categoria_cambio": app.CATEGORIA_CAMBIO_EXPORTACAO,
            "previsao_embarque_dias": "120",
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn("Grupo 1", response.get_data(as_text=True))
        self.assertIn("5,1234", response.get_data(as_text=True))
        self.assertIn("768,51", response.get_data(as_text=True))
        conn = app.db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM fechamentos").fetchone()[0], 0)
        conn.close()

        response = self.client.post("/invoices/fechamentos", data={
            "selected_ids": [str(invoice_a), str(invoice_b)],
            "data_fechamento": "2026-08-21", "data_liquidacao": "2026-08-25",
            "taxa_cambio": "5,1234", "banco_liquidacao_id": "1",
            "categoria_cambio": app.CATEGORIA_CAMBIO_EXPORTACAO,
            "previsao_embarque_dias": "120",
            "numero_contrato_0": "CENTRAL-001",
        })
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        header = conn.execute("SELECT * FROM fechamentos").fetchone()
        self.assertIsNotNone(header)
        self.assertEqual(header["contrato_id"], 1)
        self.assertEqual(app.Decimal(str(header["taxa_cambio"])), app.Decimal("5.1234"))
        self.assertEqual(app.Decimal(str(header["valor_brl"])), app.Decimal("768.51"))
        self.assertEqual(header["previsao_embarque_dias"], 120)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM fechamentos_cambio WHERE fechamento_id=?", (header["id"],)).fetchone()[0], 2)
        contract = conn.execute("SELECT valor_moeda, taxa_cambio, valor_reais, previsao_embarque_dias FROM contratos WHERE id=?", (header["contrato_id"],)).fetchone()
        self.assertEqual(app.Decimal(str(contract["valor_moeda"])), app.Decimal("150"))
        self.assertEqual(app.Decimal(str(contract["taxa_cambio"])), app.Decimal("5.1234"))
        self.assertEqual(app.Decimal(str(contract["valor_reais"])), app.Decimal("768.51"))
        self.assertIsNone(contract["previsao_embarque_dias"])
        self.assertEqual(app.invoice_summary(conn, invoice_a)["saldo_fechamentos"], app.Decimal("0"))
        self.assertEqual(
            conn.execute("SELECT status FROM invoices WHERE id=?", (invoice_a,)).fetchone()[0],
            app.INVOICE_STATUS_LIQUIDADA,
        )
        self.assertEqual(
            conn.execute("SELECT status FROM invoices WHERE id=?", (invoice_b,)).fetchone()[0],
            app.INVOICE_STATUS_LIQUIDADA,
        )
        conn.close()

        detail = self.client.get(f"/invoices/fechamentos/{header['id']}")
        self.assertEqual(detail.status_code, 200)
        self.assertIn("CENTRAL-001", detail.get_data(as_text=True))
        self.assertIn(f"Fechamento {header['id']}", detail.get_data(as_text=True))
        self.assertIn("5,1234", detail.get_data(as_text=True))
        self.assertIn("768,51", detail.get_data(as_text=True))
        self.assertIn("PREVISÃO EMBARQUE", detail.get_data(as_text=True))
        self.assertIn("120 dias", detail.get_data(as_text=True))
        listing = self.client.get("/invoices/fechamentos")
        self.assertEqual(listing.status_code, 200)
        self.assertIn("5,1234", listing.get_data(as_text=True))
        self.assertIn("768,51", listing.get_data(as_text=True))
        export = self.client.get(f"/invoices/exportar?numero_invoice=INV-CENTRAL-A")
        self.assertEqual(export.status_code, 200)
        contract_detail = self.client.get(f"/contrato/{header['contrato_id']}")
        self.assertEqual(contract_detail.status_code, 200)
        contract_html = contract_detail.get_data(as_text=True)
        self.assertIn(f"href=\"/invoices/fechamentos/{header['id']}\"", contract_html)
        self.assertIn("PREVISÃO EMBARQUE", contract_detail.get_data(as_text=True))
        self.assertIn(f"href=\"/invoice/{invoice_a}\">INV-CENTRAL-A</a>", contract_html)
        self.assertIn(f"href=\"/invoice/{invoice_b}\">INV-CENTRAL-B</a>", contract_html)
        self.assertIn("Teste", contract_html)
        self.assertIn("Cliente Teste", contract_html)
        self.assertIn("01/08/2026", contract_html)
        self.assertIn("100,00", contract_html)
        self.assertIn("50,00", contract_html)
        self.assertIn("120 dias", contract_html)

        response = self.client.post(f"/invoices/fechamentos/{header['id']}/editar", data={
            "data_fechamento": "2026-08-22", "data_liquidacao": "2026-08-26", "taxa_cambio": "5,2000",
            "banco_liquidacao_id": "1", "categoria_cambio": app.CATEGORIA_CAMBIO_EXPORTACAO,
            "previsao_embarque_dias": "120",
        })
        self.assertEqual(response.status_code, 302)
        response = self.client.post(f"/invoices/fechamentos/{header['id']}/excluir")
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM fechamentos").fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM contratos WHERE numero_contrato='CENTRAL-001'").fetchone()[0], 0)
        conn.close()

    def test_central_closing_detail_navigates_in_id_order_and_preserves_filters(self):
        closings = [
            self._register_central_closing(
                self._create_invoice("INV-NAV-LOW", "100,00"),
                "100,00", "2026-08-10", "2026-08-15",
            ),
            self._register_central_closing(
                self._create_invoice("INV-NAV-HIGH", "300,00"),
                "300,00", "2026-08-11", "2026-08-16",
            ),
            self._register_central_closing(
                self._create_invoice("INV-NAV-MIDDLE", "200,00"),
                "200,00", "2026-08-12", "2026-08-17",
            ),
        ]

        conn = app.db()
        ordered = app.central_closing_headers(conn)
        ordered_ids = [row["id"] for row in ordered]
        conn.close()
        self.assertEqual(ordered_ids, [closings[1]["id"], closings[2]["id"], closings[0]["id"]])
        navigation_ids = sorted(item["id"] for item in closings)

        first_html = self.client.get(
            f"/invoices/fechamentos/{navigation_ids[0]}"
        ).get_data(as_text=True)
        self.assertIn('aria-disabled="true">← Anterior</span>', first_html)
        self.assertIn(
            f'href="/invoices/fechamentos/{navigation_ids[1]}"', first_html
        )

        middle_html = self.client.get(
            f"/invoices/fechamentos/{navigation_ids[1]}"
        ).get_data(as_text=True)
        self.assertIn(
            f'href="/invoices/fechamentos/{navigation_ids[0]}"', middle_html
        )
        self.assertIn(
            f'href="/invoices/fechamentos/{navigation_ids[2]}"', middle_html
        )

        last_html = self.client.get(
            f"/invoices/fechamentos/{navigation_ids[2]}"
        ).get_data(as_text=True)
        self.assertIn(
            f'href="/invoices/fechamentos/{navigation_ids[1]}"', last_html
        )
        self.assertIn('aria-disabled="true">Próximo →</span>', last_html)

        filtered_query = "fechamento_data_de=11/08/2026"
        listing_html = self.client.get(
            f"/invoices/fechamentos?{filtered_query}"
        ).get_data(as_text=True)
        self.assertIn("fechamento_data_de=11", listing_html)
        filtered_detail_html = self.client.get(
            f"/invoices/fechamentos/{ordered_ids[0]}?{filtered_query}"
        ).get_data(as_text=True)
        self.assertIn(
            f'href="/invoices/fechamentos/{ordered_ids[1]}?fechamento_data_de=11',
            filtered_detail_html,
        )
        self.assertIn(
            'href="/invoices/fechamentos?fechamento_data_de=11',
            filtered_detail_html,
        )

    def test_closing_report_aggregates_gross_brl_before_rounding(self):
        invoice_ids = [
            self._create_invoice("INV-GROSS-A", "23544,82"),
            self._create_invoice("INV-GROSS-B", "23544,82"),
            self._create_invoice("INV-GROSS-C", "40026,19"),
        ]
        for invoice_id, value in zip(invoice_ids, ("23544,82", "23544,82", "40026,19")):
            response = self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
                "banco_credito_id": "1", "data_credito": "20/08/2026",
                "valor_moeda": value,
            })
            self.assertEqual(response.status_code, 302)

        response = self.client.post("/invoices/fechamentos", data={
            "selected_ids": [str(invoice_id) for invoice_id in invoice_ids],
            "data_fechamento": "2026-08-21", "data_liquidacao": "2026-08-25",
            "taxa_cambio": "5,1102", "banco_liquidacao_id": "1",
            "categoria_cambio": app.CATEGORIA_CAMBIO_EXPORTACAO,
            "previsao_embarque_dias": "120",
            "numero_contrato_0": "630872124",
        })
        self.assertEqual(response.status_code, 302)

        conn = app.db()
        header = conn.execute(
            "SELECT id, valor_brl FROM fechamentos WHERE id=(SELECT MAX(id) FROM fechamentos)"
        ).fetchone()
        self.assertEqual(app.Decimal(str(header["valor_brl"])), app.Decimal("445179.31"))
        conn.close()

        context = app.central_closing_report_context({})
        bank = context["resumo_banco_empresa"][0]
        company = bank["empresas"][0]
        contract = company["contratos"][0]
        self.assertEqual(bank["total_brl"], app.Decimal("445179.31"))
        self.assertEqual(company["total_brl"], app.Decimal("445179.31"))
        self.assertEqual(contract["total_brl"], app.Decimal("445179.31"))

        report = self.client.get("/invoices/fechamentos/relatorio")
        self.assertEqual(report.status_code, 200)
        report_html = report.get_data(as_text=True)
        self.assertIn("445.179,31", report_html)
        self.assertNotIn("445.179,32", report_html)

    def test_closing_report_lists_all_contract_invoices_linearly_and_deduplicated(self):
        central_ids = [
            self._create_invoice("001", "100,00"),
            self._create_invoice("003", "100,00"),
            self._create_invoice("002", "100,00"),
        ]
        for invoice_id in central_ids:
            response = self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
                "banco_credito_id": "1", "data_credito": "01/08/2026",
                "valor_moeda": "100,00",
            })
            self.assertEqual(response.status_code, 302)

        response = self.client.post("/invoices/fechamentos", data={
            "selected_ids": [str(invoice_id) for invoice_id in central_ids],
            "data_fechamento": "2026-08-21", "data_liquidacao": "2026-08-25",
            "taxa_cambio": "5,0000", "banco_liquidacao_id": "1",
            "categoria_cambio": app.CATEGORIA_CAMBIO_EXPORTACAO,
            "previsao_embarque_dias": "120", "numero_contrato_0": "CONTRACT-001",
        })
        self.assertEqual(response.status_code, 302)

        direct_invoice_id = self._create_invoice("004", "100,00")
        legacy_invoice_id = self._create_invoice("005", "100,00")
        conn = app.db()
        header = conn.execute(
            "SELECT id, contrato_id FROM fechamentos ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.execute(
            "INSERT INTO invoice_contrato_cambio(invoice_id,contrato_id,valor_alocado) "
            "VALUES (?,?,?)", (central_ids[0], header["contrato_id"], 100)
        )
        conn.execute(
            "INSERT INTO invoice_contrato_cambio(invoice_id,contrato_id,valor_alocado) "
            "VALUES (?,?,?)", (direct_invoice_id, header["contrato_id"], 100)
        )
        conn.execute(
            "INSERT INTO fechamentos_cambio "
            "(invoice_id,contrato_id,moeda,valor_moeda,data_fechamento,observacao) "
            "VALUES (?,?,?,?,?,?)",
            (legacy_invoice_id, header["contrato_id"], "USD", 100, "2026-08-21", "Legado"),
        )
        conn.commit()
        conn.close()

        report = self.client.get("/invoices/fechamentos/relatorio")
        self.assertEqual(report.status_code, 200)
        html = report.get_data(as_text=True)
        main_section = html.split(
            '<section class="report-section closing-report-main-section">', 1
        )[1].split("</section>", 1)[0]
        self.assertIn('<th class="closing-report-invoices">Invoices</th>', main_section)
        self.assertIn(
            '<td class="closing-report-invoices"><strong>001</strong> ; '
            '<strong>002</strong> ; <strong>003</strong> ; '
            '<strong>004</strong><br><strong>005</strong></td>',
            main_section,
        )
        self.assertLess(main_section.index("Contrato"), main_section.index("Invoices"))
        self.assertLess(main_section.index("Invoices"), main_section.index("Categoria câmbio"))
        self.assertLess(main_section.index("Data de liquidação"), main_section.index("Moeda"))
        self.assertLess(main_section.index("Moeda"), main_section.index("Valor moeda"))
        self.assertIn("<br>", main_section)
        self.assertIn('<td colspan="10"></td>', html)
        self.assertNotIn("Categoria Câmbio", html)
        self.assertNotIn("�", html)

    def test_closing_report_lists_invoices_for_pending_group(self):
        invoice_ids = [
            self._create_invoice("PENDING-002", "100,00"),
            self._create_invoice("PENDING-001", "100,00"),
        ]
        for invoice_id in invoice_ids:
            response = self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
                "banco_credito_id": "1", "data_credito": "01/08/2026",
                "valor_moeda": "100,00",
            })
            self.assertEqual(response.status_code, 302)

        response = self.client.post("/invoices/fechamentos", data={
            "selected_ids": [str(invoice_id) for invoice_id in invoice_ids],
            "data_fechamento": "2026-08-21", "data_liquidacao": "2026-08-25",
            "taxa_cambio": "5,0000", "banco_liquidacao_id": "1",
            "categoria_cambio": app.CATEGORIA_CAMBIO_EXPORTACAO,
            "previsao_embarque_dias": "120",
        })
        self.assertEqual(response.status_code, 302)

        report = self.client.get("/invoices/fechamentos/relatorio")
        self.assertEqual(report.status_code, 200)
        html = report.get_data(as_text=True)
        self.assertIn(
            '<td class="closing-report-invoices"><strong>PENDING-001</strong> ; '
            '<strong>PENDING-002</strong></td>',
            html,
        )

    def test_export_prediction_uses_unanimous_company_default_and_rejects_conflict(self):
        conn = app.db()
        conn.execute(
            "INSERT INTO empresas (razao_social,cnpj,apelido) VALUES (?,?,?)",
            ("Empresa Dois", "45765914000262", "Empresa Dois"),
        )
        conn.execute(
            "INSERT INTO competencias (empresa_id,descricao,data_inicial,data_final) VALUES (?,?,?,?)",
            (2, "Agosto/2026", "2026-08-01", "2026-08-31"),
        )
        conn.executemany(
            "INSERT INTO configuracoes_padrao (empresa_id,previsao_embarque_dias) VALUES (?,?)",
            [(1, 120), (2, 120)],
        )
        conn.commit()
        conn.close()

        invoice_a = self._create_invoice("INV-DEFAULT-A", "100,00")
        invoice_b = self._create_invoice(
            "INV-DEFAULT-B", "100,00", empresa_id=2, competencia_id=2
        )
        for invoice_id in (invoice_a, invoice_b):
            self.assertEqual(self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
                "banco_credito_id": "1", "data_credito": "20/08/2026", "valor_moeda": "100,00",
            }).status_code, 302)
        response = self.client.post("/invoices/fechamentos", data={
            "selected_ids": [str(invoice_a), str(invoice_b)],
            "data_fechamento": "2026-08-21", "data_liquidacao": "2026-08-25",
            "taxa_cambio": "5,0000", "banco_liquidacao_id": "1",
            "categoria_cambio": app.CATEGORIA_CAMBIO_EXPORTACAO,
        })
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        header = conn.execute("SELECT previsao_embarque_dias FROM fechamentos").fetchone()
        self.assertEqual(header["previsao_embarque_dias"], 120)
        conn.execute(
            "UPDATE configuracoes_padrao SET previsao_embarque_dias=240 WHERE empresa_id=2"
        )
        conn.commit()
        conn.close()

        conflict_a = self._create_invoice("INV-CONFLICT-A", "100,00")
        conflict_b = self._create_invoice(
            "INV-CONFLICT-B", "100,00", empresa_id=2, competencia_id=2
        )
        for invoice_id in (conflict_a, conflict_b):
            self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
                "banco_credito_id": "1", "data_credito": "20/08/2026", "valor_moeda": "100,00",
            })
        response = self.client.post("/invoices/fechamentos", data={
            "selected_ids": [str(conflict_a), str(conflict_b)],
            "data_fechamento": "2026-08-21", "data_liquidacao": "2026-08-25",
            "taxa_cambio": "5,0000", "banco_liquidacao_id": "1",
            "categoria_cambio": app.CATEGORIA_CAMBIO_EXPORTACAO,
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("PREVISÃO EMBARQUE", response.get_data(as_text=True))
        conn = app.db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM fechamentos").fetchone()[0], 1)
        conn.close()

    def test_export_prediction_backend_bounds_are_strict(self):
        self.assertEqual(app.parse_previsao_embarque_dias("1", required=True), 1)
        self.assertEqual(app.parse_previsao_embarque_dias("360", required=True), 360)
        for invalid in ("", "0", "361", "-1", "1.5", "texto"):
            with self.assertRaises(ValueError):
                app.parse_previsao_embarque_dias(invalid, required=True)

    def test_central_closing_without_contract_persists_awaiting_contract(self):
        invoice_id = self._create_invoice("INV-CENTRAL-PENDING", "100,00")
        self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "20/08/2026", "valor_moeda": "100,00",
        })
        response = self.client.post("/invoices/fechamentos", data={
            "selected_ids": [str(invoice_id)],
            "data_fechamento": "2026-08-21", "data_liquidacao": "2026-08-25",
            "taxa_cambio": "5,0000", "banco_liquidacao_id": "1",
            "categoria_cambio": app.CATEGORIA_CAMBIO_EXPORTACAO,
            "previsao_embarque_dias": "120",
        })
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        invoice = conn.execute(
            "SELECT status, status_manual FROM invoices WHERE id=?", (invoice_id,)
        ).fetchone()
        self.assertEqual(invoice["status"], app.INVOICE_STATUS_AGUARDANDO_CONTRATO)
        self.assertEqual(invoice["status_manual"], 0)
        self.assertEqual(conn.execute(
            "SELECT contrato_id FROM fechamentos WHERE id=(SELECT MAX(id) FROM fechamentos)"
        ).fetchone()[0], None)
        conn.close()

    def test_central_closing_accepts_different_amounts_and_splits_only_partial_invoice(self):
        partial_id = self._create_invoice("INV-CENTRAL-SPLIT", "1000,00")
        integral_id = self._create_invoice("INV-CENTRAL-INTEGRAL", "500,00")
        for invoice_id, value in ((partial_id, "1000,00"), (integral_id, "500,00")):
            self.assertEqual(self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
                "banco_credito_id": "1", "data_credito": "20/08/2026", "valor_moeda": value,
            }).status_code, 302)

        response = self.client.post("/invoices/fechamentos", data={
            "selected_ids": [str(partial_id), str(integral_id)],
            "valor_fechamento_%d" % partial_id: "400,00",
            "valor_fechamento_%d" % integral_id: "500,00",
            "data_fechamento": "2026-08-21", "data_liquidacao": "2026-08-25",
            "taxa_cambio": "5,0000", "banco_liquidacao_id": "1",
            "categoria_cambio": app.CATEGORIA_CAMBIO_EXPORTACAO,
            "previsao_embarque_dias": "120",
            "numero_contrato_grupo_1_USD": "CENTRAL-SPLIT-001",
        })
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        remainder_id = conn.execute(
            "SELECT invoice_id FROM invoice_desdobramentos WHERE invoice_anterior_id=?", (partial_id,)
        ).fetchone()[0]
        source = app.invoice_summary(conn, partial_id)
        remainder = app.invoice_summary(conn, remainder_id)
        integral = app.invoice_summary(conn, integral_id)
        self.assertEqual(source["numero_invoice"], "INV-CENTRAL-SPLIT - 1")
        self.assertEqual(source["status"], app.INVOICE_STATUS_LIQUIDADA)
        self.assertEqual(source["total_fechamentos"], app.Decimal("400"))
        self.assertEqual(remainder["valor_moeda"], app.Decimal("600"))
        self.assertEqual(remainder["total_fechamentos"], app.Decimal("0"))
        self.assertEqual(remainder["status"], app.INVOICE_STATUS_RECEBIDA_AGUARDANDO_CAMBIO)
        self.assertEqual(integral["numero_invoice"], "INV-CENTRAL-INTEGRAL")
        self.assertEqual(integral["status"], app.INVOICE_STATUS_LIQUIDADA)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM fechamentos").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM fechamentos_cambio").fetchone()[0], 2)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM recebimentos_invoice").fetchone()[0], 2)
        self.assertEqual(conn.execute(
            "SELECT valor_moeda FROM contratos WHERE numero_contrato='CENTRAL-SPLIT-001'"
        ).fetchone()[0], 900)
        conn.close()

    def test_partial_split_is_blocked_for_legacy_and_incomplete_received_invoice(self):
        conn = app.db()
        conn.execute("""
            INSERT INTO invoices
                (empresa_id,numero_invoice,tipo_documento,competencia_id,cliente_id,
                 data_emissao,moeda,valor_moeda,status,status_manual)
            VALUES (1,'INV-LEGACY-SPLIT','COMMERCIAL_INVOICE',1,1,'2026-08-01',
                    'USD',1000,'RECEBIDA_AGUARDANDO_CAMBIO',0)
        """)
        legacy_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute("""
            INSERT INTO recebimentos_invoice
                (invoice_id,banco_credito_id,data_credito,moeda,valor_moeda)
            VALUES (?,1,'2026-08-10','USD',1000)
        """, (legacy_id,))
        conn.commit()
        conn.close()
        response = self.client.post(f"/invoice/{legacy_id}/fechamentos", data={
            "valor_moeda": "400,00", "data_fechamento": "12/08/2026",
        }, follow_redirects=True)
        self.assertIn("legada", response.get_data(as_text=True))

        incomplete_id = self._create_invoice("INV-INCOMPLETE-SPLIT", "1000,00")
        self.client.post(f"/invoice/{incomplete_id}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "10/08/2026", "valor_moeda": "500,00",
        })
        response = self.client.post(f"/invoice/{incomplete_id}/fechamentos", data={
            "valor_moeda": "400,00", "data_fechamento": "12/08/2026",
        }, follow_redirects=True)
        self.assertIn("totalmente recebida", response.get_data(as_text=True))
        conn = app.db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM invoice_desdobramentos").fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM fechamentos_cambio").fetchone()[0], 0)
        self.assertEqual(conn.execute(
            "SELECT valor_moeda FROM invoices WHERE id=?", (incomplete_id,)
        ).fetchone()[0], 1000)
        conn.close()

    def test_partial_split_validates_amounts_and_rolls_back_name_collision(self):
        invoice_id = self._create_invoice("INV-SPLIT-COLLISION", "1000,00")
        self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "10/08/2026", "valor_moeda": "1000,00",
        })
        conn = app.db()
        conn.execute("""
            INSERT INTO invoices
                (empresa_id,numero_invoice,tipo_documento,competencia_id,cliente_id,
                 data_emissao,moeda,valor_moeda,status,status_manual)
            VALUES (1,'INV-SPLIT-COLLISION - 1','COMMERCIAL_INVOICE',1,1,
                    '2026-08-01','USD',100,'RECEBIDA_AGUARDANDO_CAMBIO',0)
        """)
        conn.commit()
        conn.close()
        response = self.client.post(f"/invoice/{invoice_id}/fechamentos", data={
            "valor_moeda": "400,00", "data_fechamento": "12/08/2026",
        }, follow_redirects=True)
        self.assertIn("renomear", response.get_data(as_text=True))
        conn = app.db()
        source = conn.execute(
            "SELECT numero_invoice, valor_moeda FROM invoices WHERE id=?", (invoice_id,)
        ).fetchone()
        self.assertEqual(source["numero_invoice"], "INV-SPLIT-COLLISION")
        self.assertEqual(source["valor_moeda"], 1000)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM fechamentos_cambio").fetchone()[0], 0)
        conn.close()

        conn = app.db()
        conn.execute("DELETE FROM invoices WHERE numero_invoice='INV-SPLIT-COLLISION - 1'")
        conn.commit()
        conn.close()
        for value in ("0,00", "1000,01"):
            response = self.client.post("/invoices/fechamentos", data={
                "selected_ids": [str(invoice_id)],
                "valor_fechamento_%d" % invoice_id: value,
                "data_fechamento": "2026-08-21", "data_liquidacao": "2026-08-25",
                "taxa_cambio": "5,0000", "banco_liquidacao_id": "1",
                "categoria_cambio": app.CATEGORIA_CAMBIO_EXPORTACAO,
                "previsao_embarque_dias": "120",
            }, follow_redirects=True)
            self.assertIn("valor", response.get_data(as_text=True))
        conn = app.db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM fechamentos").fetchone()[0], 0)
        conn.close()

    def test_partial_receipt_without_manual_writeoff_remains_blocked_from_split(self):
        invoice_id = self._create_invoice("INV-SPLIT-PENDING-RECEIPT", "10000,00")
        self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "10/08/2026", "valor_moeda": "7000,00",
        })

        response = self.client.post(f"/invoice/{invoice_id}/fechamentos", data={
            "valor_moeda": "4000,00", "data_fechamento": "12/08/2026",
        }, follow_redirects=True)
        self.assertIn("totalmente recebida", response.get_data(as_text=True))

        conn = app.db()
        summary = app.invoice_summary(conn, invoice_id)
        self.assertEqual(summary["saldo_recebimento"], app.Decimal("3000"))
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM invoice_desdobramentos"
        ).fetchone()[0], 0)
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM fechamentos_cambio"
        ).fetchone()[0], 0)
        conn.close()

    def test_partial_receipt_with_integral_manual_writeoff_can_split_without_reusing_writeoff(self):
        invoice_id = self._create_invoice("INV-SPLIT-MANUAL-WRITEOFF", "10000,00")
        self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "10/08/2026", "valor_moeda": "7000,00",
        })
        self.assertEqual(self.client.post(f"/invoice/{invoice_id}/baixa", data={
            "justificativa_baixa": "SALDO RESIDUAL INCOBRAVEL",
            "data_baixa": "11/08/2026",
        }).status_code, 302)

        conn = app.db()
        before_split = app.invoice_summary(conn, invoice_id)
        self.assertEqual(before_split["total_recebido"], app.Decimal("7000"))
        self.assertEqual(before_split["total_baixado"], app.Decimal("3000"))
        self.assertEqual(before_split["saldo_recebimento"], app.Decimal("0"))
        self.assertEqual(before_split["saldo_fechamentos"], app.Decimal("7000"))
        self.assertEqual(before_split["status"], app.INVOICE_STATUS_RECEBIDA_AGUARDANDO_CAMBIO)
        baixa = conn.execute(
            "SELECT valor_moeda, justificativa_baixa FROM invoice_baixas WHERE invoice_id=?",
            (invoice_id,),
        ).fetchone()
        self.assertEqual(app.Decimal(str(baixa["valor_moeda"])), app.Decimal("3000"))
        self.assertEqual(baixa["justificativa_baixa"], "SALDO RESIDUAL INCobravel".upper())
        conn.close()

        # The manual amount cannot be reused as exchange/closing availability.
        response = self.client.post(f"/invoice/{invoice_id}/fechamentos", data={
            "valor_moeda": "8000,00", "data_fechamento": "12/08/2026",
        }, follow_redirects=True)
        self.assertIn("saldo disponível", response.get_data(as_text=True))

        self.assertEqual(self.client.post(f"/invoice/{invoice_id}/fechamentos", data={
            "valor_moeda": "4000,00", "data_fechamento": "12/08/2026",
        }).status_code, 302)

        conn = app.db()
        child_id = conn.execute(
            "SELECT invoice_id FROM invoice_desdobramentos WHERE invoice_anterior_id=?",
            (invoice_id,),
        ).fetchone()[0]
        source = app.invoice_summary(conn, invoice_id)
        child = app.invoice_summary(conn, child_id)
        self.assertEqual(source["valor_moeda"], app.Decimal("7000"))
        self.assertEqual(source["total_recebido"], app.Decimal("4000"))
        self.assertEqual(source["total_baixado"], app.Decimal("3000"))
        self.assertEqual(source["saldo_recebimento"], app.Decimal("0"))
        self.assertEqual(child["valor_moeda"], app.Decimal("3000"))
        self.assertEqual(child["total_recebido"], app.Decimal("3000"))
        self.assertEqual(child["total_baixado"], app.Decimal("0"))
        self.assertEqual(child["saldo_fechamentos"], app.Decimal("3000"))
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM invoice_baixas WHERE invoice_id=?", (invoice_id,)
        ).fetchone()[0], 1)
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM invoice_baixas WHERE invoice_id=?", (child_id,)
        ).fetchone()[0], 0)
        conn.close()

    def test_manual_writeoff_requires_justification_and_excludes_only_receivable_balance(self):
        invoice_id = self._create_invoice("INV-MANUAL-WRITEOFF", "1000,00")
        self.assertEqual(self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "10/08/2026", "valor_moeda": "700,00",
        }).status_code, 302)

        conn = app.db()
        receipt_before = dict(conn.execute(
            "SELECT id, valor_moeda, data_credito FROM recebimentos_invoice WHERE invoice_id=?",
            (invoice_id,),
        ).fetchone())
        conn.close()

        detail_before = self.client.get(f"/invoice/{invoice_id}").get_data(as_text=True)
        self.assertIn('name="justificativa_baixa" data-uppercase required', detail_before)

        response = self.client.post(
            f"/invoice/{invoice_id}/baixa",
            data={"justificativa_baixa": "   "},
            follow_redirects=True,
        )
        self.assertIn("justificativa", response.get_data(as_text=True).lower())

        conn = app.db()
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM invoice_baixas WHERE invoice_id=?", (invoice_id,)
        ).fetchone()[0], 0)
        conn.close()

        response = self.client.post(
            f"/invoice/{invoice_id}/baixa",
            data={
                "justificativa_baixa": "saldo residual incobravel",
                "data_baixa": "11/08/2026",
            },
        )
        self.assertEqual(response.status_code, 302)

        conn = app.db()
        baixa = conn.execute(
            "SELECT id, moeda, valor_moeda, justificativa_baixa, data_baixa, created_at "
            "FROM invoice_baixas WHERE invoice_id=?",
            (invoice_id,),
        ).fetchone()
        receipt_after = dict(conn.execute(
            "SELECT id, valor_moeda, data_credito FROM recebimentos_invoice WHERE invoice_id=?",
            (invoice_id,),
        ).fetchone())
        summary = app.invoice_summary(conn, invoice_id)
        self.assertEqual(baixa["moeda"], "USD")
        self.assertEqual(app.Decimal(str(baixa["valor_moeda"])), app.Decimal("300"))
        self.assertEqual(baixa["justificativa_baixa"], "SALDO RESIDUAL INCOBRAVEL")
        self.assertEqual(baixa["data_baixa"], "2026-08-11")
        self.assertTrue(baixa["created_at"])
        self.assertEqual(receipt_after, receipt_before)
        self.assertEqual(summary["total_recebido"], app.Decimal("700"))
        self.assertEqual(summary["total_baixado"], app.Decimal("300"))
        self.assertEqual(summary["saldo_recebimento"], app.Decimal("0"))
        conn.close()

        saldo = self.client.get(f"/invoices/{invoice_id}/saldo").get_json()
        self.assertEqual(saldo["total_baixado"], 300.0)
        self.assertEqual(saldo["saldo_recebimento"], 0.0)
        report = app.build_invoice_report_context()
        awaiting = next(table for table in report["tables"] if table["variant"] == "awaiting")
        self.assertEqual(awaiting["total"], app.Decimal("0"))

        detail = self.client.get(f"/invoice/{invoice_id}").get_data(as_text=True)
        self.assertIn("SALDO RESIDUAL INCOBRAVEL", detail)
        self.assertNotIn("Confirmar baixa", detail)

    def test_manual_writeoff_must_be_removed_before_invoice_deletion(self):
        invoice_id = self._create_invoice("INV-MANUAL-WRITEOFF-DELETE", "1000,00")
        self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "10/08/2026", "valor_moeda": "700,00",
        })
        self.client.post(f"/invoice/{invoice_id}/baixa", data={
            "justificativa_baixa": "AJUSTE MANUAL", "data_baixa": "11/08/2026",
        })

        blocked = self.client.post(f"/invoice/{invoice_id}/excluir", follow_redirects=True)
        self.assertIn("baixa", blocked.get_data(as_text=True).lower())
        conn = app.db()
        receipt_id = conn.execute(
            "SELECT id FROM recebimentos_invoice WHERE invoice_id=?", (invoice_id,)
        ).fetchone()[0]
        baixa_id = conn.execute(
            "SELECT id FROM invoice_baixas WHERE invoice_id=?", (invoice_id,)
        ).fetchone()[0]
        conn.close()

        self.assertEqual(self.client.post(
            f"/invoice/{invoice_id}/baixas/{baixa_id}/excluir"
        ).status_code, 302)
        conn = app.db()
        self.assertEqual(app.invoice_summary(conn, invoice_id)["saldo_recebimento"], app.Decimal("300"))
        conn.close()

        blocked_by_receipt = self.client.post(f"/invoice/{invoice_id}/excluir", follow_redirects=True)
        self.assertIn("recebimento", blocked_by_receipt.get_data(as_text=True).lower())
        self.assertEqual(self.client.post(
            f"/invoice/{invoice_id}/recebimentos/{receipt_id}/excluir"
        ).status_code, 302)
        self.assertEqual(self.client.post(f"/invoice/{invoice_id}/excluir").status_code, 302)
        conn = app.db()
        self.assertIsNone(conn.execute(
            "SELECT id FROM invoices WHERE id=?", (invoice_id,)
        ).fetchone())
        conn.close()

    def test_central_closing_rejects_different_clients_without_partial_write(self):
        conn = app.db()
        conn.execute("INSERT INTO clientes (nome, pais) VALUES (?, ?)", ("Outro Cliente", "BR"))
        conn.commit()
        conn.close()
        invoice_a = self._create_invoice("INV-CENTRAL-CLIENT-A", "100,00")
        invoice_b = self._create_invoice("INV-CENTRAL-CLIENT-B", "100,00", client_id=2)
        for invoice_id in (invoice_a, invoice_b):
            self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
                "banco_credito_id": "1", "data_credito": "20/08/2026", "valor_moeda": "100,00",
            })
        response = self.client.post("/invoices/fechamentos", data={
            "selected_ids": [str(invoice_a), str(invoice_b)],
            "data_fechamento": "2026-08-21", "data_liquidacao": "2026-08-25",
            "taxa_cambio": "5,0000", "banco_liquidacao_id": "1",
            "categoria_cambio": app.CATEGORIA_CAMBIO_EXPORTACAO,
            "previsao_embarque_dias": "120",
            "numero_contrato_0": "SHOULD-ROLLBACK",
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("mesmo Cliente", response.get_data(as_text=True))
        conn = app.db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM fechamentos").fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM contratos WHERE numero_contrato='SHOULD-ROLLBACK'").fetchone()[0], 0)
        conn.close()

    def test_central_closing_splits_bank_and_rejects_duplicate_contract_atomically(self):
        conn = app.db()
        conn.execute("INSERT INTO contrapartes (nome) VALUES (?)", ("Banco Alternativo",))
        bank_two = conn.execute("SELECT id FROM contrapartes WHERE nome='Banco Alternativo'").fetchone()[0]
        conn.commit()
        conn.close()
        invoice_a = self._create_invoice("INV-CENTRAL-BANK-A", "100,00")
        invoice_b = self._create_invoice("INV-CENTRAL-BANK-B", "100,00")
        self.client.post(f"/invoice/{invoice_a}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "20/08/2026", "valor_moeda": "100,00",
        })
        self.client.post(f"/invoice/{invoice_b}/recebimentos", data={
            "banco_credito_id": str(bank_two), "data_credito": "20/08/2026", "valor_moeda": "100,00",
        })
        response = self.client.post("/invoices/fechamentos", data={
            "selected_ids": [str(invoice_a), str(invoice_b)],
            "data_fechamento": "2026-08-21", "data_liquidacao": "2026-08-25",
            "taxa_cambio": "5,0000", "banco_liquidacao_id": "1",
            "categoria_cambio": app.CATEGORIA_CAMBIO_EXPORTACAO,
            "previsao_embarque_dias": "120",
            "numero_contrato_0": "DUPLICADO", "numero_contrato_1": "DUPLICADO",
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("número DUPLICADO", response.get_data(as_text=True))
        conn = app.db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM fechamentos").fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM contratos WHERE numero_contrato='DUPLICADO'").fetchone()[0], 0)
        conn.close()

        response = self.client.post("/invoices/fechamentos/preview", data={
            "selected_ids": [str(invoice_a), str(invoice_b)],
            "data_fechamento": "2026-08-21", "data_liquidacao": "2026-08-25",
            "taxa_cambio": "5,0000", "banco_liquidacao_id": "1",
            "categoria_cambio": app.CATEGORIA_CAMBIO_EXPORTACAO,
            "previsao_embarque_dias": "120",
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn("Grupo 2", response.get_data(as_text=True))

    def test_central_closing_rejects_invoice_without_credit_bank(self):
        invoice_id = self._create_invoice("INV-CENTRAL-NO-BANK", "100,00")
        self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
            "banco_credito_id": "", "data_credito": "20/08/2026", "valor_moeda": "100,00",
        })
        response = self.client.post("/invoices/fechamentos", data={
            "selected_ids": [str(invoice_id)],
            "data_fechamento": "2026-08-21", "data_liquidacao": "2026-08-25",
            "taxa_cambio": "5,0000", "banco_liquidacao_id": "1",
            "categoria_cambio": app.CATEGORIA_CAMBIO_EXPORTACAO,
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("exatamente um Banco de Crédito", response.get_data(as_text=True))
        conn = app.db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM fechamentos").fetchone()[0], 0)
        conn.close()

    def test_central_closing_requires_rate_with_at_most_four_decimals(self):
        invoice_id = self._create_invoice("INV-CENTRAL-RATE", "100,00")
        self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "20/08/2026", "valor_moeda": "100,00",
        })
        base = {
            "selected_ids": [str(invoice_id)],
            "data_fechamento": "2026-08-21", "data_liquidacao": "2026-08-25",
            "banco_liquidacao_id": "1",
            "categoria_cambio": app.CATEGORIA_CAMBIO_EXPORTACAO,
        }
        response = self.client.post("/invoices/fechamentos", data=base, follow_redirects=True)
        self.assertIn("Taxa é obrigatória", response.get_data(as_text=True))
        response = self.client.post("/invoices/fechamentos", data={**base, "taxa_cambio": "5,12345"}, follow_redirects=True)
        self.assertIn("no máximo 4 casas", response.get_data(as_text=True))
        conn = app.db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM fechamentos").fetchone()[0], 0)
        conn.close()

    def test_registered_closing_filters_are_combined_and_report_uses_them(self):
        conn = app.db()
        conn.execute("INSERT INTO clientes (nome, pais) VALUES (?, ?)", ("Cliente Alternativo", "DE"))
        conn.execute("INSERT INTO contrapartes (nome) VALUES (?)", ("Banco Alternativo",))
        conn.commit()
        alternate_bank_id = conn.execute(
            "SELECT id FROM contrapartes WHERE nome=?", ("Banco Alternativo",)
        ).fetchone()[0]
        conn.close()

        first_invoice = self._create_invoice("INV-REPORT-A", "100,00")
        first = self._register_central_closing(
            first_invoice, "100,00", "2026-08-10", "2026-08-15",
            banco_liquidacao_id=str(alternate_bank_id),
            numero_contrato="CENTRAL-REPORT-A",
        )
        second_invoice = self._create_invoice("INV-REPORT-B", "200,00")
        second = self._register_central_closing(
            second_invoice, "200,00", "2026-08-11", "2026-08-15",
        )
        third_invoice = self._create_invoice(
            "INV-REPORT-C", "50,00", client_id=2, currency="EUR"
        )
        third = self._register_central_closing(
            third_invoice, "50,00", "2026-08-10", "2026-08-20",
            banco_liquidacao_id=str(alternate_bank_id),
        )

        query = (
            "fechamento_cliente_id=1&fechamento_data_de=10/08/2026&"
            "fechamento_data_ate=10/08/2026&liquidacao_data_de=15/08/2026&"
            f"liquidacao_data_ate=15/08/2026&fechamento_banco_liquidacao_id={alternate_bank_id}"
        )
        listing = self.client.get(f"/invoices/fechamentos?{query}")
        self.assertEqual(listing.status_code, 200)
        listing_html = listing.get_data(as_text=True)
        self.assertIn(f"/invoices/fechamentos/{first['id']}", listing_html)
        self.assertNotIn(f"/invoices/fechamentos/{second['id']}", listing_html)
        self.assertNotIn(f"/invoices/fechamentos/{third['id']}", listing_html)
        self.assertIn('formtarget="_blank"', listing_html)
        self.assertIn("/invoices/fechamentos/relatorio", listing_html)

        report = self.client.get(f"/invoices/fechamentos/relatorio?{query}")
        self.assertEqual(report.status_code, 200)
        report_html = report.get_data(as_text=True)
        self.assertIn("Empresa / apelido", report_html)
        self.assertIn("Cliente", report_html)
        self.assertIn("Banco de crédito", report_html)
        self.assertIn("Banco de liquidação", report_html)
        self.assertLess(report_html.index("Banco de crédito"), report_html.index("Banco de liquidação"))
        self.assertIn("Data de fechamento", report_html)
        self.assertIn("Data de liquidação", report_html)
        self.assertLess(report_html.index("Data de liquidação"), report_html.index("Moeda"))
        self.assertLess(report_html.index("Moeda"), report_html.index("Valor moeda"))
        self.assertIn("Categoria câmbio", report_html)
        self.assertNotIn("Categoria Câmbio", report_html)
        self.assertIn("Valor moeda", report_html)
        self.assertNotIn("Valor original", report_html)
        self.assertIn("Taxa", report_html)
        self.assertIn("Valor em BRL", report_html)
        self.assertIn("Teste", report_html)
        self.assertIn("Cliente Teste", report_html)
        self.assertIn("Banco Alternativo", report_html)
        self.assertIn('<td class="closing-report-currency">USD</td>', report_html)
        self.assertIn("100,00", report_html)
        self.assertIn("500,00", report_html)
        self.assertNotIn("closing-report-total-grid", report_html)
        self.assertIn("Total em BRL", report_html)
        self.assertIn("TOTAL POR BANCO", report_html)
        self.assertIn("TOTAL POR EMPRESA", report_html)
        self.assertIn("TOTAL POR CLIENTE", report_html)
        self.assertIn("CENTRAL-REPORT-A", report_html)
        self.assertNotIn("CNPJ", report_html)
        self.assertNotIn("45765914000181", report_html)
        self.assertIn("Imprimir / Salvar PDF", report_html)
        self.assertIn("<title></title>", report_html)
        self.assertNotIn("Emitido em:", report_html)
        self.assertNotIn("closing-report-issued-at", report_html)
        self.assertIn("<h1>Relatório de fechamentos</h1>", report_html)
        report_utf8 = report.data.decode("utf-8")
        self.assertNotIn("CÃ", report_utf8)
        self.assertNotIn("ï¿½", report_utf8)
        self.assertNotIn("�", report_utf8)
        css_response = self.client.get("/static/style.css")
        try:
            css = css_response.get_data(as_text=True)
        finally:
            css_response.close()
        self.assertIn(".closing-report-table{table-layout:auto", css)
        self.assertIn(".closing-report-currency{", css)
        self.assertIn("@page closing-report{size:A4 landscape;margin:12mm", css)
        self.assertIn("@bottom-right{content:", css)
        self.assertIn("counter(page)", css)
        self.assertIn("counter(pages)", css)
        self.assertIn("closing-report-fallback-counters", css)
        self.assertNotIn("@page closing-report{size:A4 landscape;margin:8mm", css)
        self.assertIn("vertical-align:middle", css)
        self.assertIn("calc(297mm - 24mm)", css)
        self.assertIn("PAGE_MARGIN_MM = 12", report_html)
        self.assertIn("millimetersToPixels(3)", report_html)
        self.assertIn("table-layout:fixed", css)
        self.assertIn("closing-report-table .closing-report-invoices{font-size:10px!important", css)
        self.assertIn("th.closing-report-invoices{font-size:10.5px!important", css)
        self.assertIn("closing-report-summary-table .closing-report-company-row td{font-size:9.5px", css)
        self.assertIn("closing-report-table th,.closing-report-table td{white-space:nowrap", css)
        self.assertIn("closing-report-table tbody td:nth-child(5)", css)
        self.assertIn("closing-report-table tbody td:nth-child(6)", css)
        self.assertIn("closing-report-table thead th{font-size:7.5px", css)
        self.assertIn("thead th.closing-report-invoices{font-size:10.5px!important", css)
        self.assertIn("closing-report-summary-page .closing-report-summary-table .closing-report-bank-row", css)
        self.assertIn("font-size:11px!important", css)
        self.assertIn("closing-report-summary-page .closing-report-summary-table .closing-report-company-row td{font-size:10px!important", css)
        self.assertIn("closing-report-summary-page .closing-report-summary-table .closing-report-contract-row td{font-size:9.5px!important", css)
        self.assertIn("closing-report-summary-grid>.closing-report-analytics:first-child{padding-top:22px", css)
        self.assertIn("closing-report-table thead th.closing-report-invoices{font-size:7.5px!important", css)
        self.assertIn("closing-report-table .closing-report-invoices{font-size:10px!important", css)

        all_report = self.client.get("/invoices/fechamentos/relatorio")
        self.assertEqual(all_report.status_code, 200)
        all_report_html = all_report.get_data(as_text=True)
        self.assertIn('<td class="closing-report-currency">EUR</td>', all_report_html)
        self.assertIn("300,00", all_report_html)
        self.assertIn("50,00", all_report_html)
        self.assertIn("1.750,00", all_report_html)

    def test_closing_report_renders_any_registered_currency_without_wrapping(self):
        invoice_id = self._create_invoice("INV-REPORT-GBP", "100,00", currency="GBP")
        self._register_central_closing(
            invoice_id, "100,00", "2026-08-12", "2026-08-15"
        )

        report = self.client.get("/invoices/fechamentos/relatorio")
        self.assertEqual(report.status_code, 200)
        html = report.get_data(as_text=True)
        self.assertIn('<th class="closing-report-currency">Moeda</th>', html)
        self.assertIn('<td class="closing-report-currency">GBP</td>', html)

    def test_registered_closing_filters_reject_invalid_or_inverted_dates(self):
        listing = self.client.get(
            "/invoices/fechamentos?fechamento_data_de=31/02/2026"
        )
        self.assertEqual(listing.status_code, 200)
        self.assertIn("Data inválida", listing.get_data(as_text=True))
        self.assertIn("Nenhum Fechamento centralizado registrado", listing.get_data(as_text=True))

        report = self.client.get(
            "/invoices/fechamentos/relatorio?"
            "liquidacao_data_de=20/08/2026&liquidacao_data_ate=10/08/2026"
        )
        self.assertEqual(report.status_code, 400)
        self.assertIn("data inicial de liquidação", report.get_data(as_text=True))

    def test_contract_category_migration_backfills_central_financial_contract(self):
        previous_db = app.DB
        legacy_path = Path(tempfile.mktemp(prefix="duecontrol_contract_category_migration_", suffix=".db"))
        try:
            app.DB = legacy_path
            app.init_db()
            conn = app.db()
            conn.execute("INSERT INTO clientes(nome,pais) VALUES (?,?)", ("Cliente Migração", "BR"))
            conn.execute("INSERT INTO contrapartes(nome) VALUES (?)", ("Banco Migração",))
            conn.execute("""
                INSERT INTO contratos
                    (numero_contrato,moeda,valor_moeda,valor_reais,status,cliente,cliente_id,
                     saldo_zerado_manual,categoria_cambio)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, ("MIG-FIN-001", "USD", 75, 375, app.STATUS_PENDENTE,
                  "Cliente Migração", 1, 0, app.CATEGORIA_CAMBIO_EXPORTACAO))
            conn.execute("""
                INSERT INTO fechamentos
                    (cliente_id,banco_credito_id,moeda,categoria_cambio,data_fechamento,
                     data_liquidacao,banco_liquidacao_id,taxa_cambio,valor_brl,contrato_id)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (1, 1, "USD", app.CATEGORIA_CAMBIO_FINANCEIRO, "2026-08-01",
                  "2026-08-02", 1, 5, 375, 1))
            conn.execute("""
                CREATE TABLE contratos_legacy (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    numero_contrato TEXT NOT NULL UNIQUE,
                    banco_liquidacao_id INTEGER,
                    banco_liquidacao TEXT,
                    data_fechamento TEXT,
                    data_liquidacao TEXT,
                    moeda TEXT NOT NULL DEFAULT 'USD',
                    taxa_cambio REAL,
                    status TEXT NOT NULL DEFAULT 'PENDENTE',
                    observacao TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    valor_moeda REAL NOT NULL DEFAULT 0,
                    valor_reais REAL,
                    banco TEXT,
                    banco_id INTEGER,
                    banco_credito TEXT,
                    data_contrato TEXT,
                    data_recebimento TEXT,
                    cnpj TEXT,
                    cliente TEXT,
                    cliente_id INTEGER,
                    competencia_id INTEGER,
                    saldo_zerado_manual INTEGER NOT NULL DEFAULT 0
                )
            """)
            conn.execute("""
                INSERT INTO contratos_legacy
                SELECT id,numero_contrato,banco_liquidacao_id,banco_liquidacao,data_fechamento,
                       data_liquidacao,moeda,taxa_cambio,status,observacao,created_at,valor_moeda,
                       valor_reais,banco,banco_id,banco_credito,data_contrato,data_recebimento,
                       cnpj,cliente,cliente_id,competencia_id,saldo_zerado_manual
                FROM contratos
            """)
            conn.commit()
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute("DROP TABLE contratos")
            conn.execute("ALTER TABLE contratos_legacy RENAME TO contratos")
            conn.execute("PRAGMA user_version=11")
            conn.commit()
            conn.close()

            app.init_db()
            conn = app.db()
            contract = conn.execute(
                "SELECT categoria_cambio,previsao_embarque_dias,status,valor_reais,saldo_zerado_manual "
                "FROM contratos WHERE id=1"
            ).fetchone()
            self.assertEqual(contract["categoria_cambio"], app.CATEGORIA_CAMBIO_FINANCEIRO)
            self.assertIsNone(contract["previsao_embarque_dias"])
            self.assertEqual(contract["status"], app.STATUS_CONCLUIDO)
            self.assertEqual(contract["valor_reais"], 375)
            self.assertEqual(contract["saldo_zerado_manual"], 0)
            self.assertIsNone(conn.execute(
                "SELECT previsao_embarque_dias FROM fechamentos WHERE id=1"
            ).fetchone()[0])
            self.assertEqual(app.contract_summary(conn, 1)["saldo"], app.Decimal("0"))
            conn.close()
        finally:
            app.DB = previous_db
            legacy_path.unlink(missing_ok=True)

    def test_cambio_category_migration_is_idempotent_and_preserves_legacy_fields(self):
        previous_db = app.DB
        legacy_path = Path(tempfile.mktemp(prefix="duecontrol_category_migration_", suffix=".db"))
        try:
            conn = sqlite3.connect(legacy_path)
            conn.execute("""
                CREATE TABLE fechamentos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cliente_id INTEGER NOT NULL,
                    banco_credito_id INTEGER NOT NULL,
                    moeda TEXT NOT NULL,
                    data_fechamento TEXT NOT NULL,
                    data_liquidacao TEXT NOT NULL,
                    banco_liquidacao_id INTEGER NOT NULL,
                    taxa_cambio REAL,
                    valor_brl REAL,
                    contrato_id INTEGER UNIQUE,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                INSERT INTO fechamentos
                    (cliente_id,banco_credito_id,moeda,data_fechamento,data_liquidacao,
                     banco_liquidacao_id,taxa_cambio,valor_brl)
                VALUES (1,2,'USD','2026-08-01','2026-08-02',3,5.1234,512.34)
            """)
            conn.execute("PRAGMA user_version=0")
            conn.commit()
            conn.close()

            app.DB = legacy_path
            app.init_db()
            conn = app.db()
            first = conn.execute("SELECT * FROM fechamentos").fetchone()
            first_snapshot = dict(first)
            self.assertEqual(first["categoria_cambio"], app.CATEGORIA_CAMBIO_EXPORTACAO)
            self.assertEqual(first["previsao_embarque_dias"], app.PREVISAO_EMBARQUE_LEGADO_DIAS)
            self.assertEqual(first["valor_brl"], 512.34)
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], app.INVOICE_SCHEMA_VERSION)
            conn.close()

            app.init_db()
            conn = app.db()
            second = conn.execute("SELECT * FROM fechamentos").fetchone()
            second_snapshot = dict(second)
            self.assertEqual(second_snapshot, first_snapshot)
            self.assertEqual(second["previsao_embarque_dias"], app.PREVISAO_EMBARQUE_LEGADO_DIAS)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM fechamentos").fetchone()[0], 1)
            conn.close()
        finally:
            app.DB = previous_db
            legacy_path.unlink(missing_ok=True)

    def test_central_closing_category_is_required_and_limited_to_two_values(self):
        invoice_id = self._create_invoice("INV-CATEGORY-VALIDATION", "100,00")
        self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "20/08/2026", "valor_moeda": "100,00",
        })
        base = {
            "selected_ids": [str(invoice_id)], "data_fechamento": "2026-08-21",
            "data_liquidacao": "2026-08-25", "taxa_cambio": "5,0000",
            "banco_liquidacao_id": "1",
        }
        missing = self.client.post("/invoices/fechamentos", data=base, follow_redirects=True)
        self.assertIn("Categoria Câmbio é obrigatória", missing.get_data(as_text=True))
        invalid = self.client.post(
            "/invoices/fechamentos",
            data={**base, "categoria_cambio": "Câmbio Turismo"},
            follow_redirects=True,
        )
        self.assertIn("Categoria Câmbio válida", invalid.get_data(as_text=True))
        page = self.client.get("/invoices/fechamentos")
        html = page.get_data(as_text=True)
        self.assertIn('name="categoria_cambio"', html)
        self.assertIn('name="categoria_cambio" required', html)
        self.assertEqual(html.count('value="Câmbio Exportação"'), 1)
        self.assertEqual(html.count('value="Câmbio Financeiro"'), 1)
        conn = app.db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM fechamentos").fetchone()[0], 0)
        conn.close()

    def test_financial_central_closing_is_concluded_zero_and_excluded_from_due_flow(self):
        invoice_id = self._create_invoice("INV-FINANCIAL-CATEGORY", "100,00")
        self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "20/08/2026", "valor_moeda": "100,00",
        })
        header = self._register_central_closing(
            invoice_id, "100,00", "2026-08-21", "2026-08-25",
            numero_contrato="FINANCIAL-001",
            categoria_cambio=app.CATEGORIA_CAMBIO_FINANCEIRO,
        )
        conn = app.db()
        contract = conn.execute("SELECT * FROM contratos WHERE id=?", (header["contrato_id"],)).fetchone()
        self.assertEqual(header["categoria_cambio"], app.CATEGORIA_CAMBIO_FINANCEIRO)
        self.assertIsNone(header["previsao_embarque_dias"])
        self.assertEqual(contract["categoria_cambio"], app.CATEGORIA_CAMBIO_FINANCEIRO)
        self.assertIsNone(contract["previsao_embarque_dias"])
        self.assertEqual(contract["status"], app.STATUS_CONCLUIDO)
        self.assertEqual(contract["saldo_zerado_manual"], 0)
        summary = app.contract_summary(conn, header["contrato_id"])
        self.assertEqual(summary["categoria_cambio"], app.CATEGORIA_CAMBIO_FINANCEIRO)
        self.assertEqual(summary["saldo"], app.Decimal("0"))
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM due_contratos").fetchone()[0], 0)
        conn.close()

        balance = self.client.get(f"/contratos/{header['contrato_id']}/saldo")
        self.assertEqual(balance.status_code, 200)
        payload = balance.get_json()
        self.assertEqual(payload["status"], app.STATUS_CONCLUIDO)
        self.assertEqual(payload["saldo_disponivel"], 0.0)
        self.assertEqual(payload["categoria_cambio"], app.CATEGORIA_CAMBIO_FINANCEIRO)
        self.assertIn(app.STATUS_CONCLUIDO, self.client.get("/").get_data(as_text=True))
        self.assertIn("FINANCIAL-001", self.client.get("/contratos").get_data(as_text=True))
        self.assertIn(app.CATEGORIA_CAMBIO_FINANCEIRO, self.client.get(
            f"/invoices/fechamentos/{header['id']}"
        ).get_data(as_text=True))
        due_page = self.client.get("/due/1")
        self.assertNotIn("FINANCIAL-001", due_page.get_data(as_text=True))
        rejected = self.client.post("/due/1/vincular", data={
            "contrato_id": str(header["contrato_id"]), "valor_vinculado": "10,00",
        }, follow_redirects=True)
        self.assertIn("Câmbio Financeiro", rejected.get_data(as_text=True))
        conn = app.db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM due_contratos").fetchone()[0], 0)
        conn.close()

    def test_financial_category_toggle_recalculates_without_manual_zeroing(self):
        invoice_id = self._create_invoice("INV-FINANCIAL-TOGGLE", "100,00")
        self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "20/08/2026", "valor_moeda": "100,00",
        })
        header = self._register_central_closing(
            invoice_id, "100,00", "2026-08-21", "2026-08-25",
            numero_contrato="TOGGLE-001",
            categoria_cambio=app.CATEGORIA_CAMBIO_FINANCEIRO,
        )
        response = self.client.post(f"/invoices/fechamentos/{header['id']}/editar", data={
            "categoria_cambio": app.CATEGORIA_CAMBIO_EXPORTACAO,
            "data_fechamento": "2026-08-22", "data_liquidacao": "2026-08-26",
            "taxa_cambio": "5,1000", "banco_liquidacao_id": "1",
            "previsao_embarque_dias": "120",
        })
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        contract = conn.execute("SELECT status,saldo_zerado_manual FROM contratos WHERE id=?", (header["contrato_id"],)).fetchone()
        self.assertEqual(contract["status"], app.STATUS_PENDENTE)
        self.assertEqual(contract["saldo_zerado_manual"], 0)
        conn.close()

        response = self.client.post(f"/invoices/fechamentos/{header['id']}/editar", data={
            "categoria_cambio": app.CATEGORIA_CAMBIO_FINANCEIRO,
            "data_fechamento": "2026-08-23", "data_liquidacao": "2026-08-27",
            "taxa_cambio": "5,2000", "banco_liquidacao_id": "1",
            "previsao_embarque_dias": "1.5",
        })
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        contract = conn.execute("SELECT status,saldo_zerado_manual FROM contratos WHERE id=?", (header["contrato_id"],)).fetchone()
        self.assertEqual(contract["status"], app.STATUS_CONCLUIDO)
        self.assertEqual(contract["saldo_zerado_manual"], 0)
        closing = conn.execute(
            "SELECT categoria_cambio,previsao_embarque_dias FROM fechamentos WHERE id=?",
            (header["id"],),
        ).fetchone()
        self.assertEqual(closing["categoria_cambio"], app.CATEGORIA_CAMBIO_FINANCEIRO)
        self.assertIsNone(closing["previsao_embarque_dias"])
        conn.close()

    def test_financial_closing_without_contract_waits_until_contract_is_added(self):
        invoice_id = self._create_invoice("INV-FINANCIAL-WAITING", "100,00")
        self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "20/08/2026", "valor_moeda": "100,00",
        })
        header = self._register_central_closing(
            invoice_id, "100,00", "2026-08-21", "2026-08-25",
            categoria_cambio=app.CATEGORIA_CAMBIO_FINANCEIRO,
        )
        conn = app.db()
        self.assertIsNone(header["contrato_id"])
        self.assertEqual(
            conn.execute("SELECT status FROM invoices WHERE id=?", (invoice_id,)).fetchone()[0],
            app.INVOICE_STATUS_AGUARDANDO_CONTRATO,
        )
        conn.close()

        response = self.client.post(f"/invoices/fechamentos/{header['id']}/editar", data={
            "categoria_cambio": app.CATEGORIA_CAMBIO_FINANCEIRO,
            "data_fechamento": "2026-08-22", "data_liquidacao": "2026-08-26",
            "taxa_cambio": "5,1000", "banco_liquidacao_id": "1",
            "numero_novo_contrato": "FINANCIAL-WAITING-001",
        })
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        contract = conn.execute(
            "SELECT id,status,saldo_zerado_manual FROM contratos WHERE numero_contrato=?",
            ("FINANCIAL-WAITING-001",),
        ).fetchone()
        self.assertEqual(contract["status"], app.STATUS_CONCLUIDO)
        self.assertEqual(contract["saldo_zerado_manual"], 0)
        self.assertEqual(
            conn.execute("SELECT status FROM invoices WHERE id=?", (invoice_id,)).fetchone()[0],
            app.INVOICE_STATUS_LIQUIDADA,
        )
        self.assertEqual(app.contract_summary(conn, contract["id"])["saldo"], app.Decimal("0"))
        conn.close()

    def test_legacy_contract_category_can_be_edited_and_recalculated(self):
        invoice_id = self._create_invoice("INV-LEGACY-CATEGORY", "100,00")
        self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "20/08/2026", "valor_moeda": "100,00",
        })
        response = self.client.post(f"/invoice/{invoice_id}/cambio", data={
            "numero_contrato": "LEGACY-CATEGORY-001", "banco_liquidacao_id": "1",
            "data_fechamento": "20/08/2026", "taxa_cambio": "5,1000",
            "valor_alocado": "100,00",
        })
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        contract = conn.execute(
            "SELECT * FROM contratos WHERE numero_contrato=?", ("LEGACY-CATEGORY-001",)
        ).fetchone()
        self.assertEqual(contract["categoria_cambio"], app.CATEGORIA_CAMBIO_EXPORTACAO)
        contract_id = contract["id"]
        conn.close()

        edit_page = self.client.get(f"/contrato/{contract_id}/editar")
        self.assertEqual(edit_page.status_code, 200)
        edit_html = edit_page.get_data(as_text=True)
        self.assertIn('name="categoria_cambio" required', edit_html)
        self.assertEqual(edit_html.count('value="Câmbio Exportação"'), 1)
        self.assertEqual(edit_html.count('value="Câmbio Financeiro"'), 1)

        response = self.client.post(f"/contrato/{contract_id}/editar", data={
            "derived_contract_form": "1", "numero_contrato": "LEGACY-CATEGORY-001",
            "categoria_cambio": app.CATEGORIA_CAMBIO_FINANCEIRO,
            "banco_liquidacao_id": "1", "data_fechamento": "20/08/2026",
            "data_liquidacao": "25/08/2026", "taxa_cambio": "5,1000",
        })
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        contract = conn.execute(
            "SELECT categoria_cambio,status,saldo_zerado_manual FROM contratos WHERE id=?",
            (contract_id,),
        ).fetchone()
        self.assertEqual(contract["categoria_cambio"], app.CATEGORIA_CAMBIO_FINANCEIRO)
        self.assertEqual(contract["status"], app.STATUS_CONCLUIDO)
        self.assertEqual(app.contract_summary(conn, contract_id)["saldo"], app.Decimal("0"))
        self.assertEqual(contract["saldo_zerado_manual"], 0)
        conn.close()
        detail_html = self.client.get(f"/contrato/{contract_id}").get_data(as_text=True)
        self.assertIn(app.CATEGORIA_CAMBIO_FINANCEIRO, detail_html)
        self.assertNotIn("LEGACY-CATEGORY-001", self.client.get("/due/1").get_data(as_text=True))

        response = self.client.post(f"/contrato/{contract_id}/editar", data={
            "derived_contract_form": "1", "numero_contrato": "LEGACY-CATEGORY-001",
            "categoria_cambio": app.CATEGORIA_CAMBIO_EXPORTACAO,
            "banco_liquidacao_id": "1", "data_fechamento": "20/08/2026",
            "data_liquidacao": "25/08/2026", "taxa_cambio": "5,1000",
        })
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        summary = app.contract_summary(conn, contract_id)
        self.assertEqual(summary["categoria_cambio"], app.CATEGORIA_CAMBIO_EXPORTACAO)
        self.assertEqual(summary["status"], app.STATUS_PENDENTE)
        self.assertEqual(summary["saldo"], app.Decimal("100"))
        conn.close()

    def test_legacy_closing_link_is_client_scoped_and_inherits_contract_category(self):
        contract_invoice = self._create_invoice("INV-LEGACY-LINK-CONTRACT", "100,00")
        self.client.post(f"/invoice/{contract_invoice}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "20/08/2026", "valor_moeda": "100,00",
        })
        self.client.post(f"/invoice/{contract_invoice}/cambio", data={
            "numero_contrato": "LEGACY-LINK-001", "valor_alocado": "100,00",
        })
        closing_invoice = self._create_invoice("INV-LEGACY-LINK-CLOSING", "50,00")
        self.client.post(f"/invoice/{closing_invoice}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "20/08/2026", "valor_moeda": "50,00",
        })
        self.client.post(f"/invoice/{closing_invoice}/fechamentos", data={
            "valor_moeda": "50,00", "data_fechamento": "21/08/2026",
        })
        conn = app.db()
        contract_id = conn.execute(
            "SELECT id FROM contratos WHERE numero_contrato=?", ("LEGACY-LINK-001",)
        ).fetchone()[0]
        pending = conn.execute(
            "SELECT id FROM fechamentos_cambio WHERE invoice_id=?", (closing_invoice,)
        ).fetchone()
        conn.close()
        detail_html = self.client.get(f"/contrato/{contract_id}").get_data(as_text=True)
        self.assertIn("INV-LEGACY-LINK-CLOSING", detail_html)

        response = self.client.post(f"/contrato/{contract_id}/fechamentos/vincular", data={
            "fechamento_id": str(pending["id"]),
        })
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        linked = conn.execute(
            "SELECT contrato_id FROM fechamentos_cambio WHERE id=?", (pending["id"],)
        ).fetchone()
        self.assertEqual(linked["contrato_id"], contract_id)
        self.assertEqual(
            app.contract_summary(conn, contract_id)["valor_moeda"], app.Decimal("150")
        )
        conn.close()

        conn = app.db()
        conn.execute("INSERT INTO clientes(nome,pais) VALUES (?,?)", ("Cliente Outro", "BR"))
        other_client_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        conn.close()
        other_invoice = self._create_invoice(
            "INV-LEGACY-LINK-OTHER", "20,00", client_id=other_client_id
        )
        self.client.post(f"/invoice/{other_invoice}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "20/08/2026", "valor_moeda": "20,00",
        })
        self.client.post(f"/invoice/{other_invoice}/fechamentos", data={
            "valor_moeda": "20,00", "data_fechamento": "21/08/2026",
        })
        conn = app.db()
        other_pending = conn.execute(
            "SELECT id FROM fechamentos_cambio WHERE invoice_id=?", (other_invoice,)
        ).fetchone()
        conn.close()
        detail_html = self.client.get(f"/contrato/{contract_id}").get_data(as_text=True)
        self.assertNotIn("INV-LEGACY-LINK-OTHER", detail_html)
        response = self.client.post(
            f"/contrato/{contract_id}/fechamentos/vincular",
            data={"fechamento_id": str(other_pending["id"])}, follow_redirects=True,
        )
        self.assertIn("mesmo Cliente", response.get_data(as_text=True))
        conn = app.db()
        self.assertIsNone(conn.execute(
            "SELECT contrato_id FROM fechamentos_cambio WHERE id=?", (other_pending["id"],)
        ).fetchone()[0])
        conn.close()

    def test_legacy_closing_remains_outside_central_registered_listing(self):
        invoice_id = self._create_invoice("INV-LEGACY-REPORT", "100,00")
        conn = app.db()
        conn.execute("""
            INSERT INTO fechamentos_cambio
                (invoice_id, moeda, valor_moeda, data_fechamento, observacao)
            VALUES (?, 'USD', 100, '2026-08-10', 'Fechamento legado')
        """, (invoice_id,))
        conn.commit()
        conn.close()

        listing = self.client.get("/invoices/fechamentos")
        self.assertEqual(listing.status_code, 200)
        listing_html = listing.get_data(as_text=True)
        self.assertIn("Nenhum Fechamento centralizado registrado", listing_html)

        report = self.client.get("/invoices/fechamentos/relatorio")
        self.assertEqual(report.status_code, 200)
        self.assertIn("Nenhum Fechamento encontrado para os filtros informados", report.get_data(as_text=True))

    def test_due_contract_link_requires_same_company_in_options_and_posts(self):
        conn = app.db()
        conn.execute(
            "INSERT INTO empresas (razao_social, cnpj, apelido) VALUES (?,?,?)",
            ("Coplaser Teste", "05928246000141", "COPLASA"),
        )
        conn.execute(
            "INSERT INTO dues (numero_due, chave_acesso, cnpj, moeda, valor_original, status) "
            "VALUES (?,?,?,?,?,?)",
            ("DUE-COMPANY-CEM", "22345678901234", "45765914000181", "USD", 100, app.STATUS_PENDENTE),
        )
        due_cem_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO dues (numero_due, chave_acesso, cnpj, moeda, valor_original, status) "
            "VALUES (?,?,?,?,?,?)",
            ("DUE-COMPANY-COPLASA", "32345678901234", "05928246000141", "USD", 100, app.STATUS_PENDENTE),
        )
        due_coplasa_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO contratos (numero_contrato, cnpj, moeda, valor_moeda, status) "
            "VALUES (?,?,?,?,?)",
            ("CONTRACT-COMPANY-CEM", "45765914000181", "USD", 100, app.STATUS_PENDENTE),
        )
        contract_cem_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO contratos (numero_contrato, cnpj, moeda, valor_moeda, status) "
            "VALUES (?,?,?,?,?)",
            ("CONTRACT-COMPANY-COPLASA", "05928246000141", "USD", 100, app.STATUS_PENDENTE),
        )
        contract_coplasa_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        conn.close()

        contract_page = self.client.get(f"/contrato/{contract_cem_id}")
        contract_html = contract_page.get_data(as_text=True)
        self.assertIn("DUE-COMPANY-CEM", contract_html)
        self.assertNotIn("DUE-COMPANY-COPLASA", contract_html)

        due_page = self.client.get(f"/due/{due_cem_id}")
        due_html = due_page.get_data(as_text=True)
        self.assertIn("CONTRACT-COMPANY-CEM", due_html)
        self.assertNotIn("CONTRACT-COMPANY-COPLASA", due_html)

        allowed = self.client.post(
            f"/contrato/{contract_cem_id}/due",
            data={"due_id": str(due_cem_id), "valor_vinculado": "10,00"},
        )
        self.assertEqual(allowed.status_code, 302)

        rejected_from_contract = self.client.post(
            f"/contrato/{contract_cem_id}/due",
            data={"due_id": str(due_coplasa_id), "valor_vinculado": "10,00"},
            follow_redirects=True,
        )
        self.assertIn("mesma empresa", rejected_from_contract.get_data(as_text=True))

        rejected_from_due = self.client.post(
            f"/due/{due_cem_id}/vincular",
            data={"contrato_id": str(contract_coplasa_id), "valor_vinculado": "10,00"},
            follow_redirects=True,
        )
        self.assertIn("mesma empresa", rejected_from_due.get_data(as_text=True))

        conn = app.db()
        self.assertEqual(
            conn.execute(
                "SELECT COUNT(*) FROM due_contratos WHERE due_id=? AND contrato_id=?",
                (due_cem_id, contract_coplasa_id),
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            conn.execute(
                "SELECT COUNT(*) FROM due_movimentacoes WHERE due_id=? AND contrato_id=? AND tipo='VINCULACAO'",
                (due_cem_id, contract_coplasa_id),
            ).fetchone()[0],
            0,
        )
        conn.close()


if __name__ == "__main__":
    unittest.main()
