document.addEventListener("DOMContentLoaded", () => {
  const toggle = document.querySelector(".nav-toggle");
  const nav = document.querySelector(".nav-links");
  if (toggle && nav) {
    toggle.addEventListener("click", () => {
      const open = nav.classList.toggle("nav-open");
      toggle.setAttribute("aria-expanded", String(open));
    });
  }

  document.querySelectorAll("form").forEach((form) => {
    form.addEventListener("submit", () => {
      const submit = form.querySelector('button[type="submit"]');
      if (submit && form.checkValidity()) {
        submit.dataset.originalText = submit.textContent;
        submit.textContent = "Please wait…";
        submit.disabled = true;
      }
    });
  });
});

async function enhanceWithAI(fieldId, btn) {
  const textarea = document.getElementById(fieldId);
  if (!textarea) return;

  const text = textarea.value.trim();
  if (!text) {
    alert("Please type a few rough notes or bullet points first, then click Polish with AI.");
    textarea.focus();
    return;
  }

  const role = document.getElementById("field-job-title")?.value.trim() || "";
  const originalHtml = btn.innerHTML;
  btn.innerHTML = '<span class="ai-spinner"></span> Polishing with AI...';
  btn.disabled = true;

  try {
    const res = await fetch("/api/ai/enhance", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, role })
    });
    const data = await res.json();
    if (data.success && data.result) {
      textarea.value = data.result;
      textarea.classList.add("ai-highlight");
      setTimeout(() => textarea.classList.remove("ai-highlight"), 1200);
    } else {
      alert(data.message || "Could not enhance text with AI.");
    }
  } catch (err) {
    alert("Network error communicating with AI service. Please try again.");
  } finally {
    btn.innerHTML = originalHtml;
    btn.disabled = false;
  }
}

async function generateSummaryAI(btn) {
  const summaryArea = document.getElementById("field-summary");
  const jobTitle = document.getElementById("field-job-title")?.value.trim() || "";
  const skills = document.getElementById("field-skills")?.value.trim() || "";
  const experience = document.getElementById("field-experience")?.value.trim() || "";

  if (!jobTitle && !skills && !experience) {
    alert("Please enter a Target Job Title or some skills first so AI can write your summary.");
    document.getElementById("field-job-title")?.focus();
    return;
  }

  const originalHtml = btn.innerHTML;
  btn.innerHTML = '<span class="ai-spinner"></span> Generating with AI...';
  btn.disabled = true;

  try {
    const res = await fetch("/api/ai/summary", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_title: jobTitle, skills, experience })
    });
    const data = await res.json();
    if (data.success && data.result) {
      summaryArea.value = data.result;
      summaryArea.classList.add("ai-highlight");
      setTimeout(() => summaryArea.classList.remove("ai-highlight"), 1200);
    } else {
      alert(data.message || "Could not generate summary with AI.");
    }
  } catch (err) {
    alert("Network error communicating with AI service. Please try again.");
  } finally {
    btn.innerHTML = originalHtml;
    btn.disabled = false;
  }
}
