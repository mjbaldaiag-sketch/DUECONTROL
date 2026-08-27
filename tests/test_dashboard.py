import tempfile
import unittest
from pathlib import Path

import app


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.previous_db = app.DB
        self.db_path = Path(tempfile.mktemp(prefix="duecontrol_dashboard_test_", suffix=".db"))
        app.DB = self.db_path
        app.init_db()
        self.client = app.app.test_client()

    def tearDown(self):
        app.DB = self.previous_db
        self.db_path.unlink(missing_ok=True)

    def test_dashboard_has_clickable_summary_cards_and_no_duplicate_actions(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        expected_cards = [
            ('dashboard-summary-card--invoice', '/invoices', 'Total Invoices: 0'),
            ('dashboard-summary-card--due', '/dues', 'Total DU-Es: 0'),
            ('dashboard-summary-card--contract', '/contratos', 'Total Contratos de Câmbio: 0'),
            ('dashboard-summary-card--due-unlinked', '/dues', 'Total DU-Es sem vínculo: 0'),
            ('dashboard-summary-card--contract-unlinked', '/contratos', 'Total Contratos sem vínculo: 0'),
        ]

        for card_class, destination, aria_label in expected_cards:
            self.assertIn(
                f'class="dashboard-summary-card {card_class}" href="{destination}" '
                f'aria-label="{aria_label}"',
                html,
            )

        self.assertNotIn('class="actions"', html)
        self.assertNotIn('+ Nova Invoice', html)
        self.assertNotIn('+ Nova DU-E', html)
        self.assertNotIn('Baixar modelo de Invoices', html)
        self.assertNotIn('Importar Invoices', html)

    def test_main_navigation_marks_current_route(self):
        routes = [
            ('/', 'Dashboard'),
            ('/invoices', 'Gestão de Invoices'),
            ('/dues', 'Gestão de DU-Es'),
            ('/contratos', 'Gestão de Contratos de Câmbio'),
            ('/derivativos', 'Gestão de Derivativos'),
            ('/configuracoes', 'Configurações'),
        ]

        for path, label in routes:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                html = response.get_data(as_text=True)
                self.assertIn(f'aria-current="page">{label}</a>', html)
                self.assertEqual(html.count('aria-current="page"'), 1)


if __name__ == "__main__":
    unittest.main()
