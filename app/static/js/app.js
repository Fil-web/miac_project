// Общие действия интерфейса без встроенного JavaScript, чтобы соблюдать CSP.
document.querySelectorAll('.js-print').forEach((button) => {
  button.addEventListener('click', () => window.print());
});

// Физическое удаление справочника требует явного подтверждения пользователя.
document.querySelectorAll('.js-confirm-form').forEach((form) => {
  form.addEventListener('submit', (event) => {
    const message = form.dataset.confirm || 'Подтвердите выполнение операции.';
    if (!window.confirm(message)) {
      event.preventDefault();
    }
  });
});

// Генератор использует криптографический источник браузера и не отправляет
// пароль на сервер до обычного сохранения формы.
function secureRandomIndex(limit) {
  const maxAccepted = Math.floor(0x100000000 / limit) * limit;
  const value = new Uint32Array(1);
  do {
    window.crypto.getRandomValues(value);
  } while (value[0] >= maxAccepted);
  return value[0] % limit;
}

function pickRandom(characters) {
  return characters[secureRandomIndex(characters.length)];
}

function generateSecurePassword() {
  const groups = [
    'ABCDEFGHJKLMNPQRSTUVWXYZ',
    'abcdefghijkmnopqrstuvwxyz',
    '23456789',
    '!@#$%&*+-_=?',
  ];
  const allCharacters = groups.join('');
  const characters = groups.map((group) => pickRandom(group));
  while (characters.length < 18) {
    characters.push(pickRandom(allCharacters));
  }
  for (let index = characters.length - 1; index > 0; index -= 1) {
    const swapIndex = secureRandomIndex(index + 1);
    [characters[index], characters[swapIndex]] = [characters[swapIndex], characters[index]];
  }
  return characters.join('');
}

async function copyGeneratedPassword(input) {
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(input.value);
    return true;
  }
  input.focus();
  input.select();
  return document.execCommand('copy');
}

document.querySelectorAll('.js-generate-password').forEach((button) => {
  button.addEventListener('click', async () => {
    const input = document.getElementById(button.dataset.target);
    const feedback = document.getElementById(button.dataset.feedback);
    if (!input || !feedback) {
      return;
    }
    input.value = generateSecurePassword();
    input.dispatchEvent(new Event('input', { bubbles: true }));
    const confirmInput = button.dataset.confirmTarget
      ? document.getElementById(button.dataset.confirmTarget)
      : null;
    if (confirmInput) {
      confirmInput.value = input.value;
      confirmInput.dispatchEvent(new Event('input', { bubbles: true }));
    }
    try {
      const copied = await copyGeneratedPassword(input);
      feedback.textContent = copied
        ? 'Пароль сгенерирован и скопирован в буфер обмена.'
        : 'Пароль сгенерирован. Скопируйте его вручную.';
    } catch (error) {
      feedback.textContent = 'Пароль сгенерирован. Скопируйте его вручную.';
    }
  });
});

// Для администратора генератор не показывается: пароль вводится вручную.
document.querySelectorAll('.js-role-sensitive-password').forEach((container) => {
  const roleSelect = document.getElementById(container.dataset.roleTarget);
  const generatorButton = container.querySelector('.js-generate-password');
  const userHelp = container.querySelector('[data-user-password-help]');
  const adminHelp = container.querySelector('[data-admin-password-help]');
  const feedback = container.querySelector('[aria-live="polite"]');
  if (!roleSelect || !generatorButton || !userHelp || !adminHelp) {
    return;
  }
  const updatePasswordControls = () => {
    const isAdmin = roleSelect.value === 'admin';
    generatorButton.classList.toggle('d-none', isAdmin);
    userHelp.classList.toggle('d-none', isAdmin);
    adminHelp.classList.toggle('d-none', !isAdmin);
    if (feedback) {
      feedback.textContent = '';
    }
  };
  roleSelect.addEventListener('change', updatePasswordControls);
  updatePasswordControls();
});

// Галочка позволяет явно сохранить комплектующее без инвентарного номера.
document.querySelectorAll('.js-without-inventory').forEach((checkbox) => {
  const input = document.getElementById(checkbox.dataset.target);
  if (!input) {
    return;
  }
  const updateInventoryState = () => {
    input.disabled = checkbox.checked;
    input.required = !checkbox.checked;
    if (checkbox.checked) {
      input.value = '';
    }
  };
  checkbox.addEventListener('change', updateInventoryState);
  updateInventoryState();
});

// Каждая таблица получает быстрый локальный фильтр по всем видимым столбцам.
// Серверные фильтры при этом продолжают отвечать за полный набор и пагинацию.
document.querySelectorAll('table').forEach((table, tableIndex) => {
  if (table.dataset.quickFilter === 'off' || !table.tBodies.length) {
    return;
  }
  const rows = Array.from(table.tBodies[0].rows).filter((row) => !row.querySelector('td[colspan]'));
  if (!rows.length) {
    return;
  }
  const tableContainer = table.closest('.table-responsive') || table;
  if (!tableContainer.parentNode) {
    return;
  }
  const filterBar = document.createElement('div');
  filterBar.className = 'table-filter-bar no-print';
  const inputId = `table-filter-${tableIndex}`;
  filterBar.innerHTML = `
    <div class="input-group input-group-sm table-filter-input">
      <span class="input-group-text"><i class="bi bi-search"></i></span>
      <input class="form-control" id="${inputId}" type="search" placeholder="Фильтр по таблице" aria-label="Фильтр по таблице">
    </div>
    <span class="small text-secondary" aria-live="polite"></span>
  `;
  tableContainer.parentNode.insertBefore(filterBar, tableContainer);
  const input = filterBar.querySelector('input');
  const result = filterBar.querySelector('[aria-live="polite"]');
  const updateTableFilter = () => {
    const query = input.value.trim().toLocaleLowerCase('ru');
    let visible = 0;
    rows.forEach((row) => {
      const matches = !query || row.textContent.toLocaleLowerCase('ru').includes(query);
      row.hidden = !matches;
      if (matches) {
        visible += 1;
      }
    });
    result.textContent = query ? `Показано: ${visible} из ${rows.length}` : `Записей: ${rows.length}`;
  };
  input.addEventListener('input', updateTableFilter);
  updateTableFilter();
});
