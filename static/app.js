(() => {
  const formatDate = (input) => {
    const digits = input.value.replace(/\D/g, '').slice(0, 8);
    input.value = digits.replace(/(\d{2})(\d)/, '$1/$2').replace(/(\d{2})(\d)/, '$1/$2');
  };

  const formatCnpj = (input) => {
    const digits = input.value.replace(/\D/g, '').slice(0, 14);
    let formatted = digits;
    if (digits.length > 12) formatted = digits.replace(/^(\d{2})(\d{3})(\d{3})(\d{4})(\d{0,2}).*/, '$1.$2.$3/$4-$5');
    else if (digits.length > 8) formatted = digits.replace(/^(\d{2})(\d{3})(\d{3})(\d{0,4}).*/, '$1.$2.$3/$4');
    else if (digits.length > 5) formatted = digits.replace(/^(\d{2})(\d{3})(\d{0,3}).*/, '$1.$2.$3');
    else if (digits.length > 2) formatted = digits.replace(/^(\d{2})(\d{0,3}).*/, '$1.$2');
    input.value = formatted;
  };

  const formatCnpjValue = (value) => {
    const input = document.createElement('input');
    input.value = value || '';
    formatCnpj(input);
    return input.value;
  };

  const formatMoney = (input) => {
    const raw = input.value.trim();
    if (!raw) return;
    const normalized = raw.includes(',') ? raw.replace(/\./g, '').replace(',', '.') : raw;
    const value = Number(normalized);
    if (Number.isFinite(value)) {
      input.value = new Intl.NumberFormat('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(value);
    }
  };

  const formatRate = (input) => {
    const raw = input.value.trim();
    if (!raw) return;
    const normalized = raw.includes(',') ? raw.replace(/\./g, '').replace(',', '.') : raw;
    const value = Number(normalized);
    if (Number.isFinite(value)) {
      input.value = new Intl.NumberFormat('pt-BR', { minimumFractionDigits: 4, maximumFractionDigits: 4 }).format(value);
    }
  };

  const copyToast = document.querySelector('[data-copy-toast]');
  let copyToastTimer;
  const showCopyToast = () => {
    if (!copyToast) return;
    copyToast.classList.add('is-visible');
    clearTimeout(copyToastTimer);
    copyToastTimer = setTimeout(() => copyToast.classList.remove('is-visible'), 2600);
  };

  const copyText = async (text) => {
    if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
      try {
        await navigator.clipboard.writeText(text);
        return;
      } catch (_) {
        // Tenta o fallback abaixo quando a permissão do Clipboard API falhar.
      }
    }
    const helper = document.createElement('textarea');
    helper.value = text;
    helper.setAttribute('readonly', '');
    helper.style.position = 'fixed';
    helper.style.opacity = '0';
    document.body.appendChild(helper);
    helper.select();
    const copied = document.execCommand('copy');
    helper.remove();
    if (!copied) throw new Error('clipboard');
  };

  document.querySelectorAll('[data-copy-value]').forEach((button) => {
    button.addEventListener('click', async () => {
      const value = button.dataset.copyValue || '';
      if (!value) return;
      try {
        await copyText(value);
        showCopyToast();
      } catch (_) {
        // O navegador pode bloquear a área de transferência sem interação segura.
      }
    });
  });

  const sortableNumber = (value) => {
    const text = String(value || '').trim();
    if (!text) return null;
    const normalized = text.includes(',') ? text.replace(/\./g, '').replace(',', '.') : text;
    const number = Number(normalized);
    return Number.isFinite(number) ? number : null;
  };

  const sortableDate = (value) => {
    const text = String(value || '').trim();
    const match = text.match(/^(\d{2})\/(\d{2})\/(\d{4})$/);
    return match ? `${match[3]}-${match[2]}-${match[1]}` : text;
  };

  document.querySelectorAll('[data-sortable]').forEach((table) => {
    const headerRow = table.querySelector('thead tr') || table.querySelector('tr');
    if (!headerRow) return;
    const headers = [...headerRow.children].filter((cell) => cell.tagName === 'TH' && cell.dataset.sortable !== 'false');
    if (!headers.length) return;

    let body = table.tBodies[0];
    if (!body) {
      body = document.createElement('tbody');
      [...table.querySelectorAll('tr')].filter((row) => row !== headerRow).forEach((row) => body.appendChild(row));
      table.appendChild(body);
    }

    let activeIndex = null;
    let activeDirection = 1;
    const rows = () => [...body.querySelectorAll('tr')].filter((row) => !row.querySelector('.empty'));
    const valueFor = (row, index, type) => {
      const cell = row.children[headers[index].cellIndex];
      const value = cell ? (cell.dataset.sortValue || cell.textContent.trim()) : '';
      if (type === 'number') return sortableNumber(value);
      if (type === 'date') return sortableDate(value);
      return value.toLocaleLowerCase();
    };
    const applySort = (index) => {
      const header = headers[index];
      const direction = activeIndex === index ? activeDirection * -1 : 1;
      activeIndex = index;
      activeDirection = direction;
      headers.forEach((item) => item.setAttribute('aria-sort', 'none'));
      header.setAttribute('aria-sort', direction === 1 ? 'ascending' : 'descending');
      const type = header.dataset.sortType || 'text';
      const sorted = rows().sort((left, right) => {
        const a = valueFor(left, index, type);
        const b = valueFor(right, index, type);
        if (a === b) return 0;
        if (a === null || a === '') return 1;
        if (b === null || b === '') return -1;
        return (a > b ? 1 : -1) * direction;
      });
      const emptyRows = [...body.querySelectorAll('tr')].filter((row) => row.querySelector('.empty'));
      body.append(...sorted, ...emptyRows);
    };

    headers.forEach((header, index) => {
      header.classList.add('sortable-header');
      header.setAttribute('tabindex', '0');
      header.setAttribute('role', 'button');
      header.setAttribute('aria-sort', 'none');
      header.addEventListener('click', () => applySort(index));
      header.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          applySort(index);
        }
      });
    });
  });

  document.querySelectorAll('[data-batch-form]').forEach((form) => {
    const checkboxes = [...form.querySelectorAll('[data-batch-checkbox]')];
    const selectAll = form.querySelector('[data-batch-select-all]');
    const count = form.querySelector('[data-batch-count]');
    const submit = form.querySelector('[data-batch-submit]');
    const sync = () => {
      const selected = checkboxes.filter((checkbox) => checkbox.checked).length;
      if (count) count.textContent = selected;
      if (submit) submit.disabled = selected === 0;
      if (selectAll) {
        selectAll.checked = checkboxes.length > 0 && selected === checkboxes.length;
        selectAll.indeterminate = selected > 0 && selected < checkboxes.length;
      }
    };
    if (selectAll) {
      selectAll.addEventListener('change', () => {
        checkboxes.forEach((checkbox) => { checkbox.checked = selectAll.checked; });
        sync();
      });
    }
    checkboxes.forEach((checkbox) => checkbox.addEventListener('change', sync));
    form.addEventListener('submit', (event) => {
      const selected = checkboxes.filter((checkbox) => checkbox.checked);
      if (!selected.length) {
        event.preventDefault();
        return;
      }
      const template = form.dataset.confirmMessage || 'Confirmar a exclusao dos registros selecionados?';
      const message = template.split('{count}').join(String(selected.length));
      if (!window.confirm(message)) event.preventDefault();
    });
    sync();
  });

  document.querySelectorAll('[data-import-confirm]').forEach((form) => {
    form.addEventListener('submit', (event) => {
      const conflicts = [...form.querySelectorAll('[data-import-conflict]')];
      const actionLabels = {
        update: 'Atualizar contrato existente',
        discard: 'Excluir linha importada',
        insert: 'Inserir linha escolhida',
      };
      const details = conflicts.map((conflict) => {
        const description = conflict.querySelector('[data-import-message]');
        const action = conflict.querySelector('input[type="radio"][name^="duplicate_action_"]:checked, input[type="radio"][name^="file_duplicate_action_"]:checked');
        const text = description ? description.textContent.trim() : '';
        const selectedAction = actionLabels[action ? action.value : ''] || 'Decisão pendente';
        return `${text}\nAção: ${selectedAction}`;
      });
      const message = details.length
        ? `Confirmar a importação?\n\n${details.join('\n\n')}`
        : 'Confirmar a importação dos contratos analisados?';
      if (!window.confirm(message)) event.preventDefault();
    });
  });

  document.querySelectorAll('[data-date-br]').forEach((input) => input.addEventListener('input', () => formatDate(input)));
  document.querySelectorAll('[data-chave-acesso]').forEach((input) => input.addEventListener('input', () => {
    input.value = input.value.replace(/[^a-zA-Z0-9]/g, '').slice(0, 14).toUpperCase();
  }));
  document.querySelectorAll('[data-cnpj]').forEach((input) => input.addEventListener('input', () => formatCnpj(input)));
  document.querySelectorAll('[data-company-select]').forEach((select) => {
    const target = document.getElementById(select.dataset.companyCnpjTarget);
    if (!target) return;
    const update = () => {
      const option = select.selectedOptions[0];
      target.value = formatCnpjValue(option ? option.dataset.cnpj : '');
    };
    select.addEventListener('change', update);
    update();
  });
  document.querySelectorAll('[data-competencia-select]').forEach((select) => {
    const form = select.closest('form');
    const empresa = form ? form.querySelector('select[name="empresa_id"]') : null;
    if (!empresa) return;
    const updateCompetencias = () => {
      const empresaId = empresa.value;
      [...select.options].forEach((option) => {
        if (!option.dataset.competenciaEmpresa) return;
        option.hidden = Boolean(empresaId) && option.dataset.competenciaEmpresa !== empresaId;
      });
      const selected = select.selectedOptions[0];
      if (selected && selected.hidden) select.value = '';
    };
    empresa.addEventListener('change', updateCompetencias);
    updateCompetencias();
  });
  document.querySelectorAll('[data-client-resolution]').forEach((resolution) => {
    const existing = resolution.querySelector('[data-client-existing]');
    const country = resolution.querySelector('[data-client-country]');
    const countrySelect = resolution.querySelector('[data-client-country-select]');
    if (!existing || !country || !countrySelect) return;
    const syncClientResolution = () => {
      const existingSelected = Boolean(existing.value);
      country.hidden = existingSelected;
      countrySelect.disabled = existingSelected;
      countrySelect.required = !existingSelected;
    };
    existing.addEventListener('change', syncClientResolution);
    syncClientResolution();
  });
  document.querySelectorAll('[data-invoice-status-select]').forEach((select) => {
    const form = select.closest('form');
    const creditDate = form ? form.querySelector('[data-invoice-credit-date]') : null;
    const dialog = document.querySelector('[data-invoice-status-dialog]');
    const dialogForm = dialog ? dialog.querySelector('[data-invoice-status-dialog-form]') : null;
    const dialogDate = dialog ? dialog.querySelector('[data-invoice-status-dialog-date]') : null;
    const cancel = dialog ? dialog.querySelector('[data-invoice-status-cancel]') : null;
    if (!form || !creditDate || !dialog || !dialogForm || !dialogDate || !cancel) return;

    const awaiting = 'AGUARDANDO_RECEBIMENTO';
    const received = 'RECEBIDA_AGUARDANDO_CAMBIO';
    let acceptedStatus = select.value;
    const showDialog = () => {
      dialogDate.value = creditDate.value || '';
      if (typeof dialog.showModal === 'function') dialog.showModal();
      else dialog.setAttribute('open', '');
      dialogDate.focus();
    };
    const closeDialog = () => {
      if (typeof dialog.close === 'function') dialog.close();
      else dialog.removeAttribute('open');
    };
    const restoreStatus = () => {
      select.value = acceptedStatus;
      dialogDate.value = creditDate.value || '';
      closeDialog();
    };

    select.addEventListener('change', () => {
      const nextStatus = select.value;
      if (nextStatus === received && acceptedStatus !== received && !creditDate.value) {
        showDialog();
        return;
      }
      if (nextStatus === awaiting) {
        creditDate.value = '';
        dialogDate.value = '';
      }
      acceptedStatus = nextStatus;
    });
    dialogForm.addEventListener('submit', (event) => {
      event.preventDefault();
      if (!dialogDate.reportValidity()) return;
      creditDate.value = dialogDate.value;
      acceptedStatus = received;
      closeDialog();
    });
    cancel.addEventListener('click', restoreStatus);
    dialog.addEventListener('cancel', (event) => {
      event.preventDefault();
      restoreStatus();
    });
    form.addEventListener('submit', (event) => {
      if (select.value === received && !creditDate.value) {
        event.preventDefault();
        showDialog();
      }
    });
  });
  document.querySelectorAll('[data-money]').forEach((input) => {
    input.addEventListener('blur', () => formatMoney(input));
    input.addEventListener('change', () => formatMoney(input));
  });
  document.querySelectorAll('[data-rate]').forEach((input) => {
    input.addEventListener('blur', () => formatRate(input));
    input.addEventListener('change', () => formatRate(input));
  });

  const bancoCredito = document.querySelector('[data-banco-credito]');
  const bancoLiquidacao = document.querySelector('[data-banco-liquidacao]');
  if (bancoCredito && bancoLiquidacao) {
    let liquidacaoSegueCredito = !bancoLiquidacao.value || bancoLiquidacao.value === bancoCredito.value;
    const sincronizarLiquidacao = () => {
      if (liquidacaoSegueCredito) bancoLiquidacao.value = bancoCredito.value;
    };
    bancoCredito.addEventListener('change', sincronizarLiquidacao);
    bancoLiquidacao.addEventListener('change', () => {
      liquidacaoSegueCredito = !bancoLiquidacao.value || bancoLiquidacao.value === bancoCredito.value;
    });
    sincronizarLiquidacao();
  }

  const contractSelect = document.querySelector('[data-contract-select]');
  if (contractSelect) {
    const total = document.querySelector('[data-contract-total]');
    const linked = document.querySelector('[data-contract-linked]');
    const available = document.querySelector('[data-contract-available]');
    const linkValue = document.querySelector('[data-link-value]');
    const display = (value) => new Intl.NumberFormat('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(Number(value || 0));
    const clear = () => { total.textContent = '—'; linked.textContent = '—'; available.textContent = '—'; linkValue.removeAttribute('max'); };
    const update = async () => {
      const id = contractSelect.value;
      if (!id) { clear(); return; }
      const option = contractSelect.selectedOptions[0];
      total.textContent = display(option.dataset.total);
      linked.textContent = display(option.dataset.vinculado);
      available.textContent = display(option.dataset.disponivel);
      linkValue.max = option.dataset.disponivel;
      try {
        const response = await fetch(`${contractSelect.dataset.saldoPrefix}${id}/saldo`);
        if (!response.ok) throw new Error('saldo');
        const data = await response.json();
        total.textContent = display(data.valor_total);
        linked.textContent = display(data.total_vinculado);
        available.textContent = display(data.saldo_disponivel);
        linkValue.max = data.saldo_disponivel;
      } catch (_) { /* os valores da opção continuam disponíveis como fallback visual */ }
    };
    contractSelect.addEventListener('change', update);
    clear();
  }
})();
