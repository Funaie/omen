/**
 * Cloudflare Pages Function: POST /api/apply -> Discord-webhook.
 *
 * Filens sti ER ruten. functions/api/apply.js bliver til /api/apply.
 *
 * Hele grunden til, at den her findes: webhook-URL'en maa ikke ligge i
 * browseren. Ligger den der, kan hvem som helst spamme Omens Discord.
 * Derfor er den en secret i Cloudflare, og frontend kender kun /api/apply.
 *
 * Miljoevariabler (Cloudflare Pages -> Settings -> Environment variables):
 *   DISCORD_WEBHOOK_URL  paakraevet, markeres som "Secret"
 *   OFFICER_ROLE_ID      valgfri. Uden den pinges ingen.
 */

const LIMITS = {
  character: 60,
  discord: 60,
  class: 30,
  spec: 40,
  logs: 300,
  attendance: 20,
  why: 400,
};

// Simpel takt-graense pr. IP. Cloudflares runtime nulstiller den ved cold
// start, saa den stopper spam-bursts — ikke en beslutsom angriber. Det er
// den rigtige afvejning for en guild-formular.
const WINDOW_MS = 10 * 60 * 1000;
const MAX_PER_WINDOW = 3;
const seen = new Map();

function rateLimited(ip) {
  const now = Date.now();
  const hits = (seen.get(ip) || []).filter((t) => now - t < WINDOW_MS);
  hits.push(now);
  seen.set(ip, hits);

  // Ryd op, saa kortet ikke vokser i det uendelige i en varm isolate.
  if (seen.size > 5000) {
    for (const [key, times] of seen) {
      if (times.every((t) => now - t >= WINDOW_MS)) seen.delete(key);
    }
  }
  return hits.length > MAX_PER_WINDOW;
}

const json = (body, status) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

/** Klip til maks laengde, og fjern det, der kan kapre en Discord-besked. */
function clean(value, max) {
  return String(value ?? "")
    .slice(0, max)
    .replace(/[`@]/g, "")          // ingen kode-blokke, ingen falske pings
    .replace(/\s+/g, " ")
    .trim();
}

async function handleApply({ request, env }) {
  if (!env.DISCORD_WEBHOOK_URL) {
    // Konfigurationsfejl, ikke brugerfejl — sig det praecist i loggen,
    // men lad vaere med at afsloere opsaetningen for den, der ansoeger.
    console.error("DISCORD_WEBHOOK_URL mangler i miljoeet");
    return json({ error: "The form is not set up yet." }, 503);
  }

  const ip = request.headers.get("CF-Connecting-IP") || "ukendt";
  if (rateLimited(ip)) return json({ error: "Too many attempts." }, 429);

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "Malformed request." }, 400);
  }

  // Honeypot: feltet er usynligt for mennesker. Er det udfyldt, er det en bot.
  // Vi svarer 200, saa bot'en tror den lykkedes og ikke proever igen.
  if (body.website) return json({ ok: true }, 200);

  const fields = {};
  for (const [name, max] of Object.entries(LIMITS)) {
    fields[name] = clean(body[name], max);
  }

  for (const name of ["character", "discord", "class", "spec"]) {
    if (!fields[name]) return json({ error: `The field "${name}" is missing.` }, 400);
  }

  const ping = env.OFFICER_ROLE_ID ? `<@&${env.OFFICER_ROLE_ID}> ` : "";

  const embed = {
    title: fields.character,
    description: `**${fields.class}** · ${fields.spec}`,
    color: 0xc8a45c,
    url: fields.logs && /^https?:\/\//i.test(fields.logs) ? fields.logs : undefined,
    fields: [
      { name: "Discord", value: fields.discord, inline: true },
      { name: "Thursdays", value: fields.attendance || "—", inline: true },
    ],
    timestamp: new Date().toISOString(),
    footer: { text: "Sent from the application form" },
  };

  if (fields.why) embed.fields.push({ name: "Why Omen?", value: fields.why });
  if (fields.logs && !embed.url) embed.fields.push({ name: "Logs", value: fields.logs });

  const discord = await fetch(env.DISCORD_WEBHOOK_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      content: `${ping}New application from **${fields.character}**`,
      embeds: [embed],
      // Kun officer-rollen maa pinges — aldrig @everyone, uanset hvad
      // nogen skriver i et felt.
      allowed_mentions: env.OFFICER_ROLE_ID
        ? { roles: [env.OFFICER_ROLE_ID] }
        : { parse: [] },
    }),
  });

  if (!discord.ok) {
    console.error("Discord svarede", discord.status, await discord.text());
    return json({ error: "Could not deliver the application." }, 502);
  }

  return json({ ok: true }, 200);
}

// ÉN eksporteret handler, der selv forgrener paa metoden.
//
// Foerste udgave eksporterede kun onRequestPost ud fra en antagelse om, at
// Pages selv svarede 405 paa alt andet. Det gjorde den ikke: en GET faldt
// igennem til den statiske haandtering og fik HELE forsiden serveret som
// HTML med status 200. Et API-endpoint, der svarer med en hjemmeside.
// Maalt i produktion, ikke gaettet.
export async function onRequest(context) {
  if (context.request.method !== "POST") {
    return new Response("Method Not Allowed", {
      status: 405,
      headers: { Allow: "POST", "Content-Type": "text/plain" },
    });
  }
  return handleApply(context);
}
