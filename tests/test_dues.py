import io
import tempfile
import unittest
from pathlib import Path

import pandas as pd

import app


class DueNumberTests(unittest.TestCase):
    def setUp(self):
        self.previous_db = app.DB
        self.db_path = Path(tempfile.mktemp(prefix="duecontrol_due_number_test_", suffix=".db"))
        app.DB = self.db_path
        app.init_db()
        conn = app.db()
        conn.execute(
            "INSERT INTO empresas (razao_social, cnpj, apelido) VALUES (?,?,?)",
            ("Empresa Teste", "45765914000181", "Teste"),
        )
        conn.execute(
            "INSERT INTO competencias (empresa_id, descricao, data_inicial, data_final) "
            "VALUES (?,?,?,?)",
            (1, "Setembro/2026", "2026-09-01", "2026-09-30"),
        )
        conn.execute("INSERT INTO clientes (nome, pais) VALUES (?,?)", ("Cliente Teste", "BR"))
        conn.commit()
        conn.close()
        self.client = app.app.test_client()

    def tearDown(self):
        app.DB = self.previous_db
        self.db_path.unlink(missing_ok=True)

    def _due_form(self, numero):
        return {
            "numero_due": numero,
            "chave_acesso": "12345678901234",
            "empresa_id": "1",
            "cliente_id": "1",
            "competencia_id": "1",
            "moeda": "USD",
            "valor_original": "100,00",
            "observacao": "",
        }

    def test_normalizes_complete_numbers_and_allows_partial_filters(self):
        self.assertEqual(
            app.normalize_numero_due("26br0009519589"),
            "26BR000951958-9",
        )
        self.assertEqual(
            app.normalize_numero_due("26BR000951958--9"),
            "26BR000951958-9",
        )
        self.assertEqual(
            app.normalize_numero_due("26BR0009", allow_partial=True),
            "26BR0009",
        )

    def test_rejects_invalid_due_numbers(self):
        invalid_values = (None, "", "26BR000951958", "26BR00095195890", "26BR000951958@9")
        for value in invalid_values:
            with self.subTest(value=value), self.assertRaises(ValueError):
                app.normalize_numero_due(value)

    def test_new_due_is_persisted_in_canonical_format(self):
        response = self.client.post("/due/nova", data=self._due_form("26br0009519589"))

        self.assertEqual(response.status_code, 302)
        conn = app.db()
        row = conn.execute("SELECT numero_due, created_at, data_due FROM dues").fetchone()
        conn.close()
        self.assertEqual(row["numero_due"], "26BR000951958-9")
        self.assertRegex(row["created_at"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
        self.assertEqual(row["data_due"], row["created_at"][:10])

    def test_new_due_form_does_not_show_receipt_date(self):
        response = self.client.get("/due/nova")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertNotIn("Data de recebimento", html)
        self.assertIn('name="competencia_id"', html)

    def test_invalid_new_due_is_not_persisted(self):
        response = self.client.post("/due/nova", data=self._due_form("26BR000951958@9"))

        self.assertEqual(response.status_code, 200)
        self.assertIn("número da DU-E", response.get_data(as_text=True))
        conn = app.db()
        count = conn.execute("SELECT COUNT(*) FROM dues").fetchone()[0]
        conn.close()
        self.assertEqual(count, 0)

    def test_edit_due_is_persisted_in_canonical_format(self):
        self.client.post("/due/nova", data=self._due_form("26BR0009519589"))
        conn = app.db()
        original = conn.execute("SELECT id, created_at, data_due FROM dues").fetchone()
        conn.close()

        response = self.client.post(
            f"/due/{original['id']}/editar",
            data=self._due_form("26br0009519590"),
        )

        self.assertEqual(response.status_code, 302)
        conn = app.db()
        row = conn.execute(
            "SELECT numero_due, created_at, data_due FROM dues WHERE id=?",
            (original["id"],),
        ).fetchone()
        conn.close()
        self.assertEqual(row["numero_due"], "26BR000951959-0")
        self.assertEqual(row["created_at"], original["created_at"])
        self.assertEqual(row["data_due"], original["data_due"])

    def test_edit_preserves_legacy_receipt_date_and_launch_timestamp(self):
        conn = app.db()
        conn.execute(
            """INSERT INTO dues
               (numero_due, chave_acesso, cnpj, cliente, cliente_id, moeda,
                valor_original, status, created_at, data_due, competencia_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "26BR000951958-9", "12345678901234", "45765914000181",
                "Cliente Teste", 1, "USD", 100, "PENDENTE",
                "2026-08-20 09:30:00", "2026-08-15", 1,
            ),
        )
        due_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        conn.close()

        response = self.client.post(
            f"/due/{due_id}/editar",
            data=self._due_form("26BR0009519590"),
        )

        self.assertEqual(response.status_code, 302)
        conn = app.db()
        row = conn.execute(
            "SELECT created_at, data_due FROM dues WHERE id=?", (due_id,)
        ).fetchone()
        conn.close()
        self.assertEqual(row["created_at"], "2026-08-20 09:30:00")
        self.assertEqual(row["data_due"], "2026-08-15")

    def test_filter_matches_unformatted_and_partial_values(self):
        conn = app.db()
        conn.execute(
            "INSERT INTO dues (numero_due, moeda, valor_original) VALUES (?,?,?)",
            ("26BR000951958-9", "USD", 100),
        )
        conn.commit()
        conn.close()

        for query in ("26BR0009519589", "26BR000951958-9", "26BR0009"):
            with self.subTest(query=query):
                response = self.client.get("/dues", query_string={"numero_due": query})
                self.assertEqual(response.status_code, 200)
                self.assertIn("26BR000951958-9", response.get_data(as_text=True))

    def test_invalid_filter_does_not_return_all_dues(self):
        conn = app.db()
        conn.execute(
            "INSERT INTO dues (numero_due, moeda, valor_original) VALUES (?,?,?)",
            ("26BR000951958-9", "USD", 100),
        )
        conn.commit()
        conn.close()

        response = self.client.get("/dues", query_string={"numero_due": "26BR000951958@9"})

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Nenhuma DU-E encontrada", html)
        self.assertNotIn("26BR000951958-9", html)

    def test_excel_import_normalizes_and_rejects_invalid_or_duplicate_numbers(self):
        frame = pd.DataFrame([
            {
                "numero_due": "26br0009519589",
                "chave_acesso": "12345678901234",
                "cnpj": "45.765.914/0001-81",
                "valor_original": 100,
            },
            {
                "numero_due": "26BR000951958-9",
                "chave_acesso": "22345678901234",
                "cnpj": "45.765.914/0001-81",
                "valor_original": 100,
            },
            {
                "numero_due": "26BR000951958@0",
                "chave_acesso": "32345678901234",
                "cnpj": "45.765.914/0001-81",
                "valor_original": 100,
            },
        ])
        workbook = io.BytesIO()
        frame.to_excel(workbook, index=False)
        workbook.seek(0)

        response = self.client.post(
            "/dues/importar",
            data={"arquivo": (workbook, "dues.xlsx")},
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("26BR000951958-9", html)
        conn = app.db()
        rows = conn.execute("SELECT numero_due, created_at, data_due FROM dues").fetchall()
        conn.close()
        self.assertEqual([row[0] for row in rows], ["26BR000951958-9"])
        self.assertEqual(rows[0][2], rows[0][1][:10])

    def test_excel_import_normalizes_registered_client_name_and_links_id(self):
        conn = app.db()
        conn.execute(
            "INSERT INTO clientes (nome, pais) VALUES (?,?)",
            ("ETG COMMODITIES B. V.", "NL"),
        )
        conn.commit()
        client_id = conn.execute(
            "SELECT id FROM clientes WHERE nome=?", ("ETG COMMODITIES B. V.",)
        ).fetchone()[0]
        conn.close()

        frame = pd.DataFrame([{
            "numero_due": "26BR0011495381",
            "chave_acesso": "42345678901234",
            "cnpj": "45.765.914/0001-81",
            "cliente": "  ETG   COMMODITIES  ",
            "valor_original": 100,
        }])
        workbook = io.BytesIO()
        frame.to_excel(workbook, index=False)
        workbook.seek(0)

        response = self.client.post(
            "/dues/importar",
            data={"arquivo": (workbook, "due-client.xlsx")},
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("Clientes normalizados", response.get_data(as_text=True))
        conn = app.db()
        due = conn.execute(
            "SELECT cliente, cliente_id FROM dues WHERE numero_due=?",
            ("26BR001149538-1",),
        ).fetchone()
        conn.close()
        self.assertEqual(tuple(due), ("ETG COMMODITIES B. V.", client_id))

    def test_excel_import_assigns_current_open_competence_by_company(self):
        frame = pd.DataFrame([{
            "numero_due": "26BR0011495392",
            "chave_acesso": "52345678901234",
            "cnpj": "45.765.914/0001-81",
            "valor_original": 100,
        }])
        workbook = io.BytesIO()
        frame.to_excel(workbook, index=False)
        workbook.seek(0)

        response = self.client.post(
            "/dues/importar",
            data={"arquivo": (workbook, "due-competence.xlsx")},
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 200)
        conn = app.db()
        due = conn.execute(
            "SELECT competencia_id FROM dues WHERE numero_due=?",
            ("26BR001149539-2",),
        ).fetchone()
        conn.close()
        self.assertEqual(due["competencia_id"], 1)

    def test_init_migrates_legacy_due_client_name_to_registered_client(self):
        conn = app.db()
        conn.execute(
            "INSERT INTO clientes (nome, pais) VALUES (?,?)",
            ("LOUIS DREYFUS COMPANY SUISSE SA", "CH"),
        )
        conn.execute(
            "INSERT INTO dues (numero_due, cliente, moeda, valor_original) VALUES (?,?,?,?)",
            ("26BR001148381-2", "LOUIS DREYFUS COMPANY SUISSE", "USD", 100),
        )
        conn.commit()
        client_id = conn.execute(
            "SELECT id FROM clientes WHERE nome=?",
            ("LOUIS DREYFUS COMPANY SUISSE SA",),
        ).fetchone()[0]
        conn.close()

        app.init_db()

        conn = app.db()
        due = conn.execute(
            "SELECT cliente, cliente_id FROM dues WHERE numero_due=?",
            ("26BR001148381-2",),
        ).fetchone()
        conn.close()
        self.assertEqual(tuple(due), ("LOUIS DREYFUS COMPANY SUISSE SA", client_id))

    def test_init_migrates_missing_due_competence_by_company_and_operation_date(self):
        conn = app.db()
        conn.execute(
            "INSERT INTO dues (numero_due, cnpj, moeda, valor_original, data_due) VALUES (?,?,?,?,?)",
            ("26BR001149539-2", "45.765.914/0001-81", "USD", 100, "2026-09-22"),
        )
        conn.commit()
        conn.close()

        app.init_db()

        conn = app.db()
        due = conn.execute(
            "SELECT competencia_id FROM dues WHERE numero_due=?",
            ("26BR001149539-2",),
        ).fetchone()
        conn.close()
        self.assertEqual(due["competencia_id"], 1)

    def test_global_excel_export_includes_company_alias(self):
        conn = app.db()
        conn.execute(
            "INSERT INTO dues (numero_due, cnpj, moeda, valor_original) VALUES (?,?,?,?)",
            ("26BR000951958-9", "45765914000181", "USD", 100),
        )
        conn.commit()
        conn.close()

        response = self.client.get("/dues/exportar")

        self.assertEqual(response.status_code, 200)
        exported = pd.read_excel(io.BytesIO(response.data), sheet_name="DU-Es")
        self.assertIn("Apelido Empresa", exported.columns)
        self.assertEqual(exported.iloc[0]["Apelido Empresa"], "Teste")


if __name__ == "__main__":
    unittest.main()
