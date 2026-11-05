// Waitlist form. Frontend only for now: with no endpoint set, nothing is stored and the page says so.
// TODO(waitlist-backend): set WAITLIST_ENDPOINT once the Google Apps Script web app exists; remove demo mode then.
const WAITLIST_ENDPOINT = "";
const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function setStatus(form, text, isError) {
  const el = form.querySelector(".status");
  el.textContent = text;
  el.classList.toggle("error", Boolean(isError));
}

async function submitEmail(email) {
  if (!WAITLIST_ENDPOINT) return "demo";
  const res = await fetch(WAITLIST_ENDPOINT, {
    method: "POST",
    headers: { "Content-Type": "text/plain" },
    body: JSON.stringify({ email }),
  });
  if (!res.ok) throw new Error("bad status " + res.status);
  return "saved";
}

document.querySelectorAll("form.signup").forEach((form) => {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = form.querySelector('input[type="email"]');
    const email = input.value.trim();
    if (form.querySelector(".trap").value) return; // bots fill the hidden field
    if (!EMAIL_PATTERN.test(email)) {
      input.setAttribute("aria-invalid", "true");
      setStatus(form, "Enter an email address like you@work.com.", true);
      input.focus();
      return;
    }
    input.removeAttribute("aria-invalid");
    const button = form.querySelector("button");
    button.disabled = true;
    try {
      const result = await submitEmail(email);
      setStatus(form, result === "demo"
        ? "Preview only: signups are not connected yet, so nothing was saved."
        : "You're on the list. We'll email you when your invite is ready.");
      if (result === "saved") input.value = "";
    } catch (err) {
      setStatus(form, "That didn't go through. Check your connection and try again.", true);
    } finally {
      button.disabled = false;
    }
  });
});
