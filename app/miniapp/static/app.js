/* Telegram Mini App: identity and permissions are always determined by the server. */
(() => {
  "use strict";

  const telegram = window.Telegram && window.Telegram.WebApp;
  const session = telegram && telegram.initData;
  const status = document.getElementById("status");
  const workspace = document.getElementById("workspace");
  const section = document.getElementById("section");
  const tabs = document.getElementById("tabs");
  const profileBadge = document.getElementById("profile");
  const greeting = document.getElementById("greeting");
  const toast = document.getElementById("toast");
  const state = { user: null, current: "", tasks: [], busy: false, createKey: null };
  let toastTimer = null;

  const destinations = {
    admin: [
      { id: "tasks", label: "Задачи", symbol: "✦" },
      { id: "schedule", label: "Расписание", symbol: "◷" },
      { id: "clients", label: "Клиенты", symbol: "♧" },
      { id: "subscriptions", label: "Абонементы", symbol: "◇" },
      { id: "team", label: "Команда", symbol: "☷", ownerOnly: true },
    ],
    teacher: [
      { id: "classes", label: "Мои занятия", symbol: "◷" },
      { id: "rentals", label: "Аренда", symbol: "◇" },
    ],
    client: [
      { id: "schedule", label: "Расписание", symbol: "◷" },
      { id: "bookings", label: "Мои записи", symbol: "✦" },
      { id: "subscriptions", label: "Абонемент", symbol: "◇" },
    ],
  };
  const sectionNames = {
    tasks: "Задачи", schedule: "Расписание", clients: "Клиенты",
    subscriptions: "Абонементы", team: "Команда", classes: "Мои занятия",
    rentals: "Аренда", bookings: "Мои записи",
  };

  function element(tag, className, content) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (content !== undefined) node.textContent = content;
    return node;
  }

  function announce(message, isError = false) {
    status.textContent = message;
    status.className = isError ? "notice error" : "notice";
    status.hidden = false;
  }

  function notify(message) {
    window.clearTimeout(toastTimer);
    toast.textContent = message;
    toast.hidden = false;
    toastTimer = window.setTimeout(() => { toast.hidden = true; }, 3500);
    if (telegram && telegram.HapticFeedback) telegram.HapticFeedback.notificationOccurred("success");
  }

  async function request(path, options = {}) {
    if (!session) throw new Error("Откройте приложение из Telegram.");
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 12000);
    try {
      const response = await fetch(`/miniapp/api${path}`, {
        method: options.method || "GET",
        headers: {
          Authorization: `tma ${session}`,
          ...(options.body ? { "Content-Type": "application/json" } : {}),
          ...(options.idempotencyKey ? { "Idempotency-Key": options.idempotencyKey } : {}),
        },
        body: options.body ? JSON.stringify(options.body) : undefined,
        signal: controller.signal,
        credentials: "omit",
        cache: "no-store",
      });
      if (!response.ok) {
        if (response.status === 401 || response.status === 403) {
          throw new Error("Нет доступа. Закройте приложение и откройте его через бота ещё раз.");
        }
        if (response.status === 409) throw new Error("Данные изменились. Обновите список задач.");
        throw new Error("Не удалось выполнить действие. Повторите попытку.");
      }
      return response.json();
    } catch (error) {
      if (error.name === "AbortError") throw new Error("Сервер долго не отвечает. Повторите попытку.");
      throw error;
    } finally {
      window.clearTimeout(timeout);
    }
  }

  function makeButton(text, className, action) {
    const button = element("button", className, text);
    button.type = "button";
    button.addEventListener("click", action);
    return button;
  }

  function heading(title, subtitle, extra) {
    const row = element("div", "section-heading");
    const wrapper = element("div");
    wrapper.append(element("h2", "section-title", title), element("p", "section-subtitle", subtitle));
    row.append(wrapper);
    if (extra) row.append(extra);
    return row;
  }

  function placeholder(name) {
    section.replaceChildren();
    section.append(heading(name, "Этот раздел появится после подключения необходимых данных."));
    const card = element("div", "soon");
    card.append(
      element("span", "empty-symbol", "✳"),
      element("p", "eyebrow", "В РАЗРАБОТКЕ"),
      element("h3", "", "Пока не подключено"),
      element("p", "", "Мы не показываем вымышленные занятия или записи. Раздел откроется, когда будет готова безопасная интеграция."),
    );
    const refresh = makeButton("Проверить снова", "secondary", () => selectSection(state.current));
    card.append(refresh);
    section.append(card);
  }

  function displayDate(value) {
    if (!value) return "Без срока";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "Без срока";
    return new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }).format(date);
  }

  function taskCard(task) {
    const card = element("article", "task-card");
    const top = element("div", "card-top");
    const title = String(task.title || task.body || "Задача без названия");
    const stateName = { open: "Открыта", taken: "В работе", done: "Готово" }[task.state] || "Задача";
    const pill = element("span", `pill ${task.state || ""}`, stateName);
    top.append(element("span", "task-id", `№ ${task.id} · ${task.code || "R8"}`), pill);
    card.append(top, element("h3", "", title));
    if (task.body && task.body !== title) card.append(element("p", "", task.body));
    const bottom = element("div", "card-bottom");
    bottom.append(element("span", "due", displayDate(task.due_at)));
    const isOwner = state.user && state.user.is_owner;
    const isAssignee = state.user && Number(task.taken_by) === Number(state.user.user_id);
    const canTake = task.state === "open" && task.code !== "R4";
    const canFinish = task.state === "taken" && task.code !== "R3" && (isAssignee || isOwner);
    if (canTake || canFinish) {
      const action = task.state === "open" ? "take" : "done";
      const label = task.state === "open" ? "Взять →" : "Завершить ✓";
      const button = makeButton(label, "primary task-action", () => changeTask(task.id, action));
      button.disabled = state.busy;
      bottom.append(button);
    } else if (task.code === "R4" && task.state === "open") {
      bottom.append(element("span", "due", "Привязка клиента — через Telegram"));
    } else if (task.code === "R3" && task.state === "taken" && (isAssignee || isOwner)) {
      bottom.append(element("span", "due", `Ответить в боте: /reply ${task.id} текст`));
    }
    card.append(bottom);
    return card;
  }

  function taskEmpty() {
    const empty = element("div", "empty");
    empty.append(
      element("span", "empty-symbol", "✓"),
      element("h3", "", "Всё под контролем"),
      element("p", "", "Очередь задач пуста. Новая задача появится здесь сразу после создания."),
    );
    return empty;
  }

  function creationForm() {
    const form = element("form", "form-card");
    const label = element("label", "label", "Что необходимо сделать?");
    label.htmlFor = "new-task";
    const input = element("input", "field");
    input.id = "new-task";
    input.type = "text";
    input.required = true;
    input.maxLength = 500;
    input.placeholder = "Например, подготовить зал к занятию";
    // An edited submission is a new logical request, not a retry of the old one.
    input.addEventListener("input", () => { state.createKey = null; });
    const actions = element("div", "form-actions");
    const submit = element("button", "primary", "Создать задачу");
    submit.type = "submit";
    const cancel = makeButton("Отмена", "secondary", () => {
      state.createKey = null;
      form.remove();
    });
    actions.append(submit, cancel);
    form.append(label, input, actions);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (state.busy) return;
      const title = input.value.trim();
      if (!title) return;
      if (!state.createKey) state.createKey = window.crypto.randomUUID();
      state.busy = true;
      submit.disabled = true;
      cancel.disabled = true;
      let created = false;
      try {
        await request("/tasks", { method: "POST", body: { title }, idempotencyKey: state.createKey });
        created = true;
        state.createKey = null;
        try { await loadTasks(); }
        catch { announce("Задача создана, но список пока не обновился. Откройте раздел заново.", true); }
        notify("Задача создана");
      } catch (error) {
        announce(error.message, true);
        submit.disabled = false;
        cancel.disabled = false;
      } finally {
        state.busy = false;
        if (created) renderTasks();
      }
    });
    return form;
  }

  function renderTasks() {
    section.replaceChildren();
    const open = state.tasks.filter((task) => task.state !== "done").length;
    section.append(heading("Задачи студии", "Общая очередь администратора", element("span", "count", String(open))));
    const toolbar = element("div", "toolbar");
    toolbar.append(element("span", "section-subtitle", open ? `В работе и ожидании: ${open}` : "Нет активных задач"));
    toolbar.append(makeButton("+ Новая", "primary", () => {
      const old = document.getElementById("new-task");
      if (old) { old.focus(); return; }
      toolbar.insertAdjacentElement("afterend", creationForm());
      document.getElementById("new-task").focus();
    }));
    section.append(toolbar);
    const grid = element("div", "task-grid");
    if (!state.tasks.length) grid.append(taskEmpty());
    else for (const task of state.tasks) grid.append(taskCard(task));
    section.append(grid);
  }

  async function loadTasks() {
    const data = await request("/tasks");
    if (!Array.isArray(data.items)) throw new Error("Сервер вернул неверный список задач.");
    state.tasks = data.items;
    renderTasks();
    status.hidden = true;
  }

  async function changeTask(id, action) {
    if (state.busy) return;
    if (action === "done" && !window.confirm("Завершить эту задачу?")) return;
    state.busy = true;
    renderTasks();
    try {
      const result = await request(`/tasks/${encodeURIComponent(id)}/${action}`, { method: "POST" });
      if (result.ok !== true) {
        await loadTasks();
        announce("Задача уже изменилась или недоступна. Список обновлён.", true);
        return;
      }
      await loadTasks();
      notify(action === "take" ? "Задача взята в работу" : "Задача завершена");
    } catch (error) {
      announce(error.message, true);
    } finally {
      state.busy = false;
      renderTasks();
    }
  }

  function renderTabs() {
    tabs.replaceChildren();
    const entries = destinations[state.user.role] || destinations.client;
    for (const entry of entries) {
      if (entry.ownerOnly && !state.user.is_owner) continue;
      const tab = element("button", "tab", `${entry.symbol}  ${entry.label}`);
      tab.type = "button";
      tab.setAttribute("aria-selected", String(state.current === entry.id));
      tab.addEventListener("click", () => selectSection(entry.id));
      tabs.append(tab);
    }
  }

  async function selectSection(id) {
    state.current = id;
    renderTabs();
    status.hidden = true;
    if (id !== "tasks") { placeholder(sectionNames[id] || id); return; }
    section.replaceChildren(heading("Задачи студии", "Загружаем очередь…"));
    try { await loadTasks(); }
    catch (error) { announce(error.message, true); section.replaceChildren(makeButton("Повторить загрузку", "secondary", () => selectSection(id))); }
  }

  async function start() {
    if (!telegram || !session) {
      announce("Откройте этот экран кнопкой «Открыть приложение» внутри Telegram.", true);
      return;
    }
    telegram.ready();
    telegram.expand();
    if (telegram.colorScheme === "light") document.documentElement.dataset.theme = "light";
    try {
      const user = await request("/me");
      if (!["admin", "teacher", "client"].includes(user.role)) throw new Error("Неизвестная роль пользователя.");
      state.user = user;
      const displayRole = user.is_owner ? "Владелец" : { admin: "Администратор", teacher: "Преподаватель", client: "Клиент" }[user.role];
      profileBadge.replaceChildren(element("span", "profile-dot"), document.createTextNode(displayRole));
      greeting.textContent = { admin: "Управляйте задачами и рабочими процессами студии.", teacher: "Ваши занятия и аренды в удобном формате.", client: "Практика, расписание и личные записи рядом." }[user.role];
      workspace.hidden = false;
      await selectSection(user.role === "admin" ? "tasks" : user.role === "teacher" ? "classes" : "schedule");
    } catch (error) { announce(error.message, true); }
  }

  start();
})();
