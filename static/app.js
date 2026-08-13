(() => {
  const formatDate = (input) => {
    const digits = input.value.replace(/\D/g, '').slice(0, 8);
    input.value = digits.replace(/(\d{2})(\d)/, '$1/$2').replace(/(\d{2})(\d)/, '$1/$2');
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

  document.querySelectorAll('[data-date-br]').forEach((input) => input.addEventListener('input', () => formatDate(input)));
  document.querySelectorAll('[data-chave-acesso]').forEach((input) => input.addEventListener('input', () => {
    input.value = input.value.replace(/[^a-zA-Z0-9]/g, '').slice(0, 14).toUpperCase();
  }));
  document.querySelectorAll('[data-money]').forEach((input) => {
    input.addEventListener('blur', () => formatMoney(input));
    input.addEventListener('change', () => formatMoney(input));
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
