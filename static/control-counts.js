const places = [
  { key: "parking", name: "E-navbat parkovka", label: "Hozir parkovkada", anchor: [-62, 1, -4] },
  { key: "inside", name: "Free Zone", label: "Hozir hududda", anchor: [27, 1, -4] },
  { key: "border", name: "Chegara yo‘nalishi", label: "Parkovkada kutmoqda", anchor: [94, 1, -4] },
  { key: "gate1", name: "O‘zbekiston darvozasi", point: "kpp2", anchor: [-18, 3, 19] },
  { key: "scale", name: "Tarozi", label: "Bugun tortilgan avtomobil", anchor: [24, 1, 33] },
  { key: "gate2", name: "Afg‘oniston darvozasi", point: "ayritom", anchor: [65, 3, 19] },
];

export const mapAnchors = places.map(place => place.anchor);
const motion = matchMedia("(prefers-reduced-motion: reduce)");
const format = new Intl.NumberFormat("uz-UZ");

function valueField(label) {
  const field = document.createElement("span");
  field.className = "counter-field";
  const value = document.createElement("b");
  value.textContent = "—";
  const caption = document.createElement("small");
  caption.textContent = label;
  field.append(value, caption);
  return { field, value };
}

function updateNumber(node, value, reset) {
  if (node.dataset.count === String(value)) return;
  const animate = !reset && node.dataset.count !== undefined && !motion.matches && !document.hidden;
  node.dataset.count = String(value);
  node.textContent = format.format(value);
  if (animate && node.animate) {
    node.getAnimations().forEach(animation => animation.cancel());
    node.animate([
      { transform: "translateY(.13em) scale(.92)", opacity: .55 },
      { transform: "translateY(0) scale(1)", opacity: 1 }
    ], { duration: 420, easing: "cubic-bezier(.18,.8,.28,1)" });
  }
}

export function createCounters(container, selectCamera) {
  const cards = places.map(place => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "place-counter";
    button.dataset.place = place.key;
    button.onclick = () => selectCamera(place.key);
    const title = document.createElement("strong");
    title.textContent = place.name;
    const primary = valueField(place.point ? "Bugun kirdi" : place.label);
    const secondary = place.point ? valueField("Bugun chiqdi") : null;
    const fields = document.createElement("span");
    fields.className = "counter-fields";
    fields.append(primary.field);
    if (secondary) fields.append(secondary.field);
    button.append(title, fields);
    container.append(button);
    return { place, button, primary, secondary };
  });
  const lines = document.getElementById("map-leaders");
  const ctx = lines.getContext("2d");
  // Connect the fixed, legible cards to their world positions as the camera moves.
  function connect(points) {
    if (!ctx || document.hidden || !lines.clientWidth) return;
    const w = lines.clientWidth, h = lines.clientHeight;
    if (lines.width !== w || lines.height !== h) { lines.width = w; lines.height = h; }
    ctx.clearRect(0, 0, w, h);
    const bounds = lines.getBoundingClientRect();
    for (const [index, point] of points.entries()) {
      if (point.z < -1 || point.z > 1 || Math.abs(point.x) > 1 || Math.abs(point.y) > 1) continue;
      const button = cards[index].button, rect = button.getBoundingClientRect();
      const x = (point.x + 1) * w / 2, y = (1 - point.y) * h / 2;
      const startX = rect.left - bounds.left + rect.width / 2;
      const startY = (index < 3 ? rect.bottom : rect.top) - bounds.top;
      ctx.strokeStyle = getComputedStyle(button).getPropertyValue("--place").trim();
      ctx.globalAlpha = .62;
      ctx.beginPath(); ctx.moveTo(startX, startY); ctx.lineTo(x, y); ctx.stroke();
      ctx.globalAlpha = 1;
      ctx.fillStyle = ctx.strokeStyle;
      ctx.beginPath(); ctx.arc(x, y, 3, 0, 2*Math.PI); ctx.fill();
    }
  }
  function update(data, reset) {
    const parking = data.counts.find(row => row.area === "parking");
    const inside = data.counts.find(row => row.area === "inside");
    const occupied = { parking: parking?.parked ?? 0, inside: inside?.n ?? 0, border: parking?.border_wait ?? 0, scale:data.weighed_today };
    for (const { place, primary, secondary } of cards) {
      if (place.point) {
        const count = data.crossings.find(row => row.point === place.point);
        updateNumber(primary.value, count?.arrivals ?? 0, reset);
        updateNumber(secondary.value, count?.departures ?? 0, reset);
      } else updateNumber(primary.value, occupied[place.key], reset);
    }
  }
  return { connect, update };
}
