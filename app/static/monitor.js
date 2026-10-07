"use strict";

document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll(".article-screenshot").forEach((image) => {
    image.addEventListener("error", () => {
      const notice = document.createElement("p");
      notice.className = "notice danger";
      notice.setAttribute("role", "alert");
      notice.textContent = "Article image could not load. This candidate still needs review; use Open in e-paper to view the original source.";
      image.closest("a").after(notice);
      image.hidden = true;
    }, { once: true });
  });
  const form = document.querySelector("[data-testid='check-form']");
  const progress = document.getElementById("check-progress");
  if (!form || !progress) return;

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = form.querySelector("button[type='submit']");
    if (button) button.disabled = true;
    progress.hidden = false;
    progress.setAttribute("role", "status");
    const started = Date.now();
    const update = () => {
      const elapsed = Math.floor((Date.now() - started) / 1000);
      progress.textContent = `Checking the newspaper and AI classifications (${elapsed}s). Results appear only after the complete check.`;
    };
    update();
    const ticker = window.setInterval(update, 1000);
    const controller = new AbortController();
    const deadline = window.setTimeout(() => controller.abort(), 290000);
    try {
      const response = await fetch(form.action, {
        method: "POST",
        body: new FormData(form),
        credentials: "same-origin",
        signal: controller.signal,
      });
      if (!response.ok) {
        throw new Error(response.status === 504
          ? "The server timed out. This check is incomplete; the newspaper still needs human review."
          : `The check failed (HTTP ${response.status}). No complete new result is confirmed.`);
      }
      if (!response.redirected) throw new Error("The server did not return a result page. Please retry.");
      window.location.assign(response.url);
    } catch (error) {
      progress.setAttribute("role", "alert");
      progress.textContent = error.name === "AbortError"
        ? "The check is taking too long. Completion is unconfirmed; the newspaper still needs human review. The server may still be processing."
        : error.message;
      if (button) button.disabled = false;
    } finally {
      window.clearInterval(ticker);
      window.clearTimeout(deadline);
    }
  });
});
