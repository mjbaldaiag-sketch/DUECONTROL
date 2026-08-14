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
      const cell = row.children[index];
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
  document.querySelectorAll('[data-money]').forEach((input) => {
    input.addEventListener('blur', () => formatMoney(input));
    input.addEventListener('change', () => formatMoney(input));
  });
  document.querySelectorAll('[data-rate]').forEach((input) => {
    input.addEventListener('blur', () => formatRate(input));
    input.addEventListener('change', () => formatRate(input));
  });

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
