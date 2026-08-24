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

A coluna `status` é opcional para manter compatibilidade com planilhas antigas e aceita `AGUARDANDO RECEBIMENTO`, `RECEBIDO AGUARDANDO CAMBIO` ou `LIQUIDADA`. Quando preenchida, a atribuição é preservada no cadastro; quando vazia, a Invoice começa em `AGUARDANDO RECEBIMENTO`.

Use "Baixar modelo Excel" na Gestão de Invoices. A planilha tem uma linha por Invoice e exige `competencia`. Os campos `cliente_pais` e `valor_alocado` não fazem parte do modelo: o país de um cliente novo é escolhido na prévia e os vínculos de câmbio são feitos no detalhe da Invoice.

A competência é localizada por empresa, aceitando variações de descrição quando o período informado for compatível com uma competência cadastrada. Quando não houver competência exata, próxima ou pertinente à data da Invoice, a prévia sugere o cadastro de uma nova competência para a empresa, sem gravá-la até a confirmação.

Planilhas antigas que ainda contenham `valor_alocado` continuam sendo aceitas somente para reprocessamento compatível; o modelo novo não gera essa coluna e não cria vínculos de câmbio sem ela.

Invoices existentes são reprocessadas atualizando seus dados comerciais e substituindo somente suas alocações importadas; recebimentos e vínculos com DU-E são preservados.

O importador normaliza nomes de clientes por acentos, caixa, pontuação e espaços e associa o cadastro existente quando a correspondência é segura. Para clientes não reconhecidos, a prévia solicita o País e sugere o cadastro; a Invoice só é gravada após a confirmação.

## Excel legado de contratos

As rotas e o modelo antigo de contratos continuam disponíveis como redirecionamento para a importação Invoice-cêntrica.

Atenção: esta é uma V1 básica. Antes de uso operacional, defina política de backup e a chave secreta.
