const frontendUrl = process.env.FRONTEND_URL || "http://localhost:3000";
const apiUrl = process.env.API_URL || "http://localhost:8000/api";

async function expectOk(url, label) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${label} returned ${response.status}`);
  return response;
}

const page = await expectOk(frontendUrl, "frontend");
const html = await page.text();
if (!html.includes("SpendWise")) throw new Error("frontend shell did not render SpendWise");

const health = await (await expectOk(`${apiUrl}/health`, "API health")).json();
if (health.status !== "ok") throw new Error("API health payload was not ok");

const dashboard = await (await expectOk(`${apiUrl}/dashboard`, "dashboard API")).json();
if (!dashboard.metrics || !Array.isArray(dashboard.transactions)) throw new Error("dashboard payload shape is invalid");

console.log(`smoke ok: frontend rendered, API healthy, ${dashboard.transactions.length} transaction(s) loaded`);
