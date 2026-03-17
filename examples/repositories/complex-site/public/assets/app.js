(function () {
  const config = window.APP_CONFIG || {};
  const apiBaseUrl = config.apiBaseUrl || "http://127.0.0.1:4001";

  const apiBase = document.getElementById("api-base");
  const itemCount = document.getElementById("item-count");
  const dataStatus = document.getElementById("data-status");
  const items = document.getElementById("items");
  const refreshButton = document.getElementById("refresh-button");
  const form = document.getElementById("item-form");
  const formStatus = document.getElementById("form-status");
  const apiError = document.getElementById("api-error");
  const titleInput = document.getElementById("title");

  apiBase.textContent = apiBaseUrl;

  function showApiError(message) {
    apiError.textContent = message;
    apiError.classList.remove("hidden");
    dataStatus.textContent = "API unavailable";
  }

  function clearApiError() {
    apiError.textContent = "";
    apiError.classList.add("hidden");
  }

  function renderItems(list) {
    itemCount.textContent = String(list.length);
    items.innerHTML = "";

    if (list.length === 0) {
      const empty = document.createElement("div");
      empty.className = "item";
      empty.innerHTML = "<h3>No items yet</h3><time>Create one through the form to populate the dashboard.</time>";
      items.appendChild(empty);
      return;
    }

    list.forEach((item) => {
      const element = document.createElement("article");
      element.className = "item";
      element.innerHTML = `
        <h3>${item.title}</h3>
        <time>Created at ${item.created_at}</time>
      `;
      items.appendChild(element);
    });
  }

  async function loadItems() {
    formStatus.textContent = "Loading items from the API...";
    try {
      const response = await fetch(`${apiBaseUrl}/api/items`);
      if (!response.ok) {
        throw new Error(`API returned ${response.status}`);
      }

      const list = await response.json();
      clearApiError();
      dataStatus.textContent = "Connected";
      formStatus.textContent = `Loaded ${list.length} item(s).`;
      renderItems(list);
    } catch (error) {
      renderItems([]);
      showApiError(`Could not reach the REST API at ${apiBaseUrl}. ${error.message}`);
      formStatus.textContent = "Working in degraded mode until the API is available.";
    }
  }

  async function createItem(event) {
    event.preventDefault();
    const title = titleInput.value.trim();
    if (!title) {
      formStatus.textContent = "Please enter a title first.";
      return;
    }

    formStatus.textContent = "Creating item...";
    try {
      const response = await fetch(`${apiBaseUrl}/api/items`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title })
      });

      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.error || `API returned ${response.status}`);
      }

      titleInput.value = "";
      clearApiError();
      formStatus.textContent = "Item created. Refreshing dashboard...";
      await loadItems();
    } catch (error) {
      showApiError(`Create failed: ${error.message}`);
      formStatus.textContent = "Create failed.";
    }
  }

  refreshButton.addEventListener("click", loadItems);
  form.addEventListener("submit", createItem);
  loadItems();
})();
