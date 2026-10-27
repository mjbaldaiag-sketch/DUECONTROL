import unittest
from decimal import Decimal

import app


class ContratosRelatoriosTests(unittest.TestCase):
    def test_report_brl_uses_gross_conversion_before_final_rounding(self):
        first = {
            "valor_moeda": Decimal("100"),
            "taxa_cambio": Decimal("1.005"),
            "valor_reais": Decimal("100.50"),
            "regra_conversao_brl": app.CONVERSION_RULE_HALF_DOWN,
        }
        second = {
            "valor_moeda": Decimal("200"),
            "taxa_cambio": Decimal("1.005"),
            "valor_reais": Decimal("201.00"),
            "regra_conversao_brl": app.CONVERSION_RULE_HALF_UP,
        }

        total = app.report_brl_value(first) + app.report_brl_value(second)

        self.assertEqual(total, Decimal("301.500"))
        self.assertEqual(app.money(total), "301,50")

    def test_report_brl_falls_back_to_persisted_value_without_rate(self):
        value = Decimal("123.456")

        self.assertEqual(
            app.report_brl_value({"valor_reais": value, "taxa_cambio": None}),
            value,
        )

    def test_excel_rows_expose_the_same_brl_aggregates(self):
        summary = app.report_summary()
        item = {
            "valor_moeda": Decimal("100"),
            "taxa_cambio": Decimal("1.005"),
            "valor_reais": Decimal("100.50"),
            "regra_conversao_brl": app.CONVERSION_RULE_HALF_UP,
            "resultado": None,
            "ptax_venda": None,
        }
        item["valor_brl"] = app.report_brl_value(item)
        app.add_report_item(summary, item)
        app.finish_report_summary(summary)
        app.add_result_accumulated([summary])
        context = {
            "agrupados": [{"rotulo": "01/04/2026", "moeda": "USD", "resumo": summary}],
            "mensais": [(('2026-04-01', "USD"), summary)],
            "diarios": [(('2026-04-01', "USD"), summary)],
            "contratos": [{
                "numero_contrato": "C-TEST",
                "categoria_cambio": "Câmbio Exportação",
                "data_contrato": "2026-04-01",
                "moeda": "USD",
                "valor_moeda": Decimal("100"),
                "valor_brl": item["valor_brl"],
                "taxa_cambio": Decimal("1.005"),
                "ptax_venda": None,
                "resultado": None,
            }],
        }

        resumo = app.contract_report_excel_rows(context, "resumo")
        mensal = app.contract_report_excel_rows(context, "mensal")
        diario = app.contract_report_excel_rows(context, "diario")
        contratos = app.contract_report_excel_rows(context, "contratos")

        self.assertEqual(resumo[0]["Total BRL"], 100.5)
        self.assertEqual(mensal[0]["Total BRL"], 100.5)
        self.assertEqual(diario[0]["Total BRL"], 100.5)
        self.assertEqual(contratos[0]["Valor BRL"], 100.5)


if __name__ == "__main__":
    unittest.main()
