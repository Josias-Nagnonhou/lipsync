"use strict";

const COLAB = "https://colab.research.google.com/github/Josias-Nagnonhou/lipsync/blob/main/LipSync.ipynb";
const TAILLE_ENVOI = 8 * 1024 * 1024; // les fichiers partent en morceaux de 8 Mo
const NB_BARRES = 140;

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

const memo = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch {} },
  del(k) { try { localStorage.removeItem(k); } catch {} },
};

// ------------------------------------------------------------ adresse du moteur
let MOTEUR = null;
(function lireAdresse() {
  const url = new URL(location.href);
  const p = url.searchParams.get("moteur");
  if (p) {
    memo.set("lipsync_moteur", p.replace(/\/+$/, ""));
    url.searchParams.delete("moteur");
    history.replaceState(null, "", url.pathname + url.search + url.hash);
  }
  MOTEUR = memo.get("lipsync_moteur");
})();

$("#lienColab").href = COLAB;

async function api(chemin, opts = {}) {
  const r = await fetch(MOTEUR + chemin, opts);
  if (!r.ok) {
    let msg = `Erreur ${r.status}`;
    try { msg = (await r.json()).detail || msg; } catch {}
    throw new Error(msg);
  }
  return r.json();
}

// ------------------------------------------------------------ état du moteur
let allume = null;
async function verifierMoteur() {
  const pill = $("#moteurPill"), txt = $("#moteurTexte");
  if (!MOTEUR) return afficherEteint();
  try {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), 8000);
    const e = await api("/api/etat", { signal: ctrl.signal });
    clearTimeout(t);
    pill.className = "moteur on";
    txt.textContent = `Moteur allumé, ${e.gpu}`;
    if (allume !== true) { allume = true; $("#vueEteint").hidden = true; $("#vueApp").hidden = false; chargerVideos(); }
  } catch {
    afficherEteint();
  }
}
function afficherEteint() {
  $("#moteurPill").className = "moteur off";
  $("#moteurTexte").textContent = "Moteur éteint";
  if (allume !== false) { allume = false; $("#vueApp").hidden = true; $("#vueEteint").hidden = false; }
}
$("#moteurPill").addEventListener("click", () => { allume === false ? window.open(COLAB, "_blank", "noopener") : verifierMoteur(); });
$("#formAdresse").addEventListener("submit", (ev) => {
  ev.preventDefault();
  MOTEUR = $("#champAdresse").value.trim().replace(/\/+$/, "");
  memo.set("lipsync_moteur", MOTEUR);
  allume = null; verifierMoteur();
});

// ------------------------------------------------------------ onglets
function aller(vue) {
  $$(".onglet").forEach(b => b.setAttribute("aria-selected", String(b.dataset.vue === vue)));
  $("#vueCreer").hidden = vue !== "creer";
  $("#vueVideos").hidden = vue !== "videos";
  if (vue === "videos") chargerVideos();
}
$$(".onglet").forEach(b => b.addEventListener("click", () => aller(b.dataset.vue)));
$$("[data-aller]").forEach(b => b.addEventListener("click", () => aller(b.dataset.aller)));

// ------------------------------------------------------------ dépôt des fichiers
const choisi = { visage: null, voix: null, duree: 0, onde: [] };

function brancherZone(zone, input, onFichier) {
  zone.addEventListener("click", (ev) => {
    if (ev.target.closest("audio, .changer")) return;
    if (zone.classList.contains("pleine")) { ev.preventDefault(); return; }
  });
  zone.addEventListener("dragover", (ev) => { ev.preventDefault(); zone.classList.add("survol"); });
  zone.addEventListener("dragleave", () => zone.classList.remove("survol"));
  zone.addEventListener("drop", (ev) => {
    ev.preventDefault(); zone.classList.remove("survol");
    const f = ev.dataTransfer.files[0];
    if (f) onFichier(f);
  });
  input.addEventListener("change", () => input.files[0] && onFichier(input.files[0]));
}
$$(".changer").forEach(b => b.addEventListener("click", (ev) => {
  ev.preventDefault(); ev.stopPropagation(); $("#" + b.dataset.cible).click();
}));

