import sqlite3
import tempfile
import unittest
from pathlib import Path

import app


class InvoiceTransferenciaTests(unittest.TestCase):
    def setUp(self):
        self.previous_db = app.DB
        self.db_path = Path(tempfile.mktemp(prefix="duecontrol_transfer_test_", suffix=".db"))
        app.DB = self.db_path
        app.init_db()
        conn = app.db()
        conn.executemany(
            "INSERT INTO empresas (razao_social, cnpj, apelido) VALUES (?,?,?)",
            [
                ("Empresa Origem", "11111111000101", "Origem"),
                ("Empresa Destino", "22222222000102", "Destino"),
            ],
        )
        conn.executemany(
            "INSERT INTO competencias (empresa_id, descricao, data_inicial, data_final) VALUES (?,?,?,?)",
            [
                (1, "Agosto/2026", "2026-08-01", "2026-08-31"),
                (2, "Agosto/2026", "2026-08-01", "2026-08-31"),
            ],
        )
        conn.execute("INSERT INTO clientes (nome, pais) VALUES (?,?)", ("Cliente", "US"))
        conn.execute("INSERT INTO contrapartes (nome) VALUES (?)", ("Banco",))
        conn.commit()
        conn.close()
        self.client = app.app.test_client()

    def tearDown(self):
        app.DB = self.previous_db
        self.db_path.unlink(missing_ok=True)

    def _invoice(self, number, value=1000, status=app.INVOICE_STATUS_AGUARDANDO_RECEBIMENTO):
        conn = app.db()
        cursor = conn.execute("""
            INSERT INTO invoices
                (empresa_id, numero_invoice, tipo_documento, competencia_id, cliente_id,
                 data_emissao, moeda, valor_moeda, status, status_manual, desdobramento_habilitado)
            VALUES (1, ?, 'COMMERCIAL_INVOICE', 1, 1, '2026-08-01', 'USD', ?, ?, 0, 1)
        """, (number, value, status))
        conn.commit()
        conn.close()
        return cursor.lastrowid

    def _transfer(self, invoice_id, amount, competence=2):
        return self.client.post("/invoices/transferencias", data={
            "selected_ids": str(invoice_id),
            "empresa_destino_id": "2",
            "data_transferencia": "20/08/2026",
            f"valor_transferencia_{invoice_id}": str(amount).replace(".", ","),
            f"competencia_destino_{invoice_id}": str(competence),
        })

    def test_schema_is_idempotent_and_total_transfer_updates_current_invoice(self):
        app.init_db()
        conn = app.db()
        invoice_id = self._invoice("TR-TOTAL")
        response = self._transfer(invoice_id, 1000)
        self.assertEqual(response.status_code, 302)
        invoice = conn.execute(
            "SELECT empresa_id, competencia_id, valor_moeda FROM invoices WHERE id=?",
            (invoice_id,),
        ).fetchone()
        self.assertEqual((invoice["empresa_id"], invoice["competencia_id"]), (2, 2))
        self.assertEqual(app.Decimal(str(invoice["valor_moeda"])), app.Decimal("1000"))
        item = conn.execute("SELECT * FROM transferencias_empresas_itens").fetchone()
        self.assertEqual(item["invoice_origem_id"], invoice_id)
        self.assertEqual(item["invoice_destino_id"], invoice_id)
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 27)
        conn.close()

    def test_partial_transfer_preserves_sum_and_estorno_restores_invoice(self):
        invoice_id = self._invoice("TR-PARCIAL")
        response = self._transfer(invoice_id, 400)
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        child_id = conn.execute(
            "SELECT invoice_destino_id FROM transferencias_empresas_itens WHERE invoice_origem_id=?",
            (invoice_id,),
        ).fetchone()[0]
        values = conn.execute(
            "SELECT empresa_id, valor_moeda FROM invoices WHERE id IN (?,?) ORDER BY id",
            (invoice_id, child_id),
        ).fetchall()
        self.assertEqual([row["empresa_id"] for row in values], [1, 2])
        self.assertEqual(sum(app.Decimal(str(row["valor_moeda"])) for row in values), app.Decimal("1000"))
        transfer_id = conn.execute("SELECT id FROM transferencias_empresas").fetchone()[0]
        conn.close()

        response = self.client.post(
            f"/invoices/transferencias/{transfer_id}/estornar",
            data={"motivo_estorno": "revisão"}, follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        conn = app.db()
        self.assertIsNone(conn.execute("SELECT id FROM invoices WHERE id=?", (child_id,)).fetchone())
        source = conn.execute(
            "SELECT empresa_id, competencia_id, valor_moeda FROM invoices WHERE id=?", (invoice_id,)
        ).fetchone()
        self.assertEqual((source["empresa_id"], source["competencia_id"]), (1, 1))
        self.assertEqual(app.Decimal(str(source["valor_moeda"])), app.Decimal("1000"))
        self.assertEqual(conn.execute("SELECT status FROM transferencias_empresas WHERE id=?", (transfer_id,)).fetchone()[0], "ESTORNADA")
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM auditoria_eventos WHERE entidade='TRANSFERENCIA_EMPRESAS' AND entidade_id=?", (transfer_id,)).fetchone()[0], 2)
        conn.close()

    def test_partial_received_transfer_repartitions_allocation_without_duplication(self):
        invoice_id = self._invoice("TR-RECEBIDA")
        response = self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "10/08/2026", "valor_moeda": "1000,00",
        })
        self.assertEqual(response.status_code, 302)
        response = self._transfer(invoice_id, 300)
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        child_id = conn.execute(
            "SELECT invoice_destino_id FROM transferencias_empresas_itens WHERE invoice_origem_id=?",
            (invoice_id,),
        ).fetchone()[0]
        allocations = conn.execute(
            "SELECT invoice_id, valor_moeda FROM invoice_recebimento_alocacoes ORDER BY invoice_id"
        ).fetchall()
        self.assertEqual(len(allocations), 2)
        self.assertEqual(sum(app.Decimal(str(row["valor_moeda"])) for row in allocations), app.Decimal("1000"))
        self.assertEqual(app.Decimal(str(next(row["valor_moeda"] for row in allocations if row["invoice_id"] == child_id))), app.Decimal("300"))
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM recebimentos_invoice").fetchone()[0], 1)
        transfer_id = conn.execute("SELECT id FROM transferencias_empresas").fetchone()[0]
        conn.close()
        self.client.post(f"/invoices/transferencias/{transfer_id}/estornar")
        conn = app.db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM invoice_recebimento_alocacoes WHERE invoice_id=?", (invoice_id,)).fetchone()[0], 1)
        self.assertEqual(app.Decimal(str(conn.execute("SELECT valor_moeda FROM invoice_recebimento_alocacoes WHERE invoice_id=?", (invoice_id,)).fetchone()[0])), app.Decimal("1000"))
        conn.close()

    def test_total_transfer_of_received_invoice_keeps_same_invoice_identity(self):
        invoice_id = self._invoice("TR-TOTAL-RECEBIDA")
        self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
            "banco_credito_id": "1", "data_credito": "10/08/2026", "valor_moeda": "1000,00",
        })
        response = self._transfer(invoice_id, 1000)
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT empresa_id FROM invoices WHERE id=?", (invoice_id,)).fetchone()[0], 2)
        self.assertEqual(conn.execute("SELECT tipo_transferencia FROM transferencias_empresas_itens").fetchone()[0], "TOTAL")
        conn.close()

    def test_estorno_is_blocked_by_financial_liquidation(self):
        invoice_id = self._invoice("TR-BLOQUEIO-LIQUIDACAO")
        self._transfer(invoice_id, 300)
        conn = app.db()
        child_id = conn.execute("SELECT invoice_destino_id FROM transferencias_empresas_itens").fetchone()[0]
        operation = conn.execute("""
            INSERT INTO operacoes_financeiras
                (cliente_id, empresa_id, banco_credito_id, moeda, categoria_operacao,
                 valor_contratado_moeda, valor_contratado_brl, status)
            VALUES (1, 2, 1, 'USD', 'ACC', 300, 0, 'ABERTA')
        """).lastrowid
        conn.execute("""
            INSERT INTO operacoes_financeiras_invoices
                (operacao_financeira_id, invoice_id, moeda, valor_moeda)
            VALUES (?,?,?,?)
        """, (operation, child_id, "USD", 100))
        conn.execute("""
            INSERT INTO operacoes_financeiras_liquidacoes
                (operacao_financeira_id, data_liquidacao, taxa_liquidacao,
                 valor_liquidado_moeda, valor_liquidado_brl)
            VALUES (?,?,?,?,?)
        """, (operation, "2026-08-25", 5, 100, 500))
        conn.commit()
        transfer_id = conn.execute("SELECT id FROM transferencias_empresas").fetchone()[0]
        conn.close()
        response = self.client.post(f"/invoices/transferencias/{transfer_id}/estornar", follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("liquidação financeira vinculada", response.get_data(as_text=True))

    def test_rejects_invalid_status_amount_competence_and_destination_conflict(self):
        liquidated = self._invoice("TR-LIQUIDADA", status=app.INVOICE_STATUS_LIQUIDADA)
        conn = app.db()
        conn.execute("UPDATE invoices SET status_manual=1 WHERE id=?", (liquidated,))
        conn.commit()
        conn.close()
        response = self._transfer(liquidated, 100)
        self.assertEqual(response.status_code, 302)
        invoice_id = self._invoice("TR-VALIDACAO")
        response = self._transfer(invoice_id, 1001)
        self.assertEqual(response.status_code, 302)
        invoice_id = self._invoice("TR-COMPETENCIA")
        response = self._transfer(invoice_id, 100, competence=1)
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        conn.execute("""
            INSERT INTO invoices
                (empresa_id, numero_invoice, tipo_documento, competencia_id, cliente_id,
                 data_emissao, moeda, valor_moeda, status, desdobramento_habilitado)
            VALUES (2, 'TR-CONFLITO', 'COMMERCIAL_INVOICE', 2, 1, '2026-08-01', 'USD', 20, ?, 1)
        """, (app.INVOICE_STATUS_AGUARDANDO_RECEBIMENTO,))
        conn.commit()
        conn.close()
        invoice_id = self._invoice("TR-CONFLITO", value=100)
        response = self._transfer(invoice_id, 20)
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM transferencias_empresas").fetchone()[0], 0)
        conn.close()

    def test_active_transfer_blocks_direct_invoice_delete_and_utf8_renders(self):
        invoice_id = self._invoice("TR-UI")
        self._transfer(invoice_id, 250)
        conn = app.db()
        child_id = conn.execute("SELECT invoice_destino_id FROM transferencias_empresas_itens").fetchone()[0]
        conn.close()
        self.assertEqual(self.client.get("/invoices").status_code, 200)
        self.assertEqual(self.client.get(f"/invoice/{child_id}").status_code, 200)
        response = self.client.get("/invoices/transferencias")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Transferências entre empresas", body)
        self.assertIn("⇄", body)
        response = self.client.post(f"/invoice/{invoice_id}/excluir", follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("transferência entre empresas ativa", response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
