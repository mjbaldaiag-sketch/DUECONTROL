import sqlite3
import tempfile
import unittest
from pathlib import Path

import app


class ConfiguracoesPadraoTests(unittest.TestCase):
    def setUp(self):
        self.previous_db = app.DB
        self.db_path = Path(tempfile.mktemp(prefix="duecontrol_padrao_test_", suffix=".db"))
        app.DB = self.db_path
        app.init_db()
        conn = app.db()
        conn.executemany(
            "INSERT INTO empresas (razao_social, cnpj, apelido, prioridade) VALUES (?,?,?,?)",
            [
                ("Empresa Um", "45000000000100", "Empresa Um", 1),
                ("Empresa Dois", "45000000000200", "Empresa Dois", 2),
            ],
        )
        conn.executemany(
            "INSERT INTO competencias (empresa_id, descricao, data_inicial, data_final) VALUES (?,?,?,?)",
            [
                (1, "Agosto/2026", "2026-08-01", "2026-08-31"),
                (2, "Agosto/2026", "2026-08-01", "2026-08-31"),
            ],
        )
        conn.executemany(
            "INSERT INTO contrapartes (nome) VALUES (?)",
            [("Banco Crédito",), ("Banco Referenciado",), ("Banco Liquidação",), ("Banco Manual",)],
        )
        conn.commit()
        conn.close()
        self.client = app.app.test_client()

    def tearDown(self):
        app.DB = self.previous_db
        self.db_path.unlink(missing_ok=True)

    def _save_defaults(self, empresa_id="1", credito="1", referenciado="2", liquidacao="3"):
        return self.client.post("/configuracoes/padroes", data={
            "empresa_id": empresa_id,
            "banco_credito_id": credito,
            "banco_referenciado_id": referenciado,
            "banco_liquidacao_id": liquidacao,
        })

    def _create_invoice(self, number, reference=None, company_id="1", competence_id=None):
        data = {
            "empresa_id": company_id,
            "numero_invoice": number,
            "tipo_documento": "COMMERCIAL_INVOICE",
            "competencia_id": competence_id or ("1" if company_id == "1" else "2"),
            "moeda": "USD",
            "valor_moeda": "100,00",
        }
        if reference is not None:
            data["banco_referenciado_id"] = str(reference)
        response = self.client.post("/invoice/nova", data=data)
        self.assertEqual(response.status_code, 302)
        conn = app.db()
        invoice = conn.execute(
            "SELECT id FROM invoices WHERE numero_invoice=?", (number,)
        ).fetchone()
        conn.close()
        return invoice["id"]

    def test_settings_are_saved_edited_cleared_and_scoped_by_company(self):
        response = self.client.get("/configuracoes")
        self.assertEqual(response.status_code, 200)
        self.assertIn("/configuracoes/padroes", response.get_data(as_text=True))

        response = self.client.get("/configuracoes/padroes")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Configurações Padrão", html)
        self.assertIn('name="banco_credito_id"', html)
        self.assertIn('name="banco_referenciado_id"', html)
        self.assertIn('name="banco_liquidacao_id"', html)

        self.assertEqual(self._save_defaults().status_code, 302)
        self.assertEqual(self._save_defaults("2", "1", "2", "3").status_code, 302)
        conn = app.db()
        rows = conn.execute(
            "SELECT empresa_id,banco_credito_id,banco_referenciado_id,banco_liquidacao_id "
            "FROM configuracoes_padrao ORDER BY empresa_id"
        ).fetchall()
        self.assertEqual(
            [tuple(row) for row in rows],
            [(1, 1, 2, 3), (2, 1, 2, 3)],
        )
        conn.close()

        self.assertEqual(self._save_defaults("1", "4", "", "").status_code, 302)
        conn = app.db()
        self.assertEqual(tuple(conn.execute(
            "SELECT banco_credito_id,banco_referenciado_id,banco_liquidacao_id "
            "FROM configuracoes_padrao WHERE empresa_id=1"
        ).fetchone()), (4, None, None))
        conn.close()

        self.assertEqual(self._save_defaults("1", "", "", "").status_code, 302)
        conn = app.db()
        self.assertIsNone(conn.execute(
            "SELECT id FROM configuracoes_padrao WHERE empresa_id=1"
        ).fetchone())
        self.assertIsNotNone(conn.execute(
            "SELECT id FROM configuracoes_padrao WHERE empresa_id=2"
        ).fetchone())
        conn.close()

    def test_settings_reject_unknown_company_or_bank(self):
        response = self.client.post("/configuracoes/padroes", data={
            "empresa_id": "999", "banco_credito_id": "1",
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn("empresa selecionada não foi encontrada", response.get_data(as_text=True))

        response = self.client.post("/configuracoes/padroes", data={
            "empresa_id": "1", "banco_credito_id": "999",
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn("Banco / Contraparte selecionado não foi encontrado", response.get_data(as_text=True))

    def test_defaults_fill_new_forms_without_overwriting_existing_or_manual_values(self):
        self.assertEqual(self._save_defaults().status_code, 302)

        response = self.client.get("/invoice/nova")
        html = response.get_data(as_text=True)
        self.assertIn('data-default-banco-referenciado="2"', html)

        response = self.client.post("/invoice/nova", data={
            "empresa_id": "1", "banco_referenciado_id": "1",
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('<option value="1" selected>Banco Crédito</option>', response.get_data(as_text=True))

        invoice_id = self._create_invoice("INV-DEFAULT")
        response = self.client.get(f"/invoice/{invoice_id}")
        html = response.get_data(as_text=True)
        receipt_form = html.split("Recebimentos em", 1)[1].split("Fechamentos", 1)[0]
        self.assertIn('<option value="1" selected>Banco Crédito</option>', receipt_form)
        self.assertNotIn('<option value="2" selected>Banco Referenciado</option>', receipt_form)
        closing_form = html.split("Fechamentos de", 1)[1].split("Contratos Câmbio", 1)[0]
        self.assertIn('<option value="3" selected>Banco Liquidação</option>', closing_form)
        exchange_form = html.split("<section><h2>Contratos Câmbio", 1)[1]
        self.assertIn('<option value="3" selected>Banco Liquidação</option>', exchange_form)

        referenced_invoice_id = self._create_invoice("INV-REFERENCE", reference=2)
        response = self.client.get(f"/invoice/{referenced_invoice_id}")
        receipt_form = response.get_data(as_text=True).split("Recebimentos em", 1)[1].split("Fechamentos", 1)[0]
        self.assertIn('<option value="2" selected>Banco Referenciado</option>', receipt_form)
        self.assertNotIn('<option value="1" selected>Banco Crédito</option>', receipt_form)

        old_invoice_id = self._create_invoice("INV-OLD", reference=1)
        self.assertEqual(self._save_defaults("1", "1", "2", "3").status_code, 302)
        response = self.client.get(f"/invoice/{old_invoice_id}/editar")
        self.assertIn('<option value="1" selected>Banco Crédito</option>', response.get_data(as_text=True))

        self.assertEqual(self.client.post(f"/invoice/{invoice_id}/recebimentos", data={
            "banco_credito_id": "4", "data_credito": "10/08/2026", "valor_moeda": "100,00",
        }).status_code, 302)
        self.assertEqual(self.client.post(f"/invoice/{invoice_id}/fechamentos", data={
            "valor_moeda": "100,00", "data_fechamento": "11/08/2026",
            "numero_novo_contrato": "C-MANUAL", "banco_liquidacao_id": "4",
        }).status_code, 302)
        conn = app.db()
        self.assertEqual(conn.execute(
            "SELECT banco_credito_id FROM recebimentos_invoice WHERE invoice_id=?", (invoice_id,)
        ).fetchone()[0], 4)
        self.assertEqual(conn.execute(
            "SELECT banco_liquidacao_id FROM contratos WHERE numero_contrato='C-MANUAL'"
        ).fetchone()[0], 4)
        self.assertEqual(conn.execute(
            "SELECT banco_referenciado_id FROM invoices WHERE id=?", (old_invoice_id,)
        ).fetchone()[0], 1)
        conn.close()

    def test_schema_migration_creates_defaults_table_without_changing_records(self):
        migration_path = Path(tempfile.mktemp(prefix="duecontrol_padrao_migration_", suffix=".db"))
        previous_db = app.DB
        try:
            app.DB = migration_path
            conn = sqlite3.connect(migration_path)
            conn.executescript("""
                CREATE TABLE empresas (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, razao_social TEXT NOT NULL,
                    cnpj TEXT NOT NULL UNIQUE, apelido TEXT, created_at TEXT
                );
                CREATE TABLE clientes (id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT NOT NULL,
                    pais TEXT NOT NULL, created_at TEXT, UNIQUE(nome, pais));
                CREATE TABLE contrapartes (id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT NOT NULL UNIQUE,
                    created_at TEXT);
                CREATE TABLE dues (id INTEGER PRIMARY KEY AUTOINCREMENT, numero_due TEXT NOT NULL UNIQUE,
                    data_due TEXT, cnpj TEXT, cliente TEXT, moeda TEXT NOT NULL DEFAULT 'USD',
                    valor_original REAL NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'PENDENTE',
                    observacao TEXT, competencia_id INTEGER);
                CREATE TABLE contratos (id INTEGER PRIMARY KEY AUTOINCREMENT, numero_contrato TEXT NOT NULL UNIQUE,
                    banco TEXT, banco_credito TEXT, banco_liquidacao TEXT, data_contrato TEXT,
                    data_fechamento TEXT, data_recebimento TEXT, data_liquidacao TEXT, cnpj TEXT, cliente TEXT,
                    moeda TEXT NOT NULL DEFAULT 'USD', valor_moeda REAL NOT NULL DEFAULT 0, taxa_cambio REAL,
                    valor_reais REAL, status TEXT NOT NULL DEFAULT 'PENDENTE', saldo_zerado_manual INTEGER NOT NULL DEFAULT 0,
                    observacao TEXT, created_at TEXT, competencia_id INTEGER);
                CREATE TABLE invoices (id INTEGER PRIMARY KEY AUTOINCREMENT, empresa_id INTEGER NOT NULL,
                    numero_invoice TEXT NOT NULL, tipo_documento TEXT NOT NULL, cliente_id INTEGER,
                    data_emissao TEXT, moeda TEXT NOT NULL DEFAULT 'USD', valor_moeda REAL NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'AGUARDANDO_RECEBIMENTO', observacao TEXT, created_at TEXT,
                    UNIQUE(empresa_id, numero_invoice, tipo_documento));
                CREATE TABLE recebimentos_invoice (id INTEGER PRIMARY KEY AUTOINCREMENT, invoice_id INTEGER NOT NULL,
                    banco_credito_id INTEGER, data_credito TEXT NOT NULL, moeda TEXT NOT NULL, valor_moeda REAL NOT NULL,
                    documento TEXT, observacao TEXT, created_at TEXT);
                INSERT INTO empresas(razao_social,cnpj) VALUES ('Empresa Antiga','11111111000111');
                INSERT INTO contrapartes(nome) VALUES ('Banco Antigo');
                INSERT INTO invoices(empresa_id,numero_invoice,tipo_documento,valor_moeda)
                    VALUES (1,'INV-ANTIGA','COMMERCIAL_INVOICE',10);
                PRAGMA user_version = 5;
            """)
            conn.commit()
            conn.close()

            app.init_db()
            conn = app.db()
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], app.INVOICE_SCHEMA_VERSION)
            self.assertIsNotNone(conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='configuracoes_padrao'"
            ).fetchone())
            self.assertEqual(conn.execute(
                "SELECT numero_invoice FROM invoices WHERE id=1"
            ).fetchone()[0], "INV-ANTIGA")
            conn.close()
        finally:
            app.DB = previous_db
            migration_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
