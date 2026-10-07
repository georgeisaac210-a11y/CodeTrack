/* =========================================================================
   StudyPilot frontend — talks to the Flask API on :5000
   =========================================================================
   Auth model: passwordless email login.
     - POST /api/auth/request {email}         -> 6-digit code (demo: shown on screen)
     - POST /api/auth/verify  {email, code}   -> session token
     - token is kept in localStorage and sent as `Authorization: Bearer <token>`
       on every subsequent request.
   ========================================================================= */

const API_BASE = "http://127.0.0.1:5000/api";
const TOKEN_KEY = "studypilot_token";
const USER_KEY = "studypilot_user";

// --------------------------------------------------------------------------
// Token storage
// --------------------------------------------------------------------------

function getToken() {
  return localStorage.getItem(TOKEN_KEY);
}
function setSession(token, user) {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(USER_KEY, JSON.stringify(user || {}));
}
function clearSession() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
}
function getStoredUser() {
  try { return JSON.parse(localStorage.getItem(USER_KEY) || "{}"); }
  catch { return {}; }
}

// --------------------------------------------------------------------------
// Fetch helpers — all attach the bearer token; 401 kicks back to sign-in
// --------------------------------------------------------------------------

async function request(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  let res;
  try {
    res = await fetch(`${API_BASE}${path}`, { ...options, headers });
  } catch (networkErr) {
    throw new Error(
      "Could not reach the backend. Make sure the Flask server is running on http://127.0.0.1:5000."
    );
  }

  if (res.status === 401) {
    clearSession();
    showAuthScreen();
    throw new Error("Your session expired. Please sign in again.");
  }

  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { raw: text }; }

  if (!res.ok) {
    const msg = (data && (data.error || data.message)) || `Request failed (${res.status})`;
    throw new Error(msg);
  }
  return data;
}

const apiGet = (p) => request(p, { method: "GET" });
const apiPost = (p, body) => request(p, { method: "POST", body: JSON.stringify(body || {}) });
const apiPut = (p, body) => request(p, { method: "PUT", body: JSON.stringify(body || {}) });
const apiDelete = (p) => request(p, { method: "DELETE" });

// --------------------------------------------------------------------------
// Small DOM helpers
// --------------------------------------------------------------------------

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  Object.entries(attrs).forEach(([k, v]) => {
    if (k === "text") node.textContent = v;
    else if (k === "html") node.innerHTML = v;
    else node.setAttribute(k, v);
  });
  children.forEach((c) => c && node.appendChild(c));
  return node;
}
function $(id) { return document.getElementById(id); }
function todayISO() { return new Date().toISOString().slice(0, 10); }
function show(node, visible) { node.style.display = visible ? "" : "none"; }

// ==========================================================================
// AUTH SCREEN
// ==========================================================================

let pendingEmail = "";

function showAuthScreen() {
  show($("authScreen"), true);
  show($("appRoot"), false);
  show($("emailForm"), true);
  show($("codeForm"), false);
  $("authTitle").textContent = "Sign in";
  $("authSub").textContent = "Enter your email and we'll send you a login code.";
  hideAuthError();
}

function showApp() {
  show($("authScreen"), false);
  show($("appRoot"), true);
}

function showAuthError(msg) {
  const box = $("authError");
  box.textContent = msg;
  show(box, true);
}
function hideAuthError() { show($("authError"), false); }

$("emailForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  hideAuthError();
  const email = $("emailInput").value.trim();
  const name = $("nameInput").value.trim();
  const btn = $("sendCodeBtn");
  btn.disabled = true;
  btn.textContent = "Sending…";
  try {
    const res = await apiPost("/auth/request", { email, name });
    pendingEmail = res.email || email;
    show($("emailForm"), false);
    show($("codeForm"), true);
    $("authTitle").textContent = "Check your email";
    $("authSub").textContent = `Enter the 6-digit code for ${pendingEmail}.`;
    $("codeInput").value = "";
    $("codeInput").focus();

    const note = $("demoCodeNote");
    if (res.demo_code) {
      note.innerHTML = `Demo code: <strong>${res.demo_code}</strong>`;
      show(note, true);
    } else {
      show(note, false);
    }
  } catch (err) {
    showAuthError(err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Send login code";
  }
});