brancherZone($("#zoneVisage"), $("#fichierVisage"), (f) => {
  if (!/^(image|video)\//.test(f.type)) return montrerErreur("Pour le visage, choisis une photo ou une vidéo.");
  choisi.visage = f;
  const box = $("#apercuVisage");
  box.innerHTML = "";
  const url = URL.createObjectURL(f);
  if (f.type.startsWith("image/")) {
    const img = new Image(); img.src = url; img.alt = "Visage choisi"; box.append(img);
  } else {
    const v = document.createElement("video");
    Object.assign(v, { src: url, muted: true, loop: true, autoplay: true, playsInline: true });
    box.append(v);
  }
  $("#zoneVisage").classList.add("pleine");
  if (!$("#titre").value) $("#titre").placeholder = "Ex. Psaume 91, épisode 12";
  majResume();
});

brancherZone($("#zoneVoix"), $("#fichierVoix"), async (f) => {
  if (!/^audio\//.test(f.type) && !/\.(mp3|wav|m4a|aac|ogg|flac)$/i.test(f.name)) {
    return montrerErreur("Pour la voix, choisis un fichier audio (mp3, wav, m4a…).");
  }
  choisi.voix = f;
  $("#zoneVoix").classList.add("pleine");
  $("#nomVoix").textContent = f.name;
  $("#dureeVoix").textContent = "…";
  const lecteur = $("#lecteurVoix");
  lecteur.src = URL.createObjectURL(f);
  if (!$("#titre").value) $("#titre").value = f.name.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " ").slice(0, 80);
  try {
    const { duree, onde } = await analyserVoix(f);
    choisi.duree = duree; choisi.onde = onde;
  } catch {
    choisi.duree = await new Promise(res => {
      lecteur.onloadedmetadata = () => res(lecteur.duration || 0);
      lecteur.onerror = () => res(0);
    });
    choisi.onde = [];
  }
  $("#dureeVoix").textContent = choisi.duree ? formatDuree(choisi.duree) : "";
  dessinerOnde($("#ondeVoix"), choisi.onde, 1, "voix");
  majResume();
});

async function analyserVoix(fichier) {
  // décodage à basse fréquence : léger même pour 30 minutes d'audio, y compris sur téléphone
  const buf = await fichier.arrayBuffer();
  const Ctx = window.OfflineAudioContext || window.webkitOfflineAudioContext;
  const ctx = new Ctx(1, 8000, 8000);
  const audio = await ctx.decodeAudioData(buf);
  const data = audio.getChannelData(0);
  const pas = Math.max(1, Math.floor(data.length / NB_BARRES));
  const onde = [];
  for (let i = 0; i < NB_BARRES; i++) {
    let max = 0;
    for (let j = i * pas, fin = Math.min(data.length, (i + 1) * pas); j < fin; j += 4) {
      const v = Math.abs(data[j]); if (v > max) max = v;
    }
    onde.push(max);
  }
  const haut = Math.max(...onde, 0.01);
  return { duree: audio.duration, onde: onde.map(v => +(v / haut).toFixed(3)) };
}

// ------------------------------------------------------------ dessin de l'onde
const couleurs = () => {
  const s = getComputedStyle(document.documentElement);
  return { safran: s.getPropertyValue("--safran").trim(), trait: s.getPropertyValue("--trait").trim(),
           menthe: s.getPropertyValue("--menthe").trim(), corail: s.getPropertyValue("--corail").trim() };
};
function dessinerOnde(canvas, onde, part, mode, passages = 0) {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (!w) return;
  canvas.width = w * dpr; canvas.height = h * dpr;
  const c = canvas.getContext("2d");
  c.scale(dpr, dpr);
  c.clearRect(0, 0, w, h);
  const col = couleurs();
  const valeurs = onde && onde.length ? onde : Array.from({ length: NB_BARRES }, (_, i) => 0.35 + 0.25 * Math.sin(i * 0.7) * Math.sin(i * 0.13));
  const n = Math.min(valeurs.length, Math.max(24, Math.floor(w / 5)));
  const barre = w / n;
  const plein = mode === "fini" ? col.menthe : mode === "erreur" ? col.corail : col.safran;
  for (let i = 0; i < n; i++) {
    const v = valeurs[Math.floor(i * valeurs.length / n)];
    const bh = Math.max(3, v * (h - 4));
    c.fillStyle = (i + 0.5) / n <= part ? plein : col.trait;
    const x = i * barre + barre * 0.18, bw = Math.max(1.5, barre * 0.64);
    c.beginPath();
    c.roundRect ? c.roundRect(x, (h - bh) / 2, bw, bh, bw / 2) : c.rect(x, (h - bh) / 2, bw, bh);
    c.fill();
  }
  if (passages > 1 && mode !== "fini") {
    // repères des passages traités un par un par le moteur
    c.fillStyle = "rgba(243,237,227,.35)";
    for (let k = 1; k < passages; k++) c.fillRect(Math.round(k * w / passages), h - 3, 1, 3);
  }
}

// ------------------------------------------------------------ réglages et résumé
const reglage = (nom) => $(`input[name="${nom}"]:checked`).value;
$("#precision").addEventListener("input", (e) => { $("#valPrecision").textContent = String(e.target.value).replace(".", ","); });
$("#morceau").addEventListener("input", (e) => { $("#valMorceau").textContent = e.target.value + " s"; majResume(); });

