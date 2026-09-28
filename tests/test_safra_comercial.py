import tempfile
import unittest
from pathlib import Path

import app


class SafraComercialTests(unittest.TestCase):
    def setUp(self):
        self.previous_db = app.DB
        self.db_path = Path(tempfile.mktemp(prefix="duecontrol_safra_test_", suffix=".db"))
        app.DB = self.db_path
        app.init_db()
        conn = app.db()
        conn.execute(
            "INSERT INTO empresas (razao_social, cnpj, apelido) VALUES (?,?,?)",
            ("Usina Teste", "12345678000100", "Usina"),
        )
        conn.execute(
            "INSERT INTO competencias (empresa_id, descricao, data_inicial, data_final) VALUES (?,?,?,?)",
            (1, "Safra 26/27", "2026-04-01", "2027-03-31"),
        )
        conn.execute("INSERT INTO clientes (nome, pais) VALUES (?,?)", ("Comprador", "BR"))
        conn.commit()
        conn.close()
        self.client = app.app.test_client()

    def tearDown(self):
        app.DB = self.previous_db
        self.db_path.unlink(missing_ok=True)

    def _create_contract_item(self, volume="100"):
        response = self.client.post("/safra/contratos/novo", data={
            "numero_contrato": "AC-001",
            "empresa_id": "1",
            "competencia_id": "1",
            "cliente_id": "1",
            "mercado": "EXTERNO",
            "data_contrato": "2026-04-10",
        })
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        contract_id = conn.execute("SELECT id FROM contratos_comerciais").fetchone()[0]
        conn.close()
        response = self.client.post(f"/safra/contratos/{contract_id}/itens/novo", data={
            "mes_referencia": "2026-04",
            "tranche": "T1",
            "produto": "AÇÚCAR",
            "unidade": "MT",
            "volume_contratado": volume,
            "qualidade": "ESALQ",
            "moeda": "USD",
            "incoterm": "FOB",
            "preco_contratado": "450",
        })
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        item_id = conn.execute("SELECT id FROM contrato_comercial_itens").fetchone()[0]
        conn.close()
        return contract_id, item_id

    def test_schema_reuses_competencia_and_creates_only_commercial_tables(self):
        conn = app.db()
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], app.INVOICE_SCHEMA_VERSION)
        tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        self.assertNotIn("safras", tables)
        self.assertTrue({
            "producoes", "contratos_comerciais", "contrato_comercial_itens",
            "fixacoes", "auditoria_eventos",
        }.issubset(tables))
        conn.close()

    def test_production_validates_competencia_and_stores_monthly_rows(self):
        response = self.client.post("/safra/producao/novo", data={
            "empresa_id": "1", "competencia_id": "1", "mes_referencia": "2026-04",
            "tranche": "T1", "produto": "AÇÚCAR", "unidade": "MT",
            "quantidade_estimada": "100", "quantidade_realizada": "80",
        })
        self.assertEqual(response.status_code, 302)
        response = self.client.post("/safra/producao/novo", data={
            "empresa_id": "1", "competencia_id": "1", "mes_referencia": "2026-03",
            "tranche": "T1", "produto": "AÇÚCAR", "unidade": "MT",
            "quantidade_estimada": "100",
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("fora do período", response.get_data(as_text=True))
        conn = app.db()
        row = conn.execute("SELECT mes_referencia, quantidade_estimada, quantidade_realizada FROM producoes").fetchone()
        self.assertEqual(tuple(row), ("2026-04", 100.0, 80.0))
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM auditoria_eventos WHERE entidade='PRODUCAO'").fetchone()[0], 1)
        conn.close()

    def test_contract_fixation_totals_and_overfixation_are_controlled(self):
        contract_id, item_id = self._create_contract_item("100")
        response = self.client.post(
            f"/safra/contratos/{contract_id}/itens/{item_id}/fixacoes/nova",
            data={"data_fixacao": "2026-04-15", "volume_fixado": "60", "premio": "-3", "tela": "K6"},
        )
        self.assertEqual(response.status_code, 302)
        response = self.client.post(
            f"/safra/contratos/{contract_id}/itens/{item_id}/fixacoes/nova",
            data={"data_fixacao": "2026-04-16", "volume_fixado": "20"},
        )
        self.assertEqual(response.status_code, 302)
        response = self.client.post(
            f"/safra/contratos/{contract_id}/itens/{item_id}/fixacoes/nova",
            data={"data_fixacao": "2026-04-15", "volume_fixado": "50"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("excede o volume a fixar", response.get_data(as_text=True))
        conn = app.db()
        totals = conn.execute("""
            SELECT i.volume_contratado, COALESCE(SUM(f.volume_fixado),0)
            FROM contrato_comercial_itens i
            LEFT JOIN fixacoes f ON f.contrato_item_id=i.id AND f.status='ATIVA'
            WHERE i.id=? GROUP BY i.id
        """, (item_id,)).fetchone()
        self.assertEqual(tuple(totals), (100.0, 80.0))
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM auditoria_eventos").fetchone()[0], 4)
        conn.close()

    def test_incoterm_rejects_fobizacao_and_cancellation_updates_derived_balance(self):
        contract_id, item_id = self._create_contract_item("100")
        response = self.client.post(f"/safra/contratos/{contract_id}/itens/{item_id}/editar", data={
            "mes_referencia": "2026-04", "tranche": "T1", "produto": "AÇÚCAR", "unidade": "MT",
            "volume_contratado": "100", "moeda": "USD", "incoterm": "FOBIZAÇÃO",
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("não é Incoterm", response.get_data(as_text=True))
        response = self.client.post(
            f"/safra/contratos/{contract_id}/itens/{item_id}/fixacoes/nova",
            data={"data_fixacao": "2026-04-15", "volume_fixado": "60"},
        )
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        fixation_id = conn.execute("SELECT id FROM fixacoes").fetchone()[0]
        conn.close()
        response = self.client.post(
            f"/safra/contratos/{contract_id}/itens/{item_id}/fixacoes/{fixation_id}/cancelar",
        )
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        self.assertEqual(conn.execute("SELECT status FROM fixacoes WHERE id=?", (fixation_id,)).fetchone()[0], "CANCELADA")
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM auditoria_eventos WHERE acao='CANCELADO'").fetchone()[0], 1)
        conn.close()


if __name__ == "__main__":
    unittest.main()