$("codeForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  hideAuthError();
  const code = $("codeInput").value.trim();
  try {
    const res = await apiPost("/auth/verify", {
      email: pendingEmail,
      code,
      name: $("nameInput").value.trim(),
    });
    setSession(res.token, res.user);
    await enterApp();
  } catch (err) {
    showAuthError(err.message);
  }
});

$("backToEmail").addEventListener("click", () => {
  show($("codeForm"), false);
  show($("emailForm"), true);
  $("authTitle").textContent = "Sign in";
  $("authSub").textContent = "Enter your email and we'll send you a login code.";
  hideAuthError();
});

$("logoutBtn").addEventListener("click", async () => {
  try { await apiPost("/auth/logout", {}); } catch { /* token may already be gone */ }
  clearSession();
  showAuthScreen();
});

function renderUser(user) {
  const name = user.name || (user.email || "Student").split("@")[0];
  $("userName").textContent = name;
  $("userEmail").textContent = user.email || "";
  $("userAvatar").textContent = (name[0] || "S").toUpperCase();
}

// ==========================================================================
// TABS
// ==========================================================================

document.querySelectorAll(".tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    $(`tab-${btn.dataset.tab}`).classList.add("active");
    const t = btn.dataset.tab;
    if (t === "dashboard") loadDashboard();
    if (t === "plan") loadPlan();
    if (t === "courses") loadCourses();
    if (t === "assessments") loadAssessments();
    if (t === "scores") { loadScores(); loadStudyLogs(); }
  });
});

// ==========================================================================
// DASHBOARD
// ==========================================================================

async function loadDashboard() {
  const hours = parseFloat($("totalHoursInput").value) || 20;
  const data = await apiGet(`/overview?hours=${hours}`);

  renderUser(data.user);
  $("summaryText").textContent = data.summary;

  // ---- stat strip ----
  const risks = data.risk || [];
  const high = risks.filter((r) => r.risk_level === "High").length;
  const upcomingCount = (data.plan.allocations || []).filter((a) => a.next_assessment).length;
  const topRisk = risks[0];
  const totalHours = (data.plan.allocations || []).reduce((s, a) => s + a.allocated_hours, 0);

  const strip = $("statStrip");
  strip.innerHTML = "";
  strip.appendChild(stat("Courses tracked", String(risks.length)));
  strip.appendChild(stat("High risk", String(high), high > 0 ? "risk-high" : "risk-low"));
  strip.appendChild(stat("With a deadline", String(upcomingCount)));
  strip.appendChild(stat("Hours planned", `${Math.round(totalHours)}h`));

  // ---- risk ranking ----
  const list = $("riskList");
  list.innerHTML = "";
  if (risks.length === 0) {
    list.appendChild(el("div", { class: "empty-state", text: "No courses yet. Add one in the Courses tab, or click “Load demo data”." }));
  } else {
    risks.forEach((r) => {
      const row = el("div", { class: `risk-row ${r.risk_level}` });

      const nameBlock = el("div", { class: "name-block" });
      nameBlock.appendChild(el("div", { class: "name", text: r.course_name }));
      nameBlock.appendChild(el("div", { class: "driver", text: `Driven by ${r.top_driver}` }));
      row.appendChild(nameBlock);

      const track = el("div", { class: "risk-bar-track" });
      track.appendChild(el("div", { class: `risk-bar-fill ${r.risk_level}`, style: `width:${r.risk_score}%` }));
      row.appendChild(track);

      row.appendChild(el("span", { class: "score", text: `${r.risk_score}/100` }));
      row.appendChild(el("span", { class: "badge", text: r.risk_level }));
      list.appendChild(row);
    });
  }

  // ---- next up ----
  const up = $("upcomingList");
  up.innerHTML = "";
  const withDeadlines = (data.plan.allocations || [])
    .filter((a) => a.next_assessment)
    .sort((a, b) => a.next_assessment.days_left - b.next_assessment.days_left);

  if (withDeadlines.length === 0) {
    up.appendChild(el("div", { class: "empty-state", text: "No upcoming deadlines recorded." }));
  } else {
    withDeadlines.slice(0, 5).forEach((a) => {
      const n = a.next_assessment;
      const d = n.days_left;
      const cls = d <= 2 ? "urgent" : d <= 7 ? "soon" : "later";
      const label = d === 0 ? "Due today" : `${d} day${d === 1 ? "" : "s"} left`;

      const item = el("div", { class: "upcoming-item" });
      const left = el("div");
      left.appendChild(el("div", { class: "up-title", text: n.title }));
      left.appendChild(el("div", { class: "up-meta", text: `${a.course_name} · ${n.type} · ${n.due_date}` }));
      item.appendChild(left);
      item.appendChild(el("span", { class: `up-days ${cls}`, text: label }));
      up.appendChild(item);
    });
  }
}