function majResume() {
  const pret = choisi.visage && choisi.voix;
  $("#btnLancer").disabled = !pret;
  const r = $("#resumeTravail");
  if (!choisi.visage && !choisi.voix) r.textContent = "Ajoute un visage et une voix.";
  else if (!choisi.visage) r.textContent = "Il manque le visage.";
  else if (!choisi.voix) r.textContent = "Il manque la voix.";
  else if (choisi.duree) {
    const n = Math.ceil(choisi.duree / +$("#morceau").value);
    r.innerHTML = `Vidéo de <b>${formatDuree(choisi.duree)}</b>, traitée en <b>${n} passage${n > 1 ? "s" : ""}</b>. Tu peux fermer cette page pendant le travail.`;
  } else r.textContent = "Tout est prêt.";
}

function montrerErreur(msg) {
  const e = $("#erreurCreer");
  e.textContent = msg; e.hidden = !msg;
}

// ------------------------------------------------------------ envoi
async function envoyerFichier(f, onProgres) {
  const { id } = await api("/api/envoi", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ nom: f.name, taille: f.size }),
  });
  const total = Math.max(1, Math.ceil(f.size / TAILLE_ENVOI));
  for (let i = 0; i < total; i++) {
    const morceau = f.slice(i * TAILLE_ENVOI, (i + 1) * TAILLE_ENVOI);
    let essai = 0;
    for (;;) {
      try { await api(`/api/envoi/${id}/${i}`, { method: "PUT", body: morceau }); break; }
      catch (e) { if (++essai >= 3) throw e; await new Promise(r => setTimeout(r, 1500 * essai)); }
    }
    onProgres((i + 1) / total);
  }
  return (await api(`/api/envoi/${id}/fin`, { method: "POST" })).fichier;
}

$("#vueCreer").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  if (!choisi.visage || !choisi.voix) return;
  montrerErreur("");
  const btn = $("#btnLancer");
  btn.disabled = true; btn.textContent = "Envoi en cours…";
  $("#envoi").hidden = false;
  const tailleTotale = choisi.visage.size + choisi.voix.size;
  const prog = (deja, f) => (p) => {
    const pct = Math.round(((deja + p * f.size) / tailleTotale) * 100);
    $("#barreEnvoi").style.width = pct + "%";
    $("#texteEnvoi").textContent = `Envoi des fichiers au moteur : ${pct} %`;
  };
  try {
    const visage = await envoyerFichier(choisi.visage, prog(0, choisi.visage));
    const audio = await envoyerFichier(choisi.voix, prog(choisi.visage.size, choisi.voix));
    const { nom } = await api("/api/projets", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        titre: $("#titre").value.trim() || choisi.voix.name.replace(/\.[^.]+$/, ""),
        visage, audio, onde: choisi.onde,
        morceau: +$("#morceau").value, resolution: reglage("resolution"),
        qualite: reglage("qualite"), precision: +$("#precision").value,
      }),
    });
    reinitialiser();
    nouvelle = nom;
    aller("videos");
  } catch (e) {
    montrerErreur("L'envoi n'a pas abouti : " + e.message + ". Vérifie que la page du moteur est toujours ouverte, puis réessaie.");
  } finally {
    btn.textContent = "Lancer la vidéo"; $("#envoi").hidden = true; $("#barreEnvoi").style.width = "0";
    majResume();
  }
});

function reinitialiser() {
  Object.assign(choisi, { visage: null, voix: null, duree: 0, onde: [] });
  $("#zoneVisage").classList.remove("pleine"); $("#zoneVoix").classList.remove("pleine");
  $("#apercuVisage").innerHTML = ""; $("#lecteurVoix").removeAttribute("src");
  $("#fichierVisage").value = ""; $("#fichierVoix").value = ""; $("#titre").value = "";
}

// ------------------------------------------------------------ mes vidéos
let nouvelle = null;
let minuteur = null;
const ETATS = {
  attente: "En file d'attente", preparation: "Préparation", synchro: "Synchronisation en cours",
  assemblage: "Assemblage", termine: "Prête", erreur: "Arrêtée", interrompu: "Interrompue",
};

async function chargerVideos() {
  clearTimeout(minuteur);
  if (!allume) return;
  let liste = [];
  try { liste = await api("/api/projets"); } catch { return verifierMoteur(); }
  const enCours = liste.filter(v => ["attente", "preparation", "synchro", "assemblage"].includes(v.etat)).length;
  $("#compte").textContent = liste.length || "";
  $("#listeVide").hidden = liste.length > 0;
  const box = $("#listeVideos");
  const anciennes = new Map($$(".carte-video", box).map(el => [el.dataset.nom, el]));
  box.replaceChildren(...liste.map(v => carte(v, anciennes.get(v.nom))));
  minuteur = setTimeout(chargerVideos, enCours ? 4000 : 15000);
}

