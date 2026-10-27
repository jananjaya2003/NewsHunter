"use strict";

/* News Hunter monitor page: edition calendar, background checks, filtering,
   per-clipping crop/resize and bulk ZIP/PDF/CSV downloads. No dependencies,
   because the content security policy only allows same-origin scripts. */

const MONTHS = ["January", "February", "March", "April", "May", "June", "July",
  "August", "September", "October", "November", "December"];
const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function $(selector, root = document) { return root.querySelector(selector); }
function $$(selector, root = document) { return Array.from(root.querySelectorAll(selector)); }
function el(tag, attrs = {}, text = "") {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === false || value === null || value === undefined) continue;
    node.setAttribute(key, value === true ? "" : value);
  }
  if (text) node.textContent = text;
  return node;
}
function isoDate(year, month, day) {
  return `${year}-${String(month + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}
function longDate(iso) {
  const [year, month, day] = iso.split("-").map(Number);
  const value = new Date(Date.UTC(year, month - 1, day));
  return value.toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });
}
function slug(text, length = 48) {
  return (text || "item").normalize("NFKD").replace(/[^\w\s-]/g, "").trim()
    .replace(/[\s_]+/g, "-").replace(/-+/g, "-").slice(0, length).replace(/-$/, "").toLowerCase() || "item";
}
function storageGet(key, fallback) {
  try { const value = localStorage.getItem(key); return value ? JSON.parse(value) : fallback; } catch { return fallback; }
}
function storageSet(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode: settings stay for this visit */ }
}
function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = el("a", { href: url, download: filename });
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
}
function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/* ---------- Calendar and checking ---------- */

function setupFinder() {
  const finder = $(".finder");
  if (!finder) return;
  const publication = finder.dataset.publication;
  const maxDate = finder.dataset.maxDate;
  const dateInput = $("#edition-date");
  const status = $("#date-status");
  const calendar = $("#calendar");
  const form = $("#check-form");
  const start = finder.dataset.selectedDate || finder.dataset.today;
  let [viewYear, viewMonth] = start.split("-").map(Number);
  viewMonth -= 1;
  const monthCache = new Map();
  // The online site cannot list the publisher's editions, so every recent date is
  // selectable there; Check now runs online or leaves a request for the office PC.
  const remote = form.dataset.remote === "1";

  function selectable(data, value) {
    if (!remote) return data.dates.includes(value);
    return value <= maxDate && (!data.oldest || value >= data.oldest);
  }

  function describeRemote(data, value) {
    const saved = data.checked[value];
    const request = (data.requested || {})[value];
    if (!selectable(data, value)) {
      status.classList.add("bad");
      status.textContent = value > maxDate
        ? `${longDate(value)} is not published yet.`
        : `${longDate(value)} is older than the online site keeps; check it in the local app on the office PC.`;
      return;
    }
    if (saved) {
      status.classList.add("good");
      status.textContent = `${longDate(value)} · saved result: `;
      const link = el("a", { href: `/monitor?publication=${publication}&edition_date=${value}` },
        `${saved.count} items, ${saved.tenders} medical tender${saved.tenders === 1 ? "" : "s"}`);
      status.append(link, saved.complete ? "." : " (incomplete: some items still need review; press Check now to re-check).");
      return;
    }
    if (request && request.status === "failed") {
      status.classList.add("bad");
      status.textContent = `${longDate(value)} · the requested check failed: ${request.error} This edition still needs review. Press Check now to try again.`;
      return;
    }
    if (request) {
      const when = request.requested_at ? ` at ${request.requested_at.slice(11, 16)} on ${longDate(request.requested_at.slice(0, 10))}` : "";
      status.textContent = `${longDate(value)} · check requested${when}. Waiting for the office PC to run it and upload the result.`;
      return;
    }
    status.textContent = `${longDate(value)} · not checked yet. Press Check now.`;
    const weekday = new Date(`${value}T00:00:00Z`).getUTCDay();
    if (publication === "daily-news" && weekday === 0) status.textContent += " (The Daily News is not usually published on Sundays.)";
    if (publication === "sunday-observer" && weekday !== 0) status.textContent += " (The Sunday Observer is published on Sundays.)";
  }

  function describeSelection() {
    const value = dateInput.value;
    status.classList.remove("bad", "good");
    if (!value) {
      status.textContent = remote ? "Pick a date in the calendar, then press Check now." : "Leave the date empty to check the latest edition.";
      return;
    }
    const data = monthCache.get(value.slice(0, 7));
    if (!data) { status.textContent = longDate(value); return; }
    if (remote) { describeRemote(data, value); return; }
    const saved = data.checked[value];
    if (!data.dates.includes(value)) {
      status.classList.add("bad");
      const earlier = data.dates.filter((day) => day < value).pop();
      status.textContent = `No edition was published on ${longDate(value)}.` +
        (earlier ? ` Nearest earlier edition: ${longDate(earlier)}.` : "");
      return;
    }
    status.classList.add("good");
    status.textContent = `${longDate(value)} · edition available.`;
    if (value > data.today) status.textContent += " Early upload: the publisher may still be adding pages.";
    if (saved) {
      status.append(" Saved result: ");
      const link = el("a", { href: `/monitor?publication=${publication}&edition_date=${value}` },
        `${saved.count} items, ${saved.tenders} medical tender${saved.tenders === 1 ? "" : "s"}`);
      status.append(link, saved.complete ? "." : " (incomplete, re-check recommended).");
    }
  }

  async function loadMonth(year, month) {
    const key = `${year}-${String(month + 1).padStart(2, "0")}`;
    if (monthCache.has(key)) return monthCache.get(key);
    const response = await fetch(`/api/availability?publication=${publication}&month=${key}`, { credentials: "same-origin" });
    if (!response.ok) {
      const detail = await response.json().catch(() => ({}));
      throw new Error(detail.detail ? `Calendar unavailable: ${detail.detail}` : `Calendar unavailable (HTTP ${response.status})`);
    }
    const data = await response.json();
    monthCache.set(key, data);
    return data;
  }

  async function render() {
    calendar.replaceChildren(el("p", { class: "calendar-loading" },
      `Checking which ${MONTHS[viewMonth]} ${viewYear} dates have an edition…`));
    let data;
    try {
      data = await loadMonth(viewYear, viewMonth);
    } catch (error) {
      calendar.replaceChildren(el("p", { class: "calendar-loading bad" },
        `${error.message.replace(/\.$/, "")}. You can still type a date in the box.`));
      return;
    }
    const header = el("div", { class: "calendar-head" });
    const previous = el("button", { type: "button", class: "secondary small", "aria-label": "Previous month" }, "‹");
    const next = el("button", { type: "button", class: "secondary small", "aria-label": "Next month" }, "›");
    const lastAllowed = maxDate.slice(0, 7);
    const viewKey = `${viewYear}-${String(viewMonth + 1).padStart(2, "0")}`;
    next.disabled = viewKey >= lastAllowed;
    previous.addEventListener("click", () => { viewMonth -= 1; if (viewMonth < 0) { viewMonth = 11; viewYear -= 1; } render(); });
    next.addEventListener("click", () => { viewMonth += 1; if (viewMonth > 11) { viewMonth = 0; viewYear += 1; } render(); });
    header.append(previous, el("strong", {}, `${MONTHS[viewMonth]} ${viewYear}`), next);

    const grid = el("div", { class: "calendar-grid", role: "grid" });
    WEEKDAYS.forEach((name) => grid.append(el("span", { class: "weekday", role: "columnheader" }, name)));
    const firstWeekday = (new Date(Date.UTC(viewYear, viewMonth, 1)).getUTCDay() + 6) % 7;
    for (let blank = 0; blank < firstWeekday; blank += 1) grid.append(el("span", { class: "day blank" }));
    const daysInMonth = new Date(Date.UTC(viewYear, viewMonth + 1, 0)).getUTCDate();
    for (let day = 1; day <= daysInMonth; day += 1) {
      const value = isoDate(viewYear, viewMonth, day);
      const saved = data.checked[value];
      const request = !saved && (data.requested || {})[value];
      if (!selectable(data, value)) {
        const title = value > maxDate ? "Not published yet" : remote ? "Older than the online site keeps" : "No edition";
        grid.append(el("span", { class: "day none", title, role: "gridcell" }, String(day)));
        continue;
      }
      const state = saved ? " checked" : request ? (request.status === "failed" ? " request-failed" : " requested") : "";
      const spoken = saved ? `, already checked, ${saved.count} items`
        : request ? (request.status === "failed" ? ", requested check failed" : ", check requested")
        : remote ? ", not checked yet" : ", edition available";
      const button = el("button", {
        type: "button",
        class: `day available${state}${value === dateInput.value ? " selected" : ""}`,
        "data-date": value,
        "aria-label": `${longDate(value)}${spoken}`,
        "aria-pressed": value === dateInput.value ? "true" : "false",
      }, String(day));
      if (saved && saved.tenders) button.append(el("small", { class: "tender-dot", "aria-hidden": "true" }, String(saved.tenders)));
      button.addEventListener("click", () => {
        if (remote && saved) {
          window.location.assign(`/monitor?publication=${publication}&edition_date=${value}`);
          return;
        }
        dateInput.value = value;
        $$(".day.selected", grid).forEach((node) => { node.classList.remove("selected"); node.setAttribute("aria-pressed", "false"); });
        button.classList.add("selected");
        button.setAttribute("aria-pressed", "true");
        describeSelection();
      });
      grid.append(button);
    }
    calendar.replaceChildren(header, grid);
    describeSelection();
  }

  dateInput.addEventListener("change", async () => {
    if (dateInput.value) {
      const [year, month] = dateInput.value.split("-").map(Number);
      if (year !== viewYear || month - 1 !== viewMonth) { viewYear = year; viewMonth = month - 1; }
    }
    await render();
  });

  // Keep the chosen date when switching newspaper tabs.
  $$(".paper-tab").forEach((tab) => tab.addEventListener("click", (event) => {
    if (!dateInput.value) return;
    event.preventDefault();
    window.location.assign(`/monitor?publication=${tab.dataset.publicationKey}&requested_date=${dateInput.value}`);
  }));

  render();
  setupCheck(form, () => { monthCache.clear(); render(); });
}

function setupCheck(form, onQueued) {
  const panel = $("#check-progress");
  const bar = $("#progress-bar");
  const track = $(".progress-track");
  const message = $("#progress-message");
  const title = $("#progress-title");
  const timer = $("#progress-time");
  const buttons = $$("button[type='submit']", form);

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (form.dataset.remote === "1") { remoteCheck(); return; }
    const latest = event.submitter && event.submitter.id === "check-latest";
    const body = new FormData(form);
    if (latest) body.set("edition_date", "");
    buttons.forEach((button) => { button.disabled = true; });
    panel.hidden = false;
    panel.classList.remove("failed");
    const requested = body.get("edition_date");
    title.textContent = requested ? `Checking ${longDate(requested)}…` : "Checking the latest edition…";
    message.textContent = "Starting…";
    const fail = (text) => {
      panel.classList.add("failed");
      title.textContent = "Check incomplete";
      message.setAttribute("role", "alert");
      message.textContent = `${text} No new result was saved; the newspaper still needs review.`;
      buttons.forEach((button) => { button.disabled = false; });
    };
    let job;
    try {
      const response = await fetch("/api/check", { method: "POST", body, credentials: "same-origin" });
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(detail.detail || `The check could not start (HTTP ${response.status}).`);
      }
      job = await response.json();
    } catch (error) {
      fail(error.message);
      return;
    }
    const stages = $$("#progress-steps li");
    const poll = async () => {
      let data;
      try {
        const response = await fetch(`/api/jobs/${job.id}`, { credentials: "same-origin" });
        if (!response.ok) throw new Error(`Progress unavailable (HTTP ${response.status}).`);
        data = await response.json();
      } catch (error) {
        fail(error.message);
        return;
      }
      timer.textContent = `${data.elapsed_seconds}s`;
      message.textContent = data.message;
      const stageShare = data.stage_index >= 0 ? data.stage_index / data.stage_count : 0;
      const within = data.total ? data.done / data.total / data.stage_count : 0;
      const percent = data.status === "done" ? 100 : Math.min(99, Math.round((stageShare + within) * 100));
      bar.style.width = `${percent}%`;
      track.setAttribute("aria-valuenow", String(percent));
      stages.forEach((item, index) => {
        item.classList.toggle("done", index < data.stage_index || data.status === "done");
        item.classList.toggle("current", index === data.stage_index && data.status === "running");
      });
      if (data.status === "done") {
        title.textContent = "Check complete. Opening results…";
        window.location.assign(data.result_url);
        return;
      }
      if (data.status === "failed") { fail(data.error); return; }
      window.setTimeout(poll, 1200);
    };
    poll();
  });

  // Online site: one request that either finishes the check or queues it for the office PC.
  async function remoteCheck() {
    const body = new FormData(form);
    const requested = body.get("edition_date");
    if (!requested) {
      $("#date-status").textContent = "Pick a date in the calendar first.";
      return;
    }
    buttons.forEach((button) => { button.disabled = true; });
    panel.hidden = false;
    panel.classList.remove("failed", "queued");
    message.removeAttribute("role");
    $("#progress-steps").hidden = true;
    title.textContent = `Checking ${longDate(requested)}…`;
    message.textContent = "Contacting the newspaper's website. A full check can take up to 5 minutes; please keep this page open.";
    bar.style.width = "15%";
    const started = Date.now();
    const tick = window.setInterval(() => { timer.textContent = `${Math.round((Date.now() - started) / 1000)}s`; }, 1000);
    try {
      const response = await fetch("/api/check", { method: "POST", body, credentials: "same-origin" });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || `The check could not run (HTTP ${response.status}).`);
      if (data.status === "done") {
        bar.style.width = "100%";
        title.textContent = "Check complete. Opening results…";
        window.location.assign(data.result_url);
        return;
      }
      panel.classList.add("queued");
      bar.style.width = "100%";
      title.textContent = "Check requested";
      message.textContent = data.message;
      onQueued();
    } catch (error) {
      panel.classList.add("failed");
      title.textContent = "Check not done";
      message.setAttribute("role", "alert");
      message.textContent = `${error.message} No result was saved for this date.`;
    } finally {
      window.clearInterval(tick);
      buttons.forEach((button) => { button.disabled = false; });
    }
  }
}

/* ---------- Results: filters and selection ---------- */

function setupResults() {
  const dataNode = $("#edition-data");
  if (!dataNode) return null;
  const edition = JSON.parse(dataNode.textContent);
  const itemsByUid = new Map(edition.items.map((item) => [item.uid, item]));
  const cards = $$(".result-card");
  const tiles = $$(".category-tile");
  const search = $("#result-search");
  const reviewOnly = $("#review-only");
  const countLabel = $("#selected-count");
  const noVisible = $("#no-visible");
  const downloadButtons = ["#download-zip", "#download-pdf", "#download-csv"].map((id) => $(id));
  const filterKey = `nh-filters-${edition.publication}`;
  const saved = storageGet(filterKey, null);
  if (saved && Array.isArray(saved.on)) {
    tiles.forEach((tile) => {
      const on = saved.on.includes(tile.dataset.category);
      tile.classList.toggle("on", on);
      tile.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }

  // "Viewed" ticks are remembered in this browser, per edition.
  const viewedKey = `nh-viewed-${edition.publication}-${edition.edition_date}`;
  const viewed = new Set(storageGet(viewedKey, []));
  const unviewedOnly = $("#unviewed-only");
  const resultCount = $("#result-count");

  function setViewed(uid, on) {
    if (on) viewed.add(uid); else viewed.delete(uid);
    storageSet(viewedKey, Array.from(viewed));
    const card = document.getElementById(`item-${uid}`);
    if (card) {
      card.classList.toggle("is-viewed", on);
      $(".viewed-badge", card).hidden = !on;
    }
  }
  cards.forEach((card) => setViewed(card.dataset.uid, viewed.has(card.dataset.uid)));

  function activeCategories() { return new Set(tiles.filter((tile) => tile.classList.contains("on")).map((tile) => tile.dataset.category)); }

  function applyFilters() {
    const active = activeCategories();
    const query = search.value.trim().toLowerCase();
    let shown = 0;
    cards.forEach((card) => {
      const categories = card.dataset.categories.split(" ");
      const visible = categories.some((key) => active.has(key))
        && (!reviewOnly.checked || card.dataset.review === "1")
        && (!unviewedOnly.checked || !viewed.has(card.dataset.uid))
        && (!query || card.dataset.search.includes(query));
      card.hidden = !visible;
      if (visible) shown += 1;
    });
    if (noVisible) noVisible.hidden = shown > 0 || !cards.length;
    const total = Number(resultCount.dataset.total);
    resultCount.textContent = shown === total ? `${total} item${total === 1 ? "" : "s"}` : `Showing ${shown} of ${total}`;
    updateSelection();
  }

  function setCategories(on) {
    tiles.forEach((tile) => { tile.classList.toggle("on", on); tile.setAttribute("aria-pressed", on ? "true" : "false"); });
    storageSet(filterKey, { on: Array.from(activeCategories()) });
    applyFilters();
  }
  $("#categories-all").addEventListener("click", () => setCategories(true));
  $("#categories-none").addEventListener("click", () => setCategories(false));
  unviewedOnly.addEventListener("change", applyFilters);

  function selectedUids() { return $$(".item-select:checked").map((box) => box.value); }
  function updateSelection() {
    const count = selectedUids().length;
    countLabel.textContent = `${count} selected`;
    downloadButtons.forEach((button) => { if (button) button.disabled = count === 0; });
    cards.forEach((card) => card.classList.toggle("is-selected", $(".item-select", card).checked));
  }

  tiles.forEach((tile) => tile.addEventListener("click", () => {
    const on = !tile.classList.contains("on");
    tile.classList.toggle("on", on);
    tile.setAttribute("aria-pressed", on ? "true" : "false");
    storageSet(filterKey, { on: Array.from(activeCategories()) });
    applyFilters();
  }));
  search.addEventListener("input", applyFilters);
  reviewOnly.addEventListener("change", applyFilters);
  $$(".item-select").forEach((box) => box.addEventListener("change", updateSelection));
  $("#select-visible").addEventListener("click", () => {
    cards.forEach((card) => { if (!card.hidden) $(".item-select", card).checked = true; });
    updateSelection();
  });
  $("#select-none").addEventListener("click", () => { $$(".item-select").forEach((box) => { box.checked = false; }); updateSelection(); });
  applyFilters();

  $$(".article-thumb").forEach((image) => image.addEventListener("error", () => {
    const notice = el("p", { class: "notice danger thumb-missing", role: "alert" },
      "Image could not load. This item still needs review; use Open in e-paper.");
    image.closest(".thumb-button").replaceWith(notice);
  }, { once: true }));

  return { edition, itemsByUid, selectedUids, cards, setViewed, applyFilters };
}

/* ---------- Article viewer ---------- */

function setupViewer(results, editor) {
  const dialog = $("#viewer");
  if (!results || !dialog) return;
  const { itemsByUid, cards, setViewed } = results;
  const image = $("#viewer-image");
  const imageWrap = $("#viewer-image-wrap");
  const noImage = $("#viewer-no-image");
  const zoom = $("#viewer-zoom");
  let current = null;

  // Previous/next follow the cards currently shown by the filters.
  function shownUids() { return cards.filter((card) => !card.hidden).map((card) => card.dataset.uid); }

  function fact(label, value) {
    const line = el("p", { class: "fact" });
    line.append(el("strong", {}, `${label}: `), value);
    return line;
  }

  function show(uid) {
    const item = itemsByUid.get(uid);
    const card = document.getElementById(`item-${uid}`);
    if (!item || !card) return;
    current = uid;
    $("#viewer-title").textContent = item.title || "Untitled item";
    $("#viewer-meta").textContent = `${results.edition.publication_name.toUpperCase()} · ${longDate(results.edition.edition_date).toUpperCase()} · PAGE ${item.page_number} · ${item.kind === "advert" ? "ADVERT / NOTICE" : "NEWS ARTICLE"}`;
    $("#viewer-chips").replaceChildren(...$$(".chip-row .chip", card).map((chip) => chip.cloneNode(true)));
    const facts = [];
    if (item.institutions && item.institutions.length) facts.push(fact("Institution", item.institutions.join(" · ")));
    if (item.dates_mentioned && item.dates_mentioned.length) facts.push(fact("Dates in text (verify)", item.dates_mentioned.join(" · ")));
    facts.push(fact("Review", item.review_required ? "Needs human review" : "AI-confirmed (still verify before acting)"));
    $("#viewer-facts").replaceChildren(...facts);
    $("#viewer-reason").textContent = item.match_reason || "No reason recorded.";
    const text = item.text || item.excerpt || "";
    $("#viewer-text").textContent = text || "No machine-readable text was saved for this item; read it from the image or the e-paper.";
    $("#viewer-source").href = item.source_url || "#";
    $("#viewer-edit").hidden = !item.image_web_url;
    if (item.image_web_url) {
      noImage.hidden = true;
      image.hidden = false;
      image.alt = `Newspaper image: ${item.title}`;
      image.src = item.image_web_url;
    } else {
      image.hidden = true;
      image.removeAttribute("src");
      noImage.hidden = false;
    }
    imageWrap.scrollTo(0, 0);
    const order = shownUids();
    const position = order.indexOf(uid);
    $("#viewer-position").textContent = position >= 0 ? `${position + 1} of ${order.length}` : "";
    $("#viewer-prev").disabled = position <= 0;
    $("#viewer-next").disabled = position < 0 || position >= order.length - 1;
    setViewed(uid, true);
    if (!dialog.open) dialog.showModal();
  }

  function step(offset) {
    const order = shownUids();
    const next = order[order.indexOf(current) + offset];
    if (next) show(next);
  }

  image.addEventListener("error", () => {
    if (!image.getAttribute("src")) return;
    image.hidden = true;
    noImage.hidden = false;
  });
  zoom.addEventListener("change", () => imageWrap.classList.toggle("actual", zoom.checked));
  $("#viewer-prev").addEventListener("click", () => step(-1));
  $("#viewer-next").addEventListener("click", () => step(1));
  $("#viewer-close").addEventListener("click", () => dialog.close());
  $("#viewer-unview").addEventListener("click", () => { if (current) setViewed(current, false); dialog.close(); });
  $("#viewer-edit").addEventListener("click", () => { dialog.close(); if (editor && current) editor.open(current); });
  dialog.addEventListener("keydown", (event) => {
    if (event.key === "ArrowRight") step(1);
    if (event.key === "ArrowLeft") step(-1);
  });
  // Re-apply "Not viewed yet" when the viewer closes, not while paging through it.
  dialog.addEventListener("close", () => results.applyFilters());

  document.addEventListener("click", (event) => {
    const trigger = event.target.closest("[data-action='view']");
    const card = trigger && trigger.closest(".result-card");
    if (card) show(card.dataset.uid);
  });
}

/* ---------- Image rendering ---------- */

const imageCache = new Map();
function loadImage(url) {
  if (!imageCache.has(url)) {
    imageCache.set(url, new Promise((resolve, reject) => {
      const image = new Image();
      image.decoding = "async";
      image.onload = () => resolve(image);
      image.onerror = () => { imageCache.delete(url); reject(new Error("The full-size image could not be loaded.")); };
      image.src = url;
    }));
  }
  return imageCache.get(url);
}

function renderClip(image, settings) {
  const crop = settings.crop || { x: 0, y: 0, w: image.naturalWidth, h: image.naturalHeight };
  const targetWidth = settings.width && settings.width < crop.w * 4 ? settings.width : Math.round(crop.w);
  const scale = targetWidth / crop.w;
  const canvas = document.createElement("canvas");
  const clipWidth = Math.max(1, Math.round(crop.w * scale));
  const clipHeight = Math.max(1, Math.round(crop.h * scale));
  // The date label sits in a strip below the clipping so it never hides its text.
  const fontSize = Math.max(16, Math.round(clipWidth * 0.024));
  const strip = settings.stamp ? Math.round(fontSize * 1.9) : 0;
  canvas.width = clipWidth;
  canvas.height = clipHeight + strip;
  const context = canvas.getContext("2d");
  context.fillStyle = "#ffffff";
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.imageSmoothingQuality = "high";
  context.drawImage(image, crop.x, crop.y, crop.w, crop.h, 0, 0, clipWidth, clipHeight);
  if (strip) {
    context.fillStyle = "#d9ddd8";
    context.fillRect(0, clipHeight, clipWidth, Math.max(1, Math.round(fontSize / 12)));
    context.fillStyle = "#151a18";
    context.font = `700 ${fontSize}px Arial, sans-serif`;
    context.textAlign = "right";
    context.textBaseline = "middle";
    const padding = Math.round(fontSize * 0.7);
    let text = settings.stamp;
    while (text.length > 12 && context.measureText(text).width > clipWidth - padding * 2) text = `${text.slice(0, -2)}…`;
    context.fillText(text, clipWidth - padding, clipHeight + strip / 2);
  }
  return canvas;
}

function canvasBlob(canvas, format, quality) {
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error("The image could not be encoded."))),
      format === "png" ? "image/png" : "image/jpeg", quality / 100);
  });
}

function trimMargins(image, crop) {
  // Find the bounding box of non-white pixels inside the current crop.
  const sample = Math.min(1, 900 / Math.max(crop.w, crop.h));
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(crop.w * sample));
  canvas.height = Math.max(1, Math.round(crop.h * sample));
  const context = canvas.getContext("2d", { willReadFrequently: true });
  context.drawImage(image, crop.x, crop.y, crop.w, crop.h, 0, 0, canvas.width, canvas.height);
  const { data, width, height } = context.getImageData(0, 0, canvas.width, canvas.height);
  let top = height, left = width, right = -1, bottom = -1;
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const offset = (y * width + x) * 4;
      if (data[offset] < 225 || data[offset + 1] < 225 || data[offset + 2] < 225) {
        if (x < left) left = x;
        if (x > right) right = x;
        if (y < top) top = y;
        if (y > bottom) bottom = y;
      }
    }
  }
  if (right < 0) return crop;
  const pad = 6;
  const x = Math.max(0, crop.x + left / sample - pad);
  const y = Math.max(0, crop.y + top / sample - pad);
  return {
    x, y,
    w: Math.min(image.naturalWidth - x, (right - left + 1) / sample + pad * 2),
    h: Math.min(image.naturalHeight - y, (bottom - top + 1) / sample + pad * 2),
  };
}

/* ---------- Clipping editor ---------- */

function setupEditor(results) {
  const dialog = $("#editor");
  if (!dialog || !results) return null;
  const { edition, itemsByUid } = results;
  const settingsKey = `nh-clip-${edition.publication}-${edition.edition_date}`;
  const clipSettings = storageGet(settingsKey, {});
  const stage = $("#crop-stage");
  const canvas = $("#editor-canvas");
  const box = $("#crop-box");
  const loading = $("#editor-loading");
  const size = $("#edit-size");
  const custom = $("#edit-custom");
  const customLabel = $("#custom-width-label");
  const format = $("#edit-format");
  const quality = $("#edit-quality");
  const qualityValue = $("#quality-value");
  const qualityLabel = $("#quality-label");
  const info = $("#output-info");
  let current = null;
  let image = null;
  let crop = null;
  let displayScale = 1;
  let infoTimer = 0;
  // One "date label" choice for every download, remembered in this browser.
  const stampBoxes = ["#bulk-stamp", "#edit-stamp"].map((id) => $(id)).filter(Boolean);
  let stampOn = storageGet("nh-stamp", true) !== false;
  stampBoxes.forEach((box) => {
    box.checked = stampOn;
    box.addEventListener("change", () => {
      stampOn = box.checked;
      stampBoxes.forEach((other) => { other.checked = stampOn; });
      storageSet("nh-stamp", stampOn);
      if (dialog.open && image) scheduleInfo();
    });
  });
  function stampFor(uid) {
    const item = itemsByUid.get(uid);
    if (!stampOn || !item) return "";
    const page = item.page_number ? ` · Page ${item.page_number}` : "";
    return `${edition.publication_name} · ${longDate(edition.edition_date)}${page}`;
  }

  function chosenWidth() {
    if (size.value === "custom") return Math.max(200, Math.min(6000, Number(custom.value) || 1200));
    return Number(size.value);
  }
  function editorSettings() {
    return { crop: { ...crop }, width: chosenWidth(), format: format.value, quality: Number(quality.value), stamp: stampFor(current) };
  }

  function drawStage() {
    const maxWidth = stage.clientWidth || 600;
    const maxHeight = Math.max(320, window.innerHeight * 0.68);
    displayScale = Math.min(maxWidth / image.naturalWidth, maxHeight / image.naturalHeight, 1);
    canvas.width = Math.round(image.naturalWidth * displayScale);
    canvas.height = Math.round(image.naturalHeight * displayScale);
    canvas.getContext("2d").drawImage(image, 0, 0, canvas.width, canvas.height);
    placeBox();
  }
  function placeBox() {
    box.style.left = `${canvas.offsetLeft + crop.x * displayScale}px`;
    box.style.top = `${canvas.offsetTop + crop.y * displayScale}px`;
    box.style.width = `${crop.w * displayScale}px`;
    box.style.height = `${crop.h * displayScale}px`;
    scheduleInfo();
  }
  function clampCrop(next) {
    const minimum = 40;
    next.w = Math.max(minimum, Math.min(next.w, image.naturalWidth));
    next.h = Math.max(minimum, Math.min(next.h, image.naturalHeight));
    next.x = Math.max(0, Math.min(next.x, image.naturalWidth - next.w));
    next.y = Math.max(0, Math.min(next.y, image.naturalHeight - next.h));
    return next;
  }
  function scheduleInfo() {
    window.clearTimeout(infoTimer);
    const settings = editorSettings();
    const width = settings.width && settings.width < crop.w * 4 ? settings.width : Math.round(crop.w);
    const height = Math.round(crop.h * (width / crop.w));
    const label = settings.stamp ? ` · label: ${settings.stamp}` : "";
    info.textContent = `${width} × ${height} px${label} · estimating file size…`;
    infoTimer = window.setTimeout(async () => {
      try {
        const blob = await canvasBlob(renderClip(image, settings), settings.format, settings.quality);
        info.textContent = `${width} × ${height} px · ${formatBytes(blob.size)} ${settings.format.toUpperCase()}${label}`;
      } catch (error) { info.textContent = error.message; }
    }, 350);
  }

  // Dragging: move the frame, or resize it with a handle.
  let drag = null;
  box.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    box.setPointerCapture(event.pointerId);
    drag = { handle: event.target.dataset.handle || "move", x: event.clientX, y: event.clientY, start: { ...crop } };
  });
  box.addEventListener("pointermove", (event) => {
    if (!drag) return;
    const dx = (event.clientX - drag.x) / displayScale;
    const dy = (event.clientY - drag.y) / displayScale;
    const next = { ...drag.start };
    const handle = drag.handle;
    if (handle === "move") { next.x += dx; next.y += dy; }
    if (handle.includes("w")) { next.x = Math.min(drag.start.x + dx, drag.start.x + drag.start.w - 40); next.w = drag.start.w - (next.x - drag.start.x); }
    if (handle.includes("e")) next.w = drag.start.w + dx;
    if (handle.includes("n")) { next.y = Math.min(drag.start.y + dy, drag.start.y + drag.start.h - 40); next.h = drag.start.h - (next.y - drag.start.y); }
    if (handle.includes("s")) next.h = drag.start.h + dy;
    if (next.x < 0) { if (handle !== "move") next.w += next.x; next.x = 0; }
    if (next.y < 0) { if (handle !== "move") next.h += next.y; next.y = 0; }
    if (handle !== "move") {
      next.w = Math.min(next.w, image.naturalWidth - next.x);
      next.h = Math.min(next.h, image.naturalHeight - next.y);
    }
    crop = clampCrop(next);
    placeBox();
  });
  const endDrag = () => { drag = null; };
  box.addEventListener("pointerup", endDrag);
  box.addEventListener("pointercancel", endDrag);
  box.addEventListener("keydown", (event) => {
    const step = (event.shiftKey ? 50 : 10) / displayScale;
    const moves = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
    if (!moves[event.key]) return;
    event.preventDefault();
    crop = clampCrop({ ...crop, x: crop.x + moves[event.key][0], y: crop.y + moves[event.key][1] });
    placeBox();
  });

  size.addEventListener("change", () => { customLabel.hidden = size.value !== "custom"; scheduleInfo(); });
  custom.addEventListener("input", scheduleInfo);
  format.addEventListener("change", () => { qualityLabel.hidden = format.value === "png"; scheduleInfo(); });
  quality.addEventListener("input", () => { qualityValue.textContent = quality.value; scheduleInfo(); });
  $("#crop-reset").addEventListener("click", () => { crop = { x: 0, y: 0, w: image.naturalWidth, h: image.naturalHeight }; placeBox(); });
  $("#crop-trim").addEventListener("click", () => { crop = clampCrop(trimMargins(image, crop)); placeBox(); });
  window.addEventListener("resize", () => { if (dialog.open && image) drawStage(); });

  $("#edit-download").addEventListener("click", async () => {
    const settings = editorSettings();
    const blob = await canvasBlob(renderClip(image, settings), settings.format, settings.quality);
    saveBlob(blob, clipFilename(edition, current, settings.format));
  });
  $("#edit-save").addEventListener("click", () => {
    clipSettings[current.uid] = editorSettings();
    storageSet(settingsKey, clipSettings);
    const card = document.getElementById(`item-${current.uid}`);
    if (card) {
      card.classList.add("has-settings");
      const box = $(".item-select", card);
      box.checked = true;
      box.dispatchEvent(new Event("change"));
    }
    info.textContent = "Saved. This clipping is selected and bulk downloads will use these settings.";
  });

  function setControl(select, value) {
    const option = Array.from(select.options).find((item) => item.value === String(value));
    if (option) { select.value = option.value; return true; }
    return false;
  }

  async function open(uid) {
    current = itemsByUid.get(uid);
    if (!current || !current.image_web_url) return;
    $("#editor-title").textContent = `Page ${current.page_number} · ${current.title}`;
    loading.hidden = false;
    box.hidden = true;
    canvas.width = 0;
    if (!dialog.open) dialog.showModal();
    try {
      image = await loadImage(current.image_web_url);
    } catch (error) {
      loading.textContent = `${error.message} Use Open in e-paper.`;
      return;
    }
    const savedSettings = clipSettings[uid];
    crop = savedSettings ? clampCrop({ ...savedSettings.crop }) : { x: 0, y: 0, w: image.naturalWidth, h: image.naturalHeight };
    const width = savedSettings ? savedSettings.width : Number($("#bulk-size").value);
    if (!setControl(size, width)) { size.value = "custom"; custom.value = width; }
    customLabel.hidden = size.value !== "custom";
    format.value = savedSettings ? savedSettings.format : $("#bulk-format").value;
    quality.value = savedSettings ? savedSettings.quality : 88;
    qualityValue.textContent = quality.value;
    qualityLabel.hidden = format.value === "png";
    loading.hidden = true;
    box.hidden = false;
    drawStage();
    box.focus({ preventScroll: true });
  }

  document.addEventListener("click", (event) => {
    const trigger = event.target.closest("[data-action]");
    if (!trigger) return;
    const card = trigger.closest(".result-card");
    if (!card) return;
    if (trigger.dataset.action === "edit") open(card.dataset.uid);
    if (trigger.dataset.action === "quick") quickDownload(card.dataset.uid, trigger);
  });

  async function quickDownload(uid, button) {
    const item = itemsByUid.get(uid);
    button.disabled = true;
    try {
      const settings = bulkSettings(uid);
      const clip = await loadImage(item.image_web_url);
      const blob = await canvasBlob(renderClip(clip, settings), settings.format, settings.quality);
      saveBlob(blob, clipFilename(edition, item, settings.format));
    } catch (error) {
      button.textContent = "Failed – retry";
    } finally {
      button.disabled = false;
    }
  }

  function bulkSettings(uid) {
    const saved = clipSettings[uid] || { crop: null, width: Number($("#bulk-size").value), format: $("#bulk-format").value, quality: 88 };
    return { ...saved, stamp: stampFor(uid) };
  }
  for (const uid of Object.keys(clipSettings)) {
    const card = document.getElementById(`item-${uid}`);
    if (card) card.classList.add("has-settings");
  }
  return { bulkSettings, open };
}

function clipFilename(edition, item, format) {
  const paper = edition.publication === "sunday-observer" ? "SundayObserver" : "DailyNews";
  const category = (item.primary_category || "item").replace(/_/g, "-");
  return `${paper}_${edition.edition_date}_p${String(item.page_number).padStart(2, "0")}_${category}_${slug(item.title)}.${format === "png" ? "png" : "jpg"}`;
}

/* ---------- ZIP (stored) writer ---------- */

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n += 1) {
    let c = n;
    for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    table[n] = c >>> 0;
  }
  return table;
})();
function crc32(bytes) {
  let crc = 0xffffffff;
  for (let index = 0; index < bytes.length; index += 1) crc = CRC_TABLE[(crc ^ bytes[index]) & 0xff] ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}
function buildZip(files) {
  const encoder = new TextEncoder();
  const parts = [];
  const central = [];
  let offset = 0;
  const now = new Date();
  const dosTime = (now.getHours() << 11) | (now.getMinutes() << 5) | Math.floor(now.getSeconds() / 2);
  const dosDate = ((now.getFullYear() - 1980) << 9) | ((now.getMonth() + 1) << 5) | now.getDate();
  for (const file of files) {
    const name = encoder.encode(file.name);
    const crc = crc32(file.data);
    const local = new DataView(new ArrayBuffer(30));
    local.setUint32(0, 0x04034b50, true); local.setUint16(4, 20, true); local.setUint16(6, 0x0800, true);
    local.setUint16(8, 0, true); local.setUint16(10, dosTime, true); local.setUint16(12, dosDate, true);
    local.setUint32(14, crc, true); local.setUint32(18, file.data.length, true); local.setUint32(22, file.data.length, true);
    local.setUint16(26, name.length, true); local.setUint16(28, 0, true);
    parts.push(new Uint8Array(local.buffer), name, file.data);
    const entry = new DataView(new ArrayBuffer(46));
    entry.setUint32(0, 0x02014b50, true); entry.setUint16(4, 20, true); entry.setUint16(6, 20, true);
    entry.setUint16(8, 0x0800, true); entry.setUint16(10, 0, true); entry.setUint16(12, dosTime, true);
    entry.setUint16(14, dosDate, true); entry.setUint32(16, crc, true); entry.setUint32(20, file.data.length, true);
    entry.setUint32(24, file.data.length, true); entry.setUint16(28, name.length, true);
    entry.setUint32(42, offset, true);
    central.push(new Uint8Array(entry.buffer), name);
    offset += 30 + name.length + file.data.length;
  }
  const centralSize = central.reduce((total, part) => total + part.length, 0);
  const end = new DataView(new ArrayBuffer(22));
  end.setUint32(0, 0x06054b50, true); end.setUint16(8, files.length, true); end.setUint16(10, files.length, true);
  end.setUint32(12, centralSize, true); end.setUint32(16, offset, true);
  return new Blob([...parts, ...central, new Uint8Array(end.buffer)], { type: "application/zip" });
}

/* ---------- PDF writer (one clipping per A4 page) ---------- */

function pdfText(text) {
  return text.normalize("NFKD").replace(/[^\x20-\x7e]/g, "").replace(/([\\()])/g, "\\$1").slice(0, 110);
}
function buildPdf(pages) {
  const encoder = new TextEncoder();
  const chunks = [];
  const offsets = [];
  let length = 0;
  const push = (data) => { const bytes = typeof data === "string" ? encoder.encode(data) : data; chunks.push(bytes); length += bytes.length; };
  const object = (number, body) => { offsets[number] = length; push(`${number} 0 obj\n`); body(); push("\nendobj\n"); };
  push("%PDF-1.4\n%\xE2\xE3\xCF\xD3\n");
  const pageWidth = 595, pageHeight = 842, margin = 28, captionSpace = 34;
  const fontObject = 3;
  const kids = pages.map((_, index) => `${4 + index * 3} 0 R`).join(" ");
  object(1, () => push("<< /Type /Catalog /Pages 2 0 R >>"));
  object(2, () => push(`<< /Type /Pages /Kids [${kids}] /Count ${pages.length} >>`));
  object(fontObject, () => push("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"));
  pages.forEach((page, index) => {
    const pageObject = 4 + index * 3, contentObject = pageObject + 1, imageObject = pageObject + 2;
    const boxWidth = pageWidth - margin * 2, boxHeight = pageHeight - margin * 2 - captionSpace;
    const scale = Math.min(boxWidth / page.width, boxHeight / page.height);
    const drawWidth = page.width * scale, drawHeight = page.height * scale;
    const x = (pageWidth - drawWidth) / 2, y = margin + (boxHeight - drawHeight);
    const content = `BT /F1 9 Tf ${margin} ${pageHeight - margin - 10} Td (${pdfText(page.caption)}) Tj ET\n` +
      `BT /F1 7 Tf ${margin} ${pageHeight - margin - 22} Td (${pdfText(page.source)}) Tj ET\n` +
      `q ${drawWidth.toFixed(2)} 0 0 ${drawHeight.toFixed(2)} ${x.toFixed(2)} ${y.toFixed(2)} cm /Im${index} Do Q`;
    object(pageObject, () => push(`<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${pageWidth} ${pageHeight}] ` +
      `/Resources << /Font << /F1 ${fontObject} 0 R >> /XObject << /Im${index} ${imageObject} 0 R >> >> /Contents ${contentObject} 0 R >>`));
    object(contentObject, () => { push(`<< /Length ${encoder.encode(content).length} >>\nstream\n`); push(content); push("\nendstream"); });
    object(imageObject, () => {
      push(`<< /Type /XObject /Subtype /Image /Width ${page.width} /Height ${page.height} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length ${page.jpeg.length} >>\nstream\n`);
      push(page.jpeg);
      push("\nendstream");
    });
  });
  const total = 4 + pages.length * 3;
  const xref = length;
  push(`xref\n0 ${total}\n0000000000 65535 f \n`);
  for (let number = 1; number < total; number += 1) push(`${String(offsets[number]).padStart(10, "0")} 00000 n \n`);
  push(`trailer\n<< /Size ${total} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`);
  return new Blob(chunks, { type: "application/pdf" });
}

/* ---------- Bulk downloads ---------- */

function setupDownloads(results, editor) {
  if (!results || !editor) return;
  const { edition, itemsByUid, selectedUids } = results;
  const status = $("#toolbar-status");
  const categoryLabels = new Map($$(".category-tile").map((tile) => [tile.dataset.category, $(".tile-label", tile).textContent.trim()]));
  const buttons = ["#download-zip", "#download-pdf", "#download-csv"].map((id) => $(id));
  const base = `${edition.publication_name.replace(/\s+/g, "")}_${edition.edition_date}`;

  async function renderSelected(forcePdf) {
    const uids = selectedUids();
    const output = [];
    const failures = [];
    for (const [index, uid] of uids.entries()) {
      const item = itemsByUid.get(uid);
      status.textContent = `Preparing ${index + 1} of ${uids.length}: ${item.title.slice(0, 60)}…`;
      if (!item.image_web_url) { failures.push(item); continue; }
      try {
        const settings = editor.bulkSettings(uid);
        const image = await loadImage(item.image_web_url);
        const canvas = renderClip(image, settings);
        const format = forcePdf ? "jpeg" : settings.format;
        const blob = await canvasBlob(canvas, format, settings.quality);
        output.push({ item, canvas, format, data: new Uint8Array(await blob.arrayBuffer()) });
      } catch (error) {
        failures.push(item);
      }
    }
    return { output, failures };
  }
  function finish(failures, message) {
    status.textContent = message + (failures.length
      ? ` ${failures.length} item(s) had no downloadable image and were left out: ${failures.map((item) => `page ${item.page_number}`).join(", ")}. Review them in the e-paper.`
      : "");
    buttons.forEach((button) => { button.disabled = selectedUids().length === 0; });
  }
  function busy() { buttons.forEach((button) => { button.disabled = true; }); }

  $("#download-zip").addEventListener("click", async () => {
    busy();
    const { output, failures } = await renderSelected(false);
    if (output.length) {
      const used = new Set();
      const files = output.map(({ item, format, data }) => {
        let name = clipFilename(edition, item, format);
        for (let copy = 2; used.has(name); copy += 1) name = name.replace(/(\.\w+)$/, `-${copy}$1`);
        used.add(name);
        return { name, data };
      });
      saveBlob(buildZip(files), `${base}_clippings.zip`);
    }
    finish(failures, `ZIP ready with ${output.length} image(s).`);
  });

  $("#download-pdf").addEventListener("click", async () => {
    busy();
    const { output, failures } = await renderSelected(true);
    if (output.length) {
      const pages = output.map(({ item, canvas, data }) => ({
        width: canvas.width,
        height: canvas.height,
        jpeg: data,
        caption: `${edition.publication_name} ${edition.edition_date} - page ${item.page_number} - ${item.title}`,
        source: `Source: ${item.source_url}`,
      }));
      saveBlob(buildPdf(pages), `${base}_clippings.pdf`);
    }
    finish(failures, `PDF ready with ${output.length} page(s).`);
  });

  $("#download-csv").addEventListener("click", () => {
    const rows = [["Newspaper", "Edition date", "Page", "Type", "Categories", "Title", "Institutions", "Dates in text (verify)", "Needs review", "Why selected", "E-paper link", "Image file name"]];
    for (const uid of selectedUids()) {
      const item = itemsByUid.get(uid);
      const settings = editor.bulkSettings(uid);
      rows.push([
        edition.publication_name, edition.edition_date, item.page_number,
        item.kind === "advert" ? "Advert / notice" : "News article",
        item.categories.map((key) => categoryLabels.get(key) || key).join("; ") + (item.borderline ? " (possible, verify)" : ""),
        item.title, item.institutions.join("; "),
        item.dates_mentioned.join("; "), item.review_required ? "Yes" : "No",
        item.match_reason, item.source_url, clipFilename(edition, item, settings.format),
      ]);
    }
    const csv = rows.map((row) => row.map((value) => {
      let text = String(value ?? "");
      if (/^[=+\-@]/.test(text)) text = `'${text}`; // keep spreadsheet formulas inert
      return `"${text.replace(/"/g, '""')}"`;
    }).join(",")).join("\r\n");
    saveBlob(new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8" }), `${base}_items.csv`);
    status.textContent = `CSV list ready with ${rows.length - 1} row(s).`;
  });
}

document.addEventListener("DOMContentLoaded", () => {
  setupFinder();
  const results = setupResults();
  const editor = setupEditor(results);
  setupDownloads(results, editor);
  setupViewer(results, editor);
});