function stat(label, value, valueClass) {
  const box = el("div", { class: "stat" });
  box.appendChild(el("div", { class: "stat-label", text: label }));
  box.appendChild(el("div", { class: `stat-value ${valueClass || ""}`, text: value }));
  return box;
}

// ==========================================================================
// COURSES
// ==========================================================================

let coursesCache = [];

async function loadCourses() {
  coursesCache = await apiGet("/courses");
  renderCoursesTable();
  populateCourseDropdowns();
}

function renderCoursesTable() {
  const body = $("coursesTableBody");
  body.innerHTML = "";
  if (coursesCache.length === 0) {
    body.appendChild(el("tr", {}, [el("td", { colspan: "7", class: "empty-state", text: "No courses added yet." })]));
    return;
  }
  coursesCache.forEach((c) => {
    const tr = el("tr");
    tr.appendChild(el("td", { text: c.name }));
    tr.appendChild(el("td", { text: c.code || "—" }));
    tr.appendChild(el("td", { text: c.credit_hours }));
    tr.appendChild(el("td", { text: c.weekly_workload_hours }));
    tr.appendChild(el("td", { text: c.current_grade != null ? `${c.current_grade}%` : "—" }));
    tr.appendChild(el("td", { text: c.difficulty }));

    const actions = el("td");
    const editBtn = el("button", { class: "btn btn-ghost btn-small", text: "Edit", style: "margin-right:6px" });
    editBtn.addEventListener("click", () => startEditCourse(c));
    const delBtn = el("button", { class: "btn btn-danger", text: "Delete" });
    delBtn.addEventListener("click", async () => {
      if (confirm(`Delete "${c.name}"? Its deadlines and scores go too.`)) {
        await apiDelete(`/courses/${c.id}`);
        await loadCourses();
      }
    });
    actions.appendChild(editBtn);
    actions.appendChild(delBtn);
    tr.appendChild(actions);
    body.appendChild(tr);
  });
}

function startEditCourse(c) {
  $("courseId").value = c.id;
  $("courseName").value = c.name;
  $("courseCode").value = c.code || "";
  $("courseCredits").value = c.credit_hours;
  $("courseWorkload").value = c.weekly_workload_hours;
  $("courseGrade").value = c.current_grade != null ? c.current_grade : "";
  $("courseDifficulty").value = c.difficulty;
  $("courseFormTitle").textContent = `Edit “${c.name}”`;
  show($("cancelCourseEdit"), true);
  document.querySelector('[data-tab="courses"]').click();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function resetCourseForm() {
  $("courseForm").reset();
  $("courseId").value = "";
  $("courseFormTitle").textContent = "Add a course";
  show($("cancelCourseEdit"), false);
}

$("cancelCourseEdit").addEventListener("click", resetCourseForm);

$("courseForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const id = $("courseId").value;
  const payload = {
    name: $("courseName").value.trim(),
    code: $("courseCode").value.trim(),
    credit_hours: parseFloat($("courseCredits").value),
    weekly_workload_hours: parseFloat($("courseWorkload").value),
    current_grade: $("courseGrade").value === "" ? null : parseFloat($("courseGrade").value),
    difficulty: parseInt($("courseDifficulty").value, 10),
  };
  if (id) await apiPut(`/courses/${id}`, payload);
  else await apiPost("/courses", payload);
  resetCourseForm();
  await loadCourses();
});