function carte(v, el) {
  if (!el) {
    el = $("#modeleVideo").content.firstElementChild.cloneNode(true);
    el.dataset.nom = v.nom;
    const img = $(".vignette img", el);
    if (v.miniature) img.src = `${MOTEUR}/api/projets/${encodeURIComponent(v.nom)}/miniature`;
    else img.remove();
  }
  el.classList.toggle("nouvelle", v.nom === nouvelle);
  $("h3", el).textContent = v.titre;
  const statut = $(".statut", el);
  statut.className = "statut" + (v.etat === "termine" ? " ok" : ["erreur", "interrompu"].includes(v.etat) ? " ko" : "");
  statut.textContent = v.etat === "synchro" && v.total ? `${ETATS.synchro} : passage ${Math.min(v.faits + 1, v.total)} sur ${v.total}` : (ETATS[v.etat] || v.etat);

  const part = v.etat === "termine" ? 1 : v.total ? v.faits / v.total : 0;
  const mode = v.etat === "termine" ? "fini" : ["erreur", "interrompu"].includes(v.etat) ? "erreur" : "cours";
  requestAnimationFrame(() => dessinerOnde($(".progres", el), v.onde, part, mode, v.total || 0));

  const d = [];
  if (v.duree) d.push(formatDuree(v.duree));
  if (v.etat === "synchro" && v.restant) d.push(`environ ${formatAttente(v.restant)} restantes`);
  if (v.etat === "synchro" && !v.restant) d.push("calcul du temps restant après le premier passage");
  if (v.etat === "termine" && v.taille) d.push(formatTaille(v.taille));
  if (["erreur", "interrompu"].includes(v.etat)) d.push(v.etat === "interrompu" ? "Le moteur s'est éteint pendant le travail. Les passages finis sont gardés." : v.message);
  $(".details", el).textContent = d.join(", ");

  const a = $(".actions", el);
  const cle = v.etat;
  if (a.dataset.etat !== cle) {
    a.dataset.etat = cle; a.replaceChildren();
    const base = `${MOTEUR}/api/projets/${encodeURIComponent(v.nom)}/video`;
    if (v.etat === "termine") {
      a.append(bouton("Regarder", "btn btn-petit", () => ouvrirLecteur(v.titre, base)));
      const dl = document.createElement("a");
      dl.className = "btn btn-petit btn-principal"; dl.textContent = "Télécharger"; dl.href = base + "?telecharger=1";
      a.append(dl);
    } else if (["erreur", "interrompu"].includes(v.etat)) {
      a.append(bouton("Reprendre", "btn btn-petit btn-principal", async (b) => {
        b.disabled = true;
        try { await api(`/api/projets/${encodeURIComponent(v.nom)}/reprendre`, { method: "POST" }); chargerVideos(); }
        catch (e) { b.disabled = false; alert(e.message); }
      }));
    }
  }
  return el;
}

function bouton(texte, cls, fn) {
  const b = document.createElement("button");
  b.type = "button"; b.className = cls; b.textContent = texte;
  b.addEventListener("click", () => fn(b));
  return b;
}

function ouvrirLecteur(titre, src) {
  const d = $("#lecteur");
  $("#lecteurTitre").textContent = titre;
  $("#lecteurVideo").src = src;
  d.showModal();
}
$("#lecteur .fermer").addEventListener("click", () => $("#lecteur").close());
$("#lecteur").addEventListener("close", () => { const v = $("#lecteurVideo"); v.pause(); v.removeAttribute("src"); v.load(); });

// ------------------------------------------------------------ formats
function formatDuree(s) {
  s = Math.round(s);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  return h ? `${h} h ${String(m).padStart(2, "0")}` : `${m}:${String(r).padStart(2, "0")}`;
}
function formatAttente(s) {
  const m = Math.ceil(s / 60);
  if (m < 60) return `${m} min`;
  return `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")}`;
}
function formatTaille(o) { return o > 1e9 ? (o / 1e9).toFixed(1).replace(".", ",") + " Go" : Math.round(o / 1e6) + " Mo"; }

let attenteRedim;
window.addEventListener("resize", () => {
  clearTimeout(attenteRedim);
  attenteRedim = setTimeout(() => { if (choisi.voix) dessinerOnde($("#ondeVoix"), choisi.onde, 1, "voix"); chargerVideos(); }, 200);
});

verifierMoteur();
setInterval(verifierMoteur, 20000);
