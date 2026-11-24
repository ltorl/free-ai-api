const API = "http://localhost:8000/ask";

const $ = (id) => document.getElementById(id);

// Read the user's live google.com cookies and build a Cookie header string.
// Requires "cookies" permission + host_permissions for google.com.
async function getGoogleCookieString() {
  const cookies = await chrome.cookies.getAll({ domain: ".google.com" });
  // Only the cookies that matter for an authenticated search session.
  return cookies.map((c) => `${c.name}=${c.value}`).join("; ");
}

async function ask() {
  const query = $("q").value.trim();
  if (!query) return;

  $("status").textContent = "Thinking…";
  $("status").className = "muted";
  $("answer").textContent = "";
  $("sources").innerHTML = "";

  try {
    const cookie = await getGoogleCookieString();
    const res = await fetch(API, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, cookie }),
    });
    const data = await res.json();

    if (!res.ok) {
      $("status").textContent = "Error: " + (data.detail || res.status);
      $("status").className = "err";
      return;
    }

    $("status").textContent = data.ok ? "" : "Parsed nothing — check server debug_blocks.";
    $("answer").textContent = data.answer;

    for (const s of data.sources || []) {
      const a = document.createElement("a");
      a.href = s.url;
      a.target = "_blank";
      a.textContent = s.title || s.url;
      $("sources").appendChild(a);
    }
  } catch (e) {
    $("status").textContent = "Request failed: " + e.message + " (is the server running?)";
    $("status").className = "err";
  }
}

$("go").addEventListener("click", ask);
$("q").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) ask();
});