function populateCourseDropdowns() {
  ["assessmentCourse", "scoreCourse", "logCourse"].forEach((id) => {
    const select = $(id);
    const prev = select.value;
    select.innerHTML = "";
    coursesCache.forEach((c) => select.appendChild(el("option", { value: c.id, text: c.name })));
    if (prev && coursesCache.some((c) => String(c.id) === prev)) select.value = prev;
  });
}

// ==========================================================================
// ASSESSMENTS
// ==========================================================================

async function loadAssessments() {
  const assessments = await apiGet("/assessments");
  const body = $("assessmentsTableBody");
  body.innerHTML = "";
  if (assessments.length === 0) {
    body.appendChild(el("tr", {}, [el("td", { colspan: "6", class: "empty-state", text: "No deadlines added yet." })]));
    return;
  }
  const today = todayISO();
  assessments.forEach((a) => {
    const tr = el("tr");
    tr.appendChild(el("td", { text: a.course_name }));
    tr.appendChild(el("td", { text: a.title }));
    tr.appendChild(el("td", { text: a.type }));
    const due = el("td");
    due.appendChild(el("span", { text: a.due_date }));
    if (a.due_date >= today) {
      const d = Math.round((new Date(a.due_date) - new Date(today)) / 86400000);
      due.appendChild(el("span", {
        class: `up-days ${d <= 2 ? "urgent" : d <= 7 ? "soon" : "later"}`,
        text: ` · ${d}d`,
        style: "font-size:0.78rem",
      }));
    }
    tr.appendChild(due);
    tr.appendChild(el("td", { text: `${a.weight}%` }));

    const actions = el("td");
    const delBtn = el("button", { class: "btn btn-danger", text: "Delete" });
    delBtn.addEventListener("click", async () => {
      await apiDelete(`/assessments/${a.id}`);
      await loadAssessments();
    });
    actions.appendChild(delBtn);
    tr.appendChild(actions);
    body.appendChild(tr);
  });
}

$("assessmentForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  await apiPost("/assessments", {
    course_id: parseInt($("assessmentCourse").value, 10),
    title: $("assessmentTitle").value.trim(),
    type: $("assessmentType").value,
    due_date: $("assessmentDue").value,
    weight: parseFloat($("assessmentWeight").value),
  });
  $("assessmentForm").reset();
  $("assessmentWeight").value = 10;
  await loadAssessments();
});

// ==========================================================================
// SCORES + STUDY LOGS
// ==========================================================================

async function loadScores() {
  const scores = await apiGet("/scores");
  const courseMap = Object.fromEntries(coursesCache.map((c) => [c.id, c.name]));
  const body = $("scoresTableBody");
  body.innerHTML = "";
  if (scores.length === 0) {
    body.appendChild(el("tr", {}, [el("td", { colspan: "5", class: "empty-state", text: "No scores logged yet." })]));
    return;
  }
  scores.forEach((s) => {
    const tr = el("tr");
    tr.appendChild(el("td", { text: courseMap[s.course_id] || `#${s.course_id}` }));
    tr.appendChild(el("td", { text: s.label }));
    tr.appendChild(el("td", { text: `${s.score}%` }));
    tr.appendChild(el("td", { text: s.date_taken }));
    const actions = el("td");
    const delBtn = el("button", { class: "btn btn-danger", text: "Delete" });
    delBtn.addEventListener("click", async () => {
      await apiDelete(`/scores/${s.id}`);
      await loadScores();
    });
    actions.appendChild(delBtn);
    tr.appendChild(actions);
    body.appendChild(tr);
  });
}

async function loadStudyLogs() {
  // study logs feed the workload component of the risk score
  await apiGet("/study-logs");
}

$("scoreForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  await apiPost("/scores", {
    course_id: parseInt($("scoreCourse").value, 10),
    label: $("scoreLabel").value.trim(),
    score: parseFloat($("scoreValue").value),
    date_taken: $("scoreDate").value,
  });
  $("scoreForm").reset();
  $("scoreDate").value = todayISO();
  await loadScores();
});

