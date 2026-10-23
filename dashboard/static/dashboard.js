// Sends a form or a button's data to the server as JSON, then reloads the
// page so the server renders the new state. No polling, no timers.
async function send(api, body) {
  const r = await fetch(api, {method: "POST", headers: {"Content-Type": "application/json"},
                              body: JSON.stringify(body)});
  if (!r.ok) {
    const e = await r.json().catch(() => ({}));
    const box = document.getElementById("err");
    box.textContent = e.error || "That did not save. Try again.";
    box.hidden = false;
    return;
  }
  location.reload();
}

document.addEventListener("submit", e => {
  const f = e.target.closest("form[data-api]");
  if (!f) return;
  e.preventDefault();
  send(f.dataset.api, Object.fromEntries(new FormData(f)));
});

document.addEventListener("click", e => {
  const b = e.target.closest("button[data-api]");
  if (!b || b.form) return;
  send(b.dataset.api, JSON.parse(b.dataset.body || "{}"));
});

// Selects with data-api save as soon as they change.
document.addEventListener("change", e => {
  const s = e.target.closest("select[data-api]");
  if (s) send(s.dataset.api, Object.assign(JSON.parse(s.dataset.body || "{}"), {[s.name]: s.value}));
});
