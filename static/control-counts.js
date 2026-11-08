const places = [
  { key: "parking", name: "E-navbat parkovka", label: "ta mashina", subLabel: "↑chiqdi", title: "Hozir tashqi E-navbat parkovkada turgan va bugun chiqib ketgan jami transport", anchor: [-98, 11, -38] },
  { key: "inside", name: "Free Zone ichida", label: "ta mashina", title: "Hozir Free Zone hududi ichida turgan transport", anchor: [36, 11, -38] },
  { key: "border", name: "Chegara · Katta yo‘l darvozasi", label: "kutmoqda", subLabel: "↑chiqdi", title: "Katta ko‘chadagi Chegara darvozasi: E-navbat parkovkada kutayotgan va bugun Parkovka orqali Ayritomga (Chegaraga) chiqib ketgan transport", anchor: [90, 9.5, 43] },
  { key: "gate1", name: "O‘zbekiston darvozasi", point: "kpp2", title: "Parkovka va Free Zone orasidagi darvozalar oqimi", anchor: [-22, 9.5, -6] },
  { key: "scale", name: "Tarozi (Free Zone)", label: "ta tortildi", title: "Bugun Free Zone ichidagi tarozida tortilgan transport", anchor: [14, 6.5, 18] },
  { key: "gate2", name: "Afg‘oniston darvozasi (FZ)", point: "ayritom", title: "Free Zone dan Afg‘oniston (Chegara) darvozasi orqali o‘tish (KPP1)", anchor: [90, 9.5, -6] },
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
    button.title = place.title || place.name;
    button.onclick = () => selectCamera(place.key);
    const title = document.createElement("strong");
    title.textContent = place.name;
    const primary = valueField(place.point ? "↓kirdi" : place.label);
    const secondary = place.point
      ? valueField("↑chiqdi")
      : (place.subLabel ? valueField(place.subLabel) : null);
    const fields = document.createElement("span");
    fields.className = "counter-fields";
    fields.append(primary.field);
    if (secondary) fields.append(secondary.field);
    button.append(title, fields);
    container.append(button);
    return { place, button, primary, secondary };
  });

  function connect(points) {
    if (document.hidden) return;
    const w = container.clientWidth, h = container.clientHeight;
    if (!w || !h || window.innerWidth < 840) {
      for (const { button } of cards) {
        button.style.left = "";
        button.style.top = "";
        button.style.opacity = "";
      }
      return;
    }
    const placed = [];
    for (const [index, point] of points.entries()) {
      const button = cards[index].button;
      const bw = button.offsetWidth || 165, bh = button.offsetHeight || 30;
      const inView = point.z >= -1 && point.z <= 1 && Math.abs(point.x) <= 1.08 && Math.abs(point.y) <= 1.08;
      let x = (point.x + 1) * w / 2 - bw / 2;
      let y = (1 - point.y) * h / 2 - bh / 2;
      if (!inView || !Number.isFinite(x) || !Number.isFinite(y)) {
        x = w - bw - 14 - (index % 2) * (bw + 8);
        y = 12 + Math.floor(index / 2) * (bh + 6);
        button.style.opacity = "0.68";
      } else {
        button.style.opacity = "1";
      }
      const minX = 12, maxX = Math.max(minX, w - bw - 12);
      const minY = (x < 380 ? 56 : 12);
      const maxY = Math.max(minY, h - bh - (x < 280 ? 52 : 12));
      x = Math.max(minX, Math.min(maxX, x));
      y = Math.max(minY, Math.min(maxY, y));
      for (const prev of placed) {
        if (Math.abs(x - prev.x) < (bw + prev.w) * 0.52 && Math.abs(y - prev.y) < (bh + prev.h) * 0.68) {
          y = prev.y + bh + 6 <= maxY ? prev.y + bh + 6 : Math.max(minY, prev.y - bh - 6);
        }
      }
      placed.push({ x, y, w: bw, h: bh });
      button.style.left = Math.round(x) + "px";
      button.style.top = Math.round(y) + "px";
    }
  }

  function update(data, reset) {
    const parking = data.counts.find(row => row.area === "parking");
    const inside = data.counts.find(row => row.area === "inside");
    const pFlow = data.parking_flow || {};
    const pCross = (data.crossings || []).find(row => row.point === "parkovka_tashqi");
    const occupied = {
      parking: parking?.parked ?? 0,
      inside: inside?.n ?? 0,
      border: parking?.border_wait ?? 0,
      scale: data.weighed_today ?? 0
    };
    const subValues = {
      parking: pFlow.departures ?? pCross?.departures ?? 0,
      border: pFlow.border_departures ?? 0
    };
    for (const { place, primary, secondary } of cards) {
      if (place.point) {
        const count = data.crossings.find(row => row.point === place.point);
        updateNumber(primary.value, count?.arrivals ?? 0, reset);
        updateNumber(secondary.value, count?.departures ?? 0, reset);
      } else {
        updateNumber(primary.value, occupied[place.key], reset);
        if (secondary && place.key in subValues) {
          updateNumber(secondary.value, subValues[place.key], reset);
        }
      }
    }
  }
  return { connect, update };
}
