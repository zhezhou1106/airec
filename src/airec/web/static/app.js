// Progressive behaviour for the airec pages. No framework: the server renders everything.

// Inside my-hub the wallpaper shows through, as it does on the hub's own pages.
if (window.self !== window.top) document.documentElement.classList.add("embedded");

async function send(method, url, body) {
  const response = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    const detail = typeof payload.detail === "string" ? payload.detail : "That did not work. Try again.";
    throw new Error(detail);
  }
  return response.json();
}

function directionId(element) {
  return element.closest("[data-id]").dataset.id;
}

function renderPreview(output, result) {
  output.replaceChildren();
  const summary = document.createElement("p");
  summary.className = "preview-summary";
  summary.textContent =
    `Last ${result.days} days: ${result.candidates} candidates, ${result.relevant} relevant, ` +
    `${result.papers.length} passed the quality gate.`;
  output.append(summary);
  const list = document.createElement("ul");
  for (const paper of result.papers) {
    const item = document.createElement("li");
    const link = Object.assign(document.createElement("a"), {
      href: paper.url,
      target: "_blank",
      rel: "noopener",
      textContent: paper.title,
    });
    const reason = Object.assign(document.createElement("span"), {
      className: "muted",
      textContent: paper.reason,
    });
    item.append(link, reason);
    list.append(item);
  }
  output.append(list);
}

document.addEventListener("click", async (event) => {
  const button = event.target.closest("button");
  if (!button) return;

  if (button.dataset.toggle) {
    const target = document.querySelector(button.dataset.toggle);
    target.hidden = !target.hidden;
    if (!target.hidden) target.querySelector("textarea")?.focus();
    return;
  }

  if (button.dataset.pause) {
    await send("POST", `/api/directions/${directionId(button)}/paused`, {
      paused: button.dataset.pause === "true",
    });
    location.reload();
    return;
  }

  if ("remove" in button.dataset) {
    // Two clicks instead of a browser dialog, which is blocked inside the hub's frame.
    if (!button.dataset.armed) {
      button.dataset.armed = "true";
      button.textContent = button.dataset.confirmLabel;
      setTimeout(() => {
        delete button.dataset.armed;
        button.textContent = "Remove";
      }, 4000);
      return;
    }
    await send("DELETE", `/api/directions/${directionId(button)}`);
    location.reload();
    return;
  }

  if ("preview" in button.dataset) {
    const card = button.closest("[data-id]");
    const output = card.querySelector("[data-preview-output]");
    output.hidden = false;
    output.textContent = "Searching and judging the last two weeks. This takes a minute or two.";
    output.classList.add("is-loading");
    button.disabled = true;
    try {
      renderPreview(output, await send("POST", `/api/directions/${directionId(button)}/preview`));
    } catch (error) {
      output.textContent = error.message;
    } finally {
      output.classList.remove("is-loading");
      button.disabled = false;
    }
  }
});

document.addEventListener("submit", async (event) => {
  const form = event.target.closest("[data-direction-form]");
  if (!form) return;
  event.preventDefault();
  const error = form.querySelector(".form-error");
  error.hidden = true;
  try {
    await send(form.dataset.method, form.dataset.url, {
      description: form.elements.description.value,
    });
    location.reload();
  } catch (failure) {
    error.textContent = failure.message;
    error.hidden = false;
  }
});

document.addEventListener("change", (event) => {
  if (event.target.matches("[data-week-picker]")) {
    location.search = `?week=${encodeURIComponent(event.target.value)}`;
  }
});
