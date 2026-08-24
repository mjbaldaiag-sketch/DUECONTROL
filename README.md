# Sistema DU-E + Invoices + Contratos de Câmbio

V1 local em Python + Flask + SQLite.

## Instalação no Windows

1. Instale Python 3.11+.
2. Abra o terminal nesta pasta.
3. Crie ambiente virtual:
   `python -m venv .venv`
4. Ative:
   `.venv\Scripts\activate`
5. Instale:
   `pip install -r requirements.txt`
6. Execute:
   `python app.py`
7. Abra no navegador:
   `http://127.0.0.1:5000`

O banco `due.db` é criado automaticamente.

## Gestão de Invoices

A Invoice é o primeiro nível do fluxo financeiro. No detalhe da Invoice são registrados os recebimentos e os vínculos com contratos de câmbio e DU-Es para rastreabilidade.

Os saldos são calculados separadamente:

- saldo de recebimento = valor da Invoice - recebimentos;
- saldo sem câmbio = recebimentos - câmbio alocado.

Os contratos de câmbio são consolidados por número a partir das Invoices vinculadas. Não há cadastro independente de contrato.

## Excel de Invoices

Use "Baixar modelo Excel" na Gestão de Invoices. A planilha tem uma linha por alocação Invoice↔Contrato Câmbio, com `contrato_comercial` como referência da Invoice e `numero_contrato_cambio` para agrupar as Invoices no mesmo contrato financeiro.

Invoices existentes são reprocessadas atualizando seus dados comerciais e substituindo somente suas alocações importadas; recebimentos e vínculos com DU-E são preservados.

O importador normaliza nomes de clientes por acentos, caixa, pontuação e espaços e associa o cadastro existente quando a correspondência é segura. Para clientes não reconhecidos, a prévia solicita o País e sugere o cadastro; a Invoice só é gravada após a confirmação.

## Excel legado de contratos

As rotas e o modelo antigo de contratos continuam disponíveis como redirecionamento para a importação Invoice-cêntrica.

Atenção: esta é uma V1 básica. Antes de uso operacional, defina política de backup e a chave secreta.
