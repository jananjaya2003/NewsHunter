"use strict";

document.addEventListener("DOMContentLoaded", () => {
  const form = document.querySelector("[data-testid='check-form']");
  const progress = document.getElementById("check-progress");
  if (!form || !progress) {
    return;
  }

  form.addEventListener("submit", () => {
    const button = form.querySelector("button[type='submit']");
    if (button) {
      button.disabled = true;
    }
    progress.hidden = false;
  });
});