$("logForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  await apiPost("/study-logs", {
    course_id: parseInt($("logCourse").value, 10),
    hours: parseFloat($("logHours").value),
    log_date: $("logDate").value,
  });
  $("logForm").reset();
  $("logDate").value = todayISO();
  const fb = $("logFeedback");
  fb.textContent = "Study hours logged — this feeds the workload signal in your risk scores.";
  show(fb, true);
  setTimeout(() => show(fb, false), 4000);
});

// ==========================================================================
// WEEKLY PLAN
// ==========================================================================

async function loadPlan() {
  const hours = parseFloat($("totalHoursInput").value) || 20;
  const plan = await apiGet(`/plan?hours=${hours}`);

  const allocList = $("allocationList");
  allocList.innerHTML = "";
  if (!plan.allocations || plan.allocations.length === 0) {
    allocList.appendChild(el("div", { class: "empty-state", text: "Add courses to generate a plan." }));
  } else {
    plan.allocations.forEach((a) => {
      const row = el("div", { class: `allocation-row ${a.risk_level}` });
      const top = el("div", { class: "row-top" });
      top.appendChild(el("span", { class: "name", text: a.course_name }));
      top.appendChild(el("span", { class: "hours", text: `${a.allocated_hours} hrs` }));
      row.appendChild(top);
      let meta = `Risk ${a.risk_score}/100 (${a.risk_level}) · driven by ${a.top_driver}`;
      if (a.next_assessment) {
        const d = a.next_assessment.days_left;
        meta += ` · next: ${a.next_assessment.type} “${a.next_assessment.title}” ${d === 0 ? "due today" : `in ${d} day${d === 1 ? "" : "s"}`}`;
      }
      row.appendChild(el("div", { class: "meta", text: meta }));
      allocList.appendChild(row);
    });
  }

  const weekGrid = $("weekGrid");
  weekGrid.innerHTML = "";
  Object.entries(plan.days || {}).forEach(([day, sessions]) => {
    const col = el("div", { class: "day-col" });
    col.appendChild(el("h3", { text: day }));
    if (!sessions || sessions.length === 0) {
      col.appendChild(el("div", { class: "empty-state", text: "—", style: "font-size:0.78rem;padding:4px 0" }));
    } else {
      sessions.forEach((s) => {
        const chip = el("div", { class: `session-chip ${s.risk_level}` });
        chip.appendChild(el("div", { text: s.course_name }));
        chip.appendChild(el("span", { class: "chip-hours", text: `${s.hours} hrs` }));
        col.appendChild(chip);
      });
    }
    weekGrid.appendChild(col);
  });
}

// Changing the hours figure refreshes whichever view is on screen
$("totalHoursInput").addEventListener("change", () => {
  if ($("tab-plan").classList.contains("active")) loadPlan();
  if ($("tab-dashboard").classList.contains("active")) loadDashboard();
});

// ==========================================================================
// DEMO DATA
// ==========================================================================

$("seedBtn").addEventListener("click", async () => {
  if (!confirm("This replaces your current courses, deadlines and scores with demo data. Continue?")) return;
  await apiPost("/seed", {});
  await refreshAll();
  alert("Demo data loaded.");
});

// ==========================================================================
// INIT
// ==========================================================================

async function refreshAll() {
  await loadCourses();
  await Promise.all([loadAssessments(), loadScores(), loadDashboard()]);
  if ($("tab-plan").classList.contains("active")) await loadPlan();
}

async function enterApp() {
  const stored = getStoredUser();
  if (stored.email) renderUser(stored);
  showApp();
  await refreshAll();
}

async function boot() {
  $("scoreDate").value = todayISO();
  $("logDate").value = todayISO();

  const token = getToken();
  if (!token) {
    showAuthScreen();
    return;
  }
  // Validate the stored token before showing the app
  try {
    const me = await apiGet("/auth/me");
    setSession(token, me);
    renderUser(me);
    showApp();
    await refreshAll();
  } catch (err) {
    // token invalid/expired -> request() already cleared it on 401
    showAuthScreen();
  }
}

boot();
