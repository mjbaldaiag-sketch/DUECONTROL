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

Use "Baixar modelo Excel" na Gestão de Invoices. A planilha tem uma linha por Invoice e exige `empresa`, `invoice`, `tipo`, `competencia`, `moeda` e `valor_moeda`; `empresa` pode ser o CNPJ, a razão social ou o apelido cadastrado. O modelo também aceita os dados de recebimento (`banco_credito` e `data_credito`) e de câmbio já fechado (`banco_liquidacao`, `contrato_cambio`, `data_fechamento`, `data_liquidacao`, `taxa_cambio` e `valor_brl`). Ao informar um Contrato Câmbio sem `valor_alocado`, o valor integral da Invoice é vinculado automaticamente. Os campos `cliente_pais` e `valor_alocado` não fazem parte do modelo novo, mas planilhas legadas com `valor_alocado` continuam compatíveis.

A competência é localizada por empresa, aceitando variações de descrição quando o período informado for compatível com uma competência cadastrada. Quando não houver competência exata, próxima ou pertinente à data da Invoice, a prévia sugere o cadastro de uma nova competência para a empresa, sem gravá-la até a confirmação.

Quando `data_credito` é informada, o importador registra o recebimento em USD; para uma Invoice com status `RECEBIDO AGUARDANDO CAMBIO`, a data é obrigatória. O `valor_brl` é validado contra `valor_moeda`/valor alocado e `taxa_cambio`, e os bancos informados precisam estar cadastrados em Configurações. Planilhas antigas que ainda contenham `valor_alocado` continuam sendo aceitas para reprocessamento compatível.

Invoices existentes são reprocessadas atualizando seus dados comerciais e substituindo somente suas alocações importadas; recebimentos e vínculos com DU-E são preservados.

O importador normaliza nomes de clientes por acentos, caixa, pontuação e espaços e associa o cadastro existente quando a correspondência é segura. Para clientes não reconhecidos, a prévia solicita o País e sugere o cadastro; a Invoice só é gravada após a confirmação.

## Excel legado de contratos

As rotas e o modelo antigo de contratos continuam disponíveis como redirecionamento para a importação Invoice-cêntrica.

Atenção: esta é uma V1 básica. Antes de uso operacional, defina política de backup e a chave secreta.
